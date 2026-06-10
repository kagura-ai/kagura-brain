"""Drive OpenAI's Codex CLI as a brain via one headless ``codex exec`` subprocess.

Sibling of :mod:`kagura_brain.claude`, sharing the launcher core in
:mod:`kagura_brain.core`. ``codex exec`` is the headless analog of ``claude -p``.

**Subscription-auth parity (the primary requirement).** Like the Claude adapter
strips ``ANTHROPIC_*``, this adapter strips every ``OPENAI_*`` / ``CODEX_*`` env
var from the child so the ``codex login`` (ChatGPT subscription) credentials in
the default ``~/.codex`` win. It is a *prefix* sweep, not a fixed tuple, so an
unknown future override var under those prefixes cannot leak through
(fail-secure). In particular it removes:

- ``OPENAI_API_KEY`` — would switch to metered API-key billing / a foreign account.
- ``OPENAI_BASE_URL`` — would silently redirect subscription traffic to a foreign
  endpoint (an exfiltration vector with no analog on the Claude side).
- ``CODEX_HOME`` — relocates the auth/config dir; stripping it forces the default
  ``~/.codex`` where ``codex login`` wrote the subscription credentials.

Routing Codex at a *caller-chosen* endpoint (Ollama Cloud / BYO gateway) is the
deliberate inverse of this scrub and is tracked separately (issue #2), not here.

**MCP differs from Claude.** Codex manages MCP servers via ``codex mcp`` /
``~/.codex/config.toml`` (persistent), not per-call ``--mcp-config`` /
``--allowedTools`` flags — so there is no ``mcp_args`` equivalent here. Sandbox /
approval is **opt-in**: pass ``sandbox=`` to set ``-s/--sandbox``, or
``bypass_approvals=True`` for ``--dangerously-bypass-approvals-and-sandbox``; the
default invocation loosens neither.

**Result protocol.** Returns raw stdout/stderr in a ``BrainResult`` and reuses
``extract_block`` for sentinel-delimited payloads, identical to the Claude
adapter. ``codex exec`` also offers ``-o/--output-last-message`` and
``--output-schema`` (JSON-Schema-enforced output) which could replace the marker
protocol on the Codex side — deferred as a follow-up; parity is kept for now.
"""

from __future__ import annotations

from pathlib import Path

from .core import BrainResult, _DEFAULT_TIMEOUT_S, _run, byo_inject_env, extract_block
from .doctor import CheckResult, check_binary

__all__ = [
    "BrainResult",
    "CheckResult",
    "OLLAMA_CLOUD_ENDPOINT",
    "check",
    "extract_block",
    "invoke",
]

# Convenience preset for the BYO-endpoint mode (issue #2): Ollama Cloud exposes
# an OpenAI-compatible API, so pass ``endpoint="ollama-cloud"`` (the alias) or
# this constant to ``invoke`` and it resolves to this URL. There is no Claude-side
# preset — Claude Code speaks the Anthropic protocol and Ollama Cloud does not
# expose an Anthropic-compatible endpoint, so the claude adapter takes a
# caller-supplied gateway URL only.
OLLAMA_CLOUD_ENDPOINT = "https://ollama.com/v1"

# ``endpoint="ollama-cloud"`` is a friendly alias for OLLAMA_CLOUD_ENDPOINT. A
# real endpoint is always a URL, so this short token cannot collide with one.
_ENDPOINT_ALIASES = {"ollama-cloud": OLLAMA_CLOUD_ENDPOINT}

# Local backends Codex can drive via ``--oss --local-provider`` (Codex 0.133.0).
_LOCAL_PROVIDERS = ("ollama", "lmstudio")

# Codex CLI version whose `codex exec` argv + auth/env surface this adapter was
# verified against. Bump on CLI upgrade and RE-AUDIT the deny-set prefixes below
# (new OPENAI_*/CODEX_* override vars). The version is NOT asserted as a literal
# anywhere — only its presence/shape is tested (a frozen literal would force a
# test edit on every CLI bump for no safety gain).
_CODEX_VERIFIED_VERSION = "0.133.0"

# Prefix sweep: every env var under these prefixes is stripped from the child so
# the subscription login wins. See module docstring for the threat rationale.
_AUTH_OVERRIDE_PREFIXES = ("OPENAI_", "CODEX_")

# Valid `-s/--sandbox` policies per `codex exec --help` (Codex 0.133.0).
_SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")


