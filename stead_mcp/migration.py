"""Create and restore the private Stead migration bundle.

The bundle contains secrets and household history. It is deliberately kept out
of Git, written mode 0600, and must be transferred through a private channel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, Optional

FORMAT_NAME = "stead-backup"
FORMAT_VERSION = 1
ARCHIVE_ROOT = "stead-backup-v1"
PROFILE = "stead-kerstin-demo"
SERVICE = f"hermes-gateway-{PROFILE}.service"
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_FILE_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_TOTAL_SIZE = 1024 * 1024 * 1024
_ALLOWED_EXACT_FILES = {
    "manifest.json",
    "private/.env",
    "state/stead.sqlite",
    "profile/state.db",
    "profile/cron/jobs.json",
    "profile/cron/executions.db",
    "profile/channel_directory.json",
    "profile/gateway_state.json",
}
_MANDATORY_FILES = {"private/.env", "state/stead.sqlite"}
_ALLOWED_TREE_PREFIXES = ("profile/memories/", "profile/sessions/")
_ALLOWED_DIRECTORIES = {
    "",
    "private",
    "state",
    "profile",
    "profile/cron",
    "profile/memories",
    "profile/sessions",
}


class MigrationError(RuntimeError):
    """The backup or restore cannot proceed safely."""


class RollbackIncompleteError(MigrationError):
    """A restore failed and one or more original targets could not be restored."""


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_info = source.lstat()
    except FileNotFoundError as exc:
        raise MigrationError(f"required SQLite database is missing: {source}") from exc
    if not stat.S_ISREG(source_info.st_mode):
        raise MigrationError(f"SQLite source must be a regular file: {source}")
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise MigrationError(f"SQLite integrity check failed: {source}")
    finally:
        dst.close()
        src.close()
    final_info = source.lstat()
    if (source_info.st_dev, source_info.st_ino) != (final_info.st_dev, final_info.st_ino):
        raise MigrationError(f"SQLite source changed while being backed up: {source}")
    destination.chmod(0o600)


def _copy_private_file(source: Path, destination: Path, *, required: bool = False) -> bool:
    if not source.exists():
        if required:
            raise MigrationError(f"required private file is missing: {source}")
        return False
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise MigrationError(f"private path must be a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o600)
    return True


def _copy_private_tree(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    if not source.is_dir() or source.is_symlink():
        raise MigrationError(f"private path must be a real directory: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise MigrationError(f"symlinks are not allowed in private state: {path}")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("*.lock"))
    for directory in [destination, *[p for p in destination.rglob("*") if p.is_dir()]]:
        directory.chmod(0o700)
    for file_path in [p for p in destination.rglob("*") if p.is_file()]:
        file_path.chmod(0o600)
    return True


def _service_is_active() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", SERVICE],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _set_service(action: str) -> None:
    result = subprocess.run(
        ["systemctl", "--user", action, SERVICE],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MigrationError(f"could not {action} {SERVICE}")


def _repo_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def create_backup(
    *,
    output: Path,
    demo_home: Path,
    profile_home: Path,
    repo: Path,
    control_service: bool = True,
    overwrite: bool = False,
) -> Path:
    """Create a consistent private migration archive."""
    raw_output = output.expanduser()
    try:
        output_info = raw_output.lstat()
    except FileNotFoundError:
        output_info = None
    if output_info is not None and not stat.S_ISREG(output_info.st_mode):
        raise MigrationError(f"backup destination is unsafe: {raw_output}")
    output = raw_output.resolve()
    demo_home = demo_home.expanduser().resolve()
    profile_home = profile_home.expanduser().resolve()
    repo = repo.expanduser().resolve()
    if _inside(output, repo):
        raise MigrationError("backup destination must be outside the repository")
    if output.exists() and not overwrite:
        raise MigrationError(f"backup already exists: {output}")
    if not profile_home.is_dir():
        raise MigrationError(f"Stead profile does not exist: {profile_home}")

    output.parent.mkdir(parents=True, exist_ok=True)
    was_active = control_service and _service_is_active()
    if was_active:
        _set_service("stop")

    try:
        with tempfile.TemporaryDirectory(prefix="stead-backup-") as temp:
            root = Path(temp) / ARCHIVE_ROOT
            private = root / "private"
            state = root / "state"
            profile = root / "profile"
            root.mkdir(mode=0o700)

            env_file = demo_home / ".env"
            info = env_file.lstat() if env_file.exists() else None
            if info is None or not stat.S_ISREG(info.st_mode):
                raise MigrationError(f"required private file is missing or unsafe: {env_file}")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise MigrationError(f"private environment must be mode 600: {env_file}")
            if info.st_uid != os.getuid():
                raise MigrationError("private environment must be owned by the current user")
            _copy_private_file(env_file, private / ".env", required=True)
            _sqlite_backup(demo_home / "stead.sqlite", state / "stead.sqlite")

            if (profile_home / "state.db").is_file():
                _sqlite_backup(profile_home / "state.db", profile / "state.db")
            _copy_private_tree(profile_home / "memories", profile / "memories")
            _copy_private_tree(profile_home / "sessions", profile / "sessions")

            cron_source = profile_home / "cron"
            cron_target = profile / "cron"
            _copy_private_file(cron_source / "jobs.json", cron_target / "jobs.json")
            if (cron_source / "executions.db").is_file():
                _sqlite_backup(
                    cron_source / "executions.db",
                    cron_target / "executions.db",
                )
            for name in ("channel_directory.json", "gateway_state.json"):
                _copy_private_file(profile_home / name, profile / name)

            files: Dict[str, str] = {}
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                relative = path.relative_to(root).as_posix()
                files[relative] = _sha256(path)
            manifest = {
                "format": FORMAT_NAME,
                "version": FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "profile": PROFILE,
                "repo_commit": _repo_commit(repo),
                "files": files,
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_path.chmod(0o600)

            temporary_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
            try:
                with tarfile.open(temporary_output, "w:gz") as archive:
                    archive.add(root, arcname=ARCHIVE_ROOT, recursive=True)
                temporary_output.chmod(0o600)
                os.replace(temporary_output, output)
            finally:
                temporary_output.unlink(missing_ok=True)
    finally:
        if was_active:
            _set_service("start")
    return output


def _allowed_archive_path(relative: str, *, directory: bool) -> bool:
    if directory:
        return relative in _ALLOWED_DIRECTORIES or any(
            relative.startswith(prefix) for prefix in _ALLOWED_TREE_PREFIXES
        )
    return relative in _ALLOWED_EXACT_FILES or any(
        relative.startswith(prefix) for prefix in _ALLOWED_TREE_PREFIXES
    )


def _safe_members(archive: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    seen: set[str] = set()
    total_size = 0
    member_count = 0
    for member in archive:
        member_count += 1
        if member_count > MAX_ARCHIVE_MEMBERS:
            raise MigrationError("backup exceeds archive member limit")
        if "\\" in member.name:
            raise MigrationError(f"unsafe archive member: {member.name}")
        normalized = member.name
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise MigrationError(f"unsafe archive member: {member.name}")
        if not path.parts or path.parts[0] != ARCHIVE_ROOT:
            raise MigrationError(f"unexpected archive root: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise MigrationError(f"unsupported archive member: {member.name}")
        canonical = path.as_posix().rstrip("/")
        if canonical in seen:
            raise MigrationError(f"duplicate archive member: {member.name}")
        seen.add(canonical)
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        if relative == ".":
            relative = ""
        if not _allowed_archive_path(relative, directory=member.isdir()):
            raise MigrationError(f"unexpected archive path: {member.name}")
        if member.isfile():
            if member.size < 0 or member.size > MAX_ARCHIVE_FILE_SIZE:
                raise MigrationError("backup member exceeds size limit")
            total_size += member.size
            if total_size > MAX_ARCHIVE_TOTAL_SIZE:
                raise MigrationError("backup exceeds total size limit")
        yield member


def _extract_members(
    archive: tarfile.TarFile,
    members: Iterable[tarfile.TarInfo],
    destination: Path,
) -> None:
    """Extract already-validated regular files without tarfile path handling."""
    for member in members:
        target = destination.joinpath(*PurePosixPath(member.name).parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = archive.extractfile(member)
        if source is None:
            raise MigrationError(f"cannot read archive member: {member.name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(0o600)


def _load_and_verify(extracted_root: Path) -> dict:
    manifest_path = extracted_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError("backup manifest is missing or invalid") from exc
    if manifest.get("format") != FORMAT_NAME or manifest.get("version") != FORMAT_VERSION:
        raise MigrationError("unsupported Stead backup format")
    if manifest.get("profile") != PROFILE:
        raise MigrationError("backup belongs to a different Hermes profile")
    expected = manifest.get("files")
    if not isinstance(expected, dict):
        raise MigrationError("backup manifest has no file inventory")
    if not _MANDATORY_FILES.issubset(expected):
        raise MigrationError("backup manifest is missing required files")
    for relative, checksum in expected.items():
        if not isinstance(relative, str) or not _allowed_archive_path(
            relative, directory=False
        ):
            raise MigrationError("backup manifest contains an unexpected file")
        if relative == "manifest.json":
            raise MigrationError("backup manifest cannot inventory itself")
        if not isinstance(checksum, str) or len(checksum) != 64 or any(
            char not in "0123456789abcdef" for char in checksum
        ):
            raise MigrationError("backup manifest contains an invalid checksum")
    actual = {
        path.relative_to(extracted_root).as_posix(): _sha256(path)
        for path in extracted_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != expected:
        raise MigrationError("backup checksum or file inventory mismatch")
    return manifest


def _path_exists(path: Path) -> bool:
    """Return true for regular paths and broken symlinks."""
    return path.exists() or path.is_symlink()


def _remove_path(path: Path) -> None:
    """Remove the path itself without ever following a symlink."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _assert_sqlite_integrity(path: Path) -> None:
    try:
        database = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
        )
        try:
            result = database.execute("PRAGMA integrity_check").fetchone()
        finally:
            database.close()
    except (OSError, sqlite3.Error) as exc:
        raise MigrationError(f"SQLite integrity check failed: {path.name}") from exc
    if not result or result[0] != "ok":
        raise MigrationError(f"SQLite integrity check failed: {path.name}")


