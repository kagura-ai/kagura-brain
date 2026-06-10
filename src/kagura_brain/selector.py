"""Provider-neutral brain selector — confine the claude/codex dispatch to the library.

Every consumer that supports more than one backend used to map a backend name →
adapter (``claude`` / ``codex``) + endpoint/api_key and re-encode the "codex has
no per-call MCP" rule itself (kagura-engineer's ``run/brain_select.py``,
kagura-planner #11). At N=2 consumers that generic seam belongs here — the
library already owns the adapters, the :class:`~kagura_brain.core.BrainResult`
seam, and the cross-consumer ``doctor`` helpers; a selector over its own adapters
is the natural next API.

:func:`select` returns a frozen :class:`BrainHandle` whose ``.invoke`` dispatches
on its **capability** (``supports_mcp``), not on the backend name: a claude
handle forwards ``mcp_config`` / ``allowed_tools`` to the adapter; a codex handle
drops them (logging once) because codex wires MCP out-of-band
(``codex mcp`` / ``~/.codex/config.toml``), not per call.

**Library stays pure (issue #14 design boundaries).**

- *Config-agnostic.* ``select`` takes primitives, never a consumer's pydantic
  ``Config``. Each consumer maps its own config → these args.
- *No env read / no secret handling.* The consumer reads
  :data:`BRAIN_API_KEY_ENV` (or wherever) and passes ``api_key=`` in; the library
  owns only the standard *name*. Keeps it memory-free / pure / trivially testable.
- *Consumer owns its ``allowed_tools``.* The library knows *capabilities* (codex
  can't MCP); the consumer supplies *which tools*.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import claude, codex
from .core import BrainResult, _DEFAULT_TIMEOUT_S

__all__ = ["BRAIN_API_KEY_ENV", "BrainHandle", "select"]

# The standard env-var name all consumers agree on for the BYO-endpoint key. The
# library owns only the *name* — it never reads the env itself (a consumer reads
# it and passes ``api_key=`` into :func:`select`), keeping this module pure.
BRAIN_API_KEY_ENV = "KAGURA_BRAIN_API_KEY"

_logger = logging.getLogger(__name__)

# backend name → (adapter module, supports_mcp). The single source of truth for
# both the valid-backend set and each backend's MCP capability.
_BACKENDS = {
    "claude": (claude, True),
    "codex": (codex, False),
}


@lru_cache(maxsize=1)
def _warn_codex_mcp_unsupported() -> None:
    """Log the codex MCP-drop exactly once per process.

    A codex handle drops ``mcp_config`` / ``allowed_tools`` on every ``invoke``;
    without a guard a per-call loop would spam an identical warning. ``lru_cache``
    fires the body once and returns the cached ``None`` thereafter; tests reset it
    via ``.cache_clear()``.
    """
    _logger.warning(
        "codex backend has no per-call MCP wiring; dropping mcp_config/"
        "allowed_tools (codex manages MCP out-of-band via `codex mcp` / "
        "~/.codex/config.toml)"
    )


@dataclass(frozen=True)
class BrainHandle:
    """A selected brain backend + its capability, with a uniform ``invoke``.

    Built by :func:`select`. ``invoke`` dispatches on ``supports_mcp`` so the
    "codex has no per-call MCP" rule lives here once, not in every consumer.

    ``BrainHandle`` is exported and directly constructable, so it fails closed in
    :meth:`__post_init__`: an unknown ``backend`` — or a ``supports_mcp`` that
    contradicts that backend's known capability — raises ``ValueError`` at
    construction, so a hand-built or deserialized handle can never silently
    mis-route or suppress/mis-fire the codex MCP-drop warning.
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

        claude → forwards ``mcp_config`` + ``allowed_tools`` (+ stored
        endpoint/api_key for a BYO gateway). codex → **drops**
        ``mcp_config`` / ``allowed_tools`` (logging once via
        :func:`_warn_codex_mcp_unsupported`) and forwards endpoint/api_key only.

        The MCP-drop is gated on the ``supports_mcp`` *capability*; the adapter is
        chosen by ``backend`` (the two axes are kept distinct so a future
        MCP-capable backend would not be mis-routed to the claude adapter). The
        per-adapter ``invoke`` is called directly rather than via the registry so
        its typed ``BrainResult`` return is preserved under mypy strict.
        """
        if not self.supports_mcp and (mcp_config or allowed_tools):
            _warn_codex_mcp_unsupported()
        if self.backend == "codex":
            return codex.invoke(
                prompt,
                cwd=cwd,
                timeout=timeout,
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

    ``"claude"`` (the default) → claude adapter, ``supports_mcp=True``;
    ``"codex"`` → codex adapter, ``supports_mcp=False``. An unknown backend
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
