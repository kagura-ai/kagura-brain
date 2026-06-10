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

# backend name → (adapter module, supports_mcp). The single source of truth for
# both the valid-backend set and each backend's MCP capability. Both adapters
# wire MCP (claude per-call, codex via translated `-c mcp_servers.*` overrides),
# so both advertise True — kept as a per-backend field so a future MCP-less
# backend can opt out in one place.
_BACKENDS = {
    "claude": (claude, True),
    "codex": (codex, True),
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
        expected_mcp = _BACKENDS[self.backend][1]
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
    ) -> BrainResult:
        """Run one brain turn on this backend, returning its :class:`BrainResult`.

        ``mcp_config`` + ``allowed_tools`` (and the stored endpoint/api_key for a
        BYO gateway) are forwarded to the chosen adapter. The adapter owns the
        per-provider mechanism: claude passes ``--mcp-config`` / ``--allowedTools``
        per call; codex translates ``mcp_config`` into ``-c mcp_servers.*``
        overrides and ignores ``allowed_tools`` (no codex analog). The adapter is
        chosen by ``backend`` and each ``invoke`` is called directly (not via the
        registry) so its typed ``BrainResult`` return survives mypy strict.
        """
        if self.backend == "codex":
            return codex.invoke(
                prompt,
                cwd=cwd,
                timeout=timeout,
                mcp_config=mcp_config,
                allowed_tools=allowed_tools,
                endpoint=self.endpoint,
                api_key=self.api_key,
            )
        return claude.invoke(
            prompt,
            cwd=cwd,
            timeout=timeout,
            mcp_config=mcp_config,
            allowed_tools=allowed_tools,
            endpoint=self.endpoint,
            api_key=self.api_key,
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
        _adapter, supports_mcp = _BACKENDS[backend]
    except KeyError:
        raise ValueError(
            f"unknown backend {backend!r}; expected one of {sorted(_BACKENDS)}"
        ) from None
    del _adapter  # capability is all select needs here; invoke picks the adapter
    return BrainHandle(
        backend=backend,
        supports_mcp=supports_mcp,
        endpoint=endpoint,
        api_key=api_key,
    )
