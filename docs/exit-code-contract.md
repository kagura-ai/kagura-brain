# The Kagura ecosystem exit-code contract

> Canonical reference for what a process exit code *means* across the Kagura
> harnesses. Audit-2026-06 cross-repo finding **E1** flagged that
> kagura-brain maps `red → exit 2` (halt) while kagura-code-reviewer maps
> `red → exit 1`, so a bare `exit 2` looked ambiguous to a caller that drives
> both. This document codifies the **two-level contract** that already
> neutralizes the conflict at the seam, so the ambiguity never becomes a
> harmful code path.

kagura-brain owns the gate-verdict vocabulary (see
[`src/kagura_brain/verdict.py`](../src/kagura_brain/verdict.py)), so the
canonical contract lives here. The conflict spans repos, but the resolution is
a single idea: **there are two distinct vocabularies, and the harness seam
converts between them by reading the JSON envelope — never by inferring a
verdict from an exit code.**

## Two levels, two vocabularies

### 1. Gate vocabulary (canonical)

The verdict gate that autonomous harnesses use to decide *proceed vs halt*.
This is the vocabulary kagura-brain owns and kagura-engineer consumes.

| code | meaning | semantic |
|------|---------|----------|
| `0` = proceed | green / yellow verdict — the phase is OK to continue | proceed/OK |
| `1` = hard fail | a hard failure raised by the harness itself | hard fail |
| `2` = halt | red / unknown / missing verdict — stop and surface to a human | blocked/halt |

Defaulting the unknown/missing case to **halt** is the safe direction: better
to stop and surface to a human than mis-read a verdict and let an autonomous
run barrel ahead.

- brain: `verdict.py` — `PROCEED_EXIT = 0`, `HALT_EXIT = 2`
- engineer: `run/__init__.py`, `review/__init__.py`, `goal/__init__.py`

### 2. Reviewer-internal vocabulary (standalone CLI)

kagura-code-reviewer is *also* a standalone CLI. Its exit codes are tuned for a
human (or shell script) running it directly, where "the review found a problem"
and "the tool itself broke" are usefully distinct:

| code | meaning |
|------|---------|
| `0` | green / yellow |
| `1` | red (the reviewer's verdict is red) |
| `2` | git / config error |
| `3` | backend failure |

The code-reviewer README documents the invariant **`verdict == red` iff exit
`1`**, and its test suite pins it. This is a *different* vocabulary from the
canonical gate codes above — note `1` and `2` mean different things in each
level. That is intentional and safe, because of the seam (below).

## The seam: how the two levels reconcile

kagura-engineer calls kagura-code-reviewer as a subprocess. It does **not**
infer a verdict from the reviewer's exit code. Instead:

1. The verdict is read from the reviewer's **JSON envelope** — the structured
   output — never from the process exit code.
2. The reviewer's *infrastructure* exit codes `{2, 3}` (git/config error,
   backend failure) are intercepted and converted to the harness's own
   `FAIL(1)`. This is pinned by the engineer's `_INFRA_RETURNCODES = {2, 3}`
   contract test (regression guard for this exact seam).
3. A `red` verdict (read from the envelope) maps to the canonical
   `BLOCKED(2)` — halt — in the gate vocabulary.

Because the seam reads the envelope and explicitly converts the reviewer's
infra codes, the standalone `red → 1` of code-reviewer and the canonical
`red → 2` of the gate vocabulary never collide in a harmful way. The two
levels are bridged by data (the envelope), not by overloading a single integer.

### Decision: do not change code-reviewer's `red → 1`

Reconciling code-reviewer onto `red → 2` would be a **breaking change** for
standalone users (its README and ~41 test lines pin `red iff 1`) with **zero
benefit** — the engineer seam already converts. So the contract is codified
**as-is**: the two-level split is the design, not a bug to paper over.

## A third, command-local meaning: `setup`'s exit 2

kagura-engineer's `setup` command uses exit `2` with a **command-local**
meaning: `NEEDS_USER` (the run needs human input before it can proceed),
documented in the engineer's `plan-2-setup.md`. This is fine *as long as it
never flows through the gate vocabulary* — `setup` is an interactive
provisioning step, not a gate phase, so its `2` is read by the human at the
terminal, not by a verdict gate. It is called out here only so the overlap is
explicit and intentional.

## For consumers

- **Driving a harness gate** (proceed/halt decisions)? Use the **gate
  vocabulary**: `0` proceed, `2` halt. Read verdicts from the JSON envelope
  when one is available; treat unknown/missing as halt.
- **Running kagura-code-reviewer standalone**? Use the **reviewer-internal
  vocabulary**: `0` ok, `1` red, `2` git/config, `3` backend.
- **Writing a harness that consumes the reviewer**? Read the envelope, not the
  exit code. Intercept reviewer infra codes `{2, 3}` as your own hard-fail.

## Origin

audit-2026-06 ecosystem finding **E1** (decision memo `20da8bae`). Companion
finding E2 (version pin drift) was fixed in kagura-planner#20.
