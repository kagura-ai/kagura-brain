# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
