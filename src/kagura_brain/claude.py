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

# Valid ``--permission-mode`` values for ``claude -p`` (Claude Code). ``invoke``
# validates against this set so a typo (e.g. "acceptedits") surfaces as a
# ``ValueError`` at the call boundary instead of an opaque CLI error. The harder
# bypass (every tool pre-approved) is the separate ``--dangerously-skip-permissions``
# flag, exposed as the ``dangerously_skip_permissions`` knob — not a mode here.
#
# ``bypassPermissions`` caveat (issue #23): it is a real Claude Code mode, so it
# stays in this list, but its effect is **equivalent to a full bypass** — it
# pre-approves every tool just like ``dangerously_skip_permissions``. It is the
# one ``permission_mode`` value whose name does NOT carry the ``dangerously_``
# signal, so a caller can reach full bypass through the milder-looking
# ``permission_mode`` argument. The mutual-exclusion check below does not treat it
# specially (it is a valid CLI mode, not the flag), so prefer
# ``dangerously_skip_permissions=True`` when you mean full bypass — it surfaces
# the danger in the argument name — and reserve ``permission_mode`` for the
# genuinely milder modes (``acceptEdits``/``plan``).
_PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")


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
    permission_mode: str | None = None,
    dangerously_skip_permissions: bool = False,
    endpoint: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> BrainResult:
    """Run one headless ``claude -p`` on Claude Code subscription auth.

    Strips every ``ANTHROPIC_*`` and ``CLAUDE_*`` env var (the
    ``_AUTH_OVERRIDE_PREFIXES`` sweep) from the child env via
    :func:`kagura_brain.core._run` so a stale credential, endpoint override, or
    Bedrock/Vertex routing flag (``CLAUDE_CODE_USE_BEDROCK``/``_USE_VERTEX``, #11)
    cannot win over the subscription login.

    **Permission knob (issue #21, opt-in).** In headless ``-p`` mode no human is
    present to answer a permission prompt, so every tool needing approval
    (``Bash``/``gh``/``git``/``Edit``/``Write``) is auto-denied and an autonomous
    run cannot do real work. Two opt-in knobs lift that, mirroring the codex
    adapter's ``sandbox`` / ``bypass_approvals``:

    - ``permission_mode`` (one of :data:`_PERMISSION_MODES`) appends
      ``--permission-mode <mode>``; an unrecognized mode raises ``ValueError``.
    - ``dangerously_skip_permissions`` appends ``--dangerously-skip-permissions``
      (full bypass — every tool pre-approved). This is what an autonomous "idea →
      PR" consumer turns on deliberately; the run's red/yellow/green gates become
      the safety layer instead of per-action prompts.

    The two are mutually exclusive (``--dangerously-skip-permissions`` overrides
    any ``--permission-mode``, so accepting both would emit a contradictory argv
    that gives a false sense of confinement). The **default** sets neither, so the
    headless invocation is byte-for-byte unchanged.

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

    ``model`` (issue #28), when set, appends ``--model <model>`` to pin the model
    (validated by Claude Code at runtime). ``None`` (default) leaves the headless
    argv byte-for-byte unchanged.
    """
    if permission_mode is not None and dangerously_skip_permissions:
        # --dangerously-skip-permissions overrides any --permission-mode, so
        # accepting both would hand back an argv whose --permission-mode is
        # silently nullified — a false sense of confinement. Reject the conflict
        # (mirrors codex's sandbox + bypass_approvals rejection).
        raise ValueError(
            "permission_mode and dangerously_skip_permissions are mutually "
            "exclusive: --dangerously-skip-permissions overrides the permission mode"
        )
    perm_flags: list[str] = []
    if permission_mode is not None:
        if permission_mode not in _PERMISSION_MODES:
            raise ValueError(
                f"invalid permission_mode {permission_mode!r}; "
                f"expected one of {_PERMISSION_MODES}"
            )
        perm_flags += ["--permission-mode", permission_mode]
    if dangerously_skip_permissions:
        perm_flags.append("--dangerously-skip-permissions")
    inject_env = byo_inject_env(
        endpoint,
        api_key,
        url_key="ANTHROPIC_BASE_URL",
        token_key="ANTHROPIC_AUTH_TOKEN",
    )
    # The prompt rides stdin, not argv (issue #17 follow-up): a Windows ``.cmd``
    # shim is launched via ``cmd.exe /c`` and would re-parse an argv-borne prompt
    # (metachar / ``%VAR%`` injection). On stdin it also sidesteps the old
    # ``-``-prefix footgun — ``claude -p`` reads the prompt from stdin when no
    # positional is given, so no ``--`` separator is needed.
    # `--model` pins the model; codex's adapter does the same. None leaves the
    # headless argv byte-for-byte unchanged. Claude validates the name at runtime.
    model_flags = ["--model", model] if model is not None else []
    argv = [
        "claude",
        "-p",
        *perm_flags,
        *model_flags,
        *mcp_args(mcp_config, allowed_tools),
    ]
    return _run(
        argv,
        cwd=cwd,
        timeout=timeout,
        deny_prefixes=_AUTH_OVERRIDE_PREFIXES,
        inject_env=inject_env,
        stdin_text=prompt,
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
