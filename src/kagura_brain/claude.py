"""Drive Claude Code as a brain via one headless ``claude -p`` subprocess.

The Claude adapter runs the child on Claude Code **subscription** auth: it
deny-sets the ``ANTHROPIC_*`` credential env vars so a stale value inherited
from the environment (notably a surrounding Claude Code session) cannot override
the subscription login and make ``claude -p`` die with "Invalid API key"
(kagura-engineer #34, kagura-planner PR#5). The shared subprocess/env-scrub/
timeout/decode seam lives in :mod:`kagura_brain.core`; this module owns only the
Claude argv and its credential deny-set.

Domain concerns (prompt building, verdict/marker vocabulary) stay in the
consuming harness; ``extract_block`` (re-exported from ``core``) handles
sentinel-delimited payloads generically.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .core import BrainResult, _DEFAULT_TIMEOUT_S, _run, extract_block

__all__ = ["BrainResult", "extract_block", "invoke", "mcp_args"]

# Credential env vars that would override Claude Code subscription auth if a
# stale value is inherited (notably from a surrounding Claude Code session).
# Stripped from the child env so the subscription login always wins (#34).
_AUTH_OVERRIDE_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


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


def invoke(
    prompt: str,
    *,
    cwd: Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
    mcp_config: str | None = None,
    allowed_tools: Sequence[str] = (),
) -> BrainResult:
    """Run one headless ``claude -p`` on Claude Code subscription auth.

    Strips the credential env vars in ``_AUTH_OVERRIDE_ENV`` from the child env
    (via :func:`kagura_brain.core._run`) so a stale value cannot override the
    subscription login.
    """
    # ``-p``/``--print`` is a boolean flag and the prompt is positional, so a
    # prompt beginning with ``-`` would be parsed as an option. The ``--``
    # separator (after the MCP flags) forces it to be the prompt.
    argv = ["claude", "-p", *mcp_args(mcp_config, allowed_tools), "--", prompt]
    return _run(argv, cwd=cwd, timeout=timeout, deny_exact=_AUTH_OVERRIDE_ENV)
