"""Tests for kagura_brain.codex — the headless `codex exec` adapter.

Mirrors the Claude adapter's contract for a second provider. The load-bearing
requirement is **subscription-auth parity**: strip every ``OPENAI_*`` / ``CODEX_*``
override from the child env (a *prefix* scrub, not a fixed tuple) so the
``codex login`` (ChatGPT subscription) credentials in the default ``~/.codex``
win — and so an inherited ``OPENAI_BASE_URL`` cannot silently redirect the
subscription traffic to a foreign endpoint (the exfiltration vector absent on
the Claude side).

CI mocks ``subprocess.run`` and never launches the real CLI; a real `codex exec`
smoke is a manual/local step (see CONTRIBUTING / PR notes).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kagura_brain import codex
from kagura_brain.codex import check, invoke


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

    def test_builds_codex_exec_argv_with_separator(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        # Pin which-resolution to a miss so the adapter's bare "codex" argv is
        # asserted as built; core's argv[0] resolution is covered in test_core.
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        invoke("prompt text")
        argv = captured["argv"]
        assert argv[:2] == ["codex", "exec"]
        # Prompt is positional and passed after "--" so it is never parsed as an
        # option NOR as an `exec` subcommand (resume / review / help).
        assert argv[-2:] == ["--", "prompt text"]

    def test_prompt_starting_with_dash_is_guarded_by_separator(
        self, monkeypatch
    ) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("--help")
        assert captured["argv"][-2:] == ["--", "--help"]

    def test_subcommand_name_prompt_is_guarded_by_separator(self, monkeypatch) -> None:
        # "review" is a real `codex exec` subcommand; without the "--" separator
        # a prompt of "review" would launch the review subcommand instead of
        # being sent to the model.
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("review")
        argv = captured["argv"]
        assert argv[-2:] == ["--", "review"]

    def test_sandbox_mode_adds_flag(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", sandbox="read-only")
        argv = captured["argv"]
        assert "--sandbox" in argv and "read-only" in argv
        # Sandbox flag precedes the "--" separator.
        assert argv.index("--sandbox") < argv.index("--")

    def test_invalid_sandbox_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            invoke("idea", sandbox="wide-open")

    def test_sandbox_and_bypass_are_mutually_exclusive(self) -> None:
        # --dangerously-bypass-approvals-and-sandbox overrides the sandbox
        # policy, so passing both must error rather than emit a contradictory
        # argv that gives a false sense of confinement.
        with pytest.raises(ValueError):
            invoke("idea", sandbox="read-only", bypass_approvals=True)

    def test_no_sandbox_flag_by_default(self, monkeypatch) -> None:
        # Approval/sandbox is opt-in — the default invocation adds neither the
        # sandbox flag nor the dangerous bypass.
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        argv = captured["argv"]
        assert "--sandbox" not in argv
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv

    def test_bypass_approvals_adds_flag(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", bypass_approvals=True)
        assert "--dangerously-bypass-approvals-and-sandbox" in captured["argv"]


class TestSubscriptionAuthParity:
    def test_strips_openai_api_key(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-stale")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        env = captured["env"]
        assert env is not None, "invoke must pass an explicit env"
        assert "OPENAI_API_KEY" not in env

    def test_strips_openai_base_url_endpoint_override(self, monkeypatch) -> None:
        # An inherited OPENAI_BASE_URL would silently redirect subscription
        # traffic to a foreign endpoint (exfiltration). It must be scrubbed.
        monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example/v1")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert "OPENAI_BASE_URL" not in captured["env"]

    def test_strips_codex_home(self, monkeypatch) -> None:
        # CODEX_HOME relocates the auth dir; stripping it forces the default
        # ~/.codex where `codex login` wrote the subscription credentials.
        monkeypatch.setenv("CODEX_HOME", "/tmp/foreign-codex")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert "CODEX_HOME" not in captured["env"]

    def test_preserves_unrelated_env(self, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/dev")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        env = captured["env"]
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/dev"


class TestByoEndpoint:
    """Issue #2 — explicit, opt-in BYO endpoint routing for the Codex adapter.

    ``endpoint=`` + ``api_key=`` inject ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``
    AFTER the ``OPENAI_*``/``CODEX_*`` scrub, so a caller-supplied endpoint wins
    while ambient overrides stay stripped. ``endpoint="ollama-cloud"`` is a
    convenience alias for the OpenAI-compatible Ollama Cloud endpoint.
    """

    def test_injects_caller_endpoint_and_api_key(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", endpoint="https://gw.example/v1", api_key="sk-byo")
        env = captured["env"]
        assert env["OPENAI_BASE_URL"] == "https://gw.example/v1"
        assert env["OPENAI_API_KEY"] == "sk-byo"

    def test_caller_endpoint_overrides_ambient(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example/v1")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", endpoint="https://good.example/v1", api_key="sk-byo")
        assert captured["env"]["OPENAI_BASE_URL"] == "https://good.example/v1"

    def test_ollama_cloud_alias_resolves_to_endpoint_constant(
        self, monkeypatch
    ) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", endpoint="ollama-cloud", api_key="sk-byo")
        assert codex.OLLAMA_CLOUD_ENDPOINT == "https://ollama.com/v1"
        assert captured["env"]["OPENAI_BASE_URL"] == codex.OLLAMA_CLOUD_ENDPOINT

    def test_endpoint_without_api_key_raises(self) -> None:
        with pytest.raises(ValueError):
            invoke("idea", endpoint="https://good.example/v1")

    def test_api_key_without_endpoint_raises(self) -> None:
        with pytest.raises(ValueError):
            invoke("idea", api_key="sk-byo")

    def test_default_path_strips_all_openai(self, monkeypatch) -> None:
        # No endpoint → subscription-auth parity unchanged: OPENAI_* stripped,
        # nothing injected.
        monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-stale")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert "OPENAI_BASE_URL" not in captured["env"]
        assert "OPENAI_API_KEY" not in captured["env"]


class TestLocalProvider:
    """Issue #2 — Codex ``--oss --local-provider`` local passthrough.

    For a LOCAL ollama/lmstudio backend Codex needs no env override (no deny-set
    conflict); it is purely an argv passthrough. It is mutually exclusive with
    the cloud BYO endpoint mode (env override) — passing both is contradictory.
    """

    def test_local_provider_adds_oss_flags(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", local_provider="ollama")
        argv = captured["argv"]
        assert "--oss" in argv
        assert "--local-provider" in argv and "ollama" in argv
        # Flags precede the "--" separator.
        assert argv.index("--local-provider") < argv.index("--")

    def test_lmstudio_is_accepted(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", local_provider="lmstudio")
        assert "lmstudio" in captured["argv"]

    def test_invalid_local_provider_raises(self) -> None:
        with pytest.raises(ValueError):
            invoke("idea", local_provider="bogus")

    def test_local_provider_and_endpoint_are_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError):
            invoke(
                "idea",
                local_provider="ollama",
                endpoint="https://good.example/v1",
                api_key="sk-byo",
            )

    def test_local_provider_still_scrubs_openai(self, monkeypatch) -> None:
        # Local passthrough injects nothing, so the OPENAI_*/CODEX_* scrub is
        # unchanged — an ambient endpoint override is still stripped.
        monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example/v1")
        captured: dict = {}

        def _run(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", local_provider="ollama")
        assert "OPENAI_BASE_URL" not in captured["env"]


class TestRuntimeSeam:
    def test_forwards_cwd_and_timeout(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["kwargs"] = k
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea", cwd=Path("/repo"), timeout=42)
        assert captured["kwargs"]["cwd"] == Path("/repo")
        assert captured["kwargs"]["timeout"] == 42

    def test_timeout_returns_partial_output_normalized(self, monkeypatch) -> None:
        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(
                cmd=a[0], timeout=k.get("timeout", 0), output=b"partial\xff", stderr=b""
            )

        monkeypatch.setattr(subprocess, "run", _raise)
        res = invoke("idea")
        assert res.timed_out is True
        assert res.returncode == -1
        assert res.stdout == "partial�"
        assert res.detail() == "partial�"

    def test_decodes_output_as_utf8_with_replacement(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["kwargs"] = k
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert captured["kwargs"].get("encoding") == "utf-8"
        assert captured["kwargs"].get("errors") == "replace"


class TestVersionCanary:
    def test_verified_version_is_a_dotted_version_string(self) -> None:
        # Pin the Codex CLI version the argv/env surface was verified against, so
        # a CLI upgrade is a conscious revisit of the deny-set. Assert SHAPE, not
        # a frozen literal (mirrors test_import's version-shape check).
        version = codex._CODEX_VERIFIED_VERSION
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), version


class TestCheck:
    """Presence-only pre-flight wrapper (issue #9). Auth is NOT probed here —
    codex has no clean non-interactive auth check."""

    def test_present_is_ok(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/codex")
        res = check()
        assert res.name == "codex"
        assert res.ok is True
        assert "/usr/local/bin/codex" in res.detail

    def test_absent_is_fail(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: None)
        res = check()
        assert res.name == "codex"
        assert res.ok is False


class TestMcpConfig:
    """codex wires MCP by translating a claude-format ``.mcp.json`` into
    ``-c mcp_servers.<name>=<TOML>`` overrides on the ``codex exec`` argv (codex
    has no ``--mcp-config`` flag; ``-c key=value`` parses the value as TOML)."""

    def _capture_argv(self, monkeypatch) -> dict:
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        return captured

    def _write(self, tmp_path: Path, data: dict) -> str:
        p = tmp_path / ".mcp.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return str(p)

    def test_translates_server_to_codex_override(self, monkeypatch, tmp_path) -> None:
        cfg = self._write(
            tmp_path,
            {
                "mcpServers": {
                    "kagura-memory": {
                        "type": "stdio",
                        "command": "kagura-mcp",
                        "args": ["--profile", "default"],
                    }
                }
            },
        )
        captured = self._capture_argv(monkeypatch)
        invoke("p", mcp_config=cfg)
        argv = captured["argv"]
        joined = " ".join(argv)
        assert "-c" in argv
        assert "mcp_servers.kagura-memory" in joined
        assert "kagura-mcp" in joined
        assert "--profile" in joined and "default" in joined
        # codex config has no "type" key — it must NOT leak into the override.
        assert "stdio" not in joined
        # overrides precede the "--" prompt separator.
        assert argv.index("-c") < argv.index("--")

    def test_no_mcp_config_emits_no_overrides(self, monkeypatch) -> None:
        captured = self._capture_argv(monkeypatch)
        invoke("p")
        assert "-c" not in captured["argv"]
        assert "mcp_servers" not in " ".join(captured["argv"])

    def test_allowed_tools_accepted_but_not_forwarded(
        self, monkeypatch, tmp_path
    ) -> None:
        # codex has no per-call allow-list (claude's --allowedTools); the param is
        # accepted for selector signature parity but must never reach the argv.
        cfg = self._write(tmp_path, {"mcpServers": {"m": {"command": "x"}}})
        captured = self._capture_argv(monkeypatch)
        invoke("p", mcp_config=cfg, allowed_tools=("mcp__kagura-memory__recall",))
        argv = captured["argv"]
        assert "--allowedTools" not in argv
        assert "mcp__kagura-memory__recall" not in " ".join(argv)

    def test_multiple_servers_each_get_an_override(self, monkeypatch, tmp_path) -> None:
        cfg = self._write(
            tmp_path,
            {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}},
        )
        captured = self._capture_argv(monkeypatch)
        invoke("p", mcp_config=cfg)
        argv = captured["argv"]
        assert argv.count("-c") == 2
        joined = " ".join(argv)
        assert "mcp_servers.a" in joined and "mcp_servers.b" in joined

    def test_server_env_is_translated(self, monkeypatch, tmp_path) -> None:
        cfg = self._write(
            tmp_path,
            {"mcpServers": {"m": {"command": "x", "env": {"FOO": "bar"}}}},
        )
        captured = self._capture_argv(monkeypatch)
        invoke("p", mcp_config=cfg)
        joined = " ".join(captured["argv"])
        assert "env" in joined and "FOO" in joined and "bar" in joined

    def test_present_empty_args_preserved_not_dropped(
        self, monkeypatch, tmp_path
    ) -> None:
        # Presence (k in spec), not truthiness: an empty args=[] is faithful config,
        # not the same as "no args key", so it must survive translation.
        cfg = self._write(tmp_path, {"mcpServers": {"m": {"command": "x", "args": []}}})
        captured = self._capture_argv(monkeypatch)
        invoke("p", mcp_config=cfg)
        assert "args = []" in " ".join(captured["argv"])

    def test_command_and_url_conflict_drops_url(self, monkeypatch, tmp_path) -> None:
        # codex rejects a server table carrying both command (stdio) and url
        # ("url is not supported for stdio"); the stdio command wins, url is dropped.
        cfg = self._write(
            tmp_path,
            {"mcpServers": {"m": {"command": "x", "url": "https://e/mcp"}}},
        )
        captured = self._capture_argv(monkeypatch)
        invoke("p", mcp_config=cfg)
        joined = " ".join(captured["argv"])
        assert "command" in joined
        assert "url" not in joined

    def test_url_only_remote_server_is_translated(self, monkeypatch, tmp_path) -> None:
        # A url-only entry is a valid codex streamable_http server.
        cfg = self._write(tmp_path, {"mcpServers": {"r": {"url": "https://e/mcp"}}})
        captured = self._capture_argv(monkeypatch)
        invoke("p", mcp_config=cfg)
        assert "url" in " ".join(captured["argv"])

    def test_missing_file_raises_valueerror_naming_path(
        self, monkeypatch, tmp_path
    ) -> None:
        self._capture_argv(monkeypatch)
        missing = str(tmp_path / "nope.json")
        with pytest.raises(ValueError, match="could not be read"):
            invoke("p", mcp_config=missing)

    def test_malformed_json_raises_valueerror(self, monkeypatch, tmp_path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        self._capture_argv(monkeypatch)
        with pytest.raises(ValueError, match="could not be read"):
            invoke("p", mcp_config=str(bad))


class TestAllowedToolsWarning:
    """codex has no per-call allow-list; allowed_tools is dropped with a once-per-
    process warning (not silently) so a consumer isn't misled about confinement."""

    def test_warns_once_when_allowed_tools_supplied(self, monkeypatch, caplog) -> None:
        codex._warn_allowed_tools_dropped.cache_clear()
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "ok", ""))
        with caplog.at_level(logging.WARNING, logger="kagura_brain.codex"):
            invoke("p", allowed_tools=("t1",))
            invoke("p", allowed_tools=("t2",))
        warns = [r for r in caplog.records if "allowed_tools" in r.getMessage()]
        assert len(warns) == 1

    def test_no_warn_when_allowed_tools_empty(self, monkeypatch, caplog) -> None:
        codex._warn_allowed_tools_dropped.cache_clear()
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "ok", ""))
        with caplog.at_level(logging.WARNING, logger="kagura_brain.codex"):
            invoke("p")
        assert [r for r in caplog.records if "allowed_tools" in r.getMessage()] == []


class TestTomlEscaping:
    def test_control_chars_escaped_as_unicode(self) -> None:
        out = codex._toml_str("a\x00b\x1bc\x7f")
        assert "\x00" not in out and "\x1b" not in out and "\x7f" not in out
        assert "\\u0000" in out and "\\u001b" in out and "\\u007f" in out

    def test_quote_and_backslash_escaped(self) -> None:
        assert codex._toml_str('a"b\\c') == '"a\\"b\\\\c"'

    def test_newline_tab_use_short_escapes(self) -> None:
        assert codex._toml_str("a\nb\tc") == '"a\\nb\\tc"'
