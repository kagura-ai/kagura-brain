# kagura-brain

The provider-neutral **"brain" layer** shared by Kagura's CLI-brain harnesses
(`kagura-engineer`, `kagura-planner`, `kagura-code-reviewer`).

These tools run a **headless CLI coding agent as their brain**. They were each
re-implementing the same seams: launching the agent, stripping stale provider
credentials so subscription auth wins, parsing exit codes and sentinel markers,
and normalizing output. This package centralizes those seams.

Today the only adapter is **Claude Code (`claude -p`)**. The launcher shape is
provider-agnostic by design — a **Codex CLI (`codex exec`)** adapter is planned as
a sibling so the same `verdict` / `extract_block` / subprocess core serves both.

## Why a separate package (not folded into the memory SDK)

Harness-support code splits cleanly along **two axes**:

| Axis | Belongs in | Examples |
|------|-----------|----------|
| **memory** | `kagura-memory-python-sdk` | sync client facade, `.mcp.json` setup, auth resolution, recall |
| **brain** | **this package** | CLI-agent launcher (`claude -p` today), subscription-auth hygiene, verdict contract, doctor primitives |

Pushing CLI-agent spawning into a memory SDK would invert the layers
("memory spawns the agent"). So the brain axis lives here, and this package
**depends on no memory package** — the two support libs are mutually independent.

```
kagura-engineer   kagura-planner   kagura-code-reviewer
        \                |                /
         `--> kagura-brain  +  kagura-memory-python-sdk <--'
                  (brain axis)            (memory axis)
```

## Install

```bash
pip install kagura-brain
# or: uv add kagura-brain
```

Requires Python 3.11+. The only runtime dependency is `pydantic` — by design
this package depends on **no** memory package (see the axis split above).

## Status

Public surface is built incrementally under TDD, one consumer migration per PR:

- [x] `proc` — `as_text`, generic `mcp_args`
- [x] `brain.invoke()` — headless `claude -p` launcher (provider-credential deny-set, `--`-guarded prompt, utf-8 decode, timeout, marker extract)
- [x] `verdict` — `PROCEED` set + exit-code map (contract only)
- [ ] `brain` — Codex CLI (`codex exec`) adapter sharing the core
- [ ] `doctor` — git/claude/gh/ollama/reachability check primitives

## Development

```bash
uv sync --extra dev
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # type check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow and
[CHANGELOG.md](CHANGELOG.md) for release history.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
