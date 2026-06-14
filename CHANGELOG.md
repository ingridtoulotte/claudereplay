# Changelog

## Unreleased

- **`demo` command** — `claudereplay demo` builds and opens an interactive
  replay from a realistic sample session bundled in the script. Lets anyone try
  ClaudeReplay in seconds, even with no sessions of their own. Covered by
  selftest (now 33 checks).
- **Animated SVG hero** (`examples/demo.svg`) — a looping mock of the replay UI
  that plays directly on the GitHub README.
- **Fix** — non-`int` command return values no longer leak a non-zero process
  exit code (`build`/`open`/`demo` now exit `0` on success).

## 0.1.0 — 2026-06-13

First release. Replay any Claude Code session like a movie.

- **Discovery** — `list` finds every session under `~/.claude/projects/`
  (honours `CLAUDE_CONFIG_DIR`), newest first; `last`/index/id-prefix/path
  selectors.
- **Parser** — single-pass, stdlib-only. Normalizes prompts, `thinking`, text,
  tool calls, tool results (paired by id), slash commands and compaction
  boundaries into one timeline. Reconstructs file evolution from `Write`
  snapshots and `Edit` diffs; computes the context-growth series from API usage.
- **Interactive HTML replay** (`build` / `open`) — single portable file, vanilla
  JS, no deps: play/pause, step, 1×–20× speed, scrubber with color-coded tool
  ticks and moment markers, live context-growth chart, key-moments and
  files-touched rails, per-file diff viewer, keyboard controls.
- **Session intelligence** — heuristic detection of mistakes, fixes, refactors,
  new components and large context jumps; ranked "Top moments".
- **Outputs** — Markdown summary, SVG session card, SVG timeline.
- **Privacy** — 100% local, no network; `--redact` scrubs keys/tokens/secrets;
  `--max-result-bytes` caps embedded output size.
- **Tested** — built-in `--selftest` with 29 checks; CI on Linux/macOS/Windows
  across Python 3.8–3.12.
