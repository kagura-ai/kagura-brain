"""Tests for kagura_brain.proc — subprocess helpers for `claude -p`."""

from __future__ import annotations

from kagura_brain.proc import as_text, mcp_args


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


class TestMcpArgs:
    def test_none_config_yields_no_args(self) -> None:
        assert mcp_args(None) == []

    def test_empty_config_yields_no_args(self) -> None:
        assert mcp_args("") == []

    def test_config_without_allowed_tools(self) -> None:
        # Generic: no tool vocabulary baked in — bare --mcp-config when no
        # allowed tools are supplied.
        assert mcp_args("/repo/.mcp.json") == ["--mcp-config", "/repo/.mcp.json"]

    def test_config_with_allowed_tools(self) -> None:
        # The caller (e.g. kagura-engineer) supplies the memory tool names; the
        # claude-axis lib stays free of memory vocabulary.
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
        # A bare str satisfies Sequence[str], so a caller who forgets the tuple
        # would otherwise have it explode into one-character tool names. It must
        # be treated as a single tool name.
        assert mcp_args("/repo/.mcp.json", "mcp__kagura-memory__recall") == [
            "--mcp-config",
            "/repo/.mcp.json",
            "--allowedTools",
            "mcp__kagura-memory__recall",
        ]
