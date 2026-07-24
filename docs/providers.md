# Provider configuration

Claude Code and Codex expose different CLI controls. `kagura-brain` keeps their
shared launch semantics aligned while preserving those differences explicitly.

## Defaults and authentication hygiene

The default path uses the provider CLI's saved subscription login. Before launch,
the adapter copies the parent environment, removes provider override variables,
then starts the child:

| Backend | Removed from child environment |
|---|---|
| Claude | every `ANTHROPIC_*` and `CLAUDE_*` variable |
| Codex | every `OPENAI_*` and `CODEX_*` variable |

This includes endpoint overrides, cloud-routing flags, and alternate config-home
variables. The parent process is unchanged.

## Claude Code

```python
from kagura_brain import claude

result = claude.invoke(
    "Apply the approved edit.",
    permission_mode="acceptEdits",
    mcp_config=".mcp.json",
    allowed_tools=("Read",),
)
```

Supported permission modes are `default`, `acceptEdits`, `plan`, and
`bypassPermissions`. `dangerously_skip_permissions=True` emits Claude Code's
full permission-bypass flag. It is mutually exclusive with `permission_mode`.

For an Anthropic-compatible gateway, pass both values:

```python
result = claude.invoke(
    "Run through the configured gateway.",
    endpoint="https://gateway.example.com/v1",
    api_key=gateway_token,
)
```

The token is injected as `ANTHROPIC_AUTH_TOKEN` after the environment scrub.
Ollama Cloud is not a built-in Claude preset because it exposes an
OpenAI-compatible interface, not an Anthropic-compatible one.

## Codex CLI

```python
from kagura_brain import codex

result = codex.invoke(
    "Inspect the repository without writing.",
    sandbox="read-only",
    mcp_config=".mcp.json",
)
```

Sandbox modes are `read-only`, `workspace-write`, and `danger-full-access`.
`bypass_approvals=True` emits Codex's approval-and-sandbox bypass flag and is
mutually exclusive with `sandbox`.

Codex has two alternate backend forms:

```python
# Remote OpenAI Responses-compatible gateway.
remote = codex.invoke(
    "Run on the selected gateway.",
    endpoint="ollama-cloud",  # alias for https://ollama.com/v1
    api_key=gateway_token,
)

# Local backend; no endpoint or key is required.
local = codex.invoke(
    "Run locally.",
    local_provider="ollama",  # or "lmstudio"
)
```

The remote path selects an explicit custom Codex model provider, so it wins even
when Codex has a saved ChatGPT login. The endpoint must implement the OpenAI
Responses API. `local_provider` is mutually exclusive with `endpoint` and
`api_key`.

The Codex adapter's assumptions were last audited against Codex CLI `0.145.0`.
See the manual real-CLI smoke in [`CONTRIBUTING.md`](../CONTRIBUTING.md) after
upgrading Codex or changing its argv/config translation.

## Common gateway contract

For a remote gateway, `endpoint` and `api_key` are a pair:

- supplying only one raises `ValueError`;
- a non-HTTPS endpoint emits `UserWarning` because prompts and code context may
  leave the machine without transport encryption;
- explicit values are injected only after the ambient provider environment has
  been scrubbed;
- secrets are never read automatically by `select()` or an adapter.

## MCP behavior

Both adapters accept `mcp_config`, a path to a Claude-format `.mcp.json`, but
they use it differently:

- Claude passes `--mcp-config` and, when requested, `--allowedTools` for that
  invocation.
- Codex parses `mcpServers` and translates each entry into per-call
  `-c mcp_servers.*` configuration overrides. Invalid or missing JSON therefore
  raises `ValueError` before launch.
- Codex has no equivalent per-call tool allow-list. `allowed_tools` is accepted
  for selector parity, ignored by the adapter, and reported with a warning.

For unattended MCP turns, choose the provider's permission/sandbox posture
explicitly. A default headless run may deny or block approval-gated tool calls.

## Provider-neutral permissions

`BrainHandle.invoke(dangerously_skip_permissions=True)` maps one unattended-run
intent to each provider's full bypass. The blast radius is deliberately not
symmetric:

- Claude skips per-action approval prompts.
- Codex bypasses approvals **and disables the sandbox**.

If Codex must retain sandbox isolation, call `codex.invoke(sandbox=...)` directly
instead of using the provider-neutral full bypass. Conversely,
`permission_mode` is Claude-only and raises on a Codex handle;
`local_provider` is Codex-only and raises on a Claude handle.
