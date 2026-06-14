#!/usr/bin/env python3
"""Generate examples/demo.svg — an animated, looping mock of the replay UI.

Uses SMIL <animate> (plays on GitHub when embedded via <img>). One master
clock (DUR seconds) drives the scrubber playhead, the context-growth reveal,
and the staggered fade-in of event cards, so the whole thing reads as a movie.
"""
from pathlib import Path

W, H = 1200, 590
DUR = 9.0
# palette (tokyo-night, same as the real UI)
BG, PANEL, PANEL2, LINE = "#16161e", "#1a1b26", "#1f2335", "#2a2e42"
FG, DIM, ACC, ACC2 = "#c0caf5", "#565f89", "#7aa2f7", "#bb9af7"
READ, WRITE, EDIT, EXEC, SEARCH = "#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#7dcfff"
OK, ERR, THINK = "#9ece6a", "#f7768e", "#7c83a8"

# ---- scrubber geometry -----------------------------------------------------
SX0, SX1, SY = 44, W - 44, 545          # scrubber track x-range and y
TRACKW = SX1 - SX0

def at(frac):  # x position for a fraction of the timeline
    return SX0 + TRACKW * frac

# tool-tick colours along the scrubber (the shape of the session)
import random
random.seed(7)
ticks = []
pat = [READ, READ, SEARCH, READ, EDIT, READ, ERR, EDIT, READ, WRITE,
       READ, READ, EXEC, READ, WRITE, READ, READ, SEARCH, READ, EDIT]
n_ticks = 58
for i in range(n_ticks):
    x = SX0 + TRACKW * i / (n_ticks - 1)
    col = pat[i % len(pat)] if random.random() > 0.25 else READ
    h = 13 if col != ERR else 16
    ticks.append(f'<rect x="{x:.1f}" y="{SY-h+9:.1f}" width="2.4" height="{h}" rx="1" fill="{col}"/>')
ticks_svg = "".join(ticks)

# moment markers (◆) on the scrubber
moments = [0.30, 0.50, 0.68, 0.86]
moment_svg = "".join(
    f'<circle cx="{at(f):.1f}" cy="{SY+14:.1f}" r="3.4" fill="{ACC2}"/>' for f in moments)

# ---- event cards (left column), each reveals as the playhead passes --------
def reveal(frac, dim=0.26):
    """opacity animation: dim until `frac`, then full, looping with the clock."""
    a = max(0.001, min(frac, 0.999))
    return (f'<animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" '
            f'values="{dim};{dim};1;1" keyTimes="0;{a:.3f};{a:.3f};1" '
            f'calcMode="discrete"/>')

def glow(frac):
    """a brief acc ring as the playhead hits the card (the 'current' look)."""
    a = max(0.02, min(frac, 0.97))
    k0, k1, k2 = a - 0.02, a, min(a + 0.10, 0.999)
    return (f'<animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" '
            f'values="0;0;0.9;0;0" keyTimes="0;{k0:.3f};{k1:.3f};{k2:.3f};1"/>')

cards = []
CX, CW = 40, 700
def card(y, h, frac, accent, icon, icon_bg, icon_fg, label, lines):
    g = [f'<g opacity="0.26">{reveal(frac)}']
    # icon chip
    g.append(f'<rect x="{CX}" y="{y}" width="30" height="30" rx="8" fill="{icon_bg}"/>'
             f'<text x="{CX+15}" y="{y+20}" text-anchor="middle" font-size="15" fill="{icon_fg}">{icon}</text>')
    # card body
    bx = CX + 42
    g.append(f'<rect x="{bx}" y="{y}" width="{CW-bx-CX}" height="{h}" rx="11" '
             f'fill="{PANEL2}" stroke="{LINE}"/>')
    # glow ring (current)
    g.append(f'<rect x="{bx}" y="{y}" width="{CW-bx-CX}" height="{h}" rx="11" '
             f'fill="none" stroke="{accent}" stroke-width="1.6" opacity="0">{glow(frac)}</rect>')
    g.append(f'<text x="{bx+14}" y="{y+18}" font-size="10.5" letter-spacing="0.4" '
             f'fill="{DIM}">{label}</text>')
    ly = y + 36
    for col, txt, mono in lines:
        fam = 'font-family="ui-monospace,Consolas,monospace"' if mono else ''
        g.append(f'<text x="{bx+14}" y="{ly}" font-size="13" fill="{col}" {fam}>{txt}</text>')
        ly += 20
    g.append('</g>')
    cards.append("".join(g))

