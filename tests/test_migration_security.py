"""Security and transactional failure contracts for Stead migration."""
from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        database.execute("INSERT INTO sample VALUES (?)", (value,))


def _value(path: Path) -> str:
    with sqlite3.connect(path) as database:
        return str(database.execute("SELECT value FROM sample").fetchone()[0])


def _source(root: Path) -> tuple[Path, Path]:
    demo = root / "demo"
    profile = root / "profile"
    demo.mkdir(parents=True)
    profile.mkdir()
    (demo / ".env").write_text("STEAD_MODEL_PROVIDER=gemini\n")
    (demo / ".env").chmod(0o600)
    _database(demo / "stead.sqlite", "new household")
    _database(profile / "state.db", "new state")
    _database(profile / "cron" / "executions.db", "new cron")
    (profile / "cron" / "jobs.json").write_text('{"jobs": []}\n')
    for name in ("memories", "sessions"):
        (profile / name).mkdir()
        (profile / name / "value.txt").write_text(f"new {name}\n")
    (profile / "channel_directory.json").write_text('{"new": true}\n')
    (profile / "gateway_state.json").write_text('{"new": true}\n')
    return demo, profile


def _target(root: Path) -> tuple[Path, Path]:
    demo = root / "demo"
    profile = root / "profile"
    demo.mkdir(parents=True)
    profile.mkdir()
    (demo / ".env").write_text("OLD_ENV=1\n")
    (demo / ".env").chmod(0o600)
    _database(demo / "stead.sqlite", "old household")
    _database(profile / "state.db", "old state")
    _database(profile / "cron" / "executions.db", "old cron")
    (profile / "cron" / "jobs.json").write_text('{"old": true}\n')
    for name in ("memories", "sessions"):
        (profile / name).mkdir(exist_ok=True)
        (profile / name / "value.txt").write_text(f"old {name}\n")
    (profile / "channel_directory.json").write_text('{"old": true}\n')
    (profile / "gateway_state.json").write_text('{"old": true}\n')
    return demo, profile


def _assert_old_state(demo: Path, profile: Path) -> None:
    assert (demo / ".env").read_text() == "OLD_ENV=1\n"
    assert _value(demo / "stead.sqlite") == "old household"
    assert _value(profile / "state.db") == "old state"
    assert _value(profile / "cron" / "executions.db") == "old cron"
    assert (profile / "cron" / "jobs.json").read_text() == '{"old": true}\n'
    assert (profile / "memories" / "value.txt").read_text() == "old memories\n"
    assert (profile / "sessions" / "value.txt").read_text() == "old sessions\n"
    assert json.loads((profile / "channel_directory.json").read_text()) == {"old": True}
    assert json.loads((profile / "gateway_state.json").read_text()) == {"old": True}


def _assert_new_state(demo: Path, profile: Path) -> None:
    assert (demo / ".env").read_text() == "STEAD_MODEL_PROVIDER=gemini\n"
    assert _value(demo / "stead.sqlite") == "new household"
    assert _value(profile / "state.db") == "new state"
    assert _value(profile / "cron" / "executions.db") == "new cron"


def _bundle(tmp_path: Path) -> Path:
    from stead_mcp.migration import create_backup

    demo, profile = _source(tmp_path / "source")
    bundle = tmp_path / "bundle.tar.gz"
    create_backup(
        output=bundle,
        demo_home=demo,
        profile_home=profile,
        repo=REPO,
        control_service=False,
    )
    return bundle


def test_backup_rejects_symlinked_output(tmp_path: Path) -> None:
    from stead_mcp.migration import MigrationError, create_backup

    demo, profile = _source(tmp_path / "source")
    external = tmp_path / "external.tar.gz"
    external.write_text("keep")
    external.chmod(0o600)
    output = tmp_path / "bundle-link.tar.gz"
    output.symlink_to(external)

    with pytest.raises(MigrationError, match="unsafe"):
        create_backup(
            output=output,
            demo_home=demo,
            profile_home=profile,
            repo=REPO,
            control_service=False,
            overwrite=True,
        )
    assert external.read_text() == "keep"


def test_backup_rejects_symlinked_sqlite_input(tmp_path: Path) -> None:
    from stead_mcp.migration import MigrationError, create_backup

    demo, profile = _source(tmp_path / "source")
    external = tmp_path / "external.sqlite"
    _database(external, "external")
    (demo / "stead.sqlite").unlink()
    (demo / "stead.sqlite").symlink_to(external)

    with pytest.raises(MigrationError, match="regular file"):
        create_backup(
            output=tmp_path / "bundle.tar.gz",
            demo_home=demo,
            profile_home=profile,
            repo=REPO,
            control_service=False,
        )


