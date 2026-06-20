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

**MCP differs from Claude.** Codex has no per-call ``--mcp-config`` /
``--allowedTools`` flags; it configures MCP via ``~/.codex/config.toml`` /
``codex mcp`` or per-call ``-c mcp_servers.<name>=<TOML>`` overrides. So
``invoke`` accepts the same ``mcp_config`` path as the Claude adapter but
*translates* it (:func:`_mcp_overrides`) into those ``-c`` overrides, while
``allowed_tools`` has no codex analog and is accepted-but-not-forwarded. Sandbox /
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

import json
import logging
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

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

# Id of the custom codex model_provider the BYO path defines AND selects (#27).
# codex 0.141 prefers a file-based ChatGPT login (``~/.codex/auth.json``) over an
# env-injected ``OPENAI_BASE_URL``, so a BYO run would silently route to the
# ChatGPT account; selecting an explicit provider for the endpoint forces the
# caller's endpoint to win regardless of any ambient login.
_BYO_PROVIDER_ID = "kagura_byo"

# Local backends Codex can drive via ``--oss --local-provider`` (Codex 0.133.0).
_LOCAL_PROVIDERS = ("ollama", "lmstudio")

# Codex CLI version whose `codex exec` argv + auth/env surface this adapter was
# verified against. Bump on CLI upgrade and RE-AUDIT the deny-set prefixes below
# (new OPENAI_*/CODEX_* override vars). The version is NOT asserted as a literal
# anywhere — only its presence/shape is tested (a frozen literal would force a
# test edit on every CLI bump for no safety gain).
#
# 0.141.0 re-audit (#27): the BYO path was verified end-to-end against ollama-cloud
# (a run reported `provider: kagura_byo` and returned a real answer, NOT the old
# silent ChatGPT-account mis-route). 0.141 also REMOVED the `wire_api = "chat"`
# custom-provider protocol — the BYO provider now declares `wire_api = "responses"`,
# so a BYO endpoint must speak the OpenAI Responses API (Ollama Cloud does).
_CODEX_VERIFIED_VERSION = "0.141.0"

# Prefix sweep: every env var under these prefixes is stripped from the child so
# the subscription login wins. See module docstring for the threat rationale.
_AUTH_OVERRIDE_PREFIXES = ("OPENAI_", "CODEX_")

# Valid `-s/--sandbox` policies per `codex exec --help` (Codex 0.133.0).
_SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")

# Keys of a claude-format ``.mcp.json`` server entry that map onto a codex
# ``[mcp_servers.<name>]`` table. Other keys (e.g. claude's ``"type": "stdio"``)
# have no codex analog and are dropped so they can't trip ``--strict-config``.
# ``command`` (stdio) and ``url`` (streamable_http) are mutually exclusive in a
# codex server table — emitting both yields "url is not supported for stdio", so
# a ``command`` entry wins and ``url`` is dropped for it (see :func:`_mcp_overrides`).
_MCP_TRANSLATED_KEYS = ("command", "args", "env", "url")

_logger = logging.getLogger(__name__)

# Short TOML escapes for the control chars that have them; every other char below
# U+0020 must be emitted as ``\uXXXX`` (a raw control char is illegal in a TOML
# basic string and would abort codex's config parse).
_TOML_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


@lru_cache(maxsize=1)
def _warn_allowed_tools_dropped() -> None:
    """Warn once per process that ``allowed_tools`` is not enforced for codex.

    Codex has no per-call tool allow-list (claude's ``--allowedTools``); it gates
    MCP tool calls through its sandbox/approval model. ``invoke`` accepts
    ``allowed_tools`` for selector signature parity but cannot forward it, so this
    surfaces the capability gap at runtime instead of silently dropping it.
    """
    _logger.warning(
        "codex has no per-call tool allow-list; the supplied allowed_tools is "
        "ignored. Codex gates MCP tool calls via its sandbox/approval model — use "
        "sandbox= / bypass_approvals= to control that."
    )


