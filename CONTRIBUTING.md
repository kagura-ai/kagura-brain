# Contributing

Thanks for your interest in `kagura-brain` — the Claude-Code-driving
layer shared by Kagura's CLI-brain harnesses.

## Setup

```bash
git clone https://github.com/kagura-ai/kagura-brain.git
cd kagura-brain
uv sync --extra dev
```

## Scope (read before adding code)

This is the **claude axis** of the harness-support split. It centralizes the
seams for driving `claude -p` as a brain (launcher, Anthropic auth hygiene,
verdict contract, doctor primitives).

It deliberately depends on **no memory package**. MCP *config generation*
(`.mcp.json`) lives in the `kagura-memory` SDK; this package only builds the
generic `--mcp-config` argv (`proc.mcp_args`). Consumers (`kagura-engineer`,
`kagura-planner`, `kagura-code-reviewer`) depend on both and wire them together.

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

## Releasing

Releases are automated via `.github/workflows/publish.yml`:

- **TestPyPI** — run the `Publish to PyPI` workflow manually (`workflow_dispatch`).
- **PyPI** — push a `v*` tag (e.g. `v0.1.0`); this also creates a GitHub Release.

Both use PyPI Trusted Publishing (OIDC) — no API tokens. Bump the version in
`src/kagura_brain/__init__.py` and update `CHANGELOG.md` before tagging.
