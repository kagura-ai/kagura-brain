"""Tests for kagura_brain.selector — the provider-neutral brain selector (#14).

``select(backend, *, endpoint=, api_key=)`` returns a frozen ``BrainHandle``
that confines the claude/codex dispatch + the "codex has no per-call MCP" rule
to the library, so consumers stop re-encoding it. The MCP-drop is gated on the
handle's *capability* (``supports_mcp``): a claude handle forwards ``mcp_config``
/ ``allowed_tools`` to the adapter; a codex handle drops them (logging once)
since codex wires MCP out-of-band. endpoint/api_key are
primitives the consumer supplies — the library never reads the env itself.
"""

from __future__ import annotations

import logging

import pytest

from kagura_brain import BRAIN_API_KEY_ENV, BrainHandle, select
from kagura_brain import claude, codex, selector
from kagura_brain.core import BrainResult


@pytest.fixture(autouse=True)
def _reset_codex_drop_log() -> None:
    """The codex-MCP-drop log fires once per process; clear the guard so each
    test that exercises it sees a fresh first-time log."""
    selector._warn_codex_mcp_unsupported.cache_clear()


_SENTINEL = BrainResult(0, "ok", "")


class TestSelect:
    def test_default_backend_is_claude_with_mcp(self) -> None:
        handle = select()
        assert handle.backend == "claude"
        assert handle.supports_mcp is True

    def test_claude_backend_supports_mcp(self) -> None:
        assert select("claude").supports_mcp is True

    def test_codex_backend_does_not_support_mcp(self) -> None:
        handle = select("codex")
        assert handle.backend == "codex"
        assert handle.supports_mcp is False

    def test_unknown_backend_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            select("gemini")

    def test_endpoint_and_api_key_are_stored_on_handle(self) -> None:
        handle = select("codex", endpoint="ollama-cloud", api_key="secret")
        assert handle.endpoint == "ollama-cloud"
        assert handle.api_key == "secret"

    def test_handle_is_frozen(self) -> None:
        handle = select("claude")
        with pytest.raises(Exception):
            handle.backend = "codex"  # type: ignore[misc]

    def test_api_key_absent_from_repr(self) -> None:
        # The BYO key must not appear in repr() — a handle in a log line or
        # exception traceback would otherwise leak it (CSO gate2 finding, #14).
        handle = select("codex", endpoint="ollama-cloud", api_key="SECRET123")
        assert "SECRET123" not in repr(handle)
        assert handle.api_key == "SECRET123"  # still stored/usable


class TestClaudeHandleInvoke:
    def test_forwards_mcp_config_and_allowed_tools(self, monkeypatch) -> None:
        captured: dict = {}

        def _invoke(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return _SENTINEL

        monkeypatch.setattr(claude, "invoke", _invoke)
        handle = select("claude")
        handle.invoke(
            "prompt text",
            mcp_config="/repo/.mcp.json",
            allowed_tools=("mcp__kagura-memory__recall",),
        )
        assert captured["prompt"] == "prompt text"
        assert captured["kwargs"]["mcp_config"] == "/repo/.mcp.json"
        assert captured["kwargs"]["allowed_tools"] == ("mcp__kagura-memory__recall",)

    def test_forwards_endpoint_and_api_key(self, monkeypatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            claude,
            "invoke",
            lambda prompt, **k: captured.update(k) or _SENTINEL,
        )
        select("claude", endpoint="https://gw.example/v1", api_key="byo").invoke("p")
        assert captured["endpoint"] == "https://gw.example/v1"
        assert captured["api_key"] == "byo"

    def test_forwards_cwd_and_timeout(self, monkeypatch) -> None:
        from pathlib import Path

        captured: dict = {}
        monkeypatch.setattr(
            claude, "invoke", lambda prompt, **k: captured.update(k) or _SENTINEL
        )
        select("claude").invoke("p", cwd=Path("/repo"), timeout=42)
        assert captured["cwd"] == Path("/repo")
        assert captured["timeout"] == 42

    def test_returns_adapter_result(self, monkeypatch) -> None:
        result = BrainResult(0, "PONG", "")
        monkeypatch.setattr(claude, "invoke", lambda prompt, **k: result)
        assert select("claude").invoke("p") is result


class TestCodexHandleInvoke:
    def test_drops_mcp_config_and_allowed_tools(self, monkeypatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            codex, "invoke", lambda prompt, **k: captured.update(k) or _SENTINEL
        )
        select("codex").invoke(
            "p",
            mcp_config="/repo/.mcp.json",
            allowed_tools=("mcp__kagura-memory__recall",),
        )
        assert "mcp_config" not in captured
        assert "allowed_tools" not in captured

    def test_forwards_endpoint_and_api_key(self, monkeypatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            codex, "invoke", lambda prompt, **k: captured.update(k) or _SENTINEL
        )
        select("codex", endpoint="ollama-cloud", api_key="secret").invoke("p")
        assert captured["endpoint"] == "ollama-cloud"
        assert captured["api_key"] == "secret"

    def test_logs_drop_once(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(codex, "invoke", lambda prompt, **k: _SENTINEL)
        handle = select("codex")
        with caplog.at_level(logging.WARNING, logger="kagura_brain.selector"):
            handle.invoke("p", mcp_config="/repo/.mcp.json")
            handle.invoke("p", mcp_config="/repo/.mcp.json")
        drops = [r for r in caplog.records if "mcp" in r.getMessage().lower()]
        assert len(drops) == 1

    def test_no_log_when_no_mcp_args(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(codex, "invoke", lambda prompt, **k: _SENTINEL)
        with caplog.at_level(logging.WARNING, logger="kagura_brain.selector"):
            select("codex").invoke("p")
        assert caplog.records == []


class TestApiKeyEnvName:
    def test_standard_env_var_name(self) -> None:
        assert BRAIN_API_KEY_ENV == "KAGURA_BRAIN_API_KEY"


class TestPublicExports:
    def test_select_and_handle_are_top_level(self) -> None:
        import kagura_brain

        assert kagura_brain.select is select
        assert kagura_brain.BrainHandle is BrainHandle
        assert kagura_brain.BRAIN_API_KEY_ENV == "KAGURA_BRAIN_API_KEY"
