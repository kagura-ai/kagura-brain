"""Provider-agnostic launcher seam shared by the Kagura brain adapters.

A "brain" adapter drives a headless CLI coding agent as one subprocess. The
launcher shape is provider-neutral — spawn the child, strip stale provider
credentials from its env so the CLI's *subscription* login wins, normalize
timeout/output, and return a structured result. ``core`` holds exactly that
shared shape; the per-provider argv and credential deny-set live in the adapter
modules (``claude``, ``codex``).

The env-scrub is the load-bearing hygiene fix (kagura-engineer #34): a
stale/invalid credential inherited from the environment — notably one injected
by a surrounding Claude Code session — would otherwise OVERRIDE the subscription
and make the child die with "Invalid API key". ``_run`` parameterizes the
deny-set per adapter: ``deny_exact`` for a known key set (Claude), ``deny_prefixes``
for a prefix sweep (Codex, where any ``OPENAI_*``/``CODEX_*`` override must go).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_TIMEOUT_S = 1800  # 30 min


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
        """Diagnostic tail for logs/errors. A CLI may print auth errors to
        *stdout* (e.g. ``claude -p``), so fall back to stdout when stderr is
        empty. When a timeout left no output at all, surface a ``"timed out"``
        label rather than an empty string."""
        # Strip each candidate BEFORE the ``or`` chain: a whitespace-only stderr
        # (e.g. a lone "\n") is truthy, so testing it pre-strip would short-
        # circuit and yield "" — masking the "timed out" fallback on a timeout
        # that emitted only whitespace.
        fallback = "timed out" if self.timed_out else ""
        return self.stderr.strip() or self.stdout.strip() or fallback


def as_text(value: bytes | str | None) -> str:
    """Normalize subprocess stdout/stderr to ``str``.

    ``subprocess.TimeoutExpired`` carries the *raw bytes* captured before the
    kill even when the process was launched with ``encoding=...`` — so a timeout
    with partial output yields ``bytes``, not ``str``. Decode bytes (replacing
    undecodable sequences); map ``None``/empty to ``""``.
    """
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
    deny_exact: Sequence[str] = (),
    deny_prefixes: Sequence[str] = (),
) -> BrainResult:
    """Run one headless CLI subprocess with a scrubbed child env.

    Copies the parent environment and removes every key that is in
    ``deny_exact`` OR begins with any string in ``deny_prefixes``, so a stale
    credential / endpoint / config-home override cannot win over the CLI's
    subscription login. ``argv`` is passed verbatim (the adapter is responsible
    for the ``--`` prompt separator). ``OSError`` is deliberately NOT caught —
    callers verify launchability via doctor first; it also surfaces a
    non-existent ``cwd`` (``FileNotFoundError``), not only a missing binary.
    """
    child_env = os.environ.copy()
    deny_exact_set = set(deny_exact)
    prefixes = tuple(deny_prefixes)
    for key in list(child_env):
        if key in deny_exact_set or (prefixes and key.startswith(prefixes)):
            child_env.pop(key, None)
    try:
        proc = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            # Decode with utf-8/errors=replace (matching the timeout path) so a
            # non-UTF-8 locale or stray byte never raises inside _run.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        # Preserve any partial output captured before the kill (raw bytes even
        # under encoding=...) — invaluable for diagnosing a stalled phase. Keep
        # the real (possibly empty) stderr; the "timed out" label is supplied by
        # detail() only when there is no captured output to show instead.
        return BrainResult(-1, as_text(exc.stdout), as_text(exc.stderr), timed_out=True)
    return BrainResult(proc.returncode, proc.stdout, proc.stderr)


def extract_block(text: str, begin: str, end: str) -> str | None:
    """Pull the text between a sentinel marker pair (exclusive), or ``None``.

    Each marker must sit alone on its line. Markers are matched literally
    (regex-escaped). Provider-agnostic, reused unchanged across adapters, e.g.
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
