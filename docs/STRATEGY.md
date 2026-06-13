# ClaudeReplay — Product Strategy

The reasoning behind the project: who it's for, why it should exist, what to
build first, and what would make it matter for years. Condensed on purpose.

---

## 1. Core insight

A Claude Code session is the highest-density record of *how a problem was
solved* that a developer ever creates — and it's the most ephemeral. The
terminal scrolls, the context compacts, the window closes. The transcript
survives on disk as JSONL, but nothing makes it *legible*.

ClaudeReplay's bet: **the transcript is a recording. Treat it like one.** Give
it a play button.

## 2. User research — why replay a session?

Ranked by value × frequency (highest first). We reject the weak assumptions
explicitly.

| Rank | Use case | Why it's strong |
|---|---|---|
| 1 | **Debugging "how did it break?"** | You already *want* to scrub back to the moment a regression entered. This is the closest thing to a felt need. |
| 2 | **Learning / "how did it solve that?"** | The reasoning + tool sequence is the lesson. Watching beats reading scrollback. |
| 3 | **Sharing a great session** | A self-contained replay or a card is inherently social — this is the growth loop. |
| 4 | **Onboarding / knowledge transfer** | "Watch how this codebase actually gets worked on" is a real team artifact. |
| 5 | **AI workflow optimization** | Context jumps and repeated attempts surface your own inefficiencies. |
| 6 | **Documentation / PR context** | The markdown summary drops into a PR. Useful, but a by-product. |

**Rejected as weak:** "performance analysis" and "architecture review" as
*primary* drivers — they sound important but nobody opens a tool for them. They
ride along for free as panels, they don't earn the install. We optimize for
ranks 1–3 and let the rest come along.

**The one-line need we build for:** *"Show me how it got there."*

## 3. Experience design

The magic is in the first 10 seconds: `list` → pick → `open` → press ▶ and the
session *plays*. Prompt, then visible reasoning, then a tool call with its
arguments, then the result, paced by the real timestamps. The right rail makes
it feel instrumented: a context curve that climbs, moment markers you can leap
to, file diffs a click away.

Design rules we held to:
- **Time, not pagination.** Everything is a position on one timeline.
- **Reveal, don't dump.** Cards fade in as the playhead reaches them.
- **The scrubber is the visualization.** Its colored ticks *are* the tool-usage
  timeline; you read the session's shape before you press play.
- **One file out.** A replay you can't send is half a feature.

## 4. The viral moment

Three candidates, all shipped:
1. **The scrubber + 20× playback.** Dragging through someone's 4-hour session in
   ten seconds is the "I need this" beat.
2. **"Top moments from this session."** A short, shareable list of the
   fix/mistake/refactor that defined the session.
3. **The session card.** A single screenshot-worthy image — the unit that
   actually spreads on social.

The card is the acquisition channel; the replay is the retention.

## 5. Architecture

```
JSONL transcript ─► parser ─► normalized event stream ┐
                          ├─► usage series (context)   ├─► payload ─► HTML / MD / SVG
                          └─► file evolution (diffs)   ┘        ▲
                                                  heuristic analyzer
```

Decisions:
- **Stdlib-only Python.** Matches "runs anywhere", zero supply-chain surface,
  trivial to audit. The discipline *is* the trust signal.
- **One normalized event model** (`prompt · thinking · text · tool · result ·
  command · system`) so every renderer consumes the same payload.
- **File evolution from the transcript, not the disk.** `Write` gives a
  snapshot, `Edit` gives an `old→new` we diff with `difflib`. A replay is
  therefore portable and reproducible with nothing but the `.jsonl`.
- **Context = `input_tokens + cache_read + cache_creation`** per assistant turn
  — the honest "window in use", straight from API usage.
- **The HTML player is dependency-free vanilla JS** so the output stays a single
  portable file forever.

## 6. Replay engine

Requirements were: millions of events, thousands of sessions, local, smooth,
deterministic, no heavy deps. How we meet them:
- **Linear single-pass parse.** 12 MB / 433 events parses in ~23 ms; 126
  sessions in 0.5 s.
- **Truncation at embed time** (`--max-result-bytes`) keeps a giant session's
  HTML around a few hundred KB.
- **Playback is index-based**, not wall-clock-bound: the timeline is an array;
  seeking is O(1); rendering reveals up to `pos`. Deterministic by construction.
- **Pacing from clamped real gaps** so playback feels authentic but never
  stalls on a 3-hour idle gap.

## 7. Shareable outputs

Interactive HTML (the movie), Markdown summary (the report), SVG card (the
social unit), SVG timeline (the README strip). Every one is designed to be
screenshot-worthy and to carry the wordmark + "watch your AI work".

## 8. Differentiation

- **vs. log/JSON viewers** — they show records; we show *time*.
- **vs. token dashboards** — they answer "how much?"; we answer "how?".
- **vs. raw scrollback** — it's linear and unsearchable; we pair reasoning with
  tools with results and let you jump.

Nobody else turns the transcript into a *recording*. That's the wedge.

## 9. MVP (shipped in v0.1)

Discovery (`list`), parser, the interactive HTML replay (timeline, playback,
speed, scrubber, context curve, tool timeline, key moments, file diffs),
plus summary / card / timeline outputs, intelligence, and a 29-check selftest.
Smallest thing that makes a stranger *get it* in one play.

## 10. Roadmap to V1 and beyond

- **V1:** session diff (two attempts side by side), subagent timelines inlined,
  `gallery` index page, GIF/MP4 export, cross-session `--search`/`--since`.
- **10k★:** the replay becomes the default way people share "look what Claude
  did" — the card is everywhere.
- **25k★:** team mode — replays committed to a repo so onboarding = watching the
  codebase get worked on; CI posts a replay link on every AI-authored PR.
- **50k★:** pattern mining across hundreds of sessions — surfacing your own
  workflow inefficiencies, recommending context to pin, learning what your best
  sessions have in common.

## Non-goals

No cloud, no account, no telemetry, no dependency tree, no LLM call to "explain"
a session (the heuristics are honest and free). Keep it the thing you can read
in one file and trust in one minute.