@dataclass
class _RestoreItem:
    """One exact-replacement surface in the all-or-nothing restore."""

    target: Path
    source: Optional[Path]
    kind: str
    staged: Optional[Path] = None
    backup: Optional[Path] = None


def _restore_items(
    root: Path, demo_home: Path, profile_home: Path
) -> tuple[list[_RestoreItem], list[Path]]:
    profile = root / "profile"

    def optional(path: Path) -> Optional[Path]:
        return path if path.exists() else None

    items = [
        _RestoreItem(demo_home / ".env", root / "private" / ".env", "file"),
        _RestoreItem(demo_home / "stead.sqlite", root / "state" / "stead.sqlite", "sqlite"),
        _RestoreItem(demo_home / "stead.sqlite-wal", None, "absent"),
        _RestoreItem(demo_home / "stead.sqlite-shm", None, "absent"),
        _RestoreItem(profile_home / "state.db", optional(profile / "state.db"), "sqlite"),
        _RestoreItem(profile_home / "state.db-wal", None, "absent"),
        _RestoreItem(profile_home / "state.db-shm", None, "absent"),
        _RestoreItem(profile_home / "memories", optional(profile / "memories"), "tree"),
        _RestoreItem(profile_home / "sessions", optional(profile / "sessions"), "tree"),
        _RestoreItem(profile_home / "cron", optional(profile / "cron"), "tree"),
        _RestoreItem(
            profile_home / "channel_directory.json",
            optional(profile / "channel_directory.json"),
            "file",
        ),
        _RestoreItem(
            profile_home / "gateway_state.json",
            optional(profile / "gateway_state.json"),
            "file",
        ),
    ]
    sqlite_targets = [demo_home / "stead.sqlite"]
    if (profile / "state.db").is_file():
        sqlite_targets.append(profile_home / "state.db")
    if (profile / "cron" / "executions.db").is_file():
        sqlite_targets.append(profile_home / "cron" / "executions.db")
    return items, sqlite_targets


