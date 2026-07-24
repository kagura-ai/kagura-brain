# Architecture

`kagura-brain` is the brain-launching axis shared by Kagura harnesses. It owns
the provider CLI boundary and nothing from the memory domain.

## Package boundary

Harness support is split into independent axes:

| Axis | Package | Responsibilities |
|---|---|---|
| Brain | `kagura-brain` | CLI launch, child-environment hygiene, timeouts, output normalization, backend selection, verdict and doctor primitives |
| Memory | `kagura-memory` | authentication, durable recall/write, context selection, and memory-oriented MCP setup |

Consumers such as `kagura-engineer`, `kagura-planner`, and
`kagura-code-reviewer` may use both packages, but neither support package depends
on the other.

This prevents two forms of inversion:

- the memory SDK never launches a coding agent;
- the launcher never bakes in memory tool names, context vocabulary, or storage
  behavior.

## Launch pipeline

Every adapter follows the same shared shape:

```text
consumer configuration
        |
        v
provider adapter builds argv + deny prefixes
        |
        v
copy parent env -> scrub ambient provider overrides
        |
        v
inject explicit gateway values, if requested
        |
        v
resolve executable -> launch subprocess -> enforce timeout
        |
        v
normalize stdout/stderr -> BrainResult
```

The scrub-before-inject order is load-bearing. Ambient credentials must not beat
a subscription login, while a caller's explicit alternate backend must beat the
ambient environment.

Prompts are sent through stdin. In particular, this prevents Windows command
shim re-parsing of metacharacters or `%VARIABLE%` sequences that would occur if
the prompt were embedded in argv.

## Module map

| Module | Responsibility |
|---|---|
| [`core.py`](../src/kagura_brain/core.py) | `BrainResult`, subprocess execution, environment scrub/injection, timeout and text normalization, sentinel extraction |
| [`claude.py`](../src/kagura_brain/claude.py) | Claude Code argv, permissions, MCP flags, model selection, and Anthropic-compatible gateway wiring |
| [`codex.py`](../src/kagura_brain/codex.py) | Codex argv, sandbox/approval policy, MCP translation, model provider overrides, and local backends |
| [`selector.py`](../src/kagura_brain/selector.py) | validated `BrainHandle` and provider-neutral dispatch |
| [`verdict.py`](../src/kagura_brain/verdict.py) | normalization and canonical proceed/halt mapping |
| [`doctor.py`](../src/kagura_brain/doctor.py) | provider-neutral preflight checks and aggregate reports |

## Security invariants

The following behaviors are part of the public contract and should remain pinned
by tests:

1. Child environment scrub is prefix-wide (`ANTHROPIC_*`/`CLAUDE_*` or
   `OPENAI_*`/`CODEX_*`), so newly introduced provider override variables fail
   safe without a new deny-list entry.
2. Explicit gateway credentials are injected only after the scrub and only when
   the endpoint/key pair is complete.
3. Neither adapter loosens permissions by default; conflicting confinement and
   full-bypass requests raise instead of silently choosing one.
4. A `BrainHandle` rejects unknown backends and contradictory capability state.
5. API keys are excluded from `BrainHandle`'s dataclass representation.
6. Unknown or missing gate verdicts halt. See the
   [exit-code contract](exit-code-contract.md).

## Non-goals

`kagura-brain` does not:

- implement an agent loop or decide what task the model should perform;
- manage provider login state or persist provider credentials;
- generate project memory configuration or depend on a memory client;
- normalize all provider capabilities into a misleading lowest-common-
  denominator API;
- turn full bypass on automatically for headless execution.

Provider-specific controls remain available on the direct adapters when the
neutral selector cannot express the required safety boundary.
