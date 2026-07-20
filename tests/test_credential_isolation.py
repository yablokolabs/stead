"""The launcher must never inherit a credential it wasn't given.

These run the real launcher script with a poisoned environment and assert it
refuses. `EXEC_GUARD=1` makes the script stop just before exec, so nothing
starts a gateway or contacts Telegram.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "scripts" / "stead-launch.sh"

FAKE_ANTHROPIC = "sk-ant-FAKE0000000000000000000000000000"
FAKE_GEMINI = "AIzaFAKE0000000000000000000000000000000"

GOOD_ENV_FILE = """\
STEAD_TELEGRAM_BOT_TOKEN=123456789:FAKE
STEAD_ALLOWED_TELEGRAM_IDS=111222333
STEAD_MODEL_PROVIDER=anthropic
STEAD_MODEL_NAME=claude-sonnet-4-6
ANTHROPIC_API_KEY={key}
"""


def launch(tmp_path, env_body=None, ambient=None, mode=0o600):
    """Run the launcher with a scratch demo home. Returns CompletedProcess."""
    demo = tmp_path / "demo"
    demo.mkdir(exist_ok=True)
    if env_body is not None:
        env_file = demo / ".env"
        env_file.write_text(env_body)
        env_file.chmod(mode)

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.path.expanduser("~"),
        "STEAD_DEMO_HOME": str(demo),
        "EXEC_GUARD": "1",
    }
    env.update(ambient or {})
    return subprocess.run(["/bin/bash", str(LAUNCHER)], env=env,
                          capture_output=True, text=True, timeout=60)


def combined(result):
    return result.stdout + result.stderr


# 1 — an ambient key is not used when the Stead env file is absent ------------

def test_ambient_anthropic_key_is_not_used_without_the_stead_env_file(tmp_path):
    result = launch(tmp_path, env_body=None,
                    ambient={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC})

    assert result.returncode == 78
    assert "no Stead environment file" in combined(result)
    assert FAKE_ANTHROPIC not in combined(result)


@pytest.mark.parametrize("var", [
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GH_TOKEN",
])
def test_no_ambient_provider_variable_can_satisfy_the_launcher(tmp_path, var):
    result = launch(tmp_path, env_body=None, ambient={var: "AMBIENT-VALUE"})

    assert result.returncode == 78
    assert "AMBIENT-VALUE" not in combined(result)


# 2 — it fails rather than inheriting an unrelated key ------------------------

def test_env_file_without_a_credential_fails_even_with_an_ambient_one(tmp_path):
    body = GOOD_ENV_FILE.format(key="").replace("ANTHROPIC_API_KEY=\n", "")

    result = launch(tmp_path, env_body=body,
                    ambient={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC})

    assert result.returncode == 78
    assert "ANTHROPIC_API_KEY is required" in combined(result)
    assert FAKE_ANTHROPIC not in combined(result)


def test_conflicting_providers_fail_closed(tmp_path):
    body = GOOD_ENV_FILE.format(key=FAKE_ANTHROPIC) + f"GEMINI_API_KEY={FAKE_GEMINI}\n"

    result = launch(tmp_path, env_body=body)

    assert result.returncode == 78
    assert "refusing to guess" in combined(result)


def test_a_non_application_anthropic_key_is_refused(tmp_path):
    body = GOOD_ENV_FILE.format(key="oauth-subscription-token-not-an-api-key")

    result = launch(tmp_path, env_body=body)

    assert result.returncode == 78
    assert "application API key" in combined(result)


def test_unknown_provider_is_refused(tmp_path):
    body = GOOD_ENV_FILE.format(key=FAKE_ANTHROPIC).replace(
        "STEAD_MODEL_PROVIDER=anthropic", "STEAD_MODEL_PROVIDER=claude_code")

    result = launch(tmp_path, env_body=body)

    assert result.returncode == 78
    assert "not supported" in combined(result)


def test_model_outside_the_catalogue_is_refused(tmp_path):
    body = GOOD_ENV_FILE.format(key=FAKE_ANTHROPIC).replace(
        "claude-sonnet-4-6", "claude-imaginary-9")

    result = launch(tmp_path, env_body=body)

    assert result.returncode == 78
    assert "catalogue" in combined(result)


# 3 — only the dedicated env file satisfies the gate --------------------------

def test_a_loose_permission_env_file_is_refused(tmp_path):
    body = GOOD_ENV_FILE.format(key=FAKE_ANTHROPIC)

    result = launch(tmp_path, env_body=body, mode=0o644)

    assert result.returncode == 78
    assert "expected 600" in combined(result)


def test_a_complete_dedicated_env_file_passes_every_check(tmp_path):
    body = GOOD_ENV_FILE.format(key=FAKE_ANTHROPIC)

    result = launch(tmp_path, env_body=body)

    out = combined(result)
    assert "FATAL" not in out
    assert "provider=anthropic" in out
    assert FAKE_ANTHROPIC not in out          # never echoes the value


# 4 — personal Claude Code credentials are never consulted --------------------

def test_launcher_never_references_claude_code_credentials():
    text = LAUNCHER.read_text()

    assert ".credentials.json" not in text
    assert ".claude" not in text
    assert "claude_code" not in text
    # and it actively scrubs the subscription token
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text


def test_repository_never_reads_personal_credentials():
    # Production code only — this test file names the path in its assertions.
    for path in (REPO / "stead_mcp").rglob("*.py"):
        assert ".credentials.json" not in path.read_text(), path
        assert ".claude" not in path.read_text(), path
    for path in (REPO / "scripts").glob("*.sh"):
        assert ".credentials.json" not in path.read_text(), path


def test_no_credential_value_is_ever_printed_by_the_launcher():
    text = LAUNCHER.read_text()

    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "STEAD_TELEGRAM_BOT_TOKEN"):
        assert f"echo ${var}" not in text
        assert f'echo "${{{var}}}"' not in text