card(70, 56, 0.05, ACC, "&#128172;", "#26345e", "#9db4ff", "YOUR PROMPT",
     [(FG, "Add a dark-mode toggle to the settings page.", False)])
card(138, 52, 0.20, THINK, "&#128173;", "#202231", THINK, "CLAUDE · THINKING",
     [(THINK, "Theming reads :root[data-theme] — add a provider, then a toggle.", False)])
card(200, 74, 0.34, READ, "&#128065;", "#1d2a4d", READ, "TOOL CALL · READ",
     [(FG, "src/pages/Settings.jsx", True),
      (DIM, "&#8627; returns 9 lines", False)])
card(284, 60, 0.52, ERR, "&#9888;", "#341d24", ERR, "RESULT · ERROR",
     [(ERR, "String to replace not found in file.", True)])
card(354, 76, 0.70, EDIT, "&#9999;", "#352c18", EDIT, "TOOL CALL · EDIT  →  &#10003; FIXED",
     [(OK, "+ <ThemeToggle />", True),
      (DIM, "matched on the second try", False)])
card(440, 60, 0.88, WRITE, "&#10003;", "#23341f", WRITE, "TOOL CALL · WRITE",
     [(WRITE, "src/theme/ThemeContext.jsx  (130 lines)", True)])
cards_svg = "\n".join(cards)

# ---- context-growth chart (right rail), revealed by a growing clip ---------
RX, RW = 760, W - 760 - 28        # right rail x and width
chart_x, chart_y, chart_w, chart_h = RX + 16, 150, RW - 32, 90
# a rising, slightly jagged curve
import math
pts = []
N = 40
for i in range(N):
    f = i / (N - 1)
    v = (f ** 0.85) + 0.05 * math.sin(f * 22) * f
    v = max(0, min(1, v))
    x = chart_x + chart_w * f
    y = chart_y + chart_h - chart_h * v
    pts.append((x, y))
area = f"M {chart_x:.1f} {chart_y+chart_h:.1f} " + \
       " ".join(f"L {x:.1f} {y:.1f}" for x, y in pts) + \
       f" L {chart_x+chart_w:.1f} {chart_y+chart_h:.1f} Z"
line = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)

# right-rail key-moment rows, revealed in step with the run
def rail_row(y, frac, dot, col, title, sub):
    return (f'<g opacity="0.22">{reveal(frac, 0.22)}'
            f'<text x="{RX+16}" y="{y}" font-size="12.5">{dot}</text>'
            f'<text x="{RX+38}" y="{y}" font-size="12.5" fill="{FG}">{title}</text>'
            f'<text x="{RX+38}" y="{y+16}" font-size="10.5" fill="{DIM}">{sub}</text></g>')

rail = "\n".join([
    rail_row(312, 0.52, f'<tspan fill="{ERR}">&#10007;</tspan>', ERR, "Mistake", "#9 · edit missed"),
    rail_row(350, 0.70, f'<tspan fill="{OK}">&#10003;</tspan>', OK, "Fixed", "#12 · Settings.jsx"),
    rail_row(388, 0.88, f'<tspan fill="{WRITE}">&#9632;</tspan>', WRITE, "New component", "#16 · 130 lines"),
])

# playhead (master clock): vertical line + dot sweeping the scrubber
playhead = f'''
  <g>
    <line x1="{SX0}" y1="{SY-22}" x2="{SX0}" y2="{SY+22}" stroke="{ACC}" stroke-width="2" opacity="0.85">
      <animate attributeName="x1" dur="{DUR}s" repeatCount="indefinite" values="{SX0};{SX1}" keyTimes="0;1"/>
      <animate attributeName="x2" dur="{DUR}s" repeatCount="indefinite" values="{SX0};{SX1}" keyTimes="0;1"/>
    </line>
    <circle cy="{SY}" r="6" fill="{ACC}" stroke="{BG}" stroke-width="2">
      <animate attributeName="cx" dur="{DUR}s" repeatCount="indefinite" values="{SX0};{SX1}" keyTimes="0;1"/>
    </circle>
  </g>'''