def _cleanup_restore_items(items: list[_RestoreItem]) -> None:
    for item in items:
        if item.staged is not None:
            _remove_path(item.staged)


def _stage_restore_items(items: list[_RestoreItem], token: str) -> None:
    try:
        for item in items:
            if item.source is None:
                continue
            item.target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            item.staged = item.target.with_name(
                f".{item.target.name}.restore-txn-{token}"
            )
            if _path_exists(item.staged):
                raise MigrationError(f"restore staging path already exists: {item.staged}")
            if item.kind == "tree":
                shutil.copytree(item.source, item.staged)
                for path in [item.staged, *item.staged.rglob("*")]:
                    path.chmod(0o700 if path.is_dir() else 0o600)
            else:
                shutil.copyfile(item.source, item.staged)
                item.staged.chmod(0o600)
                if item.kind == "sqlite":
                    _assert_sqlite_integrity(item.staged)
    except Exception:
        _cleanup_restore_items(items)
        raise


def _commit_restore_items(
    items: list[_RestoreItem], sqlite_targets: list[Path], token: str
) -> list[Path]:
    moved: list[_RestoreItem] = []
    installed: list[_RestoreItem] = []
    try:
        for item in items:
            item.backup = item.target.with_name(
                f".{item.target.name}.previous-txn-{token}"
            )
            if _path_exists(item.backup):
                raise MigrationError(f"restore rollback path already exists: {item.backup}")
            if _path_exists(item.target):
                item.target.rename(item.backup)
                moved.append(item)
        for item in items:
            if item.staged is not None:
                item.staged.rename(item.target)
                installed.append(item)
        for database in sqlite_targets:
            _assert_sqlite_integrity(database)
    except Exception as restore_error:
        rollback_errors: list[Exception] = []
        for item in reversed(installed):
            try:
                _remove_path(item.target)
            except Exception as exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(exc)
        for item in reversed(moved):
            try:
                if item.backup is not None and _path_exists(item.backup):
                    item.backup.rename(item.target)
            except Exception as exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(exc)
        if rollback_errors:
            raise RollbackIncompleteError(
                "restore failed and rollback was incomplete"
            ) from restore_error
        raise
    else:
        cleanup_failures: list[Path] = []
        for item in moved:
            if item.backup is not None:
                try:
                    _remove_path(item.backup)
                except Exception:
                    cleanup_failures.append(item.backup)
        return cleanup_failures
    finally:
        _cleanup_restore_items(items)


