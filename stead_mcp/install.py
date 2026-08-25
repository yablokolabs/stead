"""Portable, declarative installation helpers for the Stead profile."""
from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, Optional

import yaml

PROFILE = "stead-kerstin-demo"
DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/+-]+$")

# Speech. The plugin package in this repo, the name Hermes routes `stt.provider`
# by, and the voice Stead answers in. Female British voice (Sonia); the male
# alternatives are en-GB-RyanNeural and en-GB-ThomasNeural.
VOICE_PLUGIN = "stead_voice"
STT_PROVIDER = "sarvam"
BRITISH_VOICE = "en-GB-SoniaNeural"

# Page reading. Kept separate from `web.backend` so the search half stays on the
# local proxy — see the Web search section of SECURITY.md.
EXTRACT_BACKEND = "firecrawl"


def _selected_env(path: Path, names: Iterable[str]) -> Dict[str, str]:
    """Read named, non-secret settings without evaluating the shell file."""
    wanted = set(names)
    found: Dict[str, str] = {}
    if not path.is_file():
        return found
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        if name not in wanted:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[name] = value.strip()
    return found


def render_profile_config(
    *,
    profile_home: Path,
    repo: Path,
    demo_home: Path,
    env_file: Optional[Path] = None,
) -> Path:
    """Write the complete non-secret Stead profile config for this checkout."""
    profile_home = profile_home.expanduser().resolve()
    repo = repo.expanduser().resolve()
    demo_home = demo_home.expanduser().resolve()
    env_file = (env_file or demo_home / ".env").expanduser().resolve()

    settings = _selected_env(
        env_file, ("STEAD_MODEL_PROVIDER", "STEAD_MODEL_NAME", "SEARXNG_URL")
    )
    provider = settings.get("STEAD_MODEL_PROVIDER", DEFAULT_PROVIDER)
    model = settings.get("STEAD_MODEL_NAME", DEFAULT_MODEL)
    if provider not in {"anthropic", "gemini"}:
        raise ValueError("STEAD_MODEL_PROVIDER must be anthropic or gemini")
    if not _SAFE_VALUE.fullmatch(model):
        raise ValueError("STEAD_MODEL_NAME contains unsupported characters")

    platform_toolsets = ["clarify", "memory", "session_search"]
    if settings.get("SEARXNG_URL"):
        platform_toolsets.append("web")

    config = {
        "model": {"default": model, "provider": provider},
        "approvals": {"destructive_slash_confirm": False},
        "platform_toolsets": {
            "cli": list(platform_toolsets),
            "telegram": list(platform_toolsets),
        },
        # Queries stay on the local SearXNG; only pages Stead explicitly opens
        # reach a vendor. One backend for both would hand every household query
        # to the extraction vendor as well.
        "web": {"backend": "searxng", "extract_backend": EXTRACT_BACKEND},
        "mcp_servers": {
            "stead": {
                "command": str(repo / ".venv" / "bin" / "python"),
                "args": ["-m", "stead_mcp.server"],
                "env": {
                    "PYTHONPATH": str(repo),
                    "STEAD_DEMO_HOME": str(demo_home),
                },
                "enabled": True,
            }
        },
        # Speech. Plugins are opt-in, so an unlisted stead_voice never loads
        # and both providers below would fall back to Hermes' defaults.
        "plugins": {"enabled": [VOICE_PLUGIN]},
        "stt": {"enabled": True, "provider": STT_PROVIDER},
        # Sarvam has no British voice — confirmed by Sarvam 2026-08-11, whose
        # Bulbul model speaks English only as en-IN. Edge does, so Stead hears
        # through Sarvam and speaks through Edge. Both sit behind the same
        # provider interface, so this line is the whole switch.
        "tts": {"provider": "edge", "edge": {"voice": BRITISH_VOICE}},
        # Without this a voice note comes back as text: the per-chat /voice
        # mode is keyed on the chat you talk from, not on the reminder
        # destination, so relying on it silently misses household members.
        "voice": {"auto_tts": True},
    }

    profile_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = profile_home / "config.yaml"
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    temporary.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, output)
    return output


def install_voice_plugin(*, profile_home: Path, repo: Path) -> Path:
    """Expose this checkout's speech plugin to Hermes.

    Hermes only scans ``<HERMES_HOME>/plugins``, so the package is linked
    rather than copied — an edit in the checkout is live on the next gateway
    start, and there is no second copy to drift.
    """
    profile_home = profile_home.expanduser().resolve()
    source = (repo.expanduser().resolve() / VOICE_PLUGIN)
    if not (source / "plugin.yaml").is_file():
        raise FileNotFoundError(f"{source} is not a Hermes plugin (no plugin.yaml)")

    plugins = profile_home / "plugins"
    plugins.mkdir(parents=True, exist_ok=True, mode=0o700)
    link = plugins / VOICE_PLUGIN

    # Re-runnable: replace whatever is there unless it already points home.
    if link.is_symlink():
        if link.readlink() == source:
            return link
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    elif link.exists():
        link.unlink()

    link.symlink_to(source, target_is_directory=True)
    return link


def _systemd_environment(name: str, value: Path) -> str:
    rendered = str(value.expanduser().resolve())
    if any(char in rendered for char in ("\n", "\r", "\0")):
        raise ValueError(f"{name} contains unsupported control characters")
    escaped = rendered.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{name}={escaped}"'


def render_systemd_dropin(
    *, output: Path, launcher: Path, demo_home: Path, hermes_bin: Path
) -> Path:
    """Render the checkout-specific systemd override without embedding a VM path."""
    launcher = launcher.expanduser().resolve()
    if any(ch.isspace() for ch in str(launcher)):
        raise ValueError("Stead checkout path cannot contain whitespace")
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (
        "# Generated by scripts/setup.sh from systemd/override.conf.\n"
        "# Re-run setup after moving the repository.\n"
        "[Service]\n"
        f"{_systemd_environment('STEAD_DEMO_HOME', demo_home)}\n"
        f"{_systemd_environment('STEAD_HERMES_BIN', hermes_bin)}\n"
        "ExecStart=\n"
        f"ExecStart={launcher}\n"
    )
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o644)
    os.replace(temporary, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile-config")
    profile.add_argument("--profile-home", type=Path, required=True)
    profile.add_argument("--repo", type=Path, required=True)
    profile.add_argument("--demo-home", type=Path, required=True)
    profile.add_argument("--env-file", type=Path)

    dropin = sub.add_parser("systemd-dropin")
    dropin.add_argument("--output", type=Path, required=True)
    dropin.add_argument("--launcher", type=Path, required=True)
    dropin.add_argument("--demo-home", type=Path, required=True)
    dropin.add_argument("--hermes-bin", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "profile-config":
        result = render_profile_config(
            profile_home=args.profile_home,
            repo=args.repo,
            demo_home=args.demo_home,
            env_file=args.env_file,
        )
        # The config above names stead_voice; this is what makes it resolvable.
        # Rendering one without the other leaves Stead mute.
        install_voice_plugin(profile_home=args.profile_home, repo=args.repo)
    else:
        result = render_systemd_dropin(
            output=args.output,
            launcher=args.launcher,
            demo_home=args.demo_home,
            hermes_bin=args.hermes_bin,
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
