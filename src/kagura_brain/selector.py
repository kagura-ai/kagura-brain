"""Provider-neutral brain selector — confine the claude/codex dispatch to the library.

Every consumer that supports more than one backend used to map a backend name →
adapter (``claude`` / ``codex``) + endpoint/api_key and re-encode the "codex has
no per-call MCP" rule itself (kagura-engineer's ``run/brain_select.py``,
kagura-planner #11). At N=2 consumers that generic seam belongs here — the
library already owns the adapters, the :class:`~kagura_brain.core.BrainResult`
seam, and the cross-consumer ``doctor`` helpers; a selector over its own adapters
is the natural next API.

:func:`select` returns a frozen :class:`BrainHandle` whose ``.invoke`` forwards
``mcp_config`` / ``allowed_tools`` to the chosen adapter. Both backends are now
MCP-capable, but the mechanism differs and the library hides that: the claude
adapter passes ``--mcp-config`` / ``--allowedTools`` per call, while the codex
adapter translates the same ``.mcp.json`` into ``-c mcp_servers.*`` config
overrides (codex has no per-call ``allowed_tools`` analog, so it is accepted for
parity but not forwarded). ``supports_mcp`` advertises that capability per
backend.

**Library stays pure (issue #14 design boundaries).**

- *Config-agnostic.* ``select`` takes primitives, never a consumer's pydantic
  ``Config``. Each consumer maps its own config → these args.
- *No env read / no secret handling.* The consumer reads
  :data:`BRAIN_API_KEY_ENV` (or wherever) and passes ``api_key=`` in; the library
  owns only the standard *name*. Keeps it memory-free / pure / trivially testable.
- *Consumer owns its ``allowed_tools``.* The library knows *capabilities*; the
  consumer supplies *which tools*.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import claude, codex
from .core import BrainResult, _DEFAULT_TIMEOUT_S

__all__ = ["BRAIN_API_KEY_ENV", "BrainHandle", "select"]

# The standard env-var name all consumers agree on for the BYO-endpoint key. The
# library owns only the *name* — it never reads the env itself (a consumer reads
# it and passes ``api_key=`` into :func:`select`), keeping this module pure.
BRAIN_API_KEY_ENV = "KAGURA_BRAIN_API_KEY"

# backend name → supports_mcp capability. The single source of truth for both the
# valid-backend set and each backend's advertised MCP capability. Both adapters
# wire MCP (claude per-call, codex via translated `-c mcp_servers.*` overrides),
# so both are True — kept as a per-backend flag so a future MCP-less backend can
# opt out in one place. Dispatch is by backend *name* (``invoke`` calls
# ``claude``/``codex`` directly to preserve the typed ``BrainResult`` under mypy
# strict), so this maps only the capability, not the adapter module.
_BACKENDS = {
    "claude": True,
    "codex": True,
}


@dataclass(frozen=True)
class BrainHandle:
    """A selected brain backend + its capability, with a uniform ``invoke``.

    Built by :func:`select`. ``invoke`` forwards the same ``mcp_config`` /
    ``allowed_tools`` to whichever adapter ``backend`` names, so each consumer
    stops re-encoding the per-provider MCP mechanism.

    ``BrainHandle`` is exported and directly constructable, so it fails closed in
    :meth:`__post_init__`: an unknown ``backend`` — or a ``supports_mcp`` that
    contradicts that backend's known capability — raises ``ValueError`` at
    construction, so a hand-built or deserialized handle can never silently
    mis-route or advertise the wrong capability.
    """

    backend: str
    supports_mcp: bool
    endpoint: str | None = None
    # ``repr=False`` keeps a BYO key out of the default dataclass ``__repr__`` so a
    # handle that lands in a log line or exception traceback cannot leak it (CSO
    # gate2 finding, #14). The value is still stored and forwarded to the adapter.
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.backend not in _BACKENDS:
            raise ValueError(
                f"unknown backend {self.backend!r}; expected one of {sorted(_BACKENDS)}"
            )
        expected_mcp = _BACKENDS[self.backend]
        if self.supports_mcp != expected_mcp:
            raise ValueError(
                f"supports_mcp={self.supports_mcp!r} contradicts backend "
                f"{self.backend!r} (expected {expected_mcp!r})"
            )

    def invoke(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        timeout: int = _DEFAULT_TIMEOUT_S,
        mcp_config: str | None = None,
        allowed_tools: Sequence[str] = (),
        permission_mode: str | None = None,
        dangerously_skip_permissions: bool = False,
        local_provider: str | None = None,
        model: str | None = None,
    ) -> BrainResult:
        """Run one brain turn on this backend, returning its :class:`BrainResult`.

        ``mcp_config`` + ``allowed_tools`` (and the stored endpoint/api_key for a
        BYO gateway) are forwarded to the chosen adapter. The adapter owns the
        per-provider mechanism: claude passes ``--mcp-config`` / ``--allowedTools``
        per call; codex translates ``mcp_config`` into ``-c mcp_servers.*``
        overrides and ignores ``allowed_tools`` (no codex analog). The adapter is
        chosen by ``backend`` and each ``invoke`` is called directly (not via the
        registry) so its typed ``BrainResult`` return survives mypy strict.

        ``dangerously_skip_permissions`` (issue #21) is the provider-neutral
        full-bypass switch an autonomous consumer flips to run unattended: a
        headless brain auto-denies every approval-gated tool (``Bash``/``gh``/
        ``Edit``/…) because no human can answer the prompt. The selector maps this
        single flag onto each backend's own mechanism — claude's
        ``--dangerously-skip-permissions`` and codex's
        ``--dangerously-bypass-approvals-and-sandbox`` — so the consumer never
        re-encodes the per-provider permission vocabulary. The **default**
        (``False``) forwards the safe, no-bypass value to both backends. The
        run's red/yellow/green gates are then the safety layer instead of
        per-action prompts.

        **Blast radius differs by backend** (issue #23) — the one neutral flag is
        deliberately not symmetric: claude's ``--dangerously-skip-permissions``
        skips only the per-action *approval prompts*, whereas codex's
        ``--dangerously-bypass-approvals-and-sandbox`` *also disables the
        sandbox*. Both satisfy the "no human at the gate" need an autonomous
        consumer has, but a codex run additionally loses sandbox isolation — so
        the same ``True`` relaxes strictly more on codex than on claude. Weigh
        that before flipping it for a codex backend; there is no neutral way to
        skip codex approvals while keeping its sandbox (use ``codex.invoke`` with
        ``sandbox=`` directly if you need that finer control).

        ``permission_mode`` (issue #21) is the milder, claude-only knob
        (``acceptEdits``/``plan``/… — see :data:`claude._PERMISSION_MODES`), the
        safe middle ground between "no bypass" and the full
        ``dangerously_skip_permissions``. codex has **no** ``--permission-mode``
        analog, so passing it with a codex backend raises ``ValueError`` rather
        than silently dropping a confinement intent — unlike ``allowed_tools``
        (which codex harmlessly ignores), a dropped permission mode would mislead
        the caller about how confined the run is. Use it only with claude.

        ``model`` (issue #28) pins the model on either backend (``--model``):
        codex (subscription / BYO endpoint / local ``--oss``) and claude both map
        it onto their CLI's model flag; the backend validates the name at runtime.

        ``local_provider`` (issue #28) selects codex's local ``--oss`` backend
        (``"ollama"`` / ``"lmstudio"``) — the motivating case for a fully local,
        no-cloud brain. It is **codex-only**: claude has no local backend, so
        passing it to a claude handle raises ``ValueError`` (symmetric with
        ``permission_mode`` on codex) rather than silently dropping the intent.
        """
        if self.backend == "codex":
            if permission_mode is not None:
                raise ValueError(
                    "permission_mode has no codex analog; it is claude-only. "
                    "Use dangerously_skip_permissions (mapped to codex's "
                    "--dangerously-bypass-approvals-and-sandbox) or call "
                    "claude.invoke directly"
                )
            return codex.invoke(
                prompt,
                cwd=cwd,
                timeout=timeout,
                mcp_config=mcp_config,
                allowed_tools=allowed_tools,
                bypass_approvals=dangerously_skip_permissions,
                endpoint=self.endpoint,
                api_key=self.api_key,
                local_provider=local_provider,
                model=model,
            )
        if local_provider is not None:
            # local_provider is codex's --oss-only concept (a local ollama/lmstudio
            # process); claude has no local backend. Reject rather than silently
            # drop the intent — symmetric with permission_mode on codex above.
            raise ValueError(
                "local_provider has no claude analog; it is codex --oss-only. "
                "Use select('codex', ...).invoke(..., local_provider=...) for a "
                "local backend, or call codex.invoke directly"
            )
        return claude.invoke(
            prompt,
            cwd=cwd,
            timeout=timeout,
            mcp_config=mcp_config,
            allowed_tools=allowed_tools,
            permission_mode=permission_mode,
            dangerously_skip_permissions=dangerously_skip_permissions,
            endpoint=self.endpoint,
            api_key=self.api_key,
            model=model,
        )


def select(
    backend: str = "claude",
    *,
    endpoint: str | None = None,
    api_key: str | None = None,
) -> BrainHandle:
    """Select a brain backend and return its :class:`BrainHandle`.

    ``"claude"`` (the default) and ``"codex"`` both map to ``supports_mcp=True``
    (each adapter wires MCP its own way). An unknown backend
    raises ``ValueError``. ``endpoint`` / ``api_key`` are stored on the handle
    and forwarded to the adapter's ``invoke`` for a BYO gateway — the library
    never reads them from the env (see :data:`BRAIN_API_KEY_ENV`).
    """
    try:
        supports_mcp = _BACKENDS[backend]
    except KeyError:
        raise ValueError(
            f"unknown backend {backend!r}; expected one of {sorted(_BACKENDS)}"
        ) from None
    return BrainHandle(
        backend=backend,
        supports_mcp=supports_mcp,
        endpoint=endpoint,
        api_key=api_key,
    )
