"""Clean-VM bootstrap and private-state migration contracts."""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        db.execute("INSERT INTO sample VALUES (?)", (value,))


def _value(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return str(db.execute("SELECT value FROM sample").fetchone()[0])


def _source_tree(root: Path) -> tuple[Path, Path]:
    demo = root / "demo"
    profile = root / "profile"
    demo.mkdir(parents=True)
    profile.mkdir(parents=True)

    env_file = demo / ".env"
    env_file.write_text(
        "STEAD_MODEL_PROVIDER=gemini\n"
        "STEAD_MODEL_NAME=gemini-test\n"
        "STEAD_TELEGRAM_BOT_TOKEN=123:PRIVATE\n"
    )
    env_file.chmod(0o600)
    _database(demo / "stead.sqlite", "household")
    _database(profile / "state.db", "session")
    _database(profile / "cron" / "executions.db", "cron")
    (profile / "cron" / "jobs.json").write_text('{"jobs": []}\n')
    (profile / "memories").mkdir()
    (profile / "memories" / "MEMORY.md").write_text("private memory\n")
    (profile / "memories" / "MEMORY.md.lock").write_text("stale lock\n")
    (profile / "sessions").mkdir()
    (profile / "sessions" / "sessions.json").write_text("{}\n")
    (profile / "channel_directory.json").write_text("{}\n")
    (profile / "gateway_state.json").write_text("{}\n")
    return demo, profile


def test_runtime_files_do_not_embed_the_old_vm_home() -> None:
    runtime_files = [
        REPO / "scripts" / "stead-launch.sh",
        REPO / "stead_mcp" / "scheduler.py",
        REPO / "systemd" / "override.conf",
        REPO / ".env.example",
    ]
    for path in runtime_files:
        assert "/home/azureuser" not in path.read_text(), path


def test_profile_config_renderer_uses_new_vm_paths(tmp_path: Path) -> None:
    from stead_mcp.install import render_profile_config

    repo = tmp_path / "checkout"
    demo = tmp_path / "private"
    profile = tmp_path / "profile"
    (repo / ".venv" / "bin").mkdir(parents=True)
    demo.mkdir()
    env_file = demo / ".env"
    env_file.write_text(
        "STEAD_MODEL_PROVIDER=anthropic\n"
        "STEAD_MODEL_NAME=claude-test\n"
    )
    env_file.chmod(0o600)

    output = render_profile_config(
        profile_home=profile,
        repo=repo,
        demo_home=demo,
        env_file=env_file,
    )
    config = yaml.safe_load(output.read_text())

    assert config["model"] == {
        "default": "claude-test",
        "provider": "anthropic",
    }
    server = config["mcp_servers"]["stead"]
    assert server["command"] == str(repo / ".venv" / "bin" / "python")
    assert server["env"]["PYTHONPATH"] == str(repo)
    assert server["env"]["STEAD_DEMO_HOME"] == str(demo)
    assert "web" not in config["platform_toolsets"]["cli"]
    assert "web" not in config["platform_toolsets"]["telegram"]
    assert output.stat().st_mode & 0o777 == 0o600


def test_profile_config_enables_web_only_with_a_searxng_url(tmp_path: Path) -> None:
    from stead_mcp.install import render_profile_config

    repo = tmp_path / "checkout"
    demo = tmp_path / "private"
    profile = tmp_path / "profile"
    (repo / ".venv" / "bin").mkdir(parents=True)
    demo.mkdir()
    env_file = demo / ".env"
    env_file.write_text(
        "STEAD_MODEL_PROVIDER=gemini\n"
        "STEAD_MODEL_NAME=gemini-test\n"
        "SEARXNG_URL=http://127.0.0.1:8080\n"
    )
    env_file.chmod(0o600)

    output = render_profile_config(
        profile_home=profile,
        repo=repo,
        demo_home=demo,
        env_file=env_file,
    )
    config = yaml.safe_load(output.read_text())

    assert "web" in config["platform_toolsets"]["cli"]
    assert "web" in config["platform_toolsets"]["telegram"]


def test_rendered_profile_config_passes_the_launcher_gate(tmp_path: Path) -> None:
    from stead_mcp.install import render_profile_config

    home = tmp_path / "home"
    demo = home / ".stead-demo"
    profile = home / ".hermes" / "profiles" / "stead-kerstin-demo"
    demo.mkdir(parents=True)
    env_file = demo / ".env"
    env_file.write_text(
        "STEAD_MODEL_PROVIDER=anthropic\n"
        "STEAD_MODEL_NAME=claude-test\n"
        "ANTHROPIC_API_KEY=sk-ant-test-only\n"
        "STEAD_TELEGRAM_BOT_TOKEN=123:test-only\n"
        "STEAD_ALLOWED_TELEGRAM_IDS=123456\n"
        "STEAD_TELEGRAM_CHAT_ID=123456\n"
    )
    env_file.chmod(0o600)
    render_profile_config(
        profile_home=profile,
        repo=REPO,
        demo_home=demo,
        env_file=env_file,
    )

    environment = os.environ.copy()
    environment.update(
        HOME=str(home),
        STEAD_DEMO_HOME=str(demo),
        EXEC_GUARD="1",
        HERMES_MODELS_CATALOGUE=str(tmp_path / "not-installed-catalogue"),
    )
    result = subprocess.run(
        [str(REPO / "scripts" / "stead-launch.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_private_backup_restore_round_trip(tmp_path: Path) -> None:
    from stead_mcp.migration import create_backup, restore_backup

    source_demo, source_profile = _source_tree(tmp_path / "source")
    bundle = tmp_path / "stead-private.tar.gz"
    create_backup(
        output=bundle,
        demo_home=source_demo,
        profile_home=source_profile,
        repo=REPO,
        control_service=False,
    )

    assert bundle.stat().st_mode & 0o777 == 0o600
    with tarfile.open(bundle, "r:gz") as archive:
        names = set(archive.getnames())
    assert "stead-backup-v1/manifest.json" in names
    assert "stead-backup-v1/private/.env" in names
    assert "stead-backup-v1/state/stead.sqlite" in names
    assert "stead-backup-v1/profile/config.yaml" not in names
    assert "stead-backup-v1/profile/auth.json" not in names
    assert "stead-backup-v1/profile/memories/MEMORY.md.lock" not in names

    target_demo = tmp_path / "target" / "demo"
    target_profile = tmp_path / "target" / "profile"
    target_demo.mkdir(parents=True)
    target_profile.mkdir(parents=True)
    outside_memories = tmp_path / "outside-memories"
    outside_memories.mkdir()
    (outside_memories / "untouched").write_text("outside")
    (target_profile / "memories").symlink_to(outside_memories, target_is_directory=True)
    for sidecar in (
        target_demo / "stead.sqlite-wal",
        target_demo / "stead.sqlite-shm",
        target_profile / "state.db-wal",
        target_profile / "state.db-shm",
    ):
        sidecar.write_text("stale")
    restore_backup(
        bundle=bundle,
        demo_home=target_demo,
        profile_home=target_profile,
        force=True,
        control_service=False,
    )

    assert (target_demo / ".env").read_bytes() == (source_demo / ".env").read_bytes()
    assert not (target_demo / "stead.sqlite-wal").exists()
    assert not (target_demo / "stead.sqlite-shm").exists()
    assert not (target_profile / "state.db-wal").exists()
    assert not (target_profile / "state.db-shm").exists()
    assert _value(target_demo / "stead.sqlite") == "household"
    assert _value(target_profile / "state.db") == "session"
    assert _value(target_profile / "cron" / "executions.db") == "cron"
    assert (target_profile / "memories" / "MEMORY.md").read_text() == "private memory\n"
    assert not (target_profile / "memories").is_symlink()
    assert (outside_memories / "untouched").read_text() == "outside"
    assert (target_demo / ".env").stat().st_mode & 0o777 == 0o600


def test_restore_rejects_archive_path_traversal(tmp_path: Path) -> None:
    from stead_mcp.migration import MigrationError, restore_backup

    bundle = tmp_path / "malicious.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("bad")
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(payload, arcname="stead-backup-v1/../../escape")
    bundle.chmod(0o600)

    profile = tmp_path / "profile"
    profile.mkdir()
    with pytest.raises(MigrationError, match="unsafe archive member"):
        restore_backup(
            bundle=bundle,
            demo_home=tmp_path / "demo",
            profile_home=profile,
            force=True,
            control_service=False,
        )


def test_invalid_restore_does_not_touch_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stead_mcp.migration as migration

    bundle = tmp_path / "invalid.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("bad")
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(payload, arcname="stead-backup-v1/../../escape")
    bundle.chmod(0o600)
    profile = tmp_path / "profile"
    profile.mkdir()
    service_actions: list[str] = []
    monkeypatch.setattr(migration, "_service_is_active", lambda: True)
    monkeypatch.setattr(migration, "_set_service", service_actions.append)

    with pytest.raises(migration.MigrationError):
        migration.restore_backup(
            bundle=bundle,
            demo_home=tmp_path / "demo",
            profile_home=profile,
            force=True,
            control_service=True,
        )
    assert service_actions == []


def test_force_restore_clears_optional_state_absent_from_backup(tmp_path: Path) -> None:
    from stead_mcp.migration import create_backup, restore_backup

    source_demo = tmp_path / "source-demo"
    source_profile = tmp_path / "source-profile"
    source_demo.mkdir()
    source_profile.mkdir()
    (source_demo / ".env").write_text("STEAD_MODEL_PROVIDER=gemini\n")
    (source_demo / ".env").chmod(0o600)
    _database(source_demo / "stead.sqlite", "household")
    bundle = tmp_path / "minimal.tar.gz"
    create_backup(
        output=bundle,
        demo_home=source_demo,
        profile_home=source_profile,
        repo=REPO,
        control_service=False,
    )

    target_demo = tmp_path / "target-demo"
    target_profile = tmp_path / "target-profile"
    target_profile.mkdir()
    _database(target_profile / "state.db", "stale session")
    for directory in ("memories", "sessions", "cron"):
        path = target_profile / directory
        path.mkdir()
        (path / "stale").write_text("old")
    outside_sessions = tmp_path / "outside-sessions"
    outside_sessions.mkdir()
    (outside_sessions / "untouched").write_text("outside")
    shutil.rmtree(target_profile / "sessions")
    (target_profile / "sessions").symlink_to(outside_sessions, target_is_directory=True)
    for name in ("channel_directory.json", "gateway_state.json"):
        (target_profile / name).write_text("{}")

    restore_backup(
        bundle=bundle,
        demo_home=target_demo,
        profile_home=target_profile,
        force=True,
        control_service=False,
    )
    assert not (target_profile / "state.db").exists()
    assert not (target_profile / "memories").exists()
    assert not (target_profile / "sessions").exists()
    assert not (target_profile / "sessions").is_symlink()
    assert (outside_sessions / "untouched").read_text() == "outside"
    assert not (target_profile / "cron").exists()
    assert not (target_profile / "channel_directory.json").exists()
    assert not (target_profile / "gateway_state.json").exists()


def test_non_force_restore_protects_every_existing_state_surface(tmp_path: Path) -> None:
    from stead_mcp.migration import (
        MigrationError,
        _has_existing_state,
        create_backup,
        restore_backup,
    )

    surfaces = (
        ("demo", "stead.sqlite-wal"),
        ("demo", "stead.sqlite-shm"),
        ("profile", "state.db-wal"),
        ("profile", "state.db-shm"),
        ("profile", "channel_directory.json"),
        ("profile", "gateway_state.json"),
    )
    for index, (location, name) in enumerate(surfaces):
        root = tmp_path / f"surface-{index}"
        demo = root / "demo"
        profile = root / "profile"
        demo.mkdir(parents=True)
        profile.mkdir()
        target = (demo if location == "demo" else profile) / name
        target.write_text("existing")
        assert _has_existing_state(demo, profile), name

    source_demo = tmp_path / "source-demo-protected"
    source_profile = tmp_path / "source-profile-protected"
    source_demo.mkdir()
    source_profile.mkdir()
    (source_demo / ".env").write_text("STEAD_MODEL_PROVIDER=gemini\n")
    (source_demo / ".env").chmod(0o600)
    _database(source_demo / "stead.sqlite", "household")
    bundle = tmp_path / "protected.tar.gz"
    create_backup(
        output=bundle,
        demo_home=source_demo,
        profile_home=source_profile,
        repo=REPO,
        control_service=False,
    )
    target_demo = tmp_path / "protected-demo"
    target_profile = tmp_path / "protected-profile"
    target_profile.mkdir()
    channel_state = target_profile / "channel_directory.json"
    channel_state.write_text("existing")
    with pytest.raises(MigrationError, match="already contains.*state"):
        restore_backup(
            bundle=bundle,
            demo_home=target_demo,
            profile_home=target_profile,
            force=False,
            control_service=False,
        )
    assert channel_state.read_text() == "existing"


def test_backup_refuses_destination_inside_repository(tmp_path: Path) -> None:
    from stead_mcp.migration import MigrationError, create_backup

    demo, profile = _source_tree(tmp_path)
    with pytest.raises(MigrationError, match="outside the repository"):
        create_backup(
            output=REPO / "private-backup.tar.gz",
            demo_home=demo,
            profile_home=profile,
            repo=REPO,
            control_service=False,
        )
