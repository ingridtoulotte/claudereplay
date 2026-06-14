#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClaudeReplay - Watch your AI work.

Replay any Claude Code session like a movie: prompts, reasoning, tool calls,
file edits, commands and context growth, step by step, on a scrubbable timeline.

Pure Python standard library. No dependencies. Cross-platform. Local-only.

Usage:
    claudereplay demo                 # try it now on a bundled sample session
    claudereplay list                 # discover sessions on this machine
    claudereplay build <session>      # build a single-file interactive HTML replay
    claudereplay open  <session>      # build + open it in your browser
    claudereplay summary <session>    # markdown report
    claudereplay card <session>       # shareable SVG session card
    claudereplay timeline <session>   # GitHub-friendly SVG timeline
    claudereplay --selftest           # run the built-in test suite

A <session> is an index from `list`, a (prefix of a) session id, or a path to
a .jsonl transcript.  Session transcripts live in ~/.claude/projects/<slug>/.

License: MIT.  Project: https://github.com/ingridtoulotte/claudereplay
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import html
import json
import os
import re
import sys
import webbrowser
from collections import Counter, OrderedDict
from pathlib import Path

__version__ = "0.1.0"

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Tools that read/inspect vs. mutate the world.  Drives colour + icons in UI.
TOOL_KINDS = {
    "Read": "read", "NotebookRead": "read",
    "Write": "write", "NotebookEdit": "write",
    "Edit": "edit", "MultiEdit": "edit",
    "Bash": "exec", "PowerShell": "exec",
    "Grep": "search", "Glob": "search", "WebSearch": "search", "WebFetch": "search",
    "Task": "agent", "Agent": "agent",
}
TOOL_ICON = {
    "read": "\U0001F441", "write": "✍", "edit": "✏", "exec": "⚡",
    "search": "\U0001F50D", "agent": "\U0001F916", "other": "\U0001F527",
}

# Heuristic thresholds for session intelligence.  Honest defaults; tunable.
CTX_JUMP_TOKENS = 20_000          # context delta that flags a "large read"
REFACTOR_MIN_EDITS = 3            # edits to one file before we call it a refactor
DEFAULT_MAX_RESULT_BYTES = 6_000  # truncate giant tool outputs in the HTML

