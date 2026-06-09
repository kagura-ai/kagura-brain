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

### BYO endpoint (opt-in)

By default both adapters strip the provider's endpoint/credential env so the
**subscription** login wins. To deliberately route at a **caller-chosen
endpoint** (Ollama Cloud / any compatible gateway), pass `endpoint` + `api_key`
*together* — they are injected **after** the scrub, so only these explicit
values reach the child while an ambient `*_BASE_URL` override stays stripped
([#2](https://github.com/kagura-ai/kagura-brain/issues/2)):

```python
# Codex → Ollama Cloud (OpenAI-compatible). "ollama-cloud" is an alias for
# codex.OLLAMA_CLOUD_ENDPOINT ("https://ollama.com/v1").
codex.invoke("…prompt…", endpoint="ollama-cloud", api_key="…")
# Codex → a LOCAL ollama/lmstudio backend (no endpoint/key needed):
codex.invoke("…prompt…", local_provider="ollama")
# Claude → a caller-supplied Anthropic-compatible gateway (no Ollama preset:
# Ollama Cloud is OpenAI-, not Anthropic-, compatible):
claude.invoke("…prompt…", endpoint="https://my-gateway/v1", api_key="…")
```

Supplying only one of `endpoint`/`api_key` raises `ValueError`; a non-https
endpoint emits a `UserWarning` (prompt + code context goes off-box). With none
of these set, the default subscription-auth path is byte-for-byte unchanged.

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

Requires Python 3.11+. It has **no** *Python* runtime dependencies (stdlib-only)
— and by design depends on **no** memory package (see the axis split above).

### Prerequisites: the provider CLIs

kagura-brain is a *launcher* — it drives the provider CLIs as subprocesses, so
each adapter needs **its own CLI installed and on `PATH`**. These are external
tools, not pip dependencies; install only the one(s) you use:

| Adapter | Needs | Auth (default path) |
|---------|-------|---------------------|
| `claude.invoke()` | **Claude Code CLI** (`claude`) — see [docs](https://docs.claude.com/en/docs/claude-code) | signed in to your Claude **subscription** (the adapter strips `ANTHROPIC_*` so the subscription login wins) |
| `codex.invoke()` | **Codex CLI** (`codex`) — see [docs](https://github.com/openai/codex) | `codex login` (ChatGPT subscription; the adapter strips `OPENAI_*`/`CODEX_*`). Verified against Codex `0.133.0` (`codex._CODEX_VERIFIED_VERSION`) |

The CLI binary is required **even in [BYO-endpoint mode](#byo-endpoint-opt-in)** —
only the *auth source* changes: instead of the subscription login, the
caller-supplied `endpoint` + `api_key` (or, for Codex, a local
`--oss --local-provider` backend) are used. No subscription login is needed for
the BYO path, but `claude` / `codex` must still be installed.

`doctor` primitives to check CLI launchability are planned (see Status).

## Status

Public surface is built incrementally under TDD, one consumer migration per PR:

- [x] `core` — shared seam: `BrainResult`, `_run` (subprocess + env-scrub + timeout + utf-8 decode), `as_text`, `extract_block`
- [x] `claude.invoke()` — headless `claude -p` launcher (`ANTHROPIC_*` deny-set, `--`-guarded prompt, `mcp_args`, opt-in BYO `endpoint`/`api_key`)
- [x] `codex.invoke()` — headless `codex exec` launcher (`OPENAI_*`/`CODEX_*` prefix scrub, `--`-guarded prompt, opt-in sandbox, BYO `endpoint`/`api_key` + `local_provider`)
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
