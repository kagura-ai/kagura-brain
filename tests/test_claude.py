"""Tests for kagura_brain.claude — the headless `claude -p` adapter.

The Claude adapter runs the child on Claude Code **subscription** auth: it
deny-sets the ``ANTHROPIC_*`` credential env vars (so a stale value inherited
from a surrounding Claude Code session can't override the subscription login)
and builds the `claude -p` argv with the ``--`` prompt separator. The shared
subprocess/env/timeout/decode seam lives in ``kagura_brain.core``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kagura_brain.claude import invoke, mcp_args


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestInvoke:
    def test_returns_structured_result_on_normal_run(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "PONG", ""))
        res = invoke("say PONG")
        assert res.returncode == 0
        assert res.stdout == "PONG"
        assert res.timed_out is False

    def test_strips_anthropic_api_key_for_subscription(self, monkeypatch) -> None:
        # A stale/invalid ANTHROPIC_API_KEY inherited from a surrounding Claude
        # Code session would override subscription auth and fail with "Invalid
        # API key" (#34 / planner PR#5). invoke must strip it.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale-and-invalid")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")

        env = captured["env"]
        assert env is not None, "invoke must pass an explicit env"
        assert "ANTHROPIC_API_KEY" not in env

    def test_strips_anthropic_auth_token_for_subscription(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stale-token")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]

    def test_forwards_cwd_timeout_and_mcp_args(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            captured["kwargs"] = k
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke(
            "prompt text",
            cwd=Path("/repo"),
            timeout=42,
            mcp_config="/repo/.mcp.json",
            allowed_tools=("mcp__kagura-memory__recall",),
        )

        argv = captured["argv"]
        assert argv[:2] == ["claude", "-p"]
        # The prompt is passed last, after a "--" separator, so a prompt that
        # begins with "-" can't be parsed as an option.
        assert argv[-2:] == ["--", "prompt text"]
        assert "--mcp-config" in argv and "/repo/.mcp.json" in argv
        assert "--allowedTools" in argv and "mcp__kagura-memory__recall" in argv
        # MCP flags precede the "--" separator (else they'd be swallowed as args).
        assert argv.index("--mcp-config") < argv.index("--")
        assert captured["kwargs"]["cwd"] == Path("/repo")
        assert captured["kwargs"]["timeout"] == 42

    def test_prompt_starting_with_dash_is_guarded_by_separator(
        self, monkeypatch
    ) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("--version")
        assert captured["argv"][-2:] == ["--", "--version"]


class TestSubscriptionAuthParity:
    """Parity with the codex adapter's env scrub (test_codex.py).

    The claude adapter strips the whole ``ANTHROPIC_*`` prefix so the child runs
    on Claude Code subscription auth and no inherited credential/endpoint
    override can win. This mirrors codex's ``OPENAI_*``/``CODEX_*`` prefix sweep
    — a prefix sweep, not a fixed tuple, so an unknown future override var under
    the prefix cannot leak through (issue #4).
    """

    def test_strips_anthropic_base_url_endpoint_override(self, monkeypatch) -> None:
        # An inherited ANTHROPIC_BASE_URL would silently redirect the claude -p
        # subscription traffic (prompt + code context) to a foreign endpoint —
        # the same "T2" exfiltration vector the codex adapter guards against.
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.example/v1")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert "ANTHROPIC_BASE_URL" not in captured["env"]

    def test_strips_unknown_anthropic_prefix_var(self, monkeypatch) -> None:
        # Fail-secure: a future/unknown ANTHROPIC_* override var must also be
        # scrubbed by the prefix sweep, not just the historically-known keys.
        monkeypatch.setenv("ANTHROPIC_SOME_FUTURE_OVERRIDE", "foreign")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert "ANTHROPIC_SOME_FUTURE_OVERRIDE" not in captured["env"]

    def test_preserves_unrelated_env(self, monkeypatch) -> None:
        # The scrub must be surgical — non-ANTHROPIC vars (incl. CLAUDE_CODE_*,
        # a different prefix) survive into the child env.
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert captured["env"].get("PATH") == "/usr/bin"
        assert captured["env"].get("CLAUDE_CODE_ENTRYPOINT") == "cli"


class TestByoEndpoint:
    """Issue #2 — explicit, opt-in BYO endpoint routing for the Claude adapter.

    ``endpoint=`` + ``api_key=`` inject ``ANTHROPIC_BASE_URL`` /
    ``ANTHROPIC_AUTH_TOKEN`` into the child AFTER the ``ANTHROPIC_*`` scrub, so
    a deliberate, caller-supplied endpoint wins while an ambient (inherited)
    override stays stripped. The default path (no endpoint) is unchanged — the
    full subscription-auth scrub of #1/#4 still holds.
    """

    def test_injects_caller_endpoint_and_token(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", endpoint="https://gw.example/v1", api_key="byo-token")
        env = captured["env"]
        assert env["ANTHROPIC_BASE_URL"] == "https://gw.example/v1"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "byo-token"

    def test_caller_endpoint_overrides_ambient(self, monkeypatch) -> None:
        # scrub→inject: the ambient (attacker-style) endpoint is stripped first,
        # then the caller's explicit endpoint is injected — the child sees only
        # the caller value, never the ambient one.
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example/v1")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", endpoint="https://good.example/v1", api_key="byo-token")
        assert captured["env"]["ANTHROPIC_BASE_URL"] == "https://good.example/v1"

    def test_unsupplied_prefix_var_still_stripped_in_byo(self, monkeypatch) -> None:
        # BYO injects only endpoint+token; any OTHER ambient ANTHROPIC_* var the
        # caller did not supply (e.g. ANTHROPIC_MODEL) stays scrubbed.
        monkeypatch.setenv("ANTHROPIC_MODEL", "ambient-model")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", endpoint="https://good.example/v1", api_key="byo-token")
        assert "ANTHROPIC_MODEL" not in captured["env"]

    def test_endpoint_without_api_key_raises(self) -> None:
        with pytest.raises(ValueError):
            invoke("idea", endpoint="https://good.example/v1")

    def test_api_key_without_endpoint_raises(self) -> None:
        with pytest.raises(ValueError):
            invoke("idea", api_key="byo-token")

    def test_non_https_endpoint_warns(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "ok", ""))
        with pytest.warns(UserWarning):
            invoke("idea", endpoint="http://plain.example/v1", api_key="byo-token")

    def test_default_path_strips_all_anthropic(self, monkeypatch) -> None:
        # No endpoint → subscription-auth parity is byte-for-byte unchanged: the
        # whole ANTHROPIC_* prefix is stripped, nothing is injected.
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example/v1")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stale")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert "ANTHROPIC_BASE_URL" not in captured["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]


class TestMcpArgs:
    def test_none_config_yields_no_args(self) -> None:
        assert mcp_args(None) == []

    def test_empty_config_yields_no_args(self) -> None:
        assert mcp_args("") == []

    def test_config_without_allowed_tools(self) -> None:
        assert mcp_args("/repo/.mcp.json") == ["--mcp-config", "/repo/.mcp.json"]

    def test_config_with_allowed_tools(self) -> None:
        tools = ("mcp__kagura-memory__recall", "mcp__kagura-memory__remember")
        assert mcp_args("/repo/.mcp.json", tools) == [
            "--mcp-config",
            "/repo/.mcp.json",
            "--allowedTools",
            "mcp__kagura-memory__recall",
            "mcp__kagura-memory__remember",
        ]

    def test_allowed_tools_ignored_when_no_config(self) -> None:
        assert mcp_args(None, ("mcp__kagura-memory__recall",)) == []

    def test_bare_string_tool_is_not_splatted_into_chars(self) -> None:
        assert mcp_args("/repo/.mcp.json", "mcp__kagura-memory__recall") == [
            "--mcp-config",
            "/repo/.mcp.json",
            "--allowedTools",
            "mcp__kagura-memory__recall",
        ]
