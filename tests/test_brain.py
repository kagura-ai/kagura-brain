"""Tests for kagura_brain.brain — the headless `claude -p` launcher."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kagura_brain.brain import BrainResult, extract_block, invoke


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestExtractBlock:
    def test_pulls_text_between_markers(self) -> None:
        out = "noise\nKAGURA_PLAN_BEGIN\n# Plan\n- step 1\nKAGURA_PLAN_END\ntrailing"
        assert (
            extract_block(out, "KAGURA_PLAN_BEGIN", "KAGURA_PLAN_END")
            == "# Plan\n- step 1"
        )

    def test_returns_none_when_absent(self) -> None:
        assert extract_block("no markers here", "BEGIN", "END") is None

    def test_returns_none_for_empty_text(self) -> None:
        assert extract_block("", "BEGIN", "END") is None

    def test_markers_are_regex_escaped(self) -> None:
        # Markers containing regex metacharacters must match literally.
        out = "A.B(\n# inner\nA.B)"
        assert extract_block(out, "A.B(", "A.B)") == "# inner"

    def test_normalizes_crlf_line_endings(self) -> None:
        # CRLF-authored output must not leave interior carriage returns in the
        # payload — .strip() only trims the edges.
        out = "KAGURA_PLAN_BEGIN\r\nline1\r\nline2\r\nKAGURA_PLAN_END\r\n"
        assert (
            extract_block(out, "KAGURA_PLAN_BEGIN", "KAGURA_PLAN_END") == "line1\nline2"
        )


class TestBrainResult:
    def test_ok_when_zero_and_not_timed_out(self) -> None:
        assert BrainResult(0, "out", "").ok is True

    def test_not_ok_on_nonzero_returncode(self) -> None:
        assert BrainResult(1, "", "boom").ok is False

    def test_not_ok_when_timed_out(self) -> None:
        assert BrainResult(0, "", "", timed_out=True).ok is False

    def test_detail_prefers_stderr(self) -> None:
        assert BrainResult(1, "stdout msg", "stderr msg").detail() == "stderr msg"

    def test_detail_falls_back_to_stdout(self) -> None:
        # claude -p prints auth errors to stdout, so an empty stderr must fall
        # back to stdout (the #34 stdout-fallback seam).
        assert BrainResult(1, "Invalid API key", "").detail() == "Invalid API key"


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
        # ANTHROPIC_AUTH_TOKEN is a credential that overrides subscription auth
        # just like the API key, so it must be stripped from the child env too.
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
        # -p/--print is a boolean flag and the prompt is positional, so a prompt
        # beginning with "-" would otherwise be parsed by claude as an option
        # (e.g. "--version" -> prints version, exits 0, prompt never runs).
        captured: dict = {}

        def _run(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("--version")
        assert captured["argv"][-2:] == ["--", "--version"]

    def test_decodes_output_as_utf8_with_replacement(self, monkeypatch) -> None:
        # The success path must decode child output with utf-8/errors=replace,
        # consistent with the timeout path, so a non-UTF-8 locale or a stray
        # byte never raises UnicodeDecodeError inside invoke().
        captured: dict = {}

        def _run(*a, **k):
            captured["kwargs"] = k
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        invoke("idea")
        assert captured["kwargs"].get("encoding") == "utf-8"
        assert captured["kwargs"].get("errors") == "replace"

    def test_timeout_returns_partial_output_normalized(self, monkeypatch) -> None:
        def _run(*a, **k):
            # TimeoutExpired carries raw bytes even under text=True.
            raise subprocess.TimeoutExpired(
                cmd=a[0], timeout=k.get("timeout", 0), output=b"partial\xff", stderr=b""
            )

        monkeypatch.setattr(subprocess, "run", _run)
        res = invoke("idea")

        assert res.timed_out is True
        assert res.returncode == -1
        assert res.stdout == "partial�"  # bytes decoded with errors=replace
        assert res.stderr == ""  # real (empty) stderr is preserved, not masked
        # The captured partial stdout must surface via detail() — the docstring
        # calls it "invaluable for diagnosing a stalled phase".
        assert res.detail() == "partial�"

    def test_timeout_detail_labels_when_no_output(self, monkeypatch) -> None:
        def _run(*a, **k):
            raise subprocess.TimeoutExpired(
                cmd=a[0], timeout=k.get("timeout", 0), output=b"", stderr=b""
            )

        monkeypatch.setattr(subprocess, "run", _run)
        res = invoke("idea")
        assert res.stdout == "" and res.stderr == ""
        # With no captured output, detail() falls back to a "timed out" label.
        assert res.detail() == "timed out"
