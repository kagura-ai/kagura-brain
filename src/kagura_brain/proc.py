"""Small subprocess helpers shared by harnesses that drive `claude -p`.

Ported from kagura-engineer's ``proc.py`` and generalized: ``mcp_args`` takes the
allowed-tool names from the caller rather than baking in any memory-tool
vocabulary, so this claude-axis package stays free of memory coupling.
"""

from __future__ import annotations

from collections.abc import Sequence


def mcp_args(mcp_config: str | None, allowed_tools: Sequence[str] = ()) -> list[str]:
    """Extra ``claude -p`` argv to attach an MCP server for in-task tool use.

    Additive (no ``--strict-mcp-config``) so it merges with — rather than
    replaces — any MCP servers the delegated skills rely on. ``allowed_tools``,
    when given, pre-approves those tools so they run without an interactive
    prompt; the caller supplies the names (e.g. the kagura-memory recall/remember
    tools) so this package carries no memory vocabulary. Empty when no config is
    set.
    """
    if not mcp_config:
        return []
    args = ["--mcp-config", mcp_config]
    # A bare ``str`` satisfies ``Sequence[str]``; splatting it would explode into
    # one-character tool names, so treat a lone string as a single tool name.
    if isinstance(allowed_tools, str):
        allowed_tools = (allowed_tools,)
    if allowed_tools:
        args += ["--allowedTools", *allowed_tools]
    return args


def as_text(value: bytes | str | None) -> str:
    """Normalize subprocess stdout/stderr to ``str``.

    ``subprocess.TimeoutExpired`` carries the *raw bytes* captured before the
    kill even when the process was launched with ``text=True`` — so a timeout
    with partial output yields ``bytes``, not ``str``. Decode bytes (replacing
    undecodable sequences); map ``None``/empty to ``""``.
    """
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""
