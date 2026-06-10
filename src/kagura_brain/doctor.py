"""Provider-neutral environment-check primitives for Kagura brain consumers.

A consuming harness (kagura-engineer / kagura-planner / kagura-code-reviewer)
verifies its toolchain *before* driving a brain adapter — :func:`kagura_brain.core._run`
deliberately does NOT catch ``OSError`` ("callers verify launchability via doctor
first"). ``doctor`` is that pre-flight: each primitive turns a missing binary, a
timeout, or an unreachable endpoint into a structured *fail* :class:`CheckResult`
instead of raising. It is the inverse of ``_run`` — ``_run`` lets launch failures
propagate; ``doctor`` reports them.

Design boundaries:

- **Provider-neutral.** The primitives know nothing about ``claude`` / ``codex``;
  the provider-named convenience wrappers live in the adapter modules
  (``claude.check()`` / ``codex.check()``), exactly mirroring how ``invoke()``
  wraps ``_run``. Which checks matter — and which are *required* — is the
  consumer's call (see :func:`aggregate`'s ``required`` argument), so ``doctor``
  hard-codes no toolchain policy.
- **Report-only.** Nothing here mutates, installs, or remediates.
- **stdlib-only**, memory-free, and short-fused: a health check is *seconds*
  (:data:`_DEFAULT_CHECK_TIMEOUT_S`), never the 30-minute coding-run default in
  ``core``.
- **Opt-in network.** :func:`check_endpoint` is the only primitive that touches
  the network, and only when the caller explicitly invokes it — nothing here
  auto-probes. It attaches no credentials: a bare reachability probe must never
  carry the BYO token.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

# A health check is seconds — NOT core._DEFAULT_TIMEOUT_S (1800s, a coding run).
# Any primitive that shells out or hits the network caps itself here so doctor
# never hangs a consumer's pre-flight.
_DEFAULT_CHECK_TIMEOUT_S = 5.0

_OK = "ok"
_DEGRADED = "degraded"
_FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    """One environment check: a name, a pass/fail, and a one-line human detail."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    """Aggregate verdict over a set of checks. ``status`` is one of ``"ok"`` /
    ``"degraded"`` / ``"fail"`` (see :func:`aggregate` for the rule)."""

    status: str
    results: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return self.status == _OK


def check_binary(name: str) -> CheckResult:
    """Deterministic presence check: is ``name`` on ``PATH``? (``shutil.which``).

    Presence only — never runs the binary, so it cannot prompt, bill, or block.
    """
    path = shutil.which(name)
    if path:
        return CheckResult(name, True, path)
    return CheckResult(name, False, f"{name!r} not found on PATH")


def check_auth(
    argv: Sequence[str],
    *,
    name: str | None = None,
    timeout: float = _DEFAULT_CHECK_TIMEOUT_S,
) -> CheckResult:
    """Best-effort auth check: run ``argv`` and pass iff it exits 0.

    For tools with a clean, non-interactive auth exit code (e.g.
    ``["gh", "auth", "status"]``). The caller owns the argv — it MUST be a
    command that never prompts. Unlike ``core._run``, a missing binary
    (``OSError``) or a stall (:class:`subprocess.TimeoutExpired`) is caught and
    returned as a *fail* result, not raised. ``name`` defaults to ``argv[0]``.

    Note: ``claude`` / ``codex`` have no clean "am I logged in" exit code, so
    their adapters expose presence-only ``check()`` wrappers and do NOT route
    here — see ``claude.check`` / ``codex.check``.
    """
    label = name or (argv[0] if argv else "auth")
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(label, False, f"timed out after {timeout}s")
    except OSError as exc:
        return CheckResult(label, False, f"could not run {label!r}: {exc}")
    if proc.returncode == 0:
        detail = _first_line(proc.stdout) or _first_line(proc.stderr) or "authenticated"
        return CheckResult(label, True, detail)
    detail = (
        _first_line(proc.stderr)
        or _first_line(proc.stdout)
        or f"exit {proc.returncode}"
    )
    return CheckResult(label, False, detail)


def check_endpoint(
    url: str, *, timeout: float = _DEFAULT_CHECK_TIMEOUT_S
) -> CheckResult:
    """Opt-in reachability probe for a caller-supplied endpoint (BYO mode, #2).

    Reachability, not authorization: a server that answers at all — including a
    ``401``/``404`` — is *reachable* and passes; only a transport failure (DNS,
    connection refused, timeout) is a fail. No credentials are attached: the
    probe must never carry the BYO token. Called only when the consumer chooses
    to — never auto-probed.

    Restricted to ``http``/``https``: ``urllib`` would otherwise open ``file://``
    (local file read) or ``ftp://`` URLs, so a misconfigured endpoint can't turn
    a reachability probe into a local-file / SSRF foot-gun.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return CheckResult(
            url, False, f"unsupported scheme {scheme!r} (expected http/https)"
        )
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return CheckResult(url, True, f"reachable (HTTP {resp.status})")
    except urllib.error.HTTPError as exc:
        # The server responded (even 4xx/5xx) → the endpoint is reachable.
        return CheckResult(url, True, f"reachable (HTTP {exc.code})")
    except (urllib.error.URLError, OSError) as exc:
        return CheckResult(url, False, f"unreachable: {exc}")


def aggregate(
    results: Iterable[CheckResult], *, required: Iterable[str] = ()
) -> DoctorReport:
    """Roll up checks into a tri-state verdict the consumer can branch on.

    The ``required`` set is the consumer's policy — ``doctor`` hard-codes none:

    - a **required** check failing  → ``"fail"``   (toolchain is unusable)
    - only **optional** checks failing → ``"degraded"`` (usable, some path off)
    - everything passing (or no checks) → ``"ok"``

    A required failure outranks any optional failure. With an empty ``required``
    set, even a failing check is at most ``"degraded"`` — nothing is fatal until
    the consumer says so.
    """
    items = tuple(results)
    required_set = set(required)
    if any(r.name in required_set and not r.ok for r in items):
        status = _FAIL
    elif any(not r.ok for r in items):
        status = _DEGRADED
    else:
        status = _OK
    return DoctorReport(status, items)


def _first_line(text: str) -> str:
    """First non-empty line of ``text``, stripped — or ``""``."""
    stripped = text.strip()
    return stripped.splitlines()[0].strip() if stripped else ""
