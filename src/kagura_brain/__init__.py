"""kagura-brain — the CLI-coding-agent "brain" layer shared by Kagura harnesses.

This is the **brain axis** of the harness-support split, the counterpart to the
memory axis (``kagura-memory``): the seams that drive a headless CLI coding agent
as a brain, kept out of the memory SDK so the layering stays clean (memory never
spawns the agent).

Two adapters drive their CLIs (``claude -p`` and ``codex exec``) as thin
siblings over one shared launcher core, so the same ``verdict`` /
``extract_block`` / subprocess+env+timeout seam serves both.

Surface (built incrementally, TDD, one consumer per PR):

- ``core``    — provider-agnostic seam: ``BrainResult``, ``_run`` (subprocess +
                per-adapter env-scrub + timeout + utf-8 decode), ``as_text``,
                ``extract_block``.
- ``claude``  — ``invoke()`` headless ``claude -p`` launcher (``ANTHROPIC_*`` /
                ``CLAUDE_*`` deny-set, ``--``-guarded prompt, ``mcp_args``).
- ``codex``   — ``invoke()`` headless ``codex exec`` launcher (``OPENAI_*`` /
                ``CODEX_*`` prefix scrub, ``--``-guarded prompt, opt-in sandbox).
- ``verdict`` — canonical ``PROCEED`` set + exit-code map (contract only).
- ``doctor``  — provider-neutral environment-check primitives (``check_binary`` /
                ``check_auth`` / ``check_endpoint`` / ``aggregate``); the adapters
                add presence-only ``claude.check()`` / ``codex.check()`` wrappers.

Both adapters strip their provider's credential/endpoint overrides from the
child env so the CLI's **subscription** login wins.

Deliberately depends on **no** memory package — the memory axis folds up into
kagura-memory-python-sdk instead.
"""

from __future__ import annotations

from . import claude, codex, core, doctor, verdict

__version__ = "0.2.0"

__all__ = ["__version__", "claude", "codex", "core", "doctor", "verdict"]
