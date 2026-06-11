# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1](https://github.com/kagura-ai/kagura-brain/releases/tag/v0.4.1) - 2026-06-11

Fixes headless launch on **native Windows** (no WSL), where `claude`/`codex` are
installed as npm `.cmd` shims. The launcher could not start them, and the
straightforward fix would have opened a command-injection surface — both are
addressed here. No public API change; a pure fix release.

### Fixed
- `core._run` now resolves `argv[0]` via `shutil.which` and launches a Windows
  `.cmd`/`.bat` shim through `COMSPEC /c` (keeping `shell=False`). `CreateProcess`
  only auto-appends `.exe` — never `PATHEXT` — so `subprocess.run(["claude", …])`
  died with `WinError 2` even though the doctor pre-flight (`shutil.which`,
  which *does* apply `PATHEXT`) reported the shim present. ([#17](https://github.com/kagura-ai/kagura-brain/issues/17), [#18](https://github.com/kagura-ai/kagura-brain/pull/18))

### Security
- The prompt now rides **stdin**, never an `argv` token. Routing a `.cmd`/`.bat`
  shim through `cmd.exe /c` re-parses the command line, so an argv-borne prompt
  containing `& | < > ^` or `%VAR%` would be corrupted (env-expanded even inside
  quotes) or, via a `"`-then-`&` break-out, inject an arbitrary command — the
  BatBadBut / CVE-2024-24576 class. Because adapters feed untrusted issue/PR/diff
  text into the prompt, this was a real remote-influenced exec surface on
  Windows. `core._run` gained a `stdin_text` parameter (forwarded as
  `subprocess.run(input=…)`); the `claude`/`codex` adapters drop the `--`
  separator + positional prompt and pass the prompt on stdin, so only
  developer-controlled flags ever reach `cmd.exe`. ([#18](https://github.com/kagura-ai/kagura-brain/pull/18))

## [0.4.0](https://github.com/kagura-ai/kagura-brain/releases/tag/v0.4.0) - 2026-06-10

Wires MCP into the codex adapter, so `select("codex")` now advertises
`supports_mcp=True` and `codex.invoke` can carry in-task memory tools. This is a
**library-side enabler**: a consumer benefits only once it routes through
`kagura_brain.select` (or otherwise forwards `mcp_config` to `codex.invoke`).
kagura-engineer/kagura-planner still run their own `brain_select` that gates
codex MCP off, so a follow-up there is needed before an engineer/planner codex
run actually gains grounding.

### Changed
- `codex.invoke` now accepts `mcp_config` (a claude-format `.mcp.json` path) and
  translates each `mcpServers` entry into a per-call `-c mcp_servers.<name>=<TOML>`
  config override — codex's equivalent of Claude Code's `--mcp-config` (codex has
  no `--mcp-config` flag). The inline-table form is verified against codex
  0.133.0. Keys with no codex analog (e.g. claude's `"type"`) are dropped;
  `command` (stdio) and `url` (streamable_http) are mutually exclusive, so a
  `command` wins and a conflicting `url` is dropped. A missing or non-JSON
  `mcp_config` raises `ValueError` (the claude adapter defers an unread path to
  its CLI; codex must parse it). All control chars are escaped in the emitted
  TOML. `allowed_tools` is accepted for selector signature parity but **not
  forwarded** — codex has no per-call tool allow-list and gates MCP tool calls
  through its sandbox/approval model instead (pass `sandbox=` /
  `bypass_approvals=True` for unattended MCP use); a non-empty value logs a
  once-per-process warning so the drop is visible.
- `selector`: `select("codex")` is now `supports_mcp=True`, and
  `BrainHandle.invoke` forwards `mcp_config`/`allowed_tools` to the codex adapter
  (previously dropped, logged once). The `_warn_codex_mcp_unsupported` drop-warning
  is removed. `BrainHandle.__post_init__` now requires `supports_mcp=True` for
  both `"claude"` and `"codex"`. `_BACKENDS` is now a `name → supports_mcp` map
  (the unused adapter-module slot was removed).

### Notes
- codex `-c` overrides **layer onto** `~/.codex/config.toml`; a server name that
  collides with a differently-typed existing entry (e.g. an existing
  streamable_http server) can fail config load — choose non-colliding names.

## [0.3.0](https://github.com/kagura-ai/kagura-brain/releases/tag/v0.3.0) - 2026-06-10

Promotes the brain-backend selection seam from the consumers into the library.
Every consumer that supports more than one backend used to map a backend name →
adapter + endpoint/api_key and re-encode the "codex has no per-call MCP" rule
itself (kagura-engineer's `run/brain_select.py`, kagura-planner #11). At N=2
consumers that generic core belongs in the library that already owns the
adapters, the `BrainResult` seam, and the `doctor` helpers.

### Added
- `selector` — provider-neutral `select(backend, *, endpoint=, api_key=) ->
  BrainHandle` over the existing adapters (`select`, `BrainHandle`,
  `BRAIN_API_KEY_ENV` re-exported at the top level). `"claude"` (default) →
  `supports_mcp=True`; `"codex"` → `supports_mcp=False`; an unknown backend
  raises `ValueError`. The frozen `BrainHandle.invoke(prompt, *, cwd, timeout,
  mcp_config=None, allowed_tools=())` confines the dispatch: a claude handle
  forwards `mcp_config` / `allowed_tools` (+ endpoint/api_key for a BYO gateway);
  a codex handle **drops** them — codex wires MCP out-of-band — and logs the drop
  once per process. `BRAIN_API_KEY_ENV = "KAGURA_BRAIN_API_KEY"` is the standard
  env-var *name* all consumers agree on; the library never reads the env itself
  (the consumer passes `api_key=` in), keeping it config-agnostic, secret-free,
  and dependency-free ([#14](https://github.com/kagura-ai/kagura-brain/issues/14)).

## [0.2.0](https://github.com/kagura-ai/kagura-brain/releases/tag/v0.2.0) - 2026-06-10

Adds the `doctor` toolchain-check layer (the last unchecked primitive on the
roadmap) and closes a subscription-auth scrub gap in the Claude adapter. Both
land on top of `v0.1.0`; the security fix was tracked under a separate
`v0.1.1 — Security patch` milestone but ships here since it merged to `main`
alongside the `doctor` work.

### Added
- `doctor` — provider-neutral, stdlib-only environment-check primitives a
  consuming harness calls to verify its toolchain *before* driving a brain
  adapter (the inverse of `core._run`, which lets launch failures propagate):
  `check_binary` (presence via `shutil.which`), `check_auth` (exit-code auth
  check that catches `OSError`/`TimeoutExpired` into a fail result, with a short
  health-check timeout), `check_endpoint` (opt-in HTTP reachability, http/https
  only, no credentials attached), and `aggregate` (tri-state `ok`/`degraded`/
  `fail` with a caller-specified `required` set). Adapters add presence-only
  `claude.check()` / `codex.check()` wrappers — the intuitive consumer entry
  point, mirroring how `invoke()` wraps `core._run`
  ([#9](https://github.com/kagura-ai/kagura-brain/issues/9)).

### Security
- `claude` adapter now scrubs the whole `CLAUDE_*` env prefix in addition to
  `ANTHROPIC_*`. An ambient `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX`
  inherited from the parent environment was passing through to the headless
  `claude -p` child and silently switching it from Claude Code subscription auth
  to a Bedrock/Vertex IAM path (the `CLAUDE_*` analog of the `ANTHROPIC_BASE_URL`
  re-route). The whole-prefix sweep is fail-secure — an unknown future `CLAUDE_*`
  auth flag cannot slip through — and also drops `CLAUDE_CONFIG_DIR`. The
  opt-in BYO-endpoint path is unaffected
  ([#11](https://github.com/kagura-ai/kagura-brain/issues/11)).

## [0.1.0](https://github.com/kagura-ai/kagura-brain/releases/tag/v0.1.0) - 2026-06-09

First release of `kagura-brain` — the provider-neutral "brain" axis for Kagura
harnesses, counterpart to `kagura-memory`. Supersedes the short-lived
`kagura-claude-harness` (whose name bound it to a single vendor); the launcher
code carries that package's hardening forward. Ships two adapters — Claude Code
(`claude -p`) and Codex CLI (`codex exec`) — over one shared launcher `core`,
with subscription-auth hygiene and an opt-in BYO-endpoint mode. (The "Changed"/
"Removed" entries below record the provider-neutral rename of the unpublished
`kagura-claude-harness` baseline, folded into this first published release.)

### Added
- `core` — the provider-agnostic launcher seam: `BrainResult`, `_run`
  (subprocess + per-adapter env-scrub + timeout + utf-8/`errors="replace"` decode),
  `as_text`, and `extract_block`. `_run` parameterizes the credential deny-set via
  `deny_exact` (known key set) and `deny_prefixes` (prefix sweep).
- `codex.invoke()` — headless `codex exec` launcher on Codex (ChatGPT subscription)
  auth. **Subscription-auth parity**: prefix-scrubs every `OPENAI_*`/`CODEX_*` env
  var from the child (incl. `OPENAI_API_KEY`, `OPENAI_BASE_URL` endpoint-override,
  and `CODEX_HOME`) so the `codex login` credentials in the default `~/.codex` win.
  Prompt passed after a `--` separator (guards a leading-`-` prompt and the
  `exec` subcommands `resume`/`review`/`help`); sandbox (`-s/--sandbox`) and
  `--dangerously-bypass-approvals-and-sandbox` are opt-in, neither loosened by
  default. Reuses `verdict` and `extract_block` unchanged; the `-o`/`--output-schema`
  result protocol is deferred as a follow-up (parity kept for now).
- BYO endpoint mode (opt-in) for both adapters (#2): `claude.invoke()` and
  `codex.invoke()` accept `endpoint` + `api_key` to deliberately route at a
  caller-chosen endpoint (Ollama Cloud / any compatible gateway). The pair is
  injected (`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` for Claude,
  `OPENAI_BASE_URL`/`OPENAI_API_KEY` for Codex) **after** the deny-set scrub via
  the new `core._run(inject_env=...)` seam and the shared `core.byo_inject_env`
  helper, so only explicit caller-supplied values reach the child while ambient
  `*_BASE_URL` overrides stay stripped (the default subscription path is
  unchanged). Both-or-neither (`ValueError` on a half-configured pair); a
  non-https endpoint emits a `UserWarning`. Codex adds `local_provider`
  (`ollama`|`lmstudio`) for a local `--oss --local-provider` backend (no env
  override; mutually exclusive with `endpoint`/`api_key`) and the
  `codex.OLLAMA_CLOUD_ENDPOINT` preset (alias `endpoint="ollama-cloud"`). No
  Claude-side Ollama preset — Ollama Cloud is OpenAI-, not Anthropic-, compatible.
- `verdict` — canonical `PROCEED` set, normalization (coerces non-`str` input so an
  off-contract verdict safe-halts), and exit-code map (contract only).

### Changed
- `brain.invoke()` → `claude.invoke()` — the Claude adapter now lives in
  `kagura_brain.claude` (with its `ANTHROPIC_*` deny-set and `mcp_args`), over the
  shared `core`. Import path: `from kagura_brain.claude import invoke`.
- `as_text` moved to `core`; `mcp_args` moved to `claude` (it is Claude-flag-specific).

### Removed
- `kagura_brain.brain` and `kagura_brain.proc` modules — their contents moved to
  `core` / `claude` (see Changed). No deprecation shim (no published consumers).

### Notes
- Depends on **no** memory package by design (brain axis vs. memory axis).
- `doctor` primitives (git/claude/gh/ollama/reachability) are still pending.