def invoke(
    prompt: str,
    *,
    cwd: Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
    sandbox: str | None = None,
    bypass_approvals: bool = False,
    endpoint: str | None = None,
    api_key: str | None = None,
    local_provider: str | None = None,
) -> BrainResult:
    """Run one headless ``codex exec`` on Codex (ChatGPT subscription) auth.

    Strips every ``OPENAI_*`` / ``CODEX_*`` env var from the child (via
    :func:`kagura_brain.core._run`) so the subscription login wins. ``sandbox``
    (one of :data:`_SANDBOX_MODES`) and ``bypass_approvals`` are opt-in; neither
    is set by default. Raises ``ValueError`` for an unrecognized ``sandbox`` mode.

    **BYO endpoint (issue #2, opt-in).** Two mutually-exclusive non-default
    backends:

    - *Cloud / gateway* — ``endpoint`` + ``api_key`` (both required) are injected
      as ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` *after* the scrub, so the
      caller-chosen endpoint wins while ambient overrides stay stripped.
      ``endpoint="ollama-cloud"`` is an alias for :data:`OLLAMA_CLOUD_ENDPOINT`.
      A non-https endpoint warns; supplying only one of the pair raises.
    - *Local* — ``local_provider`` (one of :data:`_LOCAL_PROVIDERS`) emits
      ``--oss --local-provider <p>`` for a local ollama/lmstudio backend. This
      needs no env override, so the scrub is untouched (nothing injected).

    Passing ``local_provider`` together with ``endpoint``/``api_key`` is a
    ``ValueError`` (a local backend and a remote endpoint are contradictory).
    With none of the three, the default subscription-auth path is unchanged.
    """
    if sandbox is not None and bypass_approvals:
        # --dangerously-bypass-approvals-and-sandbox overrides any sandbox
        # policy, so accepting both would hand back an argv whose --sandbox is
        # silently nullified — a false sense of confinement. Reject the conflict.
        raise ValueError(
            "sandbox and bypass_approvals are mutually exclusive: "
            "--dangerously-bypass-approvals-and-sandbox overrides the sandbox policy"
        )
    if local_provider is not None and (endpoint is not None or api_key is not None):
        # A local --oss backend and a remote BYO endpoint are contradictory: one
        # routes to a local process, the other to a remote URL. Reject rather
        # than silently letting one win.
        raise ValueError(
            "local_provider is mutually exclusive with endpoint/api_key: "
            "choose a local backend OR a remote endpoint, not both"
        )
    flags: list[str] = []
    if sandbox is not None:
        if sandbox not in _SANDBOX_MODES:
            raise ValueError(
                f"invalid sandbox mode {sandbox!r}; expected one of {_SANDBOX_MODES}"
            )
        flags += ["--sandbox", sandbox]
    if bypass_approvals:
        flags.append("--dangerously-bypass-approvals-and-sandbox")
    if local_provider is not None:
        if local_provider not in _LOCAL_PROVIDERS:
            raise ValueError(
                f"invalid local_provider {local_provider!r}; "
                f"expected one of {_LOCAL_PROVIDERS}"
            )
        flags += ["--oss", "--local-provider", local_provider]
    # Resolve the friendly alias (e.g. "ollama-cloud") to its URL before building
    # the BYO inject-env; a literal URL (or None) passes through unchanged.
    resolved_endpoint = endpoint
    if endpoint is not None:
        resolved_endpoint = _ENDPOINT_ALIASES.get(endpoint, endpoint)
    inject_env = byo_inject_env(
        resolved_endpoint,
        api_key,
        url_key="OPENAI_BASE_URL",
        token_key="OPENAI_API_KEY",
    )
    # The prompt is positional; ``exec`` also has subcommands (resume/review/help)
    # and a prompt beginning with ``-`` would parse as an option. The ``--``
    # separator forces the prompt to be the prompt in both cases.
    argv = ["codex", "exec", *flags, "--", prompt]
    return _run(
        argv,
        cwd=cwd,
        timeout=timeout,
        deny_prefixes=_AUTH_OVERRIDE_PREFIXES,
        inject_env=inject_env,
    )


def check() -> CheckResult:
    """Presence check for the ``codex`` CLI — the pre-flight companion to
    :func:`invoke` (mirrors how ``invoke`` wraps ``core._run``).

    Presence only (``shutil.which`` via :func:`kagura_brain.doctor.check_binary`).
    Auth ("logged in") is intentionally NOT probed: ``codex`` has no clean,
    non-interactive auth check, and a ``codex exec`` round-trip would run a real,
    billable turn. For tools that *do* expose a clean auth exit code use
    :func:`kagura_brain.doctor.check_auth` instead.
    """
    return check_binary("codex")
