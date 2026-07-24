# Getting started

This guide gets one Claude Code or Codex CLI brain turn running. Provider-specific
gateway, MCP, model, and permission options are documented separately in
[Provider configuration](providers.md).

## 1. Install the package

`kagura-brain` requires Python 3.11 or newer and has no Python runtime
dependencies.

```bash
pip install kagura-brain
# or
uv add kagura-brain
```

## 2. Install and authenticate a provider CLI

The package launches an existing CLI; it does not bundle one.

| Backend | Required executable | Default authentication |
|---|---|---|
| `claude` | [Claude Code](https://code.claude.com/docs) | sign in with the Claude subscription you want the child process to use |
| `codex` | [Codex CLI](https://github.com/openai/codex) | run `codex login` for ChatGPT subscription auth |

Confirm the executable is on `PATH`:

```bash
claude --version
# and/or
codex --version
```

The default launch path removes inherited provider credential and endpoint
variables before starting the child. This prevents a stale key or foreign base
URL in the parent process from overriding the CLI's saved subscription login.

## 3. Run a provider directly

```python
from kagura_brain import claude, codex

claude_result = claude.invoke(
    "Reply with exactly: PONG",
)

codex_result = codex.invoke(
    "Reply with exactly: PONG",
    sandbox="read-only",
)
```

Both calls return `BrainResult`:

```python
result = codex_result

if result.ok:
    print(result.stdout)
else:
    # Uses stderr, then stdout, then "timed out" as a diagnostic fallback.
    raise RuntimeError(result.detail())
```

The default timeout is 30 minutes. Override it with `timeout=<seconds>` and set
`cwd=Path(...)` to choose the child process working directory.

## 4. Select a backend in shared consumer code

Use `select()` when the same harness supports both providers:

```python
import os
from pathlib import Path

from kagura_brain import BRAIN_API_KEY_ENV, select

brain = select(
    backend=configured_backend,
    endpoint=configured_endpoint,
    api_key=os.environ.get(BRAIN_API_KEY_ENV),
)

result = brain.invoke(
    "Implement the approved task.",
    cwd=Path("/path/to/repository"),
    mcp_config=".mcp.json",
    model=configured_model,
)
```

`select()` accepts primitives rather than a consumer-specific configuration
object. The library defines the conventional key name
`KAGURA_BRAIN_API_KEY`, but never reads the environment itself; the caller owns
secret retrieval and passes the value explicitly.

Codex accepts `allowed_tools` for selector signature parity but cannot enforce a
per-call MCP tool allow-list. A non-empty value emits a once-per-process warning.
See [MCP behavior](providers.md#mcp-behavior) for the full provider difference.

## 5. Check provider availability

The adapter checks are intentionally presence-only and do not make a billable
model call:

```python
from kagura_brain import claude, codex

print(claude.check())
print(codex.check())
```

Lower-level helpers in `kagura_brain.doctor` can check a binary, run a safe auth
status command, validate an HTTP(S) endpoint, and aggregate results.

## Next steps

- Read [Provider configuration](providers.md) before enabling unattended access
  or routing prompts to a gateway.
- Read [Architecture](architecture.md) before adding a new adapter or coupling a
  consumer to launcher internals.
- Use the [Exit-code contract](exit-code-contract.md) when a harness turns model
  verdicts into proceed/halt decisions.