def test_restore_rejects_symlinked_bundle_path(tmp_path: Path) -> None:
    from stead_mcp.migration import MigrationError, restore_backup

    bundle = _bundle(tmp_path)
    link = tmp_path / "bundle-link.tar.gz"
    link.symlink_to(bundle)
    target_profile = tmp_path / "target-profile"
    target_profile.mkdir()

    with pytest.raises(MigrationError, match="unsafe"):
        restore_backup(
            bundle=link,
            demo_home=tmp_path / "target-demo",
            profile_home=target_profile,
            force=True,
            control_service=False,
        )


def test_restore_rejects_unexpected_archive_member(tmp_path: Path) -> None:
    from stead_mcp.migration import MigrationError, restore_backup

    bundle = tmp_path / "unexpected.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("unexpected")
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(payload, arcname="stead-backup-v1/unexpected.txt")
    bundle.chmod(0o600)
    profile = tmp_path / "profile"
    profile.mkdir()

    with pytest.raises(MigrationError, match="unexpected archive path"):
        restore_backup(
            bundle=bundle,
            demo_home=tmp_path / "demo",
            profile_home=profile,
            force=True,
            control_service=False,
        )


def test_archive_size_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stead_mcp.migration as migration

    bundle = tmp_path / "oversize.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        info = tarfile.TarInfo("stead-backup-v1/private/.env")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"xx"))
    monkeypatch.setattr(migration, "MAX_ARCHIVE_TOTAL_SIZE", 1, raising=False)
    with tarfile.open(bundle, "r:gz") as archive:
        with pytest.raises(migration.MigrationError, match="size limit"):
            list(migration._safe_members(archive))


def test_restore_staging_failure_preserves_all_existing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stead_mcp.migration as migration

    bundle = _bundle(tmp_path)
    demo, profile = _target(tmp_path / "target")
    original_copytree = migration.shutil.copytree

    def fail_sessions(source: Path, destination: Path, *args: Any, **kwargs: Any):
        if source.name == "sessions":
            raise OSError("simulated staging failure")
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(migration.shutil, "copytree", fail_sessions)
    with pytest.raises(OSError, match="simulated staging failure"):
        migration.restore_backup(
            bundle=bundle,
            demo_home=demo,
            profile_home=profile,
            force=True,
            control_service=False,
        )
    _assert_old_state(demo, profile)


def test_restore_commit_failure_rolls_back_all_existing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stead_mcp.migration as migration

    bundle = _bundle(tmp_path)
    demo, profile = _target(tmp_path / "target")
    original_rename = Path.rename
    service_actions: list[str] = []
    monkeypatch.setattr(migration, "_service_is_active", lambda: True)
    monkeypatch.setattr(migration, "_set_service", service_actions.append)

    def fail_sessions_install(path: Path, target: Path) -> Path:
        if ".restore-txn-" in path.name and target.name == "sessions":
            raise OSError("simulated transaction commit failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_sessions_install)
    with pytest.raises(OSError, match="simulated transaction commit failure"):
        migration.restore_backup(
            bundle=bundle,
            demo_home=demo,
            profile_home=profile,
            force=True,
            control_service=True,
        )
    _assert_old_state(demo, profile)
    assert service_actions == ["stop", "start"]


def test_post_commit_cleanup_failure_warns_and_restarts_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stead_mcp.migration as migration

    bundle = _bundle(tmp_path)
    demo, profile = _target(tmp_path / "target")
    original_remove = migration._remove_path
    service_actions: list[str] = []
    monkeypatch.setattr(migration, "_service_is_active", lambda: True)
    monkeypatch.setattr(migration, "_set_service", service_actions.append)

    def fail_rollback_cleanup(path: Path) -> None:
        if ".previous-txn-" in path.name:
            raise OSError("simulated rollback-artifact cleanup failure")
        original_remove(path)

    monkeypatch.setattr(migration, "_remove_path", fail_rollback_cleanup)
    with pytest.warns(RuntimeWarning, match="restore committed"):
        migration.restore_backup(
            bundle=bundle,
            demo_home=demo,
            profile_home=profile,
            force=True,
            control_service=True,
        )
    _assert_new_state(demo, profile)
    assert service_actions == ["stop", "start"]


def test_incomplete_rollback_leaves_previously_active_service_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stead_mcp.migration as migration

    bundle = _bundle(tmp_path)
    demo, profile = _target(tmp_path / "target")
    original_rename = Path.rename
    service_actions: list[str] = []
    monkeypatch.setattr(migration, "_service_is_active", lambda: True)
    monkeypatch.setattr(migration, "_set_service", service_actions.append)

    def fail_commit_and_rollback(path: Path, target: Path) -> Path:
        if ".restore-txn-" in path.name and target.name == "sessions":
            raise OSError("simulated commit failure")
        if ".previous-txn-" in path.name and target.name == ".env":
            raise OSError("simulated rollback failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_commit_and_rollback)
    with pytest.raises(migration.RollbackIncompleteError, match="rollback was incomplete"):
        migration.restore_backup(
            bundle=bundle,
            demo_home=demo,
            profile_home=profile,
            force=True,
            control_service=True,
        )
    assert service_actions == ["stop"]
