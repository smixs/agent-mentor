#!/usr/bin/env python3
"""Aggregate MANY agent sessions into one stats bundle for the /insights-style report.

Scans every Claude Code and/or Codex transcript modified within the window,
parses each with digest.parse, and emits:
  - a stats JSON (--out) that render_report.py turns into HTML
  - a compact human-readable digest to stdout that the model reads to write the
    eight report sections (themes, wins, friction, suggestions)

Usage:
    uv run --no-project python collect.py --agent both --days 30 --out /tmp/mentor-stats.json
    uv run --no-project python collect.py --agent claude --days 7
"""
import json, sys, os, time, glob
from collections import Counter
from datetime import datetime, timezone
from digest import parse, CLAUDE_PROJECTS, CODEX_HOME

LANG = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
        ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
        ".md": "Markdown", ".json": "JSON", ".html": "HTML", ".css": "CSS", ".sh": "Shell",
        ".sql": "SQL", ".toml": "TOML", ".yaml": "YAML", ".yml": "YAML", ".c": "C",
        ".cpp": "C++", ".swift": "Swift", ".php": "PHP", ".vue": "Vue", ".svelte": "Svelte"}


def transcript_files(agent):
    out = []
    if agent in ("claude", "both"):
        out += [f for f in glob.glob(f"{CLAUDE_PROJECTS}/**/*.jsonl", recursive=True)
                if "/subagents/" not in f and "/tool-results/" not in f]
    if agent in ("codex", "both"):
        out += glob.glob(f"{CODEX_HOME}/sessions/**/rollout-*.jsonl", recursive=True)
        out += glob.glob(f"{CODEX_HOME}/archived_sessions/rollout-*.jsonl")
    return out


def date_of(d):
    ts = d.get("last_ts") or d.get("first_ts") or ""
    return ts[:10] if ts else "?"


def collect(agent, days):
    cutoff = time.time() - days * 86400
    files = [f for f in transcript_files(agent) if os.path.getmtime(f) >= cutoff]
    tools = Counter(); langs = Counter(); models = set()
    projects = Counter(); per_day = Counter()
    files_touched = set()
    tot = Counter()
    sessions = []
    first_ts = last_ts = None
    for f in sorted(files, key=os.path.getmtime):
        try:
            d = parse(f)
        except Exception:
            continue
        if not d["prompts"] and sum(d["tools"].values()) == 0:
            continue  # empty/aborted shell
        day = date_of(d)
        tools.update(d["tools"])
        models.update(d["models"])
        files_touched.update(d["files"])
        for fp in d["files"]:
            ext = os.path.splitext(fp)[1].lower()
            if ext in LANG:
                langs.update([LANG[ext]])
        cwd = d["meta"].get("cwd") or "?"
        projects.update([cwd])
        per_day.update({day: len(d["prompts"])})
        tot.update(dict(sessions=1, messages=len(d["prompts"]),
                        tool_calls=sum(d["tools"].values()), edits=d["edits"],
                        out_tok=d["out_tok"], in_tok=d["in_tok"] + d["cache_tok"],
                        corrections=d["corrections"], interrupts=d["interrupts"],
                        refusals=d["refusals"]))
        if d["first_ts"]:
            first_ts = min(first_ts or d["first_ts"], d["first_ts"])
        if d["last_ts"]:
            last_ts = max(last_ts or d["last_ts"], d["last_ts"])
        sessions.append(dict(
            file=os.path.basename(f), agent=d["agent"], date=day, cwd=cwd,
            prompts=len(d["prompts"]),
            first_prompt=(d["prompts"][0][:220] if d["prompts"] else ""),
            top_tools=dict(d["tools"].most_common(5)),
            out_tokens=d["out_tok"], corrections=d["corrections"]))

    tot["files_touched"] = len(files_touched)
    return dict(
        window_days=days, agents=agent, generated_at=datetime.now(timezone.utc).isoformat(),
        totals=dict(tot), span=dict(first=first_ts, last=last_ts,
                                    active_days=len([k for k in per_day if k != "?"])),
        tools=dict(tools.most_common(20)), languages=dict(langs.most_common()),
        models=sorted(models), projects=dict(projects.most_common(20)),
        per_day=dict(sorted(per_day.items())),
        sessions=sessions)


def render_digest(s):
    t = s["totals"]
    L = [f"# Aggregate digest — {s['agents']} agents, last {s['window_days']} days",
         f"generated {s['generated_at']}  |  span {s['span']['first']} → {s['span']['last']}",
         "",
         "## Headline",
         f"- {t.get('messages',0)} messages across {t.get('sessions',0)} sessions "
         f"over {s['span']['active_days']} active days",
         f"- tool calls {t.get('tool_calls',0)} | edits {t.get('edits',0)} | "
         f"files touched {t.get('files_touched',0)}",
         f"- output tokens {t.get('out_tok',0):,} | input(+cache) {t.get('in_tok',0):,}",
         f"- correction-like prompts {t.get('corrections',0)} | interrupts "
         f"{t.get('interrupts',0)} | refusals {t.get('refusals',0)}",
         f"- models: {', '.join(s['models']) or '?'}",
         "",
         "## Tool mix",
         "  " + ", ".join(f"{k}×{v}" for k, v in s["tools"].items()),
         "## Languages (from edited files)",
         "  " + (", ".join(f"{k}:{v}" for k, v in s["languages"].items()) or "n/a"),
         "## Projects (by session count)",
         *[f"  {v}× {k}" for k, v in s["projects"].items()],
         "",
         "## Sessions (verbatim first prompt — cluster these into themes)"]
    for i, se in enumerate(s["sessions"], 1):
        L.append(f"{i}. [{se['agent']}] {se['date']} {se['cwd']}  "
                 f"({se['prompts']}p, {se['out_tokens']:,}t, corr {se['corrections']}) "
                 f"tools={se['top_tools']}\n     “{se['first_prompt']}”")
    return "\n".join(L)


def main(argv):
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv else "both"
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 30
    out = argv[argv.index("--out") + 1] if "--out" in argv else "/tmp/mentor-stats.json"
    s = collect(agent, days)
    with open(os.path.expanduser(out), "w", encoding="utf-8") as fh:
        json.dump(s, fh, ensure_ascii=False, indent=1)
    print(render_digest(s))
    print(f"\n[stats written to {out} — {s['totals'].get('sessions',0)} sessions]")


if __name__ == "__main__":
    main(sys.argv)
