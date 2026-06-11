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
import shutil
import subprocess
import sys
import warnings
from collections.abc import Mapping, Sequence
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


def byo_inject_env(
    endpoint: str | None,
    api_key: str | None,
    *,
    url_key: str,
    token_key: str,
) -> dict[str, str] | None:
    """Build the explicit "bring-your-own endpoint" env to inject AFTER the scrub.

    Issue #2: routing a brain CLI at a *caller-chosen* endpoint (Ollama Cloud /
    any compatible gateway) is the deliberate inverse of the subscription-auth
    scrub. The endpoint + token are mapped to the provider's env vars
    (``url_key`` / ``token_key``) and returned for :func:`_run` to inject *after*
    the deny-set sweep, so only these explicit, caller-supplied values reach the
    child — an ambient (inherited) override under the same prefix is still
    stripped. The caller owns the trust decision: a BYO endpoint ships the
    prompt + code context to a third party.

    Rules:

    - **Both-or-neither.** ``endpoint`` and ``api_key`` must be supplied
      together. A half-configured BYO mode is a ``ValueError``, never a silent
      fall-back to subscription auth (that would mask a routing mistake). An
      empty string counts as *not supplied* — a caller passing
      ``api_key=os.environ.get("KEY")`` for an unset-to-empty var gets a clear
      ``ValueError`` at this boundary, not an empty token injected into the
      child where the CLI fails with an opaque auth error.
    - **Neither → ``None``.** BYO was not requested; the caller passes ``None``
      to ``_run``'s ``inject_env`` and the default scrub stands unchanged.
    - **Non-https → warn.** A plaintext endpoint ships context in the clear; emit
      a :class:`UserWarning` (not an error — local gateways may be plain http).
    """
    if not endpoint and not api_key:
        return None
    if not endpoint or not api_key:
        raise ValueError(
            "BYO-endpoint mode requires both endpoint and api_key "
            "(a half-configured or empty endpoint will not silently fall back "
            "to subscription auth)"
        )
    if not endpoint.startswith("https://"):
        warnings.warn(
            f"BYO endpoint {endpoint!r} is not https:// — prompt and code "
            "context will be sent to it in the clear",
            UserWarning,
            stacklevel=2,
        )
    return {url_key: endpoint, token_key: api_key}


def _launch_argv(argv: Sequence[str]) -> list[str]:
    """Resolve ``argv[0]`` for spawning; wrap Windows ``.cmd``/``.bat`` shims.

    Issue #17: on native Windows, ``CreateProcess`` only auto-appends ``.exe``
    — it does NOT apply ``PATHEXT`` — so an npm shim like ``claude.cmd`` is
    invisible to ``subprocess.run(["claude", ...], shell=False)`` (WinError 2)
    even though ``shutil.which("claude")`` finds it. Spawn the which-resolved
    absolute path so the pre-flight check and the launch can't diverge, and
    route ``.cmd``/``.bat`` shims through the command interpreter explicitly
    (``COMSPEC /c <shim>``) while keeping ``shell=False``. This is safe ONLY
    because ``argv`` carries developer-controlled flags exclusively — the prompt
    rides stdin (see :func:`_run`'s ``stdin_text``), so ``cmd.exe``'s re-parse of
    its command line never sees attacker-influenced text and no injection
    surface is opened. An unresolvable ``argv[0]`` is left as-is so the
    documented ``OSError`` surfaces with the caller's own name.
    """
    exe = shutil.which(argv[0]) or argv[0]
    if sys.platform == "win32" and exe.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", exe, *argv[1:]]
    return [exe, *argv[1:]]


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
    deny_exact: Sequence[str] = (),
    deny_prefixes: Sequence[str] = (),
    inject_env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
) -> BrainResult:
    """Run one headless CLI subprocess with a scrubbed child env.

    Copies the parent environment and removes every key that is in
    ``deny_exact`` OR begins with any string in ``deny_prefixes``, so a stale
    credential / endpoint / config-home override cannot win over the CLI's
    subscription login. ``argv[0]`` is resolved via :func:`_launch_argv`
    (which-resolution + Windows ``.cmd``/``.bat`` comspec wrap, issue #17).
    ``OSError`` is deliberately NOT caught —
    callers verify launchability via doctor first; it also surfaces a
    non-existent ``cwd`` (``FileNotFoundError``), not only a missing binary.

    ``stdin_text`` (issue #17 follow-up) is fed to the child on **stdin** via
    ``subprocess`` ``input=``. The prompt MUST travel this way, never as an
    ``argv`` token: on native Windows a ``.cmd``/``.bat`` shim is launched
    through ``cmd.exe /c`` (see :func:`_launch_argv`), and ``cmd.exe`` re-parses
    its command line — metacharacters (``& | < > ^``) and ``%VAR%`` expansion in
    an argv-borne prompt would corrupt it or inject a command (the BatBadBut /
    CVE-2024-24576 class). Keeping the prompt on stdin means only
    developer-controlled flags ever reach ``cmd.exe``, so no injection surface
    is opened. ``argv`` therefore carries only flags; the adapter no longer
    appends a ``--`` separator + positional prompt.

    ``inject_env`` (issue #2) sets caller-supplied vars *after* the deny-set
    sweep, so a deliberate BYO endpoint/token wins while ambient overrides under
    the same prefix stay stripped. The order is load-bearing: injecting before
    the scrub would let the deny loop strip the caller's own values (they live
    under the denied prefix). Only the keys in ``inject_env`` are re-added —
    every other denied key stays removed.
    """
    child_env = os.environ.copy()
    deny_exact_set = set(deny_exact)
    prefixes = tuple(deny_prefixes)
    for key in list(child_env):
        if key in deny_exact_set or (prefixes and key.startswith(prefixes)):
            child_env.pop(key, None)
    # Inject AFTER the scrub — see the docstring's load-bearing-order note.
    if inject_env:
        child_env.update(inject_env)
    try:
        proc = subprocess.run(
            _launch_argv(argv),
            cwd=cwd,
            capture_output=True,
            # The prompt rides stdin, never argv — see the stdin_text docstring
            # note (keeps it out of the Windows cmd.exe shim re-parse).
            input=stdin_text,
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
