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

from .core import BrainResult, _DEFAULT_TIMEOUT_S, _run, byo_inject_env, extract_block
from .doctor import CheckResult, check_binary

__all__ = ["BrainResult", "CheckResult", "check", "extract_block", "invoke", "mcp_args"]

# Env-var prefixes stripped from the child so a value inherited from the parent
# (notably a surrounding Claude Code session) cannot override the subscription
# login. Both whole prefixes are swept — not a fixed key tuple, mirroring the
# codex adapter — so an unknown future override var under either prefix cannot
# leak through (fail-secure):
#
# - ``ANTHROPIC_*`` — ``ANTHROPIC_API_KEY``/``ANTHROPIC_AUTH_TOKEN`` (credentials,
#   #34) and ``ANTHROPIC_BASE_URL`` (gateway routing — an inherited value silently
#   redirects subscription traffic to a foreign endpoint, the "T2" exfil vector, #4).
# - ``CLAUDE_*`` — Claude Code also honors these (#11). ``CLAUDE_CODE_USE_BEDROCK``
#   / ``CLAUDE_CODE_USE_VERTEX`` silently switch ``claude -p`` from subscription
#   auth to a Bedrock/Vertex IAM path; ``CLAUDE_CONFIG_DIR`` relocates the auth dir
#   (the ``CODEX_HOME`` analog). The whole prefix is swept on purpose: benign
#   ``CLAUDE_CODE_*`` vars (e.g. the ``CLAUDE_CODE_ENTRYPOINT`` telemetry tag) are
#   dropped too — that is the fail-secure cost, and is preferred over a narrow
#   ``CLAUDE_CODE_USE_*`` allow-list that a future auth var could slip past. Do NOT
#   re-add a benign ``CLAUDE_*`` passthrough by narrowing this sweep; a caller that
#   genuinely needs one must inject it explicitly (the #2 ``inject_env`` opt-in).
#
# Deliberate BYO-endpoint routing is a separate, explicit opt-in (#2) that injects
# ``ANTHROPIC_AUTH_TOKEN``/``ANTHROPIC_BASE_URL`` AFTER this sweep — it never sets a
# ``CLAUDE_*`` var, so it is unaffected by the wider sweep.
_AUTH_OVERRIDE_PREFIXES = ("ANTHROPIC_", "CLAUDE_")


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
    endpoint: str | None = None,
    api_key: str | None = None,
) -> BrainResult:
    """Run one headless ``claude -p`` on Claude Code subscription auth.

    Strips every ``ANTHROPIC_*`` and ``CLAUDE_*`` env var (the
    ``_AUTH_OVERRIDE_PREFIXES`` sweep) from the child env via
    :func:`kagura_brain.core._run` so a stale credential, endpoint override, or
    Bedrock/Vertex routing flag (``CLAUDE_CODE_USE_BEDROCK``/``_USE_VERTEX``, #11)
    cannot win over the subscription login.

    **BYO endpoint (issue #2, opt-in).** Pass ``endpoint`` + ``api_key`` together
    to deliberately route at a caller-chosen Anthropic-compatible gateway: they
    are injected as ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` *after* the
    scrub, so only these explicit values reach the child — an ambient
    ``ANTHROPIC_BASE_URL`` is still stripped. With neither, the default
    subscription-auth path is byte-for-byte unchanged. The token maps to
    ``ANTHROPIC_AUTH_TOKEN`` (not ``ANTHROPIC_API_KEY``) — the var Claude Code
    uses for gateway auth. Supplying only one of the pair raises ``ValueError``;
    a non-https endpoint warns (context goes off-box in the clear). Note: Ollama
    Cloud is OpenAI-compatible, so it has no built-in preset here — supply your
    own Anthropic-compatible gateway URL.
    """
    inject_env = byo_inject_env(
        endpoint,
        api_key,
        url_key="ANTHROPIC_BASE_URL",
        token_key="ANTHROPIC_AUTH_TOKEN",
    )
    # ``-p``/``--print`` is a boolean flag and the prompt is positional, so a
    # prompt beginning with ``-`` would be parsed as an option. The ``--``
    # separator (after the MCP flags) forces it to be the prompt.
    argv = ["claude", "-p", *mcp_args(mcp_config, allowed_tools), "--", prompt]
    return _run(
        argv,
        cwd=cwd,
        timeout=timeout,
        deny_prefixes=_AUTH_OVERRIDE_PREFIXES,
        inject_env=inject_env,
    )


def check() -> CheckResult:
    """Presence check for the ``claude`` CLI — the pre-flight companion to
    :func:`invoke` (mirrors how ``invoke`` wraps ``core._run``).

    Presence only (``shutil.which`` via :func:`kagura_brain.doctor.check_binary`).
    Auth ("logged in") is intentionally NOT probed: Claude Code has no clean,
    non-interactive auth check, and a ``claude -p`` round-trip would run a real,
    billable turn. For tools that *do* expose a clean auth exit code (e.g.
    ``gh auth status``) use :func:`kagura_brain.doctor.check_auth` instead.
    """
    return check_binary("claude")
