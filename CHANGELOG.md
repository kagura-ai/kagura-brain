# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
