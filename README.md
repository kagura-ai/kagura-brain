# kagura-brain

The provider-neutral **"brain" layer** shared by Kagura's CLI-brain harnesses
(`kagura-engineer`, `kagura-planner`, `kagura-code-reviewer`).

These tools run a **headless CLI coding agent as their brain**. They were each
re-implementing the same seams: launching the agent, stripping stale provider
credentials so subscription auth wins, parsing exit codes and sentinel markers,
and normalizing output. This package centralizes those seams.

Two adapters ship today — **Claude Code (`claude -p`)** and **Codex CLI
(`codex exec`)** — as thin siblings over one shared launcher core (`core._run` +
`verdict` + `extract_block`). Each strips its provider's credential/endpoint
overrides from the child env so the CLI's **subscription** login wins:
`claude` deny-sets `ANTHROPIC_*`; `codex` prefix-scrubs `OPENAI_*`/`CODEX_*`
(including `OPENAI_BASE_URL` and `CODEX_HOME`).

```python
from kagura_brain import claude, codex

claude.invoke("…prompt…", mcp_config=".mcp.json")   # claude -p
codex.invoke("…prompt…", sandbox="read-only")       # codex exec
```

**MCP / approval differs by provider.** Claude takes per-call `--mcp-config` /
`--allowedTools` (see `claude.mcp_args`). Codex manages MCP servers persistently
via `codex mcp` / `~/.codex/config.toml` (no per-call flag), and its sandbox /
approval is opt-in: pass `sandbox=` (`read-only` | `workspace-write` |
`danger-full-access`) or `bypass_approvals=True` — neither is loosened by default.

Routing a CLI at a caller-chosen endpoint (Ollama Cloud / BYO gateway) is the
deliberate inverse of the credential scrub and is tracked separately
([#2](https://github.com/kagura-ai/kagura-brain/issues/2)).

## Why a separate package (not folded into the memory SDK)

Harness-support code splits cleanly along **two axes**:

| Axis | Belongs in | Examples |
|------|-----------|----------|
| **memory** | `kagura-memory-python-sdk` | sync client facade, `.mcp.json` setup, auth resolution, recall |
| **brain** | **this package** | CLI-agent launcher (`claude -p` + `codex exec`), subscription-auth hygiene, verdict contract, doctor primitives |

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

- [x] `core` — shared seam: `BrainResult`, `_run` (subprocess + env-scrub + timeout + utf-8 decode), `as_text`, `extract_block`
- [x] `claude.invoke()` — headless `claude -p` launcher (`ANTHROPIC_*` deny-set, `--`-guarded prompt, `mcp_args`)
- [x] `codex.invoke()` — headless `codex exec` launcher (`OPENAI_*`/`CODEX_*` prefix scrub, `--`-guarded prompt, opt-in sandbox)
- [x] `verdict` — `PROCEED` set + exit-code map (contract only)
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
