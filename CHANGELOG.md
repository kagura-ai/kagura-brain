# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Adds the second brain adapter (Codex CLI) and factors the shared launcher core
out of the Claude-specific module. Because 0.1.0 has not been published to PyPI
and has no consumers yet, the provider-neutral rename is done now to avoid a
later deprecation shim — these are breaking import-path changes relative to the
committed 0.1.0 baseline.

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

### Changed
- `brain.invoke()` → `claude.invoke()` — the Claude adapter now lives in
  `kagura_brain.claude` (with its `ANTHROPIC_*` deny-set and `mcp_args`), over the
  shared `core`. Import path: `from kagura_brain.claude import invoke`.
- `as_text` moved to `core`; `mcp_args` moved to `claude` (it is Claude-flag-specific).

### Removed
- `kagura_brain.brain` and `kagura_brain.proc` modules — their contents moved to
  `core` / `claude` (see Changed). No deprecation shim (no published consumers).

## [0.1.0] - 2026-06-09

First release of `kagura-brain` — the provider-neutral "brain" axis for Kagura
harnesses, counterpart to `kagura-memory`. Supersedes the short-lived
`kagura-claude-harness` (whose name bound it to a single vendor); the launcher
code carries that package's hardening forward.

### Added
- `proc` — `as_text` (normalize subprocess stdout/stderr, incl. timeout bytes) and
  generic `mcp_args` (`--mcp-config` / `--allowedTools` argv, memory-vocabulary-free;
  a bare-`str` `allowed_tools` is treated as one tool, not splatted into characters).
- `brain.invoke()` — headless `claude -p` launcher on subscription auth: strips a
  stale `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` deny-set so they cannot override
  the login; passes the prompt after a `--` separator (a leading-`-` prompt can't be
  parsed as a flag); decodes output as utf-8/`errors="replace"`; timeout normalization
  where `BrainResult.detail()` surfaces captured partial stdout; and `extract_block`
  for sentinel-delimited payloads (CRLF-normalized).
- `verdict` — canonical `PROCEED` set, normalization (coerces non-`str` input so an
  off-contract verdict safe-halts), and exit-code map (contract only).

### Notes
- Depends on **no** memory package by design (brain axis vs. memory axis).
- Today the only adapter is Claude Code (`claude -p`); a Codex CLI adapter
  (`codex exec`) is planned to share the same core.
- `doctor` primitives (git/claude/gh/ollama/reachability) are still pending.
