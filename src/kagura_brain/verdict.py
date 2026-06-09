"""Canonical gate-verdict contract shared by Kagura harnesses.

A harness phase emits a verdict token; the gate maps it to proceed/halt and a
process exit code. ``green``/``yellow`` proceed; everything else (``red``,
unknown, missing) halts — defaulting the unknown case to halt is the safe
direction: better to stop and surface to a human than mis-read a verdict and let
an autonomous run barrel ahead.

Contract-only: no parsing of prose or markers (that vocabulary stays in the
consuming harness) — just the shared proceed-set and exit-code map. Callers that
speak a domain dialect (e.g. ``pass``/``fail``) pre-map it to this vocabulary
before calling.

Exit codes: ``0`` = proceed (green/yellow), ``2`` = halt (red/unknown/missing).

.. note::
   kagura-code-reviewer historically maps ``red -> 1``. The canonical halt code
   here is ``2`` (kagura-engineer's gate vocabulary). Reconciling code-reviewer
   onto ``2`` changes that tool's exit contract, so it is a deliberate,
   human-confirmed migration — importing this module does **not** do it
   implicitly.
"""

from __future__ import annotations

PROCEED = frozenset({"green", "yellow"})

PROCEED_EXIT = 0
HALT_EXIT = 2


def normalize(verdict: str | None) -> str:
    """Canonicalize a raw verdict token: strip + lowercase; empty/None → ``"unknown"``.

    Coerces non-str input to ``str`` so an off-contract value (e.g. an int/enum
    slipping through) safe-halts rather than raising mid-gate — the unknown case
    halts anyway, so crashing would defeat the safe direction.
    """
    text = "" if verdict is None else str(verdict)
    return text.strip().lower() or "unknown"


def proceed(verdict: str | None) -> bool:
    """``True`` iff the (normalized) verdict says proceed — green or yellow."""
    return normalize(verdict) in PROCEED


def exit_code(verdict: str | None) -> int:
    """Process exit code for a verdict: ``0`` proceed, ``2`` halt (red/unknown/missing)."""
    return PROCEED_EXIT if proceed(verdict) else HALT_EXIT
