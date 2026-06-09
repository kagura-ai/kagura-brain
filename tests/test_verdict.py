"""Tests for kagura_brain.verdict — the shared gate contract."""

from __future__ import annotations

from kagura_brain.verdict import (
    HALT_EXIT,
    PROCEED,
    PROCEED_EXIT,
    exit_code,
    normalize,
    proceed,
)


class TestProceedSet:
    def test_contains_green_and_yellow(self) -> None:
        assert "green" in PROCEED and "yellow" in PROCEED

    def test_excludes_red(self) -> None:
        assert "red" not in PROCEED


class TestNormalize:
    def test_strips_and_lowercases(self) -> None:
        assert normalize("  GREEN ") == "green"
        assert normalize("Yellow") == "yellow"

    def test_none_becomes_unknown(self) -> None:
        assert normalize(None) == "unknown"

    def test_empty_and_whitespace_become_unknown(self) -> None:
        assert normalize("") == "unknown"
        assert normalize("   ") == "unknown"

    def test_passes_known_token_through(self) -> None:
        assert normalize("red") == "red"

    def test_non_string_verdict_does_not_crash(self) -> None:
        # Off-contract input (e.g. an int/enum slipping through) must safe-halt,
        # not raise AttributeError mid-gate.
        assert normalize(5) == "5"


class TestProceed:
    def test_green_and_yellow_proceed(self) -> None:
        assert proceed("green") is True
        assert proceed("yellow") is True

    def test_case_insensitive(self) -> None:
        assert proceed("GREEN") is True

    def test_red_unknown_and_missing_halt(self) -> None:
        assert proceed("red") is False
        assert proceed("unknown") is False
        assert proceed(None) is False
        assert proceed("") is False

    def test_non_string_verdict_halts(self) -> None:
        assert proceed(5) is False


class TestExitCode:
    def test_green_yellow_exit_zero(self) -> None:
        assert exit_code("green") == 0
        assert exit_code("yellow") == 0
        assert exit_code("green") == PROCEED_EXIT

    def test_red_exits_two_not_one(self) -> None:
        # Canonical halt code is 2 (engineer's gate). code-reviewer's historical
        # red->1 is a separate, human-confirmed reconciliation — not implicit here.
        assert exit_code("red") == 2
        assert exit_code("red") == HALT_EXIT

    def test_unknown_and_missing_halt_with_two(self) -> None:
        assert exit_code(None) == 2
        assert exit_code("") == 2
        assert exit_code("unknown") == 2