# context-chart playhead (rides along the rail in sync)
chart_head = f'''
  <line y1="{chart_y}" y2="{chart_y+chart_h}" stroke="{ACC}" stroke-width="1.5" opacity="0.7">
    <animate attributeName="x1" dur="{DUR}s" repeatCount="indefinite" values="{chart_x};{chart_x+chart_w}" keyTimes="0;1"/>
    <animate attributeName="x2" dur="{DUR}s" repeatCount="indefinite" values="{chart_x};{chart_x+chart_w}" keyTimes="0;1"/>
  </line>'''

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"
     font-family="ui-sans-serif,Segoe UI,Helvetica,Arial,sans-serif">
  <defs>
    <linearGradient id="ctxfill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{ACC}" stop-opacity="0.55"/>
      <stop offset="1" stop-color="{ACC}" stop-opacity="0.04"/>
    </linearGradient>
    <clipPath id="reveal">
      <rect x="{chart_x}" y="{chart_y-4}" height="{chart_h+8}" width="0">
        <animate attributeName="width" dur="{DUR}s" repeatCount="indefinite"
                 values="0;{chart_w}" keyTimes="0;1" calcMode="linear"/>
      </rect>
    </clipPath>
  </defs>

  <rect width="{W}" height="{H}" rx="16" fill="{BG}"/>

  <!-- header -->
  <rect x="0" y="0" width="{W}" height="48" fill="{PANEL}"/>
  <line x1="0" y1="48" x2="{W}" y2="48" stroke="{LINE}"/>
  <text x="28" y="30" font-size="15" font-weight="800" letter-spacing="1.5" fill="{FG}">
    <tspan fill="{ACC}">&#9654;</tspan> CLAUDEREPLAY</text>
  <text x="190" y="30" font-size="14" font-weight="700" fill="#fff">Add a dark-mode toggle</text>
  <g font-size="11" fill="{DIM}">
    <rect x="{W-260}" y="13" width="74" height="22" rx="11" fill="none" stroke="{LINE}"/>
    <text x="{W-223}" y="28" text-anchor="middle">opus-4-8</text>
    <rect x="{W-178}" y="13" width="92" height="22" rx="11" fill="none" stroke="{LINE}"/>
    <text x="{W-132}" y="28" text-anchor="middle">2026-06-13</text>
    <rect x="{W-78}" y="13" width="58" height="22" rx="11" fill="none" stroke="{LINE}"/>
    <text x="{W-49}" y="28" text-anchor="middle">1m 36s</text>
  </g>

  <!-- left: event stream -->
  {cards_svg}

  <!-- right rail -->
  <line x1="{RX-8}" y1="48" x2="{RX-8}" y2="{H}" stroke="{LINE}"/>
  <text x="{RX+16}" y="92" font-size="11" letter-spacing="1" fill="{DIM}">CONTEXT GROWTH</text>
  <rect x="{chart_x}" y="{chart_y-4}" width="{chart_w}" height="{chart_h+8}" rx="8" fill="{PANEL}"/>
  <g clip-path="url(#reveal)">
    <path d="{area}" fill="url(#ctxfill)"/>
    <path d="{line}" fill="none" stroke="{ACC}" stroke-width="2"/>
  </g>
  {chart_head}
  <text x="{chart_x}" y="{chart_y+chart_h+22}" font-size="11" fill="{DIM}">
    9k &#8594; <tspan fill="{FG}">122k</tspan> tokens · peak 122,400</text>

  <text x="{RX+16}" y="285" font-size="11" letter-spacing="1" fill="{DIM}">KEY MOMENTS</text>
  {rail}

  <!-- scrubber -->
  <text x="{SX0}" y="{SY-30}" font-size="11" letter-spacing="1" fill="{DIM}">TIMELINE</text>
  <rect x="{SX0}" y="{SY-2}" width="{TRACKW}" height="4" rx="2" fill="{LINE}"/>
  {ticks_svg}
  {moment_svg}
  {playhead}

  <!-- transport -->
  <circle cx="{SX0+14}" cy="{H-26}" r="15" fill="{ACC}"/>
  <path d="M {SX0+10} {H-33} L {SX0+10} {H-19} L {SX0+22} {H-26} Z" fill="{BG}"/>
  <text x="{SX0+42}" y="{H-21}" font-size="12" fill="{DIM}" font-family="ui-monospace,Consolas,monospace">
    space play · &#8592;/&#8594; step · &#8593;/&#8595; speed · 1&#215;&#8211;20&#215; · click a &#9670; to jump</text>
  <text x="{SX1}" y="{H-21}" text-anchor="end" font-size="11" fill="{DIM}">21 events · 7 tools · 2 files</text>
</svg>'''

out = Path(__file__).parent / "demo.svg"
out.write_text(svg, encoding="utf-8")
print(f"wrote {out} ({len(svg)} bytes)")
