"""Drive Claude Code as a brain via one headless ``claude -p`` subprocess.

This is the canonical launcher seam shared by the Kagura harnesses. It runs the
child on Claude Code **subscription** auth and centralizes the env-hygiene fix
that each harness was carrying separately (kagura-engineer #34, kagura-planner
PR#5): a stale/invalid ``ANTHROPIC_API_KEY`` inherited from the environment —
notably one injected by a surrounding Claude Code session — would otherwise
OVERRIDE the subscription and make ``claude -p`` die with "Invalid API key".

Domain concerns (prompt building, verdict/marker vocabulary) stay in the
consuming harness; this module only launches and returns raw output, plus a
generic ``extract_block`` for sentinel-delimited payloads.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .proc import as_text, mcp_args

_DEFAULT_TIMEOUT_S = 1800  # 30 min

# Credential env vars that would override Claude Code subscription auth if a
# stale value is inherited (notably from a surrounding Claude Code session).
# Stripped from the child env so the subscription login always wins (#34).
_AUTH_OVERRIDE_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@dataclass(frozen=True)
class BrainResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def detail(self) -> str:
        """Diagnostic tail for logs/errors. ``claude -p`` prints auth errors to
        *stdout*, so fall back to stdout when stderr is empty (the #34
        stdout-fallback seam). When a timeout left no output at all, surface a
        ``"timed out"`` label rather than an empty string."""
        fallback = "timed out" if self.timed_out else ""
        return (self.stderr or self.stdout or fallback).strip()


def invoke(
    prompt: str,
    *,
    cwd: Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
    mcp_config: str | None = None,
    allowed_tools: Sequence[str] = (),
) -> BrainResult:
    """Run one headless ``claude -p`` on Claude Code subscription auth.

    Strips the credential env vars in ``_AUTH_OVERRIDE_ENV`` from the child env
    so a stale value cannot override the subscription login. ``OSError`` is
    deliberately NOT caught — callers verify launchability via doctor first;
    note it also surfaces a non-existent ``cwd`` (``FileNotFoundError``), not
    only a missing ``claude`` binary.
    """
    child_env = os.environ.copy()
    for _key in _AUTH_OVERRIDE_ENV:
        child_env.pop(_key, None)
    try:
        proc = subprocess.run(
            # ``-p``/``--print`` is a boolean flag and the prompt is positional,
            # so a prompt beginning with ``-`` would be parsed as an option. The
            # ``--`` separator (after the MCP flags) forces it to be the prompt.
            ["claude", "-p", *mcp_args(mcp_config, allowed_tools), "--", prompt],
            cwd=cwd,
            capture_output=True,
            # Decode with utf-8/errors=replace (matching the timeout path) so a
            # non-UTF-8 locale or stray byte never raises inside invoke().
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        # Preserve any partial output captured before the kill (raw bytes even
        # under text=True) — invaluable for diagnosing a stalled phase. Keep the
        # real (possibly empty) stderr; the "timed out" label is supplied by
        # detail() only when there is no captured output to show instead.
        return BrainResult(-1, as_text(exc.stdout), as_text(exc.stderr), timed_out=True)
    return BrainResult(proc.returncode, proc.stdout, proc.stderr)


def extract_block(text: str, begin: str, end: str) -> str | None:
    """Pull the text between a sentinel marker pair (exclusive), or ``None``.

    Each marker must sit alone on its line. Markers are matched literally
    (regex-escaped). Generic form of a harness's bespoke extractor, e.g.
    ``extract_block(out, "KAGURA_PLAN_BEGIN", "KAGURA_PLAN_END")``.
    """
    pattern = re.compile(
        rf"^{re.escape(begin)}\s*$(.*?)^{re.escape(end)}\s*$",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text or "")
    if not m:
        return None
    # Normalize CRLF so a Windows/CRLF-authored payload doesn't carry interior
    # carriage returns that .strip() (edges only) would leave behind.
    return m.group(1).replace("\r\n", "\n").strip()
