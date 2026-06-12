"""Docs-sync guard for the canonical exit-code contract.

The prose contract in ``docs/exit-code-contract.md`` codifies the two-level
exit-code vocabulary (canonical gate codes vs reviewer-internal codes). This
test pins the *canonical gate codes* documented there against the live
constants in :mod:`kagura_brain.verdict`, so the doc cannot silently drift from
the code that owns the vocabulary (issue #19, audit-2026-06 E1).
"""

from __future__ import annotations

from pathlib import Path

from kagura_brain.verdict import HALT_EXIT, PROCEED_EXIT

DOC = Path(__file__).resolve().parent.parent / "docs" / "exit-code-contract.md"


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


class TestDocExists:
    def test_contract_doc_is_present(self) -> None:
        assert DOC.is_file(), f"missing canonical contract doc: {DOC}"


class TestCanonicalGateCodes:
    """The canonical gate-vocabulary codes must match verdict.py constants."""

    def test_documents_proceed_code_tied_to_semantic(self) -> None:
        # Drift guard: the doc must state the proceed code using verdict.py's
        # actual PROCEED_EXIT value, tied to the "proceed" semantic.
        assert f"`{PROCEED_EXIT}` = proceed" in _doc_text()

    def test_documents_halt_code_tied_to_semantic(self) -> None:
        # Drift guard: the doc must state the halt code using verdict.py's
        # actual HALT_EXIT value, tied to the "halt" semantic.
        assert f"`{HALT_EXIT}` = halt" in _doc_text()


class TestTwoLevelContractCovered:
    """The doc must cover both levels, not just the canonical gate codes."""

    def test_mentions_reviewer_internal_level(self) -> None:
        assert "reviewer-internal" in _doc_text()

    def test_documents_reviewer_red_is_one(self) -> None:
        # code-reviewer's standalone invariant: verdict == red iff exit 1.
        # Documenting it is the whole point — the seam converts, brain does not.
        # Pin the specific reviewer-table row that ties code 1 to red, not a
        # bare `1` (which appears all over the doc and would pass even if this
        # row were deleted).
        assert "`1` | red" in _doc_text()

    def test_explains_the_seam_conversion(self) -> None:
        # The engineer seam intercepts reviewer infra codes {2,3} → FAIL(1) and
        # reads the verdict from the JSON envelope, never from the exit code.
        text = _doc_text()
        assert "envelope" in text
        assert "{2, 3}" in text or "{2,3}" in text