def _has_existing_state(demo_home: Path, profile_home: Path) -> bool:
    candidates = [
        demo_home / ".env",
        demo_home / "stead.sqlite",
        demo_home / "stead.sqlite-wal",
        demo_home / "stead.sqlite-shm",
        profile_home / "state.db",
        profile_home / "state.db-wal",
        profile_home / "state.db-shm",
        profile_home / "memories",
        profile_home / "sessions",
        profile_home / "cron",
        profile_home / "channel_directory.json",
        profile_home / "gateway_state.json",
    ]
    for path in candidates:
        if path.is_symlink() or path.is_file():
            return True
        if path.is_dir() and any(path.iterdir()):
            return True
    return False


def restore_backup(
    *,
    bundle: Path,
    demo_home: Path,
    profile_home: Path,
    force: bool = False,
    control_service: bool = True,
) -> dict:
    """Restore private state, leaving tracked profile config and code untouched."""
    raw_bundle = bundle.expanduser()
    try:
        bundle_info = raw_bundle.lstat()
    except FileNotFoundError as exc:
        raise MigrationError(f"backup is missing or unsafe: {raw_bundle}") from exc
    if not stat.S_ISREG(bundle_info.st_mode):
        raise MigrationError(f"backup is missing or unsafe: {raw_bundle}")
    if stat.S_IMODE(bundle_info.st_mode) & 0o077:
        raise MigrationError("backup must not be readable by group or others (chmod 600)")
    bundle = raw_bundle.resolve()
    demo_home = demo_home.expanduser().resolve()
    profile_home = profile_home.expanduser().resolve()
    if not profile_home.is_dir():
        raise MigrationError(f"Stead profile does not exist: {profile_home}")
    if _has_existing_state(demo_home, profile_home) and not force:
        raise MigrationError("target already contains Stead state; pass --force to replace it")

    was_active = False
    restart_safe = False
    items: list[_RestoreItem] = []
    try:
        with tempfile.TemporaryDirectory(prefix="stead-restore-") as temp:
            try:
                with tarfile.open(bundle, "r:gz") as archive:
                    members = list(_safe_members(archive))
                    _extract_members(archive, members, Path(temp))
            except (OSError, tarfile.TarError) as exc:
                raise MigrationError("backup archive is unreadable or invalid") from exc
            root = Path(temp) / ARCHIVE_ROOT
            manifest = _load_and_verify(root)

            source_databases = [root / "state" / "stead.sqlite"]
            for optional_database in (
                root / "profile" / "state.db",
                root / "profile" / "cron" / "executions.db",
            ):
                if optional_database.is_file():
                    source_databases.append(optional_database)
            for database in source_databases:
                _assert_sqlite_integrity(database)

            demo_home.mkdir(parents=True, exist_ok=True, mode=0o700)
            demo_home.chmod(0o700)
            profile_home.chmod(0o700)
            items, sqlite_targets = _restore_items(root, demo_home, profile_home)
            token = uuid.uuid4().hex
            _stage_restore_items(items, token)

            was_active = control_service and _service_is_active()
            if was_active:
                _set_service("stop")
                restart_safe = True
            if _has_existing_state(demo_home, profile_home) and not force:
                raise MigrationError(
                    "target gained Stead state during restore; pass --force to replace it"
                )
            try:
                cleanup_failures = _commit_restore_items(items, sqlite_targets, token)
            except RollbackIncompleteError:
                restart_safe = False
                raise
            if cleanup_failures:
                warnings.warn(
                    "restore committed, but one or more rollback artifacts could not "
                    "be removed; inspect hidden .previous-txn-* paths",
                    RuntimeWarning,
                    stacklevel=2,
                )
            return manifest
    finally:
        try:
            _cleanup_restore_items(items)
        finally:
            # A failed restore stays stopped only when rollback was incomplete.
            if was_active and restart_safe:
                _set_service("start")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--demo-home", type=Path, required=True)
    backup.add_argument("--profile-home", type=Path, required=True)
    backup.add_argument("--repo", type=Path, required=True)
    backup.add_argument("--overwrite", action="store_true")
    backup.add_argument("--no-service-control", action="store_true")

    restore = sub.add_parser("restore")
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--demo-home", type=Path, required=True)
    restore.add_argument("--profile-home", type=Path, required=True)
    restore.add_argument("--force", action="store_true")
    restore.add_argument("--no-service-control", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = create_backup(
                output=args.output,
                demo_home=args.demo_home,
                profile_home=args.profile_home,
                repo=args.repo,
                control_service=not args.no_service_control,
                overwrite=args.overwrite,
            )
            print(f"Private Stead backup created: {result}")
            print("Copy it off this VM through a private channel before deleting the VM.")
        else:
            manifest = restore_backup(
                bundle=args.bundle,
                demo_home=args.demo_home,
                profile_home=args.profile_home,
                force=args.force,
                control_service=not args.no_service_control,
            )
            print(
                "Private Stead state restored "
                f"(source commit {manifest.get('repo_commit', 'unknown')})."
            )
    except MigrationError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
