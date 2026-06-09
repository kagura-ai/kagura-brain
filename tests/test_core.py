"""Tests for kagura_brain.core — the provider-agnostic launcher seam.

``core`` holds what both the Claude (`claude -p`) and Codex (`codex exec`)
adapters share: the ``BrainResult`` value, the ``_run`` subprocess+env-scrub+
timeout+decode core, the ``as_text`` byte normalizer, and the marker-extraction
helper ``extract_block``. The per-provider argv and credential deny-set live in
the adapter modules; this module is provider-neutral.
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pytest

from kagura_brain.core import (
    BrainResult,
    _run,
    as_text,
    byo_inject_env,
    extract_block,
)


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

    def test_injects_env_after_scrub(self, monkeypatch) -> None:
        # scrub→inject order is load-bearing: a key that lives under a denied
        # prefix AND is re-supplied by the caller must end up with the CALLER's
        # value — the ambient one is stripped first, then the caller value is
        # injected. Injecting before the scrub would let the deny loop strip the
        # caller's own value (it shares the prefix), reopening the override hole.
        monkeypatch.setenv("FOO_BASE_URL", "https://evil.example/v1")
        captured: dict = {}

        def _capture(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(
            ["tool"],
            deny_prefixes=("FOO_",),
            inject_env={"FOO_BASE_URL": "https://good.example/v1"},
        )
        assert captured["env"]["FOO_BASE_URL"] == "https://good.example/v1"

    def test_inject_env_none_is_noop(self, monkeypatch) -> None:
        monkeypatch.setenv("KEEP_ME", "v")
        captured: dict = {}

        def _capture(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(["tool"], inject_env=None)
        assert captured["env"]["KEEP_ME"] == "v"

    def test_inject_only_adds_supplied_keys(self, monkeypatch) -> None:
        # A denied-prefix key the caller does NOT re-supply stays stripped even
        # when other keys are injected — injection is surgical, not a blanket
        # re-import of the ambient environment.
        monkeypatch.setenv("FOO_BASE_URL", "https://evil.example/v1")
        monkeypatch.setenv("FOO_MODEL", "ambient-model")
        captured: dict = {}

        def _capture(*a, **k):
            captured["env"] = k.get("env")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _capture)
        _run(
            ["tool"],
            deny_prefixes=("FOO_",),
            inject_env={"FOO_BASE_URL": "https://good.example/v1"},
        )
        assert captured["env"]["FOO_BASE_URL"] == "https://good.example/v1"
        assert "FOO_MODEL" not in captured["env"]

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


class TestByoInjectEnv:
    """The shared BYO-endpoint env builder used by both adapters.

    Centralizes the credential-injection security rules in one tested place:
    both-or-neither (a half-configured BYO mode is an error, not a silent
    fallback), a non-https warning (the caller is shipping prompt/code context
    off-box), and a ``None`` result when BYO is not requested at all. The
    returned mapping is injected AFTER ``_run``'s deny-set scrub (see
    ``TestRun.test_injects_env_after_scrub``), so only these explicit values —
    never the ambient environment — reach the child.
    """

    def test_neither_returns_none(self) -> None:
        assert byo_inject_env(None, None, url_key="U", token_key="T") is None

    def test_both_returns_mapped_env(self) -> None:
        assert byo_inject_env(
            "https://good.example/v1", "tok", url_key="U", token_key="T"
        ) == {"U": "https://good.example/v1", "T": "tok"}

    def test_endpoint_without_token_raises(self) -> None:
        with pytest.raises(ValueError):
            byo_inject_env("https://good.example/v1", None, url_key="U", token_key="T")

    def test_token_without_endpoint_raises(self) -> None:
        with pytest.raises(ValueError):
            byo_inject_env(None, "tok", url_key="U", token_key="T")

    def test_empty_string_endpoint_counts_as_unsupplied(self) -> None:
        # An empty string (e.g. os.environ.get of an unset-to-empty var) is
        # "not supplied", not a valid endpoint — half-configured ⇒ ValueError,
        # never a silently-injected empty value.
        with pytest.raises(ValueError):
            byo_inject_env("", "tok", url_key="U", token_key="T")

    def test_empty_string_api_key_counts_as_unsupplied(self) -> None:
        with pytest.raises(ValueError):
            byo_inject_env("https://good.example/v1", "", url_key="U", token_key="T")

    def test_both_empty_strings_return_none(self) -> None:
        # Both falsy ⇒ BYO not requested; the default scrub path stands.
        assert byo_inject_env("", "", url_key="U", token_key="T") is None

    def test_non_https_endpoint_warns(self) -> None:
        with pytest.warns(UserWarning):
            byo_inject_env("http://plain.example/v1", "tok", url_key="U", token_key="T")

    def test_https_endpoint_does_not_warn(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning becomes an exception
            assert byo_inject_env(
                "https://secure.example/v1", "tok", url_key="U", token_key="T"
            ) == {"U": "https://secure.example/v1", "T": "tok"}
