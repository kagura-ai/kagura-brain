"""kagura-brain — the CLI-coding-agent "brain" layer shared by Kagura harnesses.

This is the **brain axis** of the harness-support split, the counterpart to the
memory axis (``kagura-memory``): the seams that drive a headless CLI coding agent
as a brain, kept out of the memory SDK so the layering stays clean (memory never
spawns the agent).

Today it drives Claude Code (``claude -p``). The launcher shape is
provider-agnostic by design — a Codex CLI adapter (``codex exec``) is planned as
a sibling so the same ``verdict`` / ``extract_block`` / subprocess+env+timeout
core serves both.

Surface (built incrementally, TDD, one consumer per PR):

- ``proc``    — subprocess helpers (``as_text``, generic ``mcp_args``).
- ``brain``   — ``invoke()`` headless ``claude -p`` launcher: strips stale
                provider credentials so subscription auth wins, stdout fallback,
                timeout normalization, exit-code + sentinel extraction.
- ``verdict`` — canonical ``PROCEED`` set + exit-code map (contract only).
- ``doctor``  — reusable environment-check primitives (git/claude/gh/ollama) [planned].

Deliberately depends on **no** memory package — the memory axis folds up into
kagura-memory-python-sdk instead.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
