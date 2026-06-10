"""Tests for kagura_brain.selector — the provider-neutral brain selector (#14).

``select(backend, *, endpoint=, api_key=)`` returns a frozen ``BrainHandle``
that confines the claude/codex dispatch to the library, so consumers stop
re-encoding it. ``invoke`` forwards the same ``mcp_config`` / ``allowed_tools``
to whichever adapter ``backend`` names — both backends are MCP-capable, but the
adapter owns the per-provider mechanism (claude per-call flags; codex translates
to ``-c mcp_servers.*`` overrides and ignores ``allowed_tools``). endpoint/api_key
are primitives the consumer supplies — the library never reads the env itself.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kagura_brain import BRAIN_API_KEY_ENV, BrainHandle, select
from kagura_brain import claude, codex
from kagura_brain.core import BrainResult

_SENTINEL = BrainResult(0, "ok", "")


class TestSelect:
    def test_default_backend_is_claude_with_mcp(self) -> None:
        handle = select()
        assert handle.backend == "claude"
        assert handle.supports_mcp is True

    def test_claude_backend_supports_mcp(self) -> None:
        assert select("claude").supports_mcp is True

    def test_codex_backend_supports_mcp(self) -> None:
        handle = select("codex")
        assert handle.backend == "codex"
        assert handle.supports_mcp is True

    def test_unknown_backend_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            select("gemini")

    def test_endpoint_and_api_key_are_stored_on_handle(self) -> None:
        handle = select("codex", endpoint="ollama-cloud", api_key="secret")
        assert handle.endpoint == "ollama-cloud"
        assert handle.api_key == "secret"

    def test_handle_is_frozen(self) -> None:
        handle = select("claude")
        with pytest.raises(FrozenInstanceError):
            handle.backend = "codex"  # type: ignore[misc]


class TestBrainHandleFailsClosed:
    """BrainHandle is exported/constructable, so it validates at construction."""

    def test_direct_construction_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown backend"):
            BrainHandle(backend="gemini", supports_mcp=True)

    def test_direct_construction_inconsistent_supports_mcp_raises(self) -> None:
        # Both backends are MCP-capable; a handle advertising otherwise must not
        # be constructable (it could mis-advertise the capability to a consumer).
        with pytest.raises(ValueError, match="contradicts backend"):
            BrainHandle(backend="claude", supports_mcp=False)
        with pytest.raises(ValueError, match="contradicts backend"):
            BrainHandle(backend="codex", supports_mcp=False)

    def test_valid_direct_construction_is_allowed(self) -> None:
        handle = BrainHandle(backend="codex", supports_mcp=True)
        assert handle.backend == "codex"
        assert handle.supports_mcp is True

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
    def test_forwards_mcp_config_and_allowed_tools(self, monkeypatch) -> None:
        # The selector forwards both to the codex adapter; the adapter (tested in
        # test_codex.py) is responsible for translating mcp_config to -c overrides
        # and ignoring allowed_tools. Here we only assert the selector forwards.
        captured: dict = {}

        def _invoke(prompt, **kwargs):
            captured["kwargs"] = kwargs
            return _SENTINEL

        monkeypatch.setattr(codex, "invoke", _invoke)
        select("codex").invoke(
            "p",
            mcp_config="/repo/.mcp.json",
            allowed_tools=("mcp__kagura-memory__recall",),
        )
        assert captured["kwargs"]["mcp_config"] == "/repo/.mcp.json"
        assert captured["kwargs"]["allowed_tools"] == ("mcp__kagura-memory__recall",)

    def test_forwards_endpoint_and_api_key(self, monkeypatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            codex, "invoke", lambda prompt, **k: captured.update(k) or _SENTINEL
        )
        select("codex", endpoint="ollama-cloud", api_key="secret").invoke("p")
        assert captured["endpoint"] == "ollama-cloud"
        assert captured["api_key"] == "secret"


class TestApiKeyEnvName:
    def test_standard_env_var_name(self) -> None:
        assert BRAIN_API_KEY_ENV == "KAGURA_BRAIN_API_KEY"


class TestPublicExports:
    def test_select_and_handle_are_top_level(self) -> None:
        import kagura_brain

        assert kagura_brain.select is select
        assert kagura_brain.BrainHandle is BrainHandle
        assert kagura_brain.BRAIN_API_KEY_ENV == "KAGURA_BRAIN_API_KEY"
