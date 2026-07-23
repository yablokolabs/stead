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
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, Optional

FORMAT_NAME = "stead-backup"
FORMAT_VERSION = 1
ARCHIVE_ROOT = "stead-backup-v1"
PROFILE = "stead-kerstin-demo"
SERVICE = f"hermes-gateway-{PROFILE}.service"


class MigrationError(RuntimeError):
    """The backup or restore cannot proceed safely."""


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
    if not source.is_file():
        raise MigrationError(f"required SQLite database is missing: {source}")
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise MigrationError(f"SQLite integrity check failed: {source}")
    finally:
        dst.close()
        src.close()
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
    output = output.expanduser().resolve()
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


def _safe_members(archive: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    for member in archive.getmembers():
        normalized = member.name.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise MigrationError(f"unsafe archive member: {member.name}")
        if not path.parts or path.parts[0] != ARCHIVE_ROOT:
            raise MigrationError(f"unexpected archive root: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise MigrationError(f"unsupported archive member: {member.name}")
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
    actual = {
        path.relative_to(extracted_root).as_posix(): _sha256(path)
        for path in extracted_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != expected:
        raise MigrationError("backup checksum or file inventory mismatch")
    return manifest


def _atomic_copy(source: Path, destination: Path, mode: int = 0o600) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.restore-{os.getpid()}")
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


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


def _replace_sqlite(source: Path, destination: Path) -> None:
    """Transactionally replace a stopped DB and retire its old WAL/SHM set."""
    _assert_sqlite_integrity(source)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.restore-{os.getpid()}")
    originals = (
        destination,
        destination.with_name(destination.name + "-wal"),
        destination.with_name(destination.name + "-shm"),
    )
    backups = tuple(
        path.with_name(f".{path.name}.previous-{os.getpid()}") for path in originals
    )
    moved: list[tuple[Path, Path]] = []
    installed = False
    _remove_path(temporary)
    for backup in backups:
        _remove_path(backup)
    try:
        # Finish and validate the new file before touching the old database set.
        shutil.copyfile(source, temporary)
        temporary.chmod(0o600)
        _assert_sqlite_integrity(temporary)
        for original, backup in zip(originals, backups):
            if _path_exists(original):
                original.rename(backup)
                moved.append((original, backup))
        temporary.rename(destination)
        installed = True
    except Exception:
        if installed:
            _remove_path(destination)
        for original, backup in reversed(moved):
            if _path_exists(backup):
                backup.rename(original)
        raise
    finally:
        _remove_path(temporary)
    for _original, backup in moved:
        _remove_path(backup)


def _replace_tree(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.restore-{os.getpid()}")
    previous = destination.with_name(f".{destination.name}.previous-{os.getpid()}")
    _remove_path(temporary)
    _remove_path(previous)
    shutil.copytree(source, temporary)
    for directory in [temporary, *[p for p in temporary.rglob("*") if p.is_dir()]]:
        directory.chmod(0o700)
    for file_path in [p for p in temporary.rglob("*") if p.is_file()]:
        file_path.chmod(0o600)
    try:
        if _path_exists(destination):
            destination.rename(previous)
        temporary.rename(destination)
        _remove_path(previous)
    except Exception:
        if not _path_exists(destination) and _path_exists(previous):
            previous.rename(destination)
        raise
    finally:
        _remove_path(temporary)


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
    bundle = bundle.expanduser().resolve()
    demo_home = demo_home.expanduser().resolve()
    profile_home = profile_home.expanduser().resolve()
    if not bundle.is_file() or bundle.is_symlink():
        raise MigrationError(f"backup is missing or unsafe: {bundle}")
    if stat.S_IMODE(bundle.stat().st_mode) & 0o077:
        raise MigrationError("backup must not be readable by group or others (chmod 600)")
    if not profile_home.is_dir():
        raise MigrationError(f"Stead profile does not exist: {profile_home}")
    if _has_existing_state(demo_home, profile_home) and not force:
        raise MigrationError("target already contains Stead state; pass --force to replace it")

    was_active = control_service and _service_is_active()
    if was_active:
        _set_service("stop")
    restored = False
    try:
        with tempfile.TemporaryDirectory(prefix="stead-restore-") as temp:
            with tarfile.open(bundle, "r:gz") as archive:
                members = list(_safe_members(archive))
                _extract_members(archive, members, Path(temp))
            root = Path(temp) / ARCHIVE_ROOT
            manifest = _load_and_verify(root)

            demo_home.mkdir(parents=True, exist_ok=True, mode=0o700)
            demo_home.chmod(0o700)
            profile_home.chmod(0o700)
            _atomic_copy(root / "private" / ".env", demo_home / ".env")
            _replace_sqlite(root / "state" / "stead.sqlite", demo_home / "stead.sqlite")

            profile = root / "profile"
            if (profile / "state.db").is_file():
                _replace_sqlite(profile / "state.db", profile_home / "state.db")
            else:
                for suffix in ("", "-wal", "-shm"):
                    (profile_home / f"state.db{suffix}").unlink(missing_ok=True)
            for name in ("memories", "sessions", "cron"):
                if (profile / name).is_dir():
                    _replace_tree(profile / name, profile_home / name)
                else:
                    _remove_path(profile_home / name)
            for name in ("channel_directory.json", "gateway_state.json"):
                if (profile / name).is_file():
                    _atomic_copy(profile / name, profile_home / name)
                else:
                    (profile_home / name).unlink(missing_ok=True)

            for database in (
                demo_home / "stead.sqlite",
                profile_home / "state.db",
                profile_home / "cron" / "executions.db",
            ):
                if database.is_file():
                    _assert_sqlite_integrity(database)
            restored = True
            return manifest
    finally:
        # A failed/partial restore must stay stopped for operator recovery.
        if was_active and restored:
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
