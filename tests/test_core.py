"""Tests for kagura_brain.core — the provider-agnostic launcher seam.

``core`` holds what both the Claude (`claude -p`) and Codex (`codex exec`)
adapters share: the ``BrainResult`` value, the ``_run`` subprocess+env-scrub+
timeout+decode core, the ``as_text`` byte normalizer, and the marker-extraction
helper ``extract_block``. The per-provider argv and credential deny-set live in
the adapter modules; this module is provider-neutral.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kagura_brain.core import BrainResult, _run, as_text, extract_block


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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
        # A CLI may print auth errors to stdout, so an empty stderr must fall
        # back to stdout (the #34 stdout-fallback seam, now shared by adapters).
        assert BrainResult(1, "Invalid API key", "").detail() == "Invalid API key"

    def test_detail_labels_timeout_when_no_output(self) -> None:
        assert BrainResult(-1, "", "", timed_out=True).detail() == "timed out"

    def test_detail_labels_timeout_when_output_is_whitespace_only(self) -> None:
        # A timeout that emitted only whitespace (a lone newline) must still
        # surface the "timed out" label — the whitespace is truthy but carries
        # no diagnostic value, so it must not short-circuit the fallback.
        assert BrainResult(-1, "  ", "\n", timed_out=True).detail() == "timed out"


class TestAsText:
    def test_decodes_bytes_to_str(self) -> None:
        assert as_text(b"PONG") == "PONG"

    def test_replaces_undecodable_bytes(self) -> None:
        # TimeoutExpired can carry a partial multibyte sequence; decode must not
        # raise — undecodable bytes become the replacement char.
        assert as_text(b"\xff") == "�"

    def test_passes_str_through_unchanged(self) -> None:
        assert as_text("already text") == "already text"

    def test_none_becomes_empty_string(self) -> None:
        assert as_text(None) == ""

    def test_empty_str_stays_empty(self) -> None:
        assert as_text("") == ""


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
        out = "A.B(\n# inner\nA.B)"
        assert extract_block(out, "A.B(", "A.B)") == "# inner"

    def test_normalizes_crlf_line_endings(self) -> None:
        out = "KAGURA_PLAN_BEGIN\r\nline1\r\nline2\r\nKAGURA_PLAN_END\r\n"
        assert (
            extract_block(out, "KAGURA_PLAN_BEGIN", "KAGURA_PLAN_END") == "line1\nline2"
        )


class TestRun:
    def test_returns_structured_result_on_normal_run(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "PONG", ""))
        res = _run(["echo", "PONG"])
        assert res.returncode == 0
        assert res.stdout == "PONG"
        assert res.timed_out is False

    def test_passes_argv_through_verbatim(self, monkeypatch) -> None:
        captured: dict = {}

        def _capture(*a, **k):
            captured["argv"] = a[0]
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(["some", "tool", "--", "prompt"])
        assert captured["argv"] == ["some", "tool", "--", "prompt"]

    def test_strips_deny_exact_keys_from_child_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SECRET_KEY", "stale-value")
        monkeypatch.setenv("KEEP_ME", "keep")
        captured: dict = {}

        def _capture(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(["tool"], deny_exact=("SECRET_KEY",))

        env = captured["env"]
        assert env is not None, "_run must pass an explicit env"
        assert "SECRET_KEY" not in env
        assert env["KEEP_ME"] == "keep"

    def test_strips_deny_prefix_keys_from_child_env(self, monkeypatch) -> None:
        # Prefix scrub: every key under a denied prefix is removed, so an unknown
        # future override var under that prefix cannot leak through (fail-secure).
        monkeypatch.setenv("OPENAI_API_KEY", "sk-stale")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example")
        monkeypatch.setenv("CODEX_HOME", "/tmp/evil")
        monkeypatch.setenv("PATH_LIKE", "unrelated")
        captured: dict = {}

        def _capture(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(["tool"], deny_prefixes=("OPENAI_", "CODEX_"))

        env = captured["env"]
        assert "OPENAI_API_KEY" not in env
        assert "OPENAI_BASE_URL" not in env
        assert "CODEX_HOME" not in env
        assert env["PATH_LIKE"] == "unrelated"

    def test_forwards_cwd_and_timeout(self, monkeypatch) -> None:
        captured: dict = {}

        def _capture(*a, **k):
            captured["kwargs"] = k
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(["tool"], cwd=Path("/repo"), timeout=42)
        assert captured["kwargs"]["cwd"] == Path("/repo")
        assert captured["kwargs"]["timeout"] == 42

    def test_decodes_output_as_utf8_with_replacement(self, monkeypatch) -> None:
        captured: dict = {}

        def _capture(*a, **k):
            captured["kwargs"] = k
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(["tool"])
        assert captured["kwargs"].get("encoding") == "utf-8"
        assert captured["kwargs"].get("errors") == "replace"

    def test_timeout_returns_partial_output_normalized(self, monkeypatch) -> None:
        def _raise(*a, **k):
            # TimeoutExpired carries raw bytes even under text/encoding mode.
            raise subprocess.TimeoutExpired(
                cmd=a[0], timeout=k.get("timeout", 0), output=b"partial\xff", stderr=b""
            )

        monkeypatch.setattr(subprocess, "run", _raise)
        res = _run(["tool"])

        assert res.timed_out is True
        assert res.returncode == -1
        assert res.stdout == "partial�"  # bytes decoded with errors=replace
        assert res.stderr == ""
        assert res.detail() == "partial�"

    def test_timeout_detail_labels_when_no_output(self, monkeypatch) -> None:
        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(
                cmd=a[0], timeout=k.get("timeout", 0), output=b"", stderr=b""
            )

        monkeypatch.setattr(subprocess, "run", _raise)
        res = _run(["tool"])
        assert res.stdout == "" and res.stderr == ""
        assert res.detail() == "timed out"