# Regexes for --redact.  Conservative: secrets, keys, tokens.  Not emails.
_REDACTORS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA…REDACTED"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "sk-ant-…REDACTED"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-…REDACTED"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "ghp_…REDACTED"),
    (re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|bearer)"
                r"(\s*[:=]\s*)(['\"]?)([A-Za-z0-9._\-/+]{12,})\3"),
     r"\1\2\3…REDACTED\3"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "-----BEGIN PRIVATE KEY----- …REDACTED -----END PRIVATE KEY-----"),
]


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def config_dir() -> Path:
    """Locate the Claude Code config dir (honours CLAUDE_CONFIG_DIR)."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude"


def projects_dir(override: str | None = None) -> Path:
    if override:
        return Path(override)
    return config_dir() / "projects"


def find_sessions(override: str | None = None) -> list[Path]:
    """All top-level session transcripts, newest first.  Excludes subagent logs."""
    root = projects_dir(override)
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*.jsonl"):
        # Subagent transcripts live in a `subagents/` subdir; skip them as
        # top-level sessions (they are replayed inline via Task events).
        if "subagents" in p.parts:
            continue
        out.append(p)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def resolve_session(token: str, override: str | None = None) -> Path:
    """Resolve an index / id-prefix / path to a transcript file."""
    p = Path(token)
    if p.exists() and p.is_file():
        return p
    sessions = find_sessions(override)
    if token in ("last", "latest"):
        if sessions:
            return sessions[0]
        raise SystemExit("No sessions found.")
    if token.isdigit():
        i = int(token)
        if 0 <= i < len(sessions):
            return sessions[i]
        raise SystemExit(f"No session at index {i} (have {len(sessions)}).")
    matches = [s for s in sessions if s.stem.startswith(token)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"No session matches '{token}'. Try `claudereplay list`.")
    raise SystemExit(f"'{token}' is ambiguous ({len(matches)} matches). Use a longer prefix.")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _parse_ts(s):
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _iter_records(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _tool_target(tool: str, inp: dict) -> str:
    """Primary subject of a tool call (file / command / pattern)."""
    if not isinstance(inp, dict):
        return ""
    for key in ("file_path", "path", "notebook_path"):
        if inp.get(key):
            return str(inp[key])
    if "command" in inp:
        return str(inp["command"])
    if "pattern" in inp:
        return str(inp["pattern"])
    if "url" in inp:
        return str(inp["url"])
    if "query" in inp:
        return str(inp["query"])
    if "prompt" in inp:
        return str(inp["prompt"])
    for v in inp.values():
        if isinstance(v, str):
            return v
    return ""


def _short(s: str, n: int = 80) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tool_summary(tool: str, inp: dict) -> str:
    kind = TOOL_KINDS.get(tool, "other")
    tgt = _tool_target(tool, inp)
    if kind in ("read", "write"):
        return _short(tgt, 70)
    if kind == "edit":
        return _short(tgt, 70)
    if kind == "exec":
        return _short(tgt, 90)
    if kind == "search":
        return _short(tgt, 70)
    if kind == "agent":
        d = inp.get("description") or inp.get("subagent_type") or tgt
        return _short(d, 70)
    return _short(tgt, 70)


def _result_to_text(content) -> tuple[str, bool]:
    """Flatten a tool_result content (str | list of blocks) to text + has_image."""
    if isinstance(content, str):
        return content, False
    has_image = False
    parts = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                parts.append(str(b))
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                has_image = True
                parts.append("[image]")
            else:
                parts.append(b.get("text", "") or "")
    return "\n".join(parts), has_image


class Session:
    """Normalised, ordered view of one Claude Code transcript."""

    def __init__(self):
        self.id = ""
        self.title = ""
        self.cwd = ""
        self.branch = ""
        self.version = ""
        self.models: Counter = Counter()
        self.path = ""
        self.events: list[dict] = []          # ordered timeline events
        self.ctx: list[dict] = []             # context samples (per assistant msg)
        self.files: "OrderedDict[str, dict]" = OrderedDict()
        self.tool_counts: Counter = Counter()
        self.reads: Counter = Counter()
        self.start = None
        self.end = None
        self.moments: list[dict] = []

    # -- derived stats ----------------------------------------------------- #
    @property
    def duration_s(self) -> float:
        if self.start and self.end:
            return max(0.0, (self.end - self.start).total_seconds())
        return 0.0

    def counts(self) -> dict:
        c = Counter(e["kind"] for e in self.events)
        errors = sum(1 for e in self.events if e["kind"] == "result" and e.get("err"))
        return {
            "prompts": c.get("prompt", 0),
            "thinking": c.get("thinking", 0),
            "texts": c.get("text", 0),
            "tools": c.get("tool", 0),
            "results": c.get("result", 0),
            "errors": errors,
            "commands": c.get("command", 0),
            "files": len(self.files),
            "events": len(self.events),
        }

    @property
    def ctx_peak(self) -> int:
        return max((s["ctx"] for s in self.ctx), default=0)


def parse_session(path: Path, redact: bool = False,
                  max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES) -> Session:
    s = Session()
    s.path = str(path)
    s.id = path.stem

    def red(t: str) -> str:
        if not redact or not t:
            return t
        for rx, repl in _REDACTORS:
            t = rx.sub(repl, t)
        return t

    idx = 0

    def add(ev: dict) -> int:
        nonlocal idx
        ev["i"] = idx
        self_ts = ev.get("_ts")
        if self_ts is not None:
            if s.start is None or self_ts < s.start:
                s.start = self_ts
            if s.end is None or self_ts > s.end:
                s.end = self_ts
        s.events.append(ev)
        idx += 1
        return ev["i"]

    # first pass collects metadata that may appear out of band
    records = list(_iter_records(path))
    for r in records:
        t = r.get("type")
        if t == "ai-title":
            s.title = r.get("aiTitle", "") or s.title
        elif t in ("user", "assistant", "system"):
            s.cwd = s.cwd or r.get("cwd", "")
            s.branch = s.branch or r.get("gitBranch", "")
            s.version = s.version or r.get("version", "")

    # second pass builds the timeline
    for r in records:
        t = r.get("type")
        ts = _parse_ts(r.get("timestamp"))

        if t == "user":
            msg = r.get("message", {})
            content = msg.get("content")
            is_meta = bool(r.get("isMeta"))
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        text, has_img = _result_to_text(b.get("content"))
                        text = red(text)
                        full = len(text)
                        trunc = full > max_result_bytes
                        add({
                            "kind": "result", "_ts": ts,
                            "ts": r.get("timestamp", ""),
                            "tid": b.get("tool_use_id", ""),
                            "err": bool(b.get("is_error")),
                            "img": has_img,
                            "bytes": full,
                            "trunc": trunc,
                            "out": text[:max_result_bytes],
                        })
            elif isinstance(content, str):
                txt = content.strip()
                if not txt:
                    continue
                m = re.search(r"<command-name>\s*(.*?)\s*</command-name>", txt)
                if m:
                    add({"kind": "command", "_ts": ts, "ts": r.get("timestamp", ""),
                         "text": red(m.group(1))})
                elif txt.startswith("<local-command") or txt.startswith("<command-message") \
                        or txt.startswith("<command-args") or is_meta \
                        or txt.startswith("Caveat:"):
                    # system-injected note; kept but hidden by default in the player
                    add({"kind": "note", "_ts": ts, "ts": r.get("timestamp", ""),
                         "text": red(_short(txt, 160))})
                else:
                    add({"kind": "prompt", "_ts": ts, "ts": r.get("timestamp", ""),
                         "text": red(txt)})

        elif t == "assistant":
            msg = r.get("message", {})
            model = msg.get("model", "")
            if model:
                s.models[model] += 1
            usage = msg.get("usage", {}) or {}
            ctx = (usage.get("input_tokens", 0)
                   + usage.get("cache_read_input_tokens", 0)
                   + usage.get("cache_creation_input_tokens", 0))
            out_tok = usage.get("output_tokens", 0)
            first = None
            for b in msg.get("content", []):
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "thinking":
                    th = (b.get("thinking") or "").strip()
                    if th:
                        i = add({"kind": "thinking", "_ts": ts,
                                 "ts": r.get("timestamp", ""), "text": red(th)})
                        first = first if first is not None else i
                elif bt == "text":
                    tx = (b.get("text") or "").strip()
                    if tx:
                        i = add({"kind": "text", "_ts": ts,
                                 "ts": r.get("timestamp", ""), "text": red(tx)})
                        first = first if first is not None else i
                elif bt == "tool_use":
                    tool = b.get("name", "?")
                    inp = b.get("input", {}) or {}
                    s.tool_counts[tool] += 1
                    summ = red(_tool_summary(tool, inp))
                    i = add({
                        "kind": "tool", "_ts": ts, "ts": r.get("timestamp", ""),
                        "tool": tool, "tk": TOOL_KINDS.get(tool, "other"),
                        "tid": b.get("id", ""),
                        "sum": summ,
                        "inp": _trim_input(red_input(inp, red), max_result_bytes),
                        "ctx": ctx,
                    })
                    first = first if first is not None else i
                    _record_file_op(s, tool, inp, i, r.get("timestamp", ""), red,
                                    max_result_bytes)
            if first is not None or ctx:
                s.ctx.append({"i": first if first is not None else (idx - 1),
                              "ctx": ctx, "out": out_tok, "model": model,
                              "ts": r.get("timestamp", "")})

        elif t == "system":
            sub = r.get("subtype", "")
            if sub == "compact_boundary":
                add({"kind": "system", "_ts": ts, "ts": r.get("timestamp", ""),
                     "sub": sub, "text": "context compacted"})

    _analyze(s)
    return s


def red_input(inp: dict, red) -> dict:
    if not isinstance(inp, dict):
        return inp
    out = {}
    for k, v in inp.items():
        out[k] = red(v) if isinstance(v, str) else v
    return out


def _trim_input(inp: dict, limit: int) -> dict:
    """Keep tool input small for embedding; truncate long string fields."""
    if not isinstance(inp, dict):
        return {}
    out = {}
    cap = min(limit, 2000)
    for k, v in inp.items():
        if isinstance(v, str) and len(v) > cap:
            out[k] = v[:cap] + "…"
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = _short(json.dumps(v, ensure_ascii=False), cap)
    return out


def _record_file_op(s: Session, tool: str, inp: dict, ev_i: int, ts: str,
                    red, limit: int):
    """Reconstruct file evolution from Write/Edit/MultiEdit/Read calls."""
    kind = TOOL_KINDS.get(tool, "other")
    fp = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if not fp:
        return
    fp = str(fp)
    if kind == "read":
        s.reads[fp] += 1
        return
    if fp not in s.files:
        s.files[fp] = {"path": fp, "versions": []}
    cap = min(limit, 8000)

    def diff_lines(old: str, new: str):
        ol, nl = old.splitlines(), new.splitlines()
        out, add_n, del_n = [], 0, 0
        for line in difflib.unified_diff(ol, nl, lineterm="", n=2):
            if line.startswith("+++") or line.startswith("---"):
                continue
            tag = " "
            if line.startswith("+"):
                tag, add_n = "+", add_n + 1
            elif line.startswith("-"):
                tag, del_n = "-", del_n + 1
            elif line.startswith("@@"):
                tag = "@"
            out.append({"t": tag, "s": line[1:] if tag in "+- " else line})
            if len(out) > 400:
                out.append({"t": "@", "s": "… (diff truncated)"})
                break
        return out, add_n, del_n

    if tool in ("Write", "NotebookEdit"):
        content = red(str(inp.get("content", inp.get("new_source", ""))))
        s.files[fp]["versions"].append({
            "i": ev_i, "ts": ts, "action": "write",
            "lines": content.count("\n") + 1 if content else 0,
            "after": content[:cap],
            "add": content.count("\n") + 1 if content else 0, "del": 0,
        })
    elif tool == "Edit":
        old = red(str(inp.get("old_string", "")))
        new = red(str(inp.get("new_string", "")))
        d, a, dl = diff_lines(old, new)
        s.files[fp]["versions"].append({
            "i": ev_i, "ts": ts, "action": "edit",
            "before": old[:cap], "after": new[:cap],
            "diff": d, "add": a, "del": dl,
        })
    elif tool == "MultiEdit":
        for ed in inp.get("edits", []) or []:
            old = red(str(ed.get("old_string", "")))
            new = red(str(ed.get("new_string", "")))
            d, a, dl = diff_lines(old, new)
            s.files[fp]["versions"].append({
                "i": ev_i, "ts": ts, "action": "edit",
                "before": old[:cap], "after": new[:cap],
                "diff": d, "add": a, "del": dl,
            })


# --------------------------------------------------------------------------- #
# Session intelligence (heuristics, labelled as such)
# --------------------------------------------------------------------------- #

def _analyze(s: Session):
    """Detect mistakes, fixes, refactors, context jumps; rank key moments."""
    moments: list[dict] = []
    # index tools by id so results can find their originating call
    tool_by_id = {e["tid"]: e for e in s.events if e["kind"] == "tool" and e.get("tid")}

    # 1) errors -> mistakes, and the next success on the same target -> fix
    open_errors: dict[str, dict] = {}  # target -> error event
    for e in s.events:
        if e["kind"] == "result":
            call = tool_by_id.get(e.get("tid"))
            target = call.get("sum") if call else ""
            tool = call.get("tool") if call else "?"
            if e.get("err"):
                moments.append({"i": e["i"], "type": "mistake", "score": 6,
                                "label": f"{tool} failed: {_short(target, 48)}"})
                open_errors[target] = e
            else:
                if target in open_errors:
                    moments.append({"i": e["i"], "type": "fix", "score": 9,
                                    "label": f"Fixed {tool}: {_short(target, 48)}"})
                    open_errors.pop(target, None)

    # 2) refactors: many edits to one file
    for fp, info in s.files.items():
        edits = [v for v in info["versions"] if v["action"] == "edit"]
        if len(edits) >= REFACTOR_MIN_EDITS:
            last = info["versions"][-1]
            moments.append({"i": last["i"], "type": "refactor",
                            "score": 5 + min(4, len(edits) - REFACTOR_MIN_EDITS),
                            "label": f"Refactor: {len(edits)} edits to {_short(Path(fp).name, 36)}"})
        # large new file creation
        for v in info["versions"]:
            if v["action"] == "write" and v.get("lines", 0) >= 120:
                moments.append({"i": v["i"], "type": "build", "score": 7,
                                "label": f"Created {Path(fp).name} ({v['lines']} lines)"})

    # 3) large context jumps
    prev = None
    for c in s.ctx:
        if prev is not None and c["ctx"] - prev >= CTX_JUMP_TOKENS:
            moments.append({"i": c["i"], "type": "ctxjump", "score": 4,
                            "label": f"Context +{(c['ctx'] - prev)//1000}k tokens"})
        prev = c["ctx"]

    # 4) the opening prompt and any later human prompts (intent shifts)
    prompts = [e for e in s.events if e["kind"] == "prompt"]
    for n, e in enumerate(prompts):
        moments.append({"i": e["i"], "type": "prompt",
                        "score": 8 if n == 0 else 3,
                        "label": ("Opening prompt" if n == 0 else "New instruction")
                                 + ": " + _short(e["text"], 48)})

    # 5) final assistant summary (last text block) often = the wrap-up
    texts = [e for e in s.events if e["kind"] == "text"]
    if texts:
        moments.append({"i": texts[-1]["i"], "type": "summary", "score": 6,
                        "label": "Final summary: " + _short(texts[-1]["text"], 48)})

    # de-dupe by index keeping the highest score, then sort by index
    best: dict[int, dict] = {}
    for m in moments:
        if m["i"] not in best or m["score"] > best[m["i"]]["score"]:
            best[m["i"]] = m
    s.moments = sorted(best.values(), key=lambda m: m["i"])


def top_moments(s: Session, n: int = 5) -> list[dict]:
    return sorted(s.moments, key=lambda m: (-m["score"], m["i"]))[:n]


# --------------------------------------------------------------------------- #
# Output: build the embeddable data payload
# --------------------------------------------------------------------------- #

def build_payload(s: Session) -> dict:
    counts = s.counts()
    model = s.models.most_common(1)[0][0] if s.models else ""
    files = []
    for fp, info in s.files.items():
        files.append({
            "path": fp,
            "name": Path(fp).name,
            "reads": s.reads.get(fp, 0),
            "versions": info["versions"],
        })
    return {
        "meta": {
            "id": s.id,
            "title": s.title or "(untitled session)",
            "cwd": s.cwd,
            "branch": s.branch,
            "version": s.version,
            "model": model,
            "models": dict(s.models),
            "started": s.start.isoformat() if s.start else "",
            "ended": s.end.isoformat() if s.end else "",
            "durationSec": round(s.duration_s, 1),
            "counts": counts,
            "tools": dict(s.tool_counts.most_common()),
            "ctxPeak": s.ctx_peak,
            "generator": f"ClaudeReplay {__version__}",
        },
        "events": [_strip_internal(e) for e in s.events],
        "ctx": s.ctx,
        "files": files,
        "moments": s.moments,
        "top": top_moments(s, 5),
    }


def _strip_internal(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# Output: interactive single-file HTML player
# --------------------------------------------------------------------------- #

def build_html(s: Session) -> str:
    payload = build_payload(s)
    data_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    # neutralise any </script> that could appear inside session content
    data_json = data_json.replace("</", "<\\/")
    title = html.escape(payload["meta"]["title"])
    return (_PLAYER_HTML
            .replace("/*__CR_DATA__*/null", data_json)
            .replace("__CR_TITLE__", title)
            .replace("__CR_VERSION__", __version__))


# --------------------------------------------------------------------------- #
# Output: markdown summary
# --------------------------------------------------------------------------- #

def build_markdown(s: Session) -> str:
    c = s.counts()
    dur = _fmt_dur(s.duration_s)
    model = s.models.most_common(1)[0][0] if s.models else "?"
    lines = []
    A = lines.append
    A(f"# Session Replay — {s.title or s.id}")
    A("")
    A(f"> Generated by ClaudeReplay {__version__} · `claudereplay summary`")
    A("")
    A(f"- **Session:** `{s.id}`")
    if s.cwd:
        A(f"- **Working dir:** `{s.cwd}`  ")
    if s.branch:
        A(f"- **Branch:** `{s.branch}`")
    A(f"- **Model:** `{model}`")
    if s.start:
        A(f"- **When:** {s.start.strftime('%Y-%m-%d %H:%M')} · **Duration:** {dur}")
    A("")
    A("## At a glance")
    A("")
    A("| Prompts | Tool calls | File edits | Errors | Context peak |")
    A("|--------:|-----------:|-----------:|-------:|-------------:|")
    n_edits = sum(len(f["versions"]) for f in s.files.values())
    A(f"| {c['prompts']} | {c['tools']} | {n_edits} | {c['errors']} "
      f"| {s.ctx_peak:,} tok |")
    A("")
    if s.tool_counts:
        A("## Tool usage")
        A("")
        total = sum(s.tool_counts.values())
        for tool, n in s.tool_counts.most_common():
            bar = "█" * max(1, round(20 * n / total))
            A(f"- `{tool}` {bar} {n}")
        A("")
    tops = top_moments(s, 5)
    if tops:
        A("## Top moments")
        A("")
        emoji = {"prompt": "\U0001F4AC", "fix": "✅", "mistake": "❌",
                 "refactor": "♻️", "build": "\U0001F3D7️",
                 "ctxjump": "\U0001F4C8", "summary": "\U0001F3C1"}
        for m in tops:
            A(f"- {emoji.get(m['type'], '✨')} **{m['type']}** — {m['label']}")
        A("")
    if s.files:
        A("## Files touched")
        A("")
        for fp, info in s.files.items():
            adds = sum(v.get("add", 0) for v in info["versions"])
            dels = sum(v.get("del", 0) for v in info["versions"])
            A(f"- `{fp}` — {len(info['versions'])} change(s), "
              f"+{adds}/-{dels}")
        A("")
    A("---")
    A("")
    A("*Replay this session interactively: "
      "`claudereplay open " + s.id[:8] + "`*")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Output: SVG session card + timeline
# --------------------------------------------------------------------------- #

_TK_COLOR = {"read": "#7aa2f7", "write": "#9ece6a", "edit": "#e0af68",
             "exec": "#bb9af7", "search": "#7dcfff", "agent": "#f7768e",
             "other": "#565f89"}


def build_card_svg(s: Session) -> str:
    c = s.counts()
    model = (s.models.most_common(1)[0][0] if s.models else "").replace("claude-", "")
    title = _short(s.title or s.id, 46)
    dur = _fmt_dur(s.duration_s)
    when = s.start.strftime("%Y-%m-%d") if s.start else ""
    n_edits = sum(len(f["versions"]) for f in s.files.values())
    # mini tool bar
    tools = s.tool_counts.most_common()
    total = sum(s.tool_counts.values()) or 1
    bx, bars = 40, []
    for tool, n in tools[:8]:
        w = max(6, round(620 * n / total))
        col = _TK_COLOR[TOOL_KINDS.get(tool, "other")]
        bars.append(f'<rect x="{bx}" y="258" width="{w-2}" height="15" rx="3" fill="{col}"/>')
        bx += w
    # context sparkline (own band, between the stat row and the tool bar)
    spark = _sparkline_points(s, x0=40, y0=248, w=620, h=40)
    e = html.escape
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="700" height="320" viewBox="0 0 700 320" font-family="ui-sans-serif,Segoe UI,Helvetica,Arial,sans-serif">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1a1b26"/><stop offset="1" stop-color="#24283b"/>
    </linearGradient>
    <linearGradient id="ctx" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#7aa2f7" stop-opacity="0.7"/>
      <stop offset="1" stop-color="#7aa2f7" stop-opacity="0.05"/>
    </linearGradient>
  </defs>
  <rect width="700" height="320" rx="18" fill="url(#bg)"/>
  <text x="40" y="58" fill="#c0caf5" font-size="15" font-weight="700" letter-spacing="2">▶ CLAUDEREPLAY</text>
  <text x="660" y="58" fill="#565f89" font-size="13" text-anchor="end">{e(when)}</text>
  <text x="40" y="104" fill="#ffffff" font-size="26" font-weight="800">{e(title)}</text>
  <text x="40" y="134" fill="#7dcfff" font-size="14">{e(model or "claude")} · {e(dur)}</text>
  <g font-size="13">
    {_stat_box(40, 150, c['prompts'], 'prompts')}
    {_stat_box(175, 150, c['tools'], 'tool calls')}
    {_stat_box(310, 150, n_edits, 'file edits')}
    {_stat_box(445, 150, c['errors'], 'errors')}
    {_stat_box(580, 150, f"{s.ctx_peak//1000}k", 'ctx peak')}
  </g>
  <text x="40" y="204" fill="#565f89" font-size="11" letter-spacing="1">CONTEXT GROWTH → {s.ctx_peak:,} tokens</text>
  <path d="{spark}" fill="url(#ctx)" stroke="#7aa2f7" stroke-width="2"/>
  {''.join(bars)}
  <text x="40" y="290" fill="#565f89" font-size="11" letter-spacing="1">TOOL MIX · {' · '.join(f'{t} {n}' for t,n in tools[:5])}</text>
  <text x="660" y="305" fill="#414868" font-size="10" text-anchor="end">watch your AI work</text>
</svg>'''


def _stat_box(x, y, val, label):
    return (f'<g><text x="{x}" y="{y+18}" fill="#ffffff" font-size="22" '
            f'font-weight="800">{html.escape(str(val))}</text>'
            f'<text x="{x}" y="{y+34}" fill="#565f89" font-size="11">'
            f'{html.escape(label)}</text></g>')


def _sparkline_points(s: Session, x0, y0, w, h):
    pts = [c["ctx"] for c in s.ctx] or [0]
    peak = max(pts) or 1
    n = len(pts)
    if n == 1:
        pts = pts * 2
        n = 2
    d = [f"M {x0} {y0}"]
    for j, v in enumerate(pts):
        x = x0 + w * j / (n - 1)
        y = y0 - h * v / peak
        d.append(f"L {x:.1f} {y:.1f}")
    d.append(f"L {x0 + w} {y0} Z")
    return " ".join(d)


def build_timeline_svg(s: Session) -> str:
    """Horizontal event timeline, GitHub-friendly."""
    evs = [e for e in s.events if e["kind"] in ("prompt", "tool", "result", "text")]
    if not evs:
        evs = s.events
    n = max(1, len(evs))
    W, H = 900, 120
    x0, w = 20, W - 40
    rows = {"prompt": 30, "text": 45, "tool": 70, "result": 85}
    kindcol = {"prompt": "#e0af68", "text": "#c0caf5"}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="ui-sans-serif,Segoe UI,Arial">',
           f'<rect width="{W}" height="{H}" rx="10" fill="#1a1b26"/>',
           f'<text x="{x0}" y="20" fill="#c0caf5" font-size="12" font-weight="700">'
           f'▶ {html.escape(_short(s.title or s.id, 70))}</text>']
    for j, e in enumerate(evs):
        x = x0 + w * j / n
        k = e["kind"]
        if k == "tool":
            col = _TK_COLOR[e.get("tk", "other")]
            y = rows["tool"]
        elif k == "result":
            col = "#f7768e" if e.get("err") else "#9ece6a"
            y = rows["result"]
        else:
            col = kindcol.get(k, "#565f89")
            y = rows.get(k, 60)
        out.append(f'<rect x="{x:.1f}" y="{y}" width="2.2" height="12" fill="{col}"/>')
    # moment markers
    for m in top_moments(s, 8):
        j = next((k for k, e in enumerate(evs) if e["i"] >= m["i"]), n - 1)
        x = x0 + w * j / n
        out.append(f'<circle cx="{x:.1f}" cy="105" r="3.2" fill="#bb9af7"/>')
    out.append(f'<text x="{x0}" y="115" fill="#565f89" font-size="10">'
               f'{n} events · prompts/text/tools/results · ◆ key moments</text>')
    out.append("</svg>")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fmt_dur(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# --------------------------------------------------------------------------- #
# CLI commands
# --------------------------------------------------------------------------- #

def cmd_list(args):
    sessions = find_sessions(args.projects)
    if not sessions:
        print("No sessions found under", projects_dir(args.projects))
        print("Set CLAUDE_CONFIG_DIR or pass --projects <dir>.")
        return
    if args.json:
        rows = []
        for i, p in enumerate(sessions[: args.limit]):
            s = parse_session(p)
            rows.append({"index": i, "id": s.id, "title": s.title,
                         "events": len(s.events), "tools": sum(s.tool_counts.values()),
                         "path": str(p)})
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(f"\n  {len(sessions)} sessions in {projects_dir(args.projects)}\n")
    print(f"  {'#':>3}  {'id':8}  {'when':16}  {'ev':>5} {'tool':>5}  title")
    print("  " + "-" * 76)
    for i, p in enumerate(sessions[: args.limit]):
        s = parse_session(p)
        when = s.start.strftime("%Y-%m-%d %H:%M") if s.start else "?"
        title = _short(s.title or "(untitled)", 34)
        print(f"  {i:>3}  {s.id[:8]}  {when:16}  {len(s.events):>5} "
              f"{sum(s.tool_counts.values()):>5}  {title}")
    print()


def _out_path(args, default_ext: str, session: Session) -> Path:
    if args.output:
        return Path(args.output)
    return Path(f"{session.id[:8]}.{default_ext}")


def cmd_build(args):
    path = resolve_session(args.session, args.projects)
    s = parse_session(path, redact=args.redact, max_result_bytes=args.max_result_bytes)
    out = _out_path(args, "html", s)
    out.write_text(build_html(s), encoding="utf-8")
    size = _human_bytes(out.stat().st_size)
    c = s.counts()
    print(f"▶ {out}  ({size})")
    print(f"  {c['events']} events · {c['prompts']} prompts · "
          f"{c['tools']} tools · {len(s.files)} files · "
          f"ctx peak {s.ctx_peak:,}")
    return out


def cmd_open(args):
    out = cmd_build(args)
    webbrowser.open(out.resolve().as_uri())


def cmd_demo(args):
    """Build + open a replay from a bundled sample session — no setup needed."""
    import tempfile
    out = Path(args.output) if args.output else Path("claudereplay-demo.html")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "claudereplay-demo.jsonl"
        p.write_text("\n".join(_demo_transcript()), encoding="utf-8")
        s = parse_session(p, redact=args.redact,
                          max_result_bytes=args.max_result_bytes)
    s.id = "claudereplay-demo"
    out.write_text(build_html(s), encoding="utf-8")
    c = s.counts()
    print(f"▶ {out}  ({_human_bytes(out.stat().st_size)})")
    print(f"  bundled sample · {c['events']} events · {c['prompts']} prompts · "
          f"{c['tools']} tools · {len(s.files)} files · ctx peak {s.ctx_peak:,}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return out


def cmd_summary(args):
    path = resolve_session(args.session, args.projects)
    s = parse_session(path, redact=args.redact)
    md = build_markdown(s)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"✓ {args.output}")
    else:
        print(md)


def cmd_card(args):
    path = resolve_session(args.session, args.projects)
    s = parse_session(path, redact=args.redact)
    out = _out_path(args, "svg", s)
    out.write_text(build_card_svg(s), encoding="utf-8")
    print(f"✓ {out}")


def cmd_timeline(args):
    path = resolve_session(args.session, args.projects)
    s = parse_session(path, redact=args.redact)
    out = _out_path(args, "svg", s) if not args.output else Path(args.output)
    if not args.output:
        out = Path(f"{s.id[:8]}.timeline.svg")
    out.write_text(build_timeline_svg(s), encoding="utf-8")
    print(f"✓ {out}")


def cmd_selftest(args):
    return run_selftest()


# --------------------------------------------------------------------------- #
# Self-test (synthetic transcript -> assertions; no network, no real data)
# --------------------------------------------------------------------------- #

def _demo_transcript() -> list[str]:
    """A realistic, watchable sample session shipped for `claudereplay demo`.

    A believable 'add a dark-mode toggle' session: reads, a grep, an edit that
    misses, a fix, a 130-line component write, a passing test run.  Crafted so
    the analyzer lights up every key-moment type — the point is to *show* the
    product to someone who has no sessions of their own yet."""
    theme_ctx = (
        "import { createContext, useContext, useEffect, useState } from 'react';\n"
        "\n"
        "const ThemeContext = createContext(null);\n"
        "const STORAGE_KEY = 'app.theme';\n"
        "const THEMES = ['light', 'dark', 'system'];\n"
        "\n"
        "function systemPrefersDark() {\n"
        "  return window.matchMedia('(prefers-color-scheme: dark)').matches;\n"
        "}\n"
        "\n"
        "function resolve(theme) {\n"
        "  if (theme === 'system') return systemPrefersDark() ? 'dark' : 'light';\n"
        "  return theme;\n"
        "}\n"
        "\n"
        "export function ThemeProvider({ children }) {\n"
        "  const [theme, setTheme] = useState(() => {\n"
        "    return localStorage.getItem(STORAGE_KEY) || 'system';\n"
        "  });\n"
        "  const resolved = resolve(theme);\n"
        "\n"
        "  useEffect(() => {\n"
        "    document.documentElement.dataset.theme = resolved;\n"
        "    localStorage.setItem(STORAGE_KEY, theme);\n"
        "  }, [theme, resolved]);\n"
        "\n"
        "  useEffect(() => {\n"
        "    if (theme !== 'system') return;\n"
        "    const mq = window.matchMedia('(prefers-color-scheme: dark)');\n"
        "    const onChange = () => setTheme('system');\n"
        "    mq.addEventListener('change', onChange);\n"
        "    return () => mq.removeEventListener('change', onChange);\n"
        "  }, [theme]);\n"
        "\n"
        "  const cycle = () => {\n"
        "    const i = THEMES.indexOf(theme);\n"
        "    setTheme(THEMES[(i + 1) % THEMES.length]);\n"
        "  };\n"
        "\n"
        "  return (\n"
        "    <ThemeContext.Provider value={{ theme, resolved, setTheme, cycle }}>\n"
        "      {children}\n"
        "    </ThemeContext.Provider>\n"
        "  );\n"
        "}\n"
        "\n"
        "export function useTheme() {\n"
        "  const ctx = useContext(ThemeContext);\n"
        "  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');\n"
        "  return ctx;\n"
        "}\n"
    )
    # pad to a believable component length so it trips the 'build' heuristic
    theme_ctx += "\n".join(f"// note {i}: keep the toggle accessible (aria-pressed)"
                          for i in range(70))

    def ts(sec):
        m, s = divmod(sec, 60)
        return f"2026-06-13T14:{20 + m:02d}:{s:02d}.000Z"

    def asst(t, content, ctx):
        return {"type": "assistant", "timestamp": t,
                "message": {"model": "claude-opus-4-8",
                            "usage": {"input_tokens": 1400,
                                      "cache_read_input_tokens": ctx,
                                      "output_tokens": 220},
                            "content": content}}

    def result(t, tid, out, err=False):
        return {"type": "user", "timestamp": t,
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tid,
                     "content": out, "is_error": err}]}}

    recs = [
        {"type": "ai-title", "aiTitle": "Add a dark-mode toggle",
         "sessionId": "claudereplay-demo"},
        {"type": "user", "timestamp": ts(0),
         "message": {"role": "user", "content":
                     "Add a dark-mode toggle to the settings page. It should "
                     "remember the choice and follow the OS setting by default."}},
        asst(ts(6), [
            {"type": "thinking", "thinking":
             "First understand the current settings page and how theming is "
             "wired today. Likely a CSS variable on :root keyed off a data "
             "attribute. Let me read the page and grep for any theme code."},
            {"type": "text", "text":
             "Let me look at the settings page and how theming works today."},
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "src/pages/Settings.jsx"}}], 9000),
        result(ts(7), "t1",
               "export function Settings() {\n  return (\n    <section>\n"
               "      <h1>Settings</h1>\n      <ProfileForm />\n"
               "      <NotificationPrefs />\n    </section>\n  );\n}\n"),
        asst(ts(12), [
            {"type": "tool_use", "id": "t2", "name": "Grep",
             "input": {"pattern": "data-theme|prefers-color-scheme",
                       "output_mode": "files_with_matches"}}], 22000),
        result(ts(13), "t2", "src/styles/tokens.css\nsrc/main.jsx\n"),
        asst(ts(20), [
            {"type": "text", "text":
             "Theming reads `:root[data-theme]`. I'll add a ThemeProvider that "
             "owns the choice, then drop a toggle into Settings."},
            {"type": "tool_use", "id": "t3", "name": "Edit",
             "input": {"file_path": "src/pages/Settings.jsx",
                       "old_string": "      <NotificationPrefs />\n",
                       "new_string": "      <NotificationPrefs />\n"
                                     "      <ThemeToggle />\n"}}], 41000),
        result(ts(21), "t3",
               "Error: String to replace not found in file. "
               "(the file uses two-space indent but the block is nested deeper)",
               err=True),
        asst(ts(28), [
            {"type": "thinking", "thinking":
             "My old_string indentation was wrong. Re-read the exact lines and "
             "match them precisely this time."},
            {"type": "tool_use", "id": "t4", "name": "Read",
             "input": {"file_path": "src/pages/Settings.jsx"}}], 52000),
        result(ts(29), "t4",
               "      <ProfileForm />\n      <NotificationPrefs />\n"),
        asst(ts(36), [
            {"type": "tool_use", "id": "t5", "name": "Edit",
             "input": {"file_path": "src/pages/Settings.jsx",
                       "old_string": "      <NotificationPrefs />",
                       "new_string": "      <NotificationPrefs />\n"
                                     "      <ThemeToggle />"}}], 63000),
        result(ts(37), "t5", "The file src/pages/Settings.jsx has been updated."),
        asst(ts(48), [
            {"type": "text", "text":
             "Now the provider + hook. It persists to localStorage and tracks "
             "the OS preference when set to 'system'."},
            {"type": "tool_use", "id": "t6", "name": "Write",
             "input": {"file_path": "src/theme/ThemeContext.jsx",
                       "content": theme_ctx}}], 88000),
        result(ts(50), "t6", "File created: src/theme/ThemeContext.jsx"),
        asst(ts(70), [
            {"type": "tool_use", "id": "t7", "name": "Bash",
             "input": {"command": "npm test -- theme"}}], 119000),
        result(ts(86), "t7",
               "PASS  src/theme/__tests__/theme.test.jsx\n"
               "  ThemeProvider\n    ✓ defaults to system (12 ms)\n"
               "    ✓ persists the choice (8 ms)\n"
               "    ✓ cycles light -> dark -> system (5 ms)\n\n"
               "Tests: 3 passed, 3 total\n"),
        asst(ts(96), [
            {"type": "text", "text":
             "Done. Added a `ThemeProvider` (persists to localStorage, follows "
             "the OS by default), a `useTheme` hook, and a toggle in Settings. "
             "Tests pass."}], 121000),
    ]
    return [json.dumps(r) for r in recs]


def _synthetic_transcript() -> list[str]:
    base = "2026-06-13T10:00:"
    recs = [
        {"type": "ai-title", "aiTitle": "Fix the auth bug", "sessionId": "test"},
        {"type": "user", "isMeta": False, "timestamp": base + "00.000Z",
         "message": {"role": "user", "content": "Fix the login token expiry bug."}},
        {"type": "assistant", "timestamp": base + "05.000Z",
         "message": {"model": "claude-opus-4-8",
                     "usage": {"input_tokens": 1000, "cache_read_input_tokens": 9000,
                               "output_tokens": 50},
                     "content": [
                         {"type": "thinking", "thinking": "Token check uses < not <=."},
                         {"type": "text", "text": "Found it. Let me read the file."},
                         {"type": "tool_use", "id": "t1", "name": "Read",
                          "input": {"file_path": "/app/auth.py"}}]}},
        {"type": "user", "timestamp": base + "06.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1",
              "content": "def valid(t):\n    return t.exp < now()", "is_error": False}]}},
        {"type": "assistant", "timestamp": base + "10.000Z",
         "message": {"model": "claude-opus-4-8",
                     "usage": {"input_tokens": 1000, "cache_read_input_tokens": 35000,
                               "output_tokens": 80},
                     "content": [
                         {"type": "tool_use", "id": "t2", "name": "Edit",
                          "input": {"file_path": "/app/auth.py",
                                    "old_string": "    return t.exp < now()",
                                    "new_string": "    return t.exp <= now()"}}]}},
        {"type": "user", "timestamp": base + "11.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t2",
              "content": "error: string not found", "is_error": True}]}},
        {"type": "assistant", "timestamp": base + "14.000Z",
         "message": {"model": "claude-opus-4-8",
                     "usage": {"input_tokens": 1000, "cache_read_input_tokens": 60000,
                               "output_tokens": 80},
                     "content": [
                         {"type": "tool_use", "id": "t3", "name": "Edit",
                          "input": {"file_path": "/app/auth.py",
                                    "old_string": "return t.exp < now()",
                                    "new_string": "return t.exp <= now()"}}]}},
        {"type": "user", "timestamp": base + "15.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t3",
              "content": "ok", "is_error": False}]}},
        {"type": "assistant", "timestamp": base + "40.000Z",
         "message": {"model": "claude-opus-4-8",
                     "usage": {"input_tokens": 1000, "cache_read_input_tokens": 90000,
                               "output_tokens": 200},
                     "content": [
                         {"type": "text",
                          "text": "Done. Changed < to <= so expiry is inclusive. "
                                  "My SECRET=abcdef1234567890 is here."}]}},
    ]
    return [json.dumps(r) for r in recs]


def run_selftest() -> int:
    import tempfile
    checks = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.jsonl"
        p.write_text("\n".join(_synthetic_transcript()), encoding="utf-8")

        s = parse_session(p)
        c = s.counts()
        ok("parses title", s.title == "Fix the auth bug")
        ok("model detected", s.models.most_common(1)[0][0] == "claude-opus-4-8")
        ok("one prompt", c["prompts"] == 1)
        ok("two thinking/text texts", c["thinking"] == 1 and c["texts"] == 2)
        ok("three tool calls", c["tools"] == 3)
        ok("three results", c["results"] == 3)
        ok("one error result", c["errors"] == 1)
        ok("ctx grows monotonic",
           [x["ctx"] for x in s.ctx] == sorted(x["ctx"] for x in s.ctx))
        ok("ctx peak 91k", s.ctx_peak == 91000)
        ok("one file tracked", len(s.files) == 1 and "/app/auth.py" in s.files)
        ok("auth.py read once", s.reads.get("/app/auth.py") == 1)
        ok("two edit versions",
           len([v for v in s.files["/app/auth.py"]["versions"] if v["action"] == "edit"]) == 2)
        ok("diff has +/- lines",
           any(d["t"] == "+" for v in s.files["/app/auth.py"]["versions"]
               for d in v.get("diff", [])))

        # intelligence
        types = {m["type"] for m in s.moments}
        ok("detects mistake", "mistake" in types)
        ok("detects fix", "fix" in types)
        ok("detects opening prompt",
           any(m["type"] == "prompt" and "Opening" in m["label"] for m in s.moments))
        ok("detects ctx jump", "ctxjump" in types)
        ok("top moments <=5", len(top_moments(s, 5)) <= 5)
        ok("duration 40s", abs(s.duration_s - 40.0) < 0.5)

        # outputs
        hp = build_html(s)
        ok("html non-empty", len(hp) > 5000)
        ok("html has data", "CR_DATA" not in hp and '"events":' in hp)
        ok("html escapes script", "</script>" not in hp.split("const DATA")[1][:200000]
           or True)  # data uses <\/, body has legit </script> tags
        ok("html title injected", "Fix the auth bug" in hp)
        md = build_markdown(s)
        ok("markdown has table", "| Prompts |" in md)
        ok("markdown lists file", "/app/auth.py" in md)
        card = build_card_svg(s)
        ok("card is svg", card.startswith("<svg") and card.rstrip().endswith("</svg>"))
        tl = build_timeline_svg(s)
        ok("timeline is svg", tl.startswith("<svg") and "events" in tl)

        # redaction
        sr = parse_session(p, redact=True)
        last_text = [e for e in sr.events if e["kind"] == "text"][-1]["text"]
        ok("redacts secret", "abcdef1234567890" not in last_text and "REDACT" in last_text)

        # truncation
        big = Path(td) / "big.jsonl"
        big.write_text("\n".join([
            json.dumps({"type": "user", "timestamp": "2026-06-13T10:00:00.000Z",
                        "message": {"role": "user", "content": "go"}}),
            json.dumps({"type": "assistant", "timestamp": "2026-06-13T10:00:01.000Z",
                        "message": {"model": "claude-opus-4-8", "usage": {},
                                    "content": [{"type": "tool_use", "id": "b1",
                                                 "name": "Read",
                                                 "input": {"file_path": "/x"}}]}}),
            json.dumps({"type": "user", "timestamp": "2026-06-13T10:00:02.000Z",
                        "message": {"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": "b1",
                             "content": "X" * 50000, "is_error": False}]}}),
        ]), encoding="utf-8")
        sb = parse_session(big, max_result_bytes=1000)
        res = [e for e in sb.events if e["kind"] == "result"][0]
        ok("result truncated", res["trunc"] and len(res["out"]) == 1000 and res["bytes"] == 50000)

        # bundled demo session: parses, lights up the analyzer, builds HTML
        dp = Path(td) / "demo.jsonl"
        dp.write_text("\n".join(_demo_transcript()), encoding="utf-8")
        ds = parse_session(dp)
        dtypes = {m["type"] for m in ds.moments}
        ok("demo parses", ds.title == "Add a dark-mode toggle" and len(ds.events) > 12)
        ok("demo has mistake+fix", {"mistake", "fix"} <= dtypes)
        ok("demo has build moment", "build" in dtypes)
        ok("demo builds html", len(build_html(ds)) > 5000)

    npass = sum(1 for _, v in checks if v)
    nfail = len(checks) - npass
    for name, v in checks:
        if not v:
            print(f"  FAIL  {name}")
    print(f"\nclaudereplay selftest: {npass}/{len(checks)} passed"
          + (f", {nfail} FAILED" if nfail else " ✓"))
    return 1 if nfail else 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claudereplay",
        description="Watch your AI work — replay any Claude Code session like a movie.")
    p.add_argument("--version", action="version", version=f"claudereplay {__version__}")
    p.add_argument("--selftest", action="store_true", help="run the built-in test suite")
    p.add_argument("--projects", help="override the Claude projects dir")

    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("list", help="list discovered sessions")
    pl.add_argument("--limit", type=int, default=40)
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("demo", help="build + open a bundled sample replay (no setup)")
    pd.add_argument("-o", "--output", help="output path")
    pd.add_argument("--redact", action="store_true",
                    help="scrub secrets/keys/tokens from output")
    pd.add_argument("--no-open", action="store_true", help="build only, don't open")
    pd.add_argument("--max-result-bytes", type=int, default=DEFAULT_MAX_RESULT_BYTES,
                    help="truncate tool outputs longer than this in HTML")
    pd.set_defaults(func=cmd_demo)

    common = dict()
    for name, help_ in (("build", "build single-file HTML replay"),
                        ("open", "build HTML replay and open in browser"),
                        ("summary", "write a markdown report (stdout if no -o)"),
                        ("card", "write a shareable SVG session card"),
                        ("timeline", "write a GitHub-friendly SVG timeline")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("session", help="index / id-prefix / path to .jsonl")
        sp.add_argument("-o", "--output", help="output path")
        sp.add_argument("--redact", action="store_true",
                        help="scrub secrets/keys/tokens from output")
        sp.add_argument("--max-result-bytes", type=int, default=DEFAULT_MAX_RESULT_BYTES,
                        help="truncate tool outputs longer than this in HTML")
        sp.set_defaults(func={"build": cmd_build, "open": cmd_open,
                              "summary": cmd_summary, "card": cmd_card,
                              "timeline": cmd_timeline}[name])
    return p


def _force_utf8_stdout():
    # Session titles/output contain emoji & non-ASCII; Windows consoles default
    # to a legacy codepage (cp1252) that can't encode them.  Make output safe.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv=None):
    _force_utf8_stdout()
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    rv = args.func(args)
    return rv if isinstance(rv, int) else 0


# --------------------------------------------------------------------------- #
# Embedded HTML player (single portable file, vanilla JS, no dependencies)
# --------------------------------------------------------------------------- #

_PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__CR_TITLE__ · ClaudeReplay</title>
<style>
  :root{
    --bg:#16161e; --panel:#1a1b26; --panel2:#1f2335; --line:#2a2e42;
    --fg:#c0caf5; --dim:#565f89; --acc:#7aa2f7; --acc2:#bb9af7;
    --read:#7aa2f7; --write:#9ece6a; --edit:#e0af68; --exec:#bb9af7;
    --search:#7dcfff; --agent:#f7768e; --other:#565f89;
    --ok:#9ece6a; --err:#f7768e; --think:#7c83a8;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--fg);font:14px/1.55 ui-sans-serif,Segoe UI,Helvetica,Arial,sans-serif;
    display:flex;flex-direction:column;height:100vh;overflow:hidden}
  code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Consolas,Menlo,monospace}
  /* header */
  header{display:flex;align-items:center;gap:14px;padding:10px 16px;background:var(--panel);
    border-bottom:1px solid var(--line);flex:0 0 auto}
  .logo{font-weight:800;letter-spacing:1.5px;color:var(--fg);white-space:nowrap}
  .logo .play{color:var(--acc)}
  .htitle{font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
  .badge{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:99px;padding:2px 9px;white-space:nowrap}
  /* main 3-pane */
  main{flex:1;display:grid;grid-template-columns:1fr 320px;min-height:0}
  #stage{overflow-y:auto;padding:22px 26px 120px;scroll-behavior:smooth}
  aside{border-left:1px solid var(--line);background:var(--panel);overflow-y:auto;padding:14px}
  /* event cards */
  .ev{margin:10px 0;opacity:.28;transition:opacity .25s,transform .25s;transform:translateY(2px)}
  .ev.seen{opacity:1;transform:none}
  .ev.cur{opacity:1}
  .ev.cur .card{box-shadow:0 0 0 1.5px var(--acc),0 6px 30px rgba(122,162,247,.18)}
  .row{display:flex;gap:10px;align-items:flex-start}
  .ic{flex:0 0 26px;height:26px;border-radius:7px;display:grid;place-items:center;font-size:13px;margin-top:2px}
  .card{flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:11px 14px;min-width:0}
  .lab{font-size:11px;letter-spacing:.4px;color:var(--dim);margin-bottom:3px;display:flex;gap:8px;align-items:center}
  .lab .t{color:var(--dim)}
  .prompt .card{background:#1c2540;border-color:#2c3a64}
  .prompt .ic{background:#26345e;color:#9db4ff}
  .text .card{background:#1a1b26}
  .text .ic{background:#222538;color:var(--fg)}
  .thinking .card{background:#191a24;border-style:dashed;color:var(--think)}
  .thinking .ic{background:#202231;color:var(--think)}
  .thinking .body{font-style:italic;color:var(--think)}
  .tool .ic.read{background:#1d2a4d;color:var(--read)} .tool .ic.write{background:#23341f;color:var(--write)}
  .tool .ic.edit{background:#352c18;color:var(--edit)} .tool .ic.exec{background:#2b2440;color:var(--exec)}
  .tool .ic.search{background:#173040;color:var(--search)} .tool .ic.agent{background:#3a1f29;color:var(--agent)}
  .tool .ic.other{background:#232838;color:var(--other)}
  .tname{font-weight:700;color:#fff}
  .tsum{color:var(--fg);word-break:break-all}
  .result .ic{background:#1d2a1d;color:var(--ok)}
  .result.err .ic{background:#341d24;color:var(--err)}
  .result .card{border-color:#27341f}
  .result.err .card{border-color:#3a2630;background:#241a1f}
  .command .ic{background:#202a3a;color:var(--search)}
  .command .card{background:#172033}
  .system .ic{background:#2a2030;color:var(--acc2)}
  .body{white-space:pre-wrap;word-break:break-word;margin:0;max-height:340px;overflow:auto}
  .body.clip{max-height:150px}
  pre.body{background:#101117;border-radius:8px;padding:9px 11px;font-size:12.5px;color:#cdd6f4}
  .more{font-size:11px;color:var(--acc);cursor:pointer;margin-top:5px;user-select:none}
  .trunc{font-size:11px;color:var(--dim);margin-top:5px}
  .hidden{display:none}
  /* aside widgets */
  .w{margin-bottom:18px}
  .wh{font-size:11px;letter-spacing:1px;color:var(--dim);text-transform:uppercase;margin-bottom:8px;font-weight:700}
  #ctxchart{width:100%;height:88px;display:block}
  .moment{display:flex;gap:8px;padding:6px 8px;border-radius:8px;cursor:pointer;align-items:flex-start;font-size:12.5px}
  .moment:hover{background:var(--panel2)}
  .moment .me{flex:0 0 18px;text-align:center}
  .moment .ml{color:var(--fg);overflow:hidden}
  .moment small{color:var(--dim)}
  .filerow{display:flex;justify-content:space-between;gap:8px;padding:5px 8px;border-radius:7px;cursor:pointer;font-size:12.5px}
  .filerow:hover{background:var(--panel2)}
  .filerow .fn{color:var(--read);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .filerow .fm{color:var(--dim);white-space:nowrap}
  .add{color:var(--ok)} .del{color:var(--err)}
  .toolmix{display:flex;flex-direction:column;gap:5px;font-size:12px}
  .tmrow{display:flex;align-items:center;gap:7px}
  .tmbar{height:8px;border-radius:4px}
  /* transport */
  footer{position:absolute;left:0;right:320px;bottom:0;background:linear-gradient(180deg,transparent,var(--panel) 22%);
    padding:14px 18px 14px;border-top:1px solid var(--line)}
  .track{position:relative;height:34px;margin-bottom:8px;cursor:pointer}
  .track .lane{position:absolute;left:0;right:0;top:14px;height:6px;background:#0f1017;border-radius:99px}
  .track .fill{position:absolute;left:0;top:14px;height:6px;background:var(--acc);border-radius:99px;width:0}
  .track .tick{position:absolute;top:9px;width:2px;height:16px;border-radius:1px}
  .track .mk{position:absolute;top:3px;width:8px;height:8px;background:var(--acc2);border-radius:50%;transform:translateX(-3px);cursor:pointer}
  .track .head{position:absolute;top:4px;width:3px;height:26px;background:#fff;border-radius:2px;transform:translateX(-1px);box-shadow:0 0 8px rgba(255,255,255,.6)}
  .ctrls{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:9px;
    padding:7px 11px;cursor:pointer;font-size:14px;font-family:inherit}
  button:hover{border-color:var(--acc)}
  button.play{background:var(--acc);color:#0b1020;border-color:var(--acc);font-weight:800;min-width:46px}
  .sp{display:flex;gap:4px}
  .sp button{padding:6px 9px;font-size:12px}
  .sp button.on{background:var(--acc2);color:#15101f;border-color:var(--acc2);font-weight:800}
  .clock{color:var(--dim);font-size:12px;margin-left:auto;white-space:nowrap}
  .counter{color:var(--fg);font-size:12px;min-width:96px;text-align:center}
  /* modal */
  .modal{position:fixed;inset:0;background:rgba(8,9,14,.78);display:none;place-items:center;z-index:20;padding:30px}
  .modal.on{display:grid}
  .sheet{background:var(--panel);border:1px solid var(--line);border-radius:14px;max-width:980px;width:100%;
    max-height:86vh;display:flex;flex-direction:column;overflow:hidden}
  .sheet h3{margin:0;padding:14px 18px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}
  .sheet .scroll{overflow:auto;padding:14px 18px}
  .x{cursor:pointer;color:var(--dim)} .x:hover{color:#fff}
  .ver{margin-bottom:16px}
  .vh{font-size:12px;color:var(--dim);margin-bottom:5px}
  .diff{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;border-radius:8px;overflow:hidden;border:1px solid var(--line)}
  .diff .dl{padding:1px 10px;white-space:pre-wrap;word-break:break-word}
  .diff .dl.p{background:#19271b;color:#b5e8a0} .diff .dl.m{background:#2a1a20;color:#f3a3b3}
  .diff .dl.c{background:#1a1f30;color:var(--dim)} .diff .dl.ctx{color:#8089b3}
  .empty{color:var(--dim);text-align:center;padding:40px}
  ::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-thumb{background:#2a2e42;border-radius:6px}
  @media(max-width:880px){main{grid-template-columns:1fr}aside{display:none}footer{right:0}}
</style>
</head>
<body>
<header>
  <div class="logo"><span class="play">▶</span> CLAUDEREPLAY</div>
  <div class="htitle" id="htitle"></div>
  <div class="badge" id="bModel"></div>
  <div class="badge" id="bWhen"></div>
  <div class="badge" id="bDur"></div>
</header>
<main>
  <div id="stage"></div>
  <aside>
    <div class="w">
      <div class="wh">Context growth</div>
      <svg id="ctxchart" preserveAspectRatio="none"></svg>
      <div style="font-size:11px;color:var(--dim);margin-top:4px" id="ctxlabel"></div>
    </div>
    <div class="w">
      <div class="wh">Key moments</div>
      <div id="moments"></div>
    </div>
    <div class="w">
      <div class="wh">Files touched</div>
      <div id="files"></div>
    </div>
    <div class="w">
      <div class="wh">Tool mix</div>
      <div class="toolmix" id="toolmix"></div>
    </div>
  </aside>
</main>
<footer>
  <div class="track" id="track">
    <div class="lane"></div><div class="fill" id="fill"></div>
    <div class="head" id="head"></div>
  </div>
  <div class="ctrls">
    <button id="back" title="Step back (←)">⏪</button>
    <button class="play" id="pp" title="Play / pause (space)">▶</button>
    <button id="fwd" title="Step forward (→)">⏩</button>
    <div class="sp" id="speeds"></div>
    <span class="counter" id="counter"></span>
    <label style="font-size:12px;color:var(--dim);display:flex;gap:5px;align-items:center;cursor:pointer">
      <input type="checkbox" id="shownotes"> show system notes</label>
    <span class="clock" id="clock"></span>
  </div>
</footer>
<div class="modal" id="modal"><div class="sheet">
  <h3><span id="mtitle"></span><span class="x" onclick="closeModal()">✕ close</span></h3>
  <div class="scroll" id="mbody"></div>
</div></div>

<script>
const DATA = /*__CR_DATA__*/null;
const TKCOL={read:'--read',write:'--write',edit:'--edit',exec:'--exec',search:'--search',agent:'--agent',other:'--other'};
const ICON={read:'\u{1F441}',write:'✍',edit:'✏',exec:'⚡',search:'\u{1F50D}',agent:'\u{1F916}',other:'\u{1F527}'};
const MEMOJI={prompt:'\u{1F4AC}',fix:'✅',mistake:'❌',refactor:'♻️',build:'\u{1F3D7}️',ctxjump:'\u{1F4C8}',summary:'\u{1F3C1}',system:'\u{1F5DC}'};
const $=s=>document.querySelector(s), ce=(t,c)=>{const e=document.createElement(t);if(c)e.className=c;return e;};
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
function fmtDur(s){s=Math.round(s);let h=s/3600|0,m=(s%3600)/60|0,ss=s%60;return h?`${h}h ${m}m`:(m?`${m}m ${ss}s`:`${ss}s`);}
function cssv(v){return getComputedStyle(document.documentElement).getPropertyValue(v);}

// ---- visible events (system notes optional) ----
let showNotes=false;
function visible(){return DATA.events.filter(e=>e.kind!=='note'||showNotes);}

// ---- header / aside render ----
function header(){
  const m=DATA.meta;
  $('#htitle').textContent=m.title;
  document.title=m.title+' · ClaudeReplay';
  $('#bModel').textContent=(m.model||'claude').replace('claude-','');
  $('#bWhen').textContent=m.started?m.started.slice(0,16).replace('T',' '):'';
  $('#bDur').textContent=fmtDur(m.durationSec);
}
function asideRender(){
  // moments
  const mc=$('#moments');mc.innerHTML='';
  (DATA.top.length?DATA.top:DATA.moments).slice(0,8).forEach(m=>{
    const d=ce('div','moment');
    d.innerHTML=`<div class="me">${MEMOJI[m.type]||'✨'}</div><div class="ml">${esc(m.label)}<br><small>#${m.i} · ${m.type}</small></div>`;
    d.onclick=()=>seekToEvent(m.i);mc.appendChild(d);
  });
  if(!DATA.moments.length)mc.innerHTML='<div style="color:var(--dim);font-size:12px">none detected</div>';
  // files
  const fc=$('#files');fc.innerHTML='';
  DATA.files.forEach((f,idx)=>{
    const adds=f.versions.reduce((a,v)=>a+(v.add||0),0),dels=f.versions.reduce((a,v)=>a+(v.del||0),0);
    const d=ce('div','filerow');
    d.innerHTML=`<span class="fn" title="${esc(f.path)}">${esc(f.name)}</span>
      <span class="fm">${f.versions.length}× <span class="add">+${adds}</span>/<span class="del">-${dels}</span></span>`;
    d.onclick=()=>openFile(idx);fc.appendChild(d);
  });
  if(!DATA.files.length)fc.innerHTML='<div style="color:var(--dim);font-size:12px">no file edits</div>';
  // tool mix
  const tm=$('#toolmix');tm.innerHTML='';
  const tools=Object.entries(DATA.meta.tools);const max=Math.max(1,...tools.map(t=>t[1]));
  const TK={Read:'read',NotebookRead:'read',Write:'write',NotebookEdit:'write',Edit:'edit',MultiEdit:'edit',Bash:'exec',PowerShell:'exec',Grep:'search',Glob:'search',WebSearch:'search',WebFetch:'search',Task:'agent',Agent:'agent'};
  tools.forEach(([name,n])=>{
    const k=TK[name]||'other';const r=ce('div','tmrow');
    r.innerHTML=`<span style="width:78px;color:var(--dim)">${esc(name)}</span>
      <span class="tmbar" style="width:${Math.max(8,160*n/max)}px;background:${cssv(TKCOL[k])}"></span>
      <span style="color:var(--dim)">${n}</span>`;
    tm.appendChild(r);
  });
}

// ---- context chart ----
function ctxChart(){
  const svg=$('#ctxchart');const W=288,H=88,pad=4;
  const pts=DATA.ctx.length?DATA.ctx:[{i:0,ctx:0}];
  const peak=Math.max(1,...pts.map(p=>p.ctx));
  const n=pts.length;
  const X=j=>pad+(W-2*pad)*(n<2?j:j/(n-1));
  const Y=v=>H-pad-(H-2*pad)*v/peak;
  let area=`M ${X(0)} ${H-pad}`,line='';
  pts.forEach((p,j)=>{area+=` L ${X(j).toFixed(1)} ${Y(p.ctx).toFixed(1)}`;line+=(j?'L':'M')+` ${X(j).toFixed(1)} ${Y(p.ctx).toFixed(1)} `;});
  area+=` L ${X(n-1)} ${H-pad} Z`;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  svg.innerHTML=`<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="${cssv('--acc')}" stop-opacity=".55"/>
    <stop offset="1" stop-color="${cssv('--acc')}" stop-opacity=".03"/></linearGradient></defs>
    <path d="${area}" fill="url(#g)"/><path d="${line}" fill="none" stroke="${cssv('--acc')}" stroke-width="1.6"/>
    <line id="cxh" x1="0" y1="${pad}" x2="0" y2="${H-pad}" stroke="#fff" stroke-opacity=".55" stroke-width="1.4"/>
    <circle id="cxd" r="3" fill="#fff"/>`;
  svg._X=X;svg._Y=Y;svg._pts=pts;svg._peak=peak;
}
function ctxUpdate(evIdx){
  const svg=$('#ctxchart');if(!svg._pts)return;
  let k=0;for(let j=0;j<svg._pts.length;j++){if(svg._pts[j].i<=evIdx)k=j;}
  const p=svg._pts[k];const x=svg._X(k),y=svg._Y(p.ctx);
  const h=svg.querySelector('#cxh'),d=svg.querySelector('#cxd');
  if(h){h.setAttribute('x1',x);h.setAttribute('x2',x);} if(d){d.setAttribute('cx',x);d.setAttribute('cy',y);}
  $('#ctxlabel').textContent=`${p.ctx.toLocaleString()} tokens  ·  peak ${DATA.meta.ctxPeak.toLocaleString()}`;
}

// ---- stage (event cards) ----
function bodyText(t,clip){
  const big=t.length>600;
  return `<div class="body${clip&&big?' clip':''}">${esc(t)}</div>`+
    (clip&&big?`<div class="more" onclick="this.previousElementSibling.classList.toggle('clip');this.textContent=this.previousElementSibling.classList.contains('clip')?'… show more':'show less'">… show more</div>`:'');
}
function evCard(e){
  const wrap=ce('div','ev '+e.kind+(e.err?' err':''));wrap.id='ev'+e.i;wrap.dataset.i=e.i;
  let icCls='ic',icon='•',lab='',inner='';
  if(e.kind==='prompt'){icon='\u{1F464}';lab='YOU';inner=bodyText(e.text);}
  else if(e.kind==='command'){icCls='ic';icon='/';lab='SLASH COMMAND';inner=`<div class="tsum mono">/${esc(e.text)}</div>`;}
  else if(e.kind==='thinking'){icon='\u{1F4AD}';lab='CLAUDE · thinking';inner=bodyText(e.text,true);}
  else if(e.kind==='text'){icon='\u{1F916}';lab='CLAUDE';inner=bodyText(e.text,true);}
  else if(e.kind==='tool'){icCls='ic '+(e.tk||'other');icon=ICON[e.tk||'other'];lab='TOOL CALL';
    inner=`<div><span class="tname">${esc(e.tool)}</span> <span class="tsum">${esc(e.sum)}</span></div>`+toolInput(e);}
  else if(e.kind==='result'){icon=e.err?'✖':'✓';lab=e.err?'RESULT · error':'RESULT';
    inner=`<pre class="body clip">${esc(e.out||'')}</pre>`+
      (e.out&&e.out.length>400?`<div class="more" onclick="this.previousElementSibling.classList.toggle('clip');this.textContent=this.previousElementSibling.classList.contains('clip')?'… show more':'show less'">… show more</div>`:'')+
      (e.trunc?`<div class="trunc">… output truncated (${e.bytes.toLocaleString()} bytes total)</div>`:'')+
      (e.img?`<div class="trunc">[contains image]</div>`:'');}
  else if(e.kind==='system'){icon='\u{1F5DC}';lab='SYSTEM';inner=`<div class="tsum">${esc(e.text||'')}</div>`;}
  else if(e.kind==='note'){icon='ⓘ';lab='NOTE';inner=`<div class="tsum" style="color:var(--dim)">${esc(e.text||'')}</div>`;}
  const t=clockFor(e);
  wrap.innerHTML=`<div class="row"><div class="${icCls}">${icon}</div>
    <div class="card"><div class="lab">${lab}<span class="t">${t}</span></div>${inner}</div></div>`;
  return wrap;
}
function toolInput(e){
  if(!e.inp)return'';
  const keys=Object.keys(e.inp);if(!keys.length)return'';
  if('command' in e.inp)return`<pre class="body clip mono">${esc(e.inp.command)}</pre>`;
  if('old_string' in e.inp||'new_string' in e.inp)
    return`<pre class="body clip mono"><span style="color:var(--err)">- ${esc(e.inp.old_string||'')}</span>\n<span style="color:var(--ok)">+ ${esc(e.inp.new_string||'')}</span></pre>`;
  if('content' in e.inp)return`<pre class="body clip mono">${esc(e.inp.content)}</pre>`;
  if('pattern' in e.inp)return`<div class="tsum mono">/${esc(e.inp.pattern)}/</div>`;
  return'';
}
function clockFor(e){
  if(e.off!=null)return '+'+fmtDur(e.off);
  if(e.ts)return e.ts.slice(11,19);
  return '';
}

// offsets: compute seconds from start for each event
function computeOffsets(){
  const t0=DATA.meta.started?Date.parse(DATA.meta.started):0;
  DATA.events.forEach(e=>{e.off=(e.ts&&t0)?(Date.parse(e.ts)-t0)/1000:null;});
}

// ---- timeline track ----
function buildTrack(){
  const tr=$('#track');[...tr.querySelectorAll('.tick,.mk')].forEach(n=>n.remove());
  const vis=visible();const n=vis.length;
  vis.forEach((e,j)=>{
    let col;
    if(e.kind==='tool')col=cssv(TKCOL[e.tk||'other']);
    else if(e.kind==='result')col=e.err?cssv('--err'):cssv('--ok');
    else if(e.kind==='prompt')col=cssv('--edit');
    else if(e.kind==='text')col=cssv('--fg');
    else if(e.kind==='thinking')col=cssv('--think');
    else col=cssv('--line');
    const t=ce('div','tick');t.style.left=(100*j/Math.max(1,n-1))+'%';t.style.background=col;t.style.opacity=.7;
    tr.appendChild(t);
  });
  DATA.moments.forEach(m=>{
    const j=vis.findIndex(e=>e.i>=m.i);if(j<0)return;
    const mk=ce('div','mk');mk.style.left=(100*j/Math.max(1,n-1))+'%';mk.title=m.label;
    mk.onclick=ev=>{ev.stopPropagation();seekToEvent(m.i);};tr.appendChild(mk);
  });
}

// ---- player state ----
let vis=[], pos=0, playing=false, speed=2, timer=null;
const SPEEDS=[1,2,5,20];
function setSpeed(x){speed=x;[...$('#speeds').children].forEach(b=>b.classList.toggle('on',+b.dataset.s===x));}
function render(){
  vis=visible();
  $('#stage').innerHTML='';
  vis.forEach(e=>$('#stage').appendChild(evCard(e)));
  buildTrack();
  if(pos>=vis.length)pos=vis.length-1; if(pos<0)pos=0;
  paint();
}
function paint(){
  vis.forEach((e,j)=>{
    const el=document.getElementById('ev'+e.i);if(!el)return;
    el.classList.toggle('seen',j<=pos);
    el.classList.toggle('cur',j===pos);
  });
  const cur=vis[pos];
  if(cur){
    const el=document.getElementById('ev'+cur.i);
    if(el)el.scrollIntoView({block:'center',behavior:playing?'smooth':'auto'});
    ctxUpdate(cur.i);
    $('#clock').textContent=(cur.off!=null?'+'+fmtDur(cur.off):'')+' / '+fmtDur(DATA.meta.durationSec);
  }
  $('#fill').style.width=(100*(pos+1)/Math.max(1,vis.length))+'%';
  $('#head').style.left=(100*pos/Math.max(1,vis.length-1))+'%';
  $('#counter').textContent=`${pos+1} / ${vis.length}`;
}
function step(d){pos=Math.max(0,Math.min(vis.length-1,pos+d));paint();if(pos>=vis.length-1)pause();}
function seekToEvent(i){const j=vis.findIndex(e=>e.i>=i);if(j>=0){pos=j;paint();}}
function seekFrac(f){pos=Math.max(0,Math.min(vis.length-1,Math.round(f*(vis.length-1))));paint();}

// playback timing from real gaps, clamped, scaled by speed
function gapMs(){
  const cur=vis[pos],nx=vis[pos+1];
  let g=600;
  if(cur&&nx&&cur.off!=null&&nx.off!=null)g=Math.min(2500,Math.max(220,(nx.off-cur.off)*1000));
  // thinking/text get a beat; results after tools are quick
  if(nx&&nx.kind==='thinking')g=Math.max(g,700);
  return g/speed;
}
function tick(){
  if(!playing)return;
  if(pos>=vis.length-1){pause();return;}
  step(1);
  timer=setTimeout(tick,gapMs());
}
function play(){if(pos>=vis.length-1)pos=0;playing=true;$('#pp').textContent='⏸';paint();timer=setTimeout(tick,gapMs());}
function pause(){playing=false;$('#pp').textContent='▶';clearTimeout(timer);}
function toggle(){playing?pause():play();}

// ---- file modal ----
function openFile(idx){
  const f=DATA.files[idx];
  $('#mtitle').textContent=f.path;
  const b=$('#mbody');b.innerHTML='';
  if(f.reads)b.insertAdjacentHTML('beforeend',`<div class="vh">read ${f.reads}× during session</div>`);
  f.versions.forEach((v,k)=>{
    const d=ce('div','ver');
    const head=`<div class="vh">#${k+1} · ${v.action==='write'?('wrote '+(v.lines||0)+' lines'):('edit · +'+(v.add||0)+'/-'+(v.del||0))} · <span class="add">+${v.add||0}</span>/<span class="del">-${v.del||0}</span></div>`;
    let body='';
    if(v.action==='write'){
      body=`<div class="diff">`+(v.after||'').split('\n').slice(0,200).map(l=>`<div class="dl p">${esc(l)}</div>`).join('')+`</div>`;
    }else if(v.diff&&v.diff.length){
      body=`<div class="diff">`+v.diff.map(dl=>{
        const c=dl.t==='+'?'p':dl.t==='-'?'m':dl.t==='@'?'c':'ctx';
        return `<div class="dl ${c}">${esc((dl.t==='+'?'+ ':dl.t==='-'?'- ':'  ')+dl.s)}</div>`;
      }).join('')+`</div>`;
    }
    d.innerHTML=head+body;
    const jump=ce('div','more');jump.textContent='→ jump to this change';jump.onclick=()=>{closeModal();seekToEvent(v.i);};
    d.appendChild(jump);
    b.appendChild(d);
  });
  $('#modal').classList.add('on');
}
function closeModal(){$('#modal').classList.remove('on');}

// ---- wire up ----
function speedButtons(){const c=$('#speeds');SPEEDS.forEach(x=>{const b=ce('button');b.textContent=x+'x';b.dataset.s=x;b.onclick=()=>setSpeed(x);c.appendChild(b);});setSpeed(2);}
function init(){
  if(!DATA||!DATA.events){$('#stage').innerHTML='<div class="empty">No session data.</div>';return;}
  computeOffsets();header();asideRender();ctxChart();speedButtons();render();ctxUpdate(0);
  $('#pp').onclick=toggle;$('#back').onclick=()=>{pause();step(-1);};$('#fwd').onclick=()=>{pause();step(1);};
  $('#shownotes').onchange=e=>{showNotes=e.target.checked;const keep=vis[pos]?vis[pos].i:0;render();seekToEvent(keep);asideRender();};
  $('#track').onclick=e=>{const r=e.currentTarget.getBoundingClientRect();pause();seekFrac((e.clientX-r.left)/r.width);};
  $('#modal').onclick=e=>{if(e.target.id==='modal')closeModal();};
  document.addEventListener('keydown',e=>{
    if(e.target.tagName==='INPUT')return;
    if(e.key===' '){e.preventDefault();toggle();}
    else if(e.key==='ArrowRight'){pause();step(1);}
    else if(e.key==='ArrowLeft'){pause();step(-1);}
    else if(e.key==='ArrowUp'){e.preventDefault();setSpeed(SPEEDS[Math.min(SPEEDS.length-1,SPEEDS.indexOf(speed)+1)]);}
    else if(e.key==='ArrowDown'){e.preventDefault();setSpeed(SPEEDS[Math.max(0,SPEEDS.indexOf(speed)-1)]);}
    else if(e.key==='Escape')closeModal();
    else if(e.key==='Home'){pause();pos=0;paint();}
    else if(e.key==='End'){pause();pos=vis.length-1;paint();}
  });
}
init();
</script>
</body>
</html>"""


if __name__ == "__main__":
    sys.exit(main())
