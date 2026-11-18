"""Visual layer for the PawPal+ Streamlit app: theme CSS and small HTML components.

Design notes ("the morning walk"):
    * Pine green for structure and primary actions, a pale dew background, and marigold used only for the pets'
      bone-shaped ID tags, the one bold element on the page.
    * Bricolage Grotesque for headings, Atkinson Hyperlegible for body text (bundled in static/fonts so the app
      works offline).
    * Category colors are shared by chips and timeline dots so "meds" always looks like meds.
All user-entered text is HTML-escaped before it is placed in markup.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

CATEGORY_COLORS = {
    "walk": "#3F7D58",
    "feeding": "#B9772A",
    "meds": "#B4473F",
    "enrichment": "#3F6E9C",
    "grooming": "#7D5FA8",
    "general": "#66786F",
}
SPECIES_WORD = {"dog": "dog", "cat": "cat", "other": "pet"}

CSS = """
<style>
@font-face { font-family: "Bricolage Grotesque"; font-weight: 600; font-display: swap;
  src: url("app/static/fonts/bricolage-grotesque-latin-600-normal.woff2") format("woff2"); }
@font-face { font-family: "Bricolage Grotesque"; font-weight: 800; font-display: swap;
  src: url("app/static/fonts/bricolage-grotesque-latin-800-normal.woff2") format("woff2"); }
@font-face { font-family: "Atkinson Hyperlegible"; font-weight: 400; font-display: swap;
  src: url("app/static/fonts/atkinson-hyperlegible-latin-400-normal.woff2") format("woff2"); }
@font-face { font-family: "Atkinson Hyperlegible"; font-weight: 700; font-display: swap;
  src: url("app/static/fonts/atkinson-hyperlegible-latin-700-normal.woff2") format("woff2"); }

:root {
  --pine: #1E4D3A; --pine-soft: #2C6650; --dew: #F1F6F2; --moss: #E2ECE5; --line: #CFDCD3;
  --marigold: #E8A33D; --ink: #1C2B24; --muted: #5C6E65; --paper: #FFFFFF;
  --body: "Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif;
  --display: "Bricolage Grotesque", "Avenir Next", system-ui, sans-serif;
}

.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button,
.stApp td, .stApp th, .stApp [data-testid="stMarkdownContainer"] { font-family: var(--body); }
.stApp p, .stApp li { line-height: 1.55; }
.block-container { max-width: 920px; padding-top: 2.2rem; padding-bottom: 5rem; }

.stApp h1, .stApp h2, .stApp h3 { font-family: var(--display); color: var(--pine); letter-spacing: -0.01em; }
.stApp h2 { font-size: 1.75rem; font-weight: 800; margin: 2.6rem 0 0.2rem; }
.stApp h3 { font-size: 1.15rem; font-weight: 600; margin-top: 1.4rem; }
[data-testid="stHeaderActionElements"] { display: none; }

/* sidebar: the setup area */
[data-testid="stSidebar"] { background: var(--moss); border-right: 1px solid var(--line); }
[data-testid="stSidebar"] h2 { font-size: 1.2rem; margin-top: 1.4rem; }
.pp-brand { font-family: var(--display); font-weight: 800; font-size: 1.55rem; color: var(--pine);
  display: flex; align-items: center; gap: .5rem; margin: .2rem 0 .1rem; }
.pp-brand svg { width: 30px; height: 30px; }
.pp-sidenote { color: var(--muted); font-size: .9rem; }

/* forms and inputs */
[data-testid="stForm"] { background: var(--paper); border: 1px solid var(--line); border-radius: 14px;
  padding: 1.1rem 1.2rem .6rem; }
[data-testid="stSidebar"] [data-testid="stForm"] { padding: .9rem .9rem .4rem; }
.stButton button, [data-testid="stFormSubmitButton"] button { border-radius: 999px; font-weight: 700;
  padding: .45rem 1.15rem; }
.stButton button:focus-visible, [data-testid="stFormSubmitButton"] button:focus-visible,
textarea:focus-visible, input:focus-visible { outline: 3px solid var(--marigold); outline-offset: 2px; }

