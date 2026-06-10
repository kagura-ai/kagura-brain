"""Tests for kagura_brain.doctor — provider-neutral environment-check primitives.

``doctor`` is the inverse of ``core._run``: where ``_run`` deliberately does NOT
catch ``OSError`` ("callers verify launchability via doctor first"), every doctor
primitive MUST turn a missing binary / timeout into a structured *fail* result
rather than raising. All checks are mocked here — no real CLI or network in CI.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.error
import urllib.request

from kagura_brain.doctor import (
    CheckResult,
    aggregate,
    check_auth,
    check_binary,
    check_endpoint,
)


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeResp:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class TestCheckBinary:
    def test_present_is_ok_with_path(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/git")
        res = check_binary("git")
        assert res.name == "git"
        assert res.ok is True
        assert "/usr/bin/git" in res.detail

    def test_absent_is_fail(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: None)
        res = check_binary("nope")
        assert res.ok is False
        assert "not found" in res.detail.lower()


class TestCheckAuth:
    def test_zero_exit_is_ok(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: _Proc(0, "Logged in to github.com", "")
        )
        res = check_auth(["gh", "auth", "status"])
        assert res.ok is True
        assert res.name == "gh"

    def test_nonzero_exit_is_fail(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: _Proc(1, "", "not logged in")
        )
        res = check_auth(["gh", "auth", "status"])
        assert res.ok is False
        assert "not logged in" in res.detail

    def test_missing_binary_is_caught_as_fail(self, monkeypatch) -> None:
        # core._run lets OSError propagate; doctor must CATCH it into a fail.
        def _boom(*a, **k):
            raise FileNotFoundError("no such file: gh")

        monkeypatch.setattr(subprocess, "run", _boom)
        res = check_auth(["gh", "auth", "status"])
        assert res.ok is False

    def test_timeout_is_caught_as_fail(self, monkeypatch) -> None:
        def _slow(*a, **k):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=5)

        monkeypatch.setattr(subprocess, "run", _slow)
        res = check_auth(["gh", "auth", "status"])
        assert res.ok is False
        assert "timed out" in res.detail.lower()

    def test_uses_a_short_timeout_not_the_coding_run_default(self, monkeypatch) -> None:
        captured: dict = {}

        def _run(*a, **k):
            captured["timeout"] = k.get("timeout")
            return _Proc(0, "ok", "")

        monkeypatch.setattr(subprocess, "run", _run)
        check_auth(["gh", "auth", "status"])
        # A health check is seconds, never core's 1800s coding-run default.
        assert captured["timeout"] is not None
        assert captured["timeout"] < 60

    def test_name_override(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "ok", ""))
        res = check_auth(["gh", "auth", "status"], name="github")
        assert res.name == "github"


class TestCheckEndpoint:
    def test_2xx_is_reachable(self, monkeypatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(200))
        res = check_endpoint("https://gw.example/v1")
        assert res.ok is True

    def test_http_error_still_counts_as_reachable(self, monkeypatch) -> None:
        # A 401/404 means the server RESPONDED → the endpoint is reachable.
        def _raise(*a, **k):
            raise urllib.error.HTTPError(
                "https://gw.example/v1",
                401,
                "Unauthorized",
                {},
                None,  # type: ignore[arg-type]
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        res = check_endpoint("https://gw.example/v1")
        assert res.ok is True

    def test_urlerror_is_unreachable(self, monkeypatch) -> None:
        def _raise(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        res = check_endpoint("https://down.example/v1")
        assert res.ok is False
        assert "unreachable" in res.detail.lower()

    def test_no_credentials_are_attached(self, monkeypatch) -> None:
        # A bare reachability probe must never carry the BYO token (or any auth).
        captured: dict = {}

        def _capture(req, *a, **k):
            captured["req"] = req
            return _FakeResp(200)

        monkeypatch.setattr(urllib.request, "urlopen", _capture)
        check_endpoint("https://gw.example/v1")
        req = captured["req"]
        assert not req.has_header("Authorization")

    def test_non_http_scheme_is_rejected_without_opening(self, monkeypatch) -> None:
        # file:// / ftp:// must never reach urlopen — a reachability probe must
        # not become a local-file read / SSRF vector.
        called = {"hit": False}

        def _should_not_run(*a, **k):
            called["hit"] = True
            return _FakeResp(200)

        monkeypatch.setattr(urllib.request, "urlopen", _should_not_run)
        res = check_endpoint("file:///etc/passwd")
        assert res.ok is False
        assert "scheme" in res.detail.lower()
        assert called["hit"] is False


class TestAggregate:
    def _r(self, name: str, ok: bool) -> CheckResult:
        return CheckResult(name, ok, "")

    def test_all_ok(self) -> None:
        rep = aggregate([self._r("git", True), self._r("gh", True)], required=["git"])
        assert rep.status == "ok"
        assert rep.ok is True

    def test_required_failure_is_fail(self) -> None:
        rep = aggregate([self._r("git", False), self._r("gh", True)], required=["git"])
        assert rep.status == "fail"
        assert rep.ok is False

    def test_optional_failure_is_degraded(self) -> None:
        rep = aggregate(
            [self._r("git", True), self._r("ollama", False)], required=["git"]
        )
        assert rep.status == "degraded"

    def test_required_failure_takes_precedence_over_optional(self) -> None:
        rep = aggregate(
            [self._r("git", False), self._r("ollama", False)], required=["git"]
        )
        assert rep.status == "fail"

    def test_empty_is_ok(self) -> None:
        assert aggregate([]).status == "ok"

    def test_no_required_set_failure_is_degraded_not_fail(self) -> None:
        # With nothing marked required, even a failing check is only degraded.
        rep = aggregate([self._r("ollama", False)])
        assert rep.status == "degraded"

    def test_results_are_preserved(self) -> None:
        rep = aggregate([self._r("git", True)], required=["git"])
        assert [r.name for r in rep.results] == ["git"]
