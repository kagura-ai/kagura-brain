# Contributing

Thanks for your interest in `kagura-brain` — the provider-neutral CLI-agent
launcher shared by Kagura's brain harnesses.

## Setup

```bash
git clone https://github.com/kagura-ai/kagura-brain.git
cd kagura-brain
uv sync --extra dev
```

## Scope (read before adding code)

This is the **brain axis** of the harness-support split. It centralizes the
seams for driving `claude -p` and `codex exec` as brains: launcher behavior,
provider auth hygiene, MCP wiring, permission boundaries, the verdict contract,
and doctor primitives.

It deliberately depends on **no memory package**. MCP *config generation*
(`.mcp.json`) lives in the `kagura-memory` SDK; this package only consumes that
generic format. Claude forwards it with per-call flags, while Codex translates
its `mcpServers` entries into per-call config overrides. Consumers
(`kagura-engineer`, `kagura-planner`, `kagura-code-reviewer`) depend on both
axes and wire them together.

**Do not** add a dependency on `kagura-memory` — it would invert the axis split.
Keep this package memory-vocabulary-free: callers pass tool names in, they are
never baked in here.

## Quality checks

```bash
uv run ruff check src/ tests/    # Lint
uv run ruff format src/ tests/   # Format
uv run mypy                      # Type check (strict)
uv run pytest tests/ -v          # Test
```

CI runs lint + format check + `mypy` strict, and the test suite on Python
3.11 / 3.12 / 3.13. Coverage must stay at or above the `fail_under` threshold
in `pyproject.toml` (90%).

## Manual smoke (real CLI)

The automated suite mocks `subprocess.run`, so it never launches a real `claude`
or `codex`. Before a release (or after touching an adapter's argv / env scrub),
run this **manual** smoke once to confirm the adapters drive the real CLIs on
**subscription auth** and that the credential scrub holds end-to-end.

**Why it is not in CI:** it requires `claude` and `codex` logged in with
subscription credentials, which cannot be provisioned in CI; a real model call is
also slow and flaky. CI deliberately stops at the mocked suite.

Prerequisites: `claude` and `codex` installed and logged in (`claude` via your
Claude subscription, `codex login` for the Codex subscription).

```bash
# Decoys: a bogus key + a foreign endpoint for BOTH providers. If the scrub works,
# the child never sees these — the subscription login wins and the call succeeds.
# If the scrub regressed, the call fails (bad key) or the request is redirected.
export ANTHROPIC_API_KEY="sk-ant-bogus-should-be-stripped"
export ANTHROPIC_BASE_URL="https://decoy.invalid/v1"
export OPENAI_API_KEY="sk-openai-bogus-should-be-stripped"
export OPENAI_BASE_URL="https://decoy.invalid/v1"

uv run python - <<'PY'
from kagura_brain import claude, codex

for name, mod in (("claude", claude), ("codex", codex)):
    res = mod.invoke("Reply with exactly: PONG")
    ok = res.returncode == 0 and not res.timed_out and "PONG" in res.stdout
    print(f"{name}: rc={res.returncode} timed_out={res.timed_out} "
          f"-> {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"  stdout: {res.stdout[:200]!r}")
        print(f"  stderr: {res.stderr[:200]!r}")
PY
```

Both lines must print `PASS`. A `FAIL` with an "Invalid API key" / auth error
means the bogus key leaked into the child (scrub regression); a success that
reaches the decoy host means the endpoint override was not stripped. Either is a
release blocker.

### BYO endpoint (issue #2) — opt-in routing still beats the ambient decoy

After touching the BYO inject seam (`core.byo_inject_env` / `_run`'s
`inject_env`), also confirm that an **explicit** caller endpoint wins over the
ambient decoy — i.e. the scrub→inject order holds end-to-end. With the same decoy
env still exported above, point Codex at a real OpenAI-compatible endpoint
(e.g. Ollama Cloud) via the opt-in args and confirm it reaches *that* endpoint,
not the decoy:

```bash
# Requires a real Ollama Cloud (or other OpenAI-compatible) key.
export OLLAMA_CLOUD_API_KEY="…"   # your real key, NOT a decoy

uv run python - <<'PY'
import os
from kagura_brain import codex

res = codex.invoke(
    "Reply with exactly: PONG",
    endpoint="ollama-cloud",                 # alias → https://ollama.com/v1
    api_key=os.environ["OLLAMA_CLOUD_API_KEY"],
)
ok = res.returncode == 0 and not res.timed_out and "PONG" in res.stdout
print(f"codex BYO: rc={res.returncode} -> {'PASS' if ok else 'FAIL'}")
if not ok:
    print(f"  stdout: {res.stdout[:200]!r}")
    print(f"  stderr: {res.stderr[:200]!r}")
PY
```

`PASS` proves the explicit endpoint/token were injected *after* the scrub (the
ambient `OPENAI_BASE_URL=https://decoy.invalid/v1` decoy was stripped, then
overridden by the caller value). A `FAIL` that reaches `decoy.invalid` means the
inject ran before the scrub (order regression) — a release blocker.

## Workflow

1. Branch from `main`: `git checkout -b {issue}-{type}/{description}`
2. Implement test-first (TDD) — one consumer migration per PR
3. Run the quality checks above
4. Push and open a PR; CI must pass
5. Squash merge to `main`

## Commit convention

[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`

## Code style

- Python 3.11+, type hints required (`mypy --strict` clean)
- `from __future__ import annotations` at the top of each module
- Google-style docstrings on public functions
- No network or memory coupling in this package
- Keep provider-specific policy in its adapter; share only behavior with the
  same semantics in `core`

## Releasing

Releases are automated via `.github/workflows/publish.yml`:

- **TestPyPI** — run the `Publish to PyPI` workflow manually (`workflow_dispatch`).
- **PyPI** — push a `v*` tag (e.g. `v0.1.0`); this also creates a GitHub Release.

Both use PyPI Trusted Publishing (OIDC) — no API tokens. Bump the version in
`src/kagura_brain/__init__.py` and update `CHANGELOG.md` before tagging.