/* tables */
[data-testid="stTable"] table { border-collapse: separate; border-spacing: 0; background: var(--paper);
  border: 1px solid var(--line); border-radius: 12px; overflow: hidden; width: 100%; }
[data-testid="stTable"] th { background: var(--moss); color: var(--pine); font-weight: 700; }
[data-testid="stTable"] td, [data-testid="stTable"] th { border-color: var(--line) !important; }

[data-testid="stExpander"] details { background: var(--paper); border: 1px solid var(--line); border-radius: 12px; }
[data-testid="stAlert"] { border-radius: 12px; }

/* hero */
.pp-hero { background: var(--pine); color: var(--dew); border-radius: 26px; padding: 2rem 2.1rem 1.7rem;
  position: relative; overflow: hidden; }
.pp-hero::after { content: ""; position: absolute; right: -60px; top: -60px; width: 240px; height: 240px;
  border-radius: 50%; border: 34px solid rgba(232, 163, 61, 0.13); }
.pp-hero h1 { color: var(--paper) !important; font-size: clamp(2rem, 4.6vw, 2.9rem); font-weight: 800;
  line-height: 1.05; margin: 0 0 .55rem; padding: 0; max-width: 18ch; }
.pp-hero p { color: #D6E6DC; font-size: 1.08rem; margin: 0 0 1.3rem; max-width: 52ch; }
.pp-hero p strong { color: var(--paper); font-family: var(--display); font-weight: 800; }
.pp-tags { display: flex; flex-wrap: wrap; gap: .4rem 1rem; align-items: center; position: relative; z-index: 1; }
.pp-tags svg { display: block; filter: drop-shadow(0 3px 0 rgba(0,0,0,.18)); }
.pp-empty-tag { border: 2px dashed rgba(241,246,242,.45); color: #D6E6DC; border-radius: 18px;
  padding: .7rem 1.1rem; font-size: .95rem; }

/* section intro text */
.pp-lede { color: var(--muted); margin: 0 0 1rem; max-width: 62ch; }

/* AI answer */
.pp-answer { background: var(--paper); border: 1px solid var(--line); border-left: 6px solid var(--pine);
  border-radius: 14px; padding: 1.1rem 1.3rem; margin: .8rem 0 1rem; }
.pp-answer p { margin: 0 0 .5rem; }
.pp-answer ul { margin: .2rem 0 .6rem 1.1rem; padding: 0; }
.pp-answer .pp-cite { color: var(--pine-soft); font-weight: 700; font-size: .85em; }
.pp-meta { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-top: 1rem; }
.pp-chip { display: inline-flex; align-items: center; gap: .35rem; border-radius: 999px; padding: .18rem .7rem;
  font-size: .82rem; font-weight: 700; background: var(--moss); color: var(--pine); }
.pp-chip .pp-dot { width: .55rem; height: .55rem; border-radius: 50%; }
.pp-conf-high { background: #DCEFE3; color: #1E5A3C; }
.pp-conf-medium { background: #FBEBCF; color: #7A4E10; }
.pp-conf-low { background: #F6DAD7; color: #8C2F28; }

/* proposed tasks */
.pp-proposals { list-style: none; margin: .3rem 0 1rem; padding: 0; border: 1px solid var(--line);
  border-radius: 14px; background: var(--paper); overflow: hidden; }
.pp-proposals li { display: grid; grid-template-columns: 1fr auto; gap: .2rem 1rem; padding: .75rem 1.1rem;
  border-top: 1px solid var(--line); }
.pp-proposals li:first-child { border-top: none; }
.pp-proposals .pp-title { font-weight: 700; }
.pp-proposals .pp-when { font-family: var(--display); font-weight: 600; color: var(--pine); text-align: right; }
.pp-proposals .pp-sub { grid-column: 1 / -1; color: var(--muted); font-size: .88rem; display: flex;
  flex-wrap: wrap; gap: .3rem .9rem; align-items: center; }

/* schedule timeline */
.pp-timeline { list-style: none; margin: 1rem 0; padding: 0; position: relative; }
.pp-timeline::before { content: ""; position: absolute; left: 5.35rem; top: .6rem; bottom: .6rem; width: 2px;
  background: var(--line); }
.pp-timeline li { display: grid; grid-template-columns: 4.6rem 1.6rem 1fr; align-items: start; padding: .5rem 0; }
.pp-timeline time { font-family: var(--display); font-weight: 600; color: var(--pine); font-size: 1.15rem;
  line-height: 1.1; padding-top: .05rem; }
.pp-timeline time small { display: block; font-size: .78rem; font-weight: 600; color: var(--muted); }
.pp-timeline .pp-node { width: 14px; height: 14px; border-radius: 50%; margin: .3rem 0 0 .2rem;
  border: 3px solid var(--dew); box-shadow: 0 0 0 1px var(--line); position: relative; z-index: 1; }
.pp-timeline .pp-card { background: var(--paper); border: 1px solid var(--line); border-radius: 12px;
  padding: .6rem .9rem; }
.pp-timeline .pp-card strong { font-size: 1.02rem; }
.pp-timeline .pp-card span { color: var(--muted); font-size: .88rem; }
.pp-timeline .pp-why { color: var(--muted); font-size: .86rem; margin: .15rem 0 0; }

@media (max-width: 640px) {
  .pp-hero { padding: 1.5rem 1.3rem 1.3rem; border-radius: 20px; }
  .pp-timeline::before { left: 4.6rem; }
  .pp-timeline li { grid-template-columns: 3.9rem 1.4rem 1fr; }
  .pp-timeline time { font-size: 1rem; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""

PAW_SVG = (
    '<svg viewBox="0 0 64 64" aria-hidden="true"><g fill="#1E4D3A">'
    '<ellipse cx="32" cy="42" rx="14" ry="12"/><ellipse cx="14" cy="27" rx="6" ry="8"/>'
    '<ellipse cx="25" cy="16" rx="6" ry="8"/><ellipse cx="39" cy="16" rx="6" ry="8"/>'
    '<ellipse cx="50" cy="27" rx="6" ry="8"/></g></svg>'
)


def greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def bone_tag(name: str, species: str, breed: str, age: int) -> str:
    """A bone-shaped pet ID tag drawn as inline SVG."""
    details = ", ".join(filter(None, [breed or SPECIES_WORD.get(species, "pet"), f"{age} yrs" if age else ""]))
    width = max(150, int(max(len(name) * 11.5, len(details) * 6.6)) + 74)
    label = escape(f"{name}, {details}")
    return (
        f'<svg width="{width}" height="66" viewBox="0 0 {width} 66" role="img" aria-label="{label}">'
        f'<g fill="#E8A33D"><rect x="18" y="15" width="{width - 36}" height="36" rx="8"/>'
        f'<circle cx="18" cy="18" r="16"/><circle cx="18" cy="48" r="16"/>'
        f'<circle cx="{width - 18}" cy="18" r="16"/><circle cx="{width - 18}" cy="48" r="16"/></g>'
        f'<circle cx="34" cy="33" r="3.2" fill="#1E4D3A" opacity=".55"/>'
        f'<text x="{width / 2 + 6}" y="31" text-anchor="middle" font-family="Bricolage Grotesque, sans-serif" '
        f'font-weight="800" font-size="18" fill="#1E4D3A">{escape(name)}</text>'
        f'<text x="{width / 2 + 6}" y="46" text-anchor="middle" font-family="Atkinson Hyperlegible, sans-serif" '
        f'font-size="11.5" fill="#3B3020">{escape(details)}</text></svg>'
    )


def hero(owner) -> str:
    pets = owner.pets
    pending = [task for pet in pets for task in pet.get_pending_tasks()]
    minutes = sum(task.duration_minutes for task in pending)
    first_name = escape(owner.name.split(" ")[0]) if owner.name.strip() else "there"
    if not pets:
        summary = "Add your first pet in the sidebar, then ask PawPal to plan their day."
        tags = '<div class="pp-empty-tag">Your pets will appear here</div>'
    else:
        names = [escape(pet.name) for pet in pets]
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        task_word = "task" if len(pending) == 1 else "tasks"
        verb = "has" if len(names) == 1 else "have"
        if pending:
            waiting = f"{who} {verb} <strong>{len(pending)} {task_word}</strong> waiting ({minutes} min)."
        else:
            waiting = f"{who} {verb} no tasks yet. Ask PawPal below to plan their day."
        summary = (
            f"{waiting} You have <strong>{owner.available_minutes} minutes</strong> today, "
            f"starting at {escape(owner.day_start)}."
        )
        tags = "".join(bone_tag(pet.name, pet.species, pet.breed, pet.age) for pet in pets)
    return (
        f'<section class="pp-hero"><h1>{greeting()}, {first_name}.</h1><p>{summary}</p>'
        f'<div class="pp-tags">{tags}</div></section>'
    )


def chip(text: str, color: str | None = None, extra_class: str = "") -> str:
    dot = f'<span class="pp-dot" style="background:{color}"></span>' if color else ""
    return f'<span class="pp-chip {extra_class}">{dot}{escape(text)}</span>'


def category_chip(category: str) -> str:
    return chip(category, CATEGORY_COLORS.get(category, CATEGORY_COLORS["general"]))


def answer_card(answer: str, confidence: float, label: str, backend: str, n_sources: int) -> str:
    """Render the assistant's answer: plain lines become paragraphs, '- ' lines become a list."""
    parts, bullets = [], []

    def flush() -> None:
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{item}</li>" for item in bullets) + "</ul>")
            bullets.clear()

    for line in answer.splitlines():
        text = escape(line.strip())
        if not text:
            continue
        for number in range(1, 10):
            text = text.replace(f"[{number}]", f'<span class="pp-cite">[{number}]</span>')
        if text.startswith("- "):
            bullets.append(text[2:])
        else:
            flush()
            parts.append(f"<p>{text}</p>")
    flush()
    meta = (
        chip(f"Confidence {confidence:.2f}, {label}", extra_class=f"pp-conf-{label}")
        + chip(f"{n_sources} care guide section{'s' if n_sources != 1 else ''}")
        + chip("Gemini" if backend == "gemini" else "Offline model")
    )
    return f'<div class="pp-answer">{"".join(parts)}<div class="pp-meta">{meta}</div></div>'


def proposals(tasks: list[dict], sources: list[dict]) -> str:
    titles = {source["id"]: source["title"].split(" - ", 1)[-1] for source in sources}
    rows = []
    for task in tasks:
        origin = "You asked for this" if task["source"] == "user" else f"From the guide: {titles.get(task['source'], task['source'])}"
        rows.append(
            f'<li><span class="pp-title">{escape(task["title"])}</span>'
            f'<span class="pp-when">{escape(task["time"] or "Any time")}</span>'
            f'<span class="pp-sub">{category_chip(task["category"])}<span>{task["minutes"]} min</span>'
            f'<span>{escape(task["priority"])} priority</span><span>{escape(task["frequency"])}</span>'
            f"<span>{escape(origin)}</span></span></li>"
        )
    return f'<ul class="pp-proposals">{"".join(rows)}</ul>'


def timeline(plan) -> str:
    rows = []
    for item in plan:
        color = CATEGORY_COLORS.get(item.task.category, CATEGORY_COLORS["general"])
        rows.append(
            f'<li><time>{item.start_time}<small>to {item.end_time}</small></time>'
            f'<span class="pp-node" style="background:{color}"></span>'
            f'<div class="pp-card"><strong>{escape(item.task.title)}</strong> '
            f'<span>{escape(item.pet_name)}, {item.task.duration_minutes} min</span>'
            f'<p class="pp-why">{escape(item.reason)}</p></div></li>'
        )
    return f'<ol class="pp-timeline">{"".join(rows)}</ol>'