def _toml_str(s: str) -> str:
    """Serialize a Python ``str`` as a TOML basic string, escaping all control chars."""
    out = []
    for ch in s:
        if ch in _TOML_SHORT_ESCAPES:
            out.append(_TOML_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_key(key: str) -> str:
    """A TOML key segment: bare when safe, otherwise a quoted key."""
    if key and all(c.isalnum() or c in "_-" for c in key):
        return key
    return _toml_str(key)


def _toml_inline(value: Any) -> str:
    """Serialize a JSON-ish value as inline TOML (str / bool / int / list / dict).

    ``str`` is checked before the ``Sequence`` branch (``str`` *is* a ``Sequence``)
    so it serializes as a basic string, not a char array.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Mapping):
        body = ", ".join(
            f"{_toml_key(str(k))} = {_toml_inline(v)}" for k, v in value.items()
        )
        return "{ " + body + " }"
    if isinstance(value, Sequence):
        return "[" + ", ".join(_toml_inline(v) for v in value) + "]"
    # Fallback: stringify anything unexpected so we never emit invalid TOML.
    return _toml_str(str(value))


def _mcp_overrides(mcp_config: str | None) -> list[str]:
    """Translate a claude-format ``.mcp.json`` into ``codex exec`` argv.

    Codex has no ``--mcp-config`` flag. The per-call config channel is
    ``-c <dotted.path>=<TOML value>``; this emits one
    ``-c mcp_servers.<name>={...}`` per entry in the file's ``mcpServers`` map,
    carrying the codex-relevant keys (:data:`_MCP_TRANSLATED_KEYS`). ``-c`` is the
    *only* per-call channel that survives this adapter's env scrub: the
    file/env-based handoff (``CODEX_HOME`` → a generated ``config.toml``) is
    unavailable because ``_AUTH_OVERRIDE_PREFIXES`` strips ``CODEX_*`` to force the
    subscription ``~/.codex``. Note these overrides *layer onto* that
    ``~/.codex/config.toml``: a server whose name already exists there is merged
    field-by-field, so a name clash with a different transport (e.g. an existing
    streamable_http server of the same name) can produce an invalid hybrid that
    codex rejects at config load — pick server names that don't collide.

    ``command`` (stdio) and ``url`` (streamable_http) are mutually exclusive in a
    codex server table; when an entry carries both, ``command`` wins and ``url`` is
    dropped. Returns ``[]`` when ``mcp_config`` is falsy or has no ``mcpServers``
    map. Raises ``ValueError`` (naming the path) when the file is missing or not
    valid JSON — unlike the claude adapter, which hands the unread path to its CLI.
    """
    if not mcp_config:
        return []
    try:
        with open(mcp_config, encoding="utf-8") as fh:
            data: Any = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"codex mcp_config {mcp_config!r} could not be read as JSON: {exc}"
        ) from exc
    servers = data.get("mcpServers") if isinstance(data, Mapping) else None
    if not isinstance(servers, Mapping):
        return []
    overrides: list[str] = []
    for name, spec in servers.items():
        if not isinstance(spec, Mapping):
            continue
        # Presence (``k in spec``), not truthiness, so a legitimately empty value
        # (``args: []`` / ``env: {}``) is translated faithfully rather than dropped.
        table = {k: spec[k] for k in _MCP_TRANSLATED_KEYS if k in spec}
        # A stdio server (``command``) and a remote server (``url``) cannot coexist
        # in one codex table; prefer the stdio launch and drop the conflicting url.
        if "command" in table:
            table.pop("url", None)
        if not table:
            continue
        overrides += ["-c", f"mcp_servers.{_toml_key(str(name))}={_toml_inline(table)}"]
    return overrides


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
    model: str | None = None,
    mcp_config: str | None = None,
    allowed_tools: Sequence[str] = (),
) -> BrainResult:
    """Run one headless ``codex exec`` on Codex (ChatGPT subscription) auth.

    Strips every ``OPENAI_*`` / ``CODEX_*`` env var from the child (via
    :func:`kagura_brain.core._run`) so the subscription login wins. ``sandbox``
    (one of :data:`_SANDBOX_MODES`) and ``bypass_approvals`` are opt-in; neither
    is set by default. Raises ``ValueError`` for an unrecognized ``sandbox`` mode.

    **BYO endpoint (issue #2, opt-in).** Two mutually-exclusive non-default
    backends:

    - *Cloud / gateway* — ``endpoint`` + ``api_key`` (both required). The key is
      injected as ``OPENAI_API_KEY`` *after* the scrub, and the endpoint is wired
      via a custom ``-c model_providers.<id>`` config override that is then
      SELECTED with ``-c model_provider=<id>`` (#27): codex 0.141 prefers a
      file-based ChatGPT login over an env-injected ``OPENAI_BASE_URL``, so
      selecting an explicit provider is what forces the caller's endpoint to win
      (an env-only override silently mis-routed to the ChatGPT account). The
      provider declares ``wire_api = "responses"`` (0.141 removed the ``"chat"``
      protocol), so **the BYO endpoint must speak the OpenAI Responses API**
      (Ollama Cloud does). ``endpoint="ollama-cloud"`` is an alias for
      :data:`OLLAMA_CLOUD_ENDPOINT`. A non-https endpoint warns; supplying only
      one of the pair raises.
    - *Local* — ``local_provider`` (one of :data:`_LOCAL_PROVIDERS`) emits
      ``--oss --local-provider <p>`` for a local ollama/lmstudio backend. This
      needs no env override, so the scrub is untouched (nothing injected).

    Passing ``local_provider`` together with ``endpoint``/``api_key`` is a
    ``ValueError`` (a local backend and a remote endpoint are contradictory).
    With none of the three, the default subscription-auth path is unchanged.

    ``model`` (issue #28), when set, appends ``--model <model>`` — pinning the
    model under any auth mode (subscription / BYO endpoint / local ``--oss``).
    codex validates the name at runtime (e.g. a ChatGPT login rejects a
    non-OpenAI model); the adapter only passes it through. ``None`` (default)
    leaves the argv unchanged.

    **MCP wiring.** ``mcp_config`` is a path to a claude-format ``.mcp.json``; its
    ``mcpServers`` are translated (:func:`_mcp_overrides`) into per-call
    ``-c mcp_servers.<name>=<TOML>`` config overrides — codex's equivalent of
    Claude Code's ``--mcp-config``. With ``mcp_config=None`` no overrides are
    added and the argv is unchanged. A missing or non-JSON ``mcp_config`` raises
    ``ValueError`` (the claude adapter defers an unread path to its CLI; codex must
    parse it to translate). ``allowed_tools`` is **accepted for selector signature
    parity but not forwarded** — codex has no per-call tool allow-list (claude's
    ``--allowedTools``); a non-empty value triggers a once-per-process warning so
    the dropped confinement is visible rather than silent. Codex gates MCP tool
    calls through its sandbox/approval model, so the caller controls that via
    ``sandbox`` / ``bypass_approvals``. Note: an MCP-server-using turn run under
    the default (no sandbox/bypass) may block on approval — pass ``sandbox=`` or
    ``bypass_approvals=True`` for unattended use. (The ``-c mcp_servers.*`` form is
    verified against codex 0.133.0; CI mocks the subprocess.)
    """
    # allowed_tools has no codex analog (see docstring); accepted for selector
    # parity only. Warn once (rather than silently dropping) so a consumer relying
    # on it for tool confinement isn't left with a false sense of enforcement.
    if allowed_tools:
        _warn_allowed_tools_dropped()
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
    if model is not None:
        # Pin the model (subscription, BYO endpoint, or a local --oss provider).
        # codex validates the name at runtime (e.g. a ChatGPT login rejects a
        # non-OpenAI model), so the adapter only passes it through.
        flags += ["--model", model]
    # Resolve the friendly alias (e.g. "ollama-cloud") to its URL before building
    # the BYO inject-env; a literal URL (or None) passes through unchanged.
    resolved_endpoint = endpoint
    byo_overrides: list[str] = []
    if endpoint is not None:
        resolved_endpoint = _ENDPOINT_ALIASES.get(endpoint, endpoint)
        # #27: codex 0.141 prefers the file-based ChatGPT login over an env-injected
        # OPENAI_BASE_URL, so a BYO run silently hit the ChatGPT account. Define an
        # explicit OpenAI-compatible model_provider for the endpoint and SELECT it,
        # so codex routes to the caller's endpoint regardless of any ambient login.
        # The key still flows via the injected OPENAI_API_KEY (env_key references it).
        # wire_api="responses": codex 0.141 removed the "chat" wire protocol for
        # custom providers (config load errors with 'wire_api = "chat" is no longer
        # supported'), so the BYO endpoint must speak the OpenAI Responses API.
        provider_table = {
            "name": _BYO_PROVIDER_ID,
            "base_url": resolved_endpoint,
            "env_key": "OPENAI_API_KEY",
            "wire_api": "responses",
        }
        byo_overrides = [
            "-c",
            f"model_providers.{_BYO_PROVIDER_ID}={_toml_inline(provider_table)}",
            "-c",
            f"model_provider={_toml_str(_BYO_PROVIDER_ID)}",
        ]
    inject_env = byo_inject_env(
        resolved_endpoint,
        api_key,
        url_key="OPENAI_BASE_URL",
        token_key="OPENAI_API_KEY",
    )
    # The prompt rides stdin, not argv (issue #17 follow-up): a Windows ``.cmd``
    # shim is launched via ``cmd.exe /c`` and would re-parse an argv-borne prompt
    # (metachar / ``%VAR%`` injection). ``codex exec`` reads instructions from
    # stdin when no positional ``PROMPT`` is given, which also sidesteps the old
    # ``-``-prefix and ``resume``/``review`` subcommand footguns — no ``--``
    # separator is needed.
    argv = ["codex", "exec", *flags, *byo_overrides, *_mcp_overrides(mcp_config)]
    return _run(
        argv,
        cwd=cwd,
        timeout=timeout,
        deny_prefixes=_AUTH_OVERRIDE_PREFIXES,
        inject_env=inject_env,
        stdin_text=prompt,
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
