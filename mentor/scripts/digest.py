#!/usr/bin/env python3
"""Condense an agent session transcript into a compact digest the mentor reads.

Works for two agents:
  - claude : Claude Code transcripts  ~/.claude/projects/<slug>/<uuid>.jsonl
  - codex  : OpenAI Codex rollouts    ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
             (also ~/.codex/archived_sessions/rollout-*.jsonl)

Both are JSONL but with different shapes; each parser normalizes to the same
dict, so render() is shared. The digest keeps signal (verbatim user prompts,
tool mix, token spend, correction signals) and drops noise (assistant prose,
tool-result bodies).

Usage:
    uv run --no-project python digest.py <path.jsonl>
    uv run --no-project python digest.py last --agent claude
    uv run --no-project python digest.py last --agent codex
    uv run --no-project python digest.py last --agent claude --exclude <session-id>
    uv run --no-project python digest.py --self-check
"""
import json, sys, glob, os, re
from collections import Counter
from datetime import datetime

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CODEX_HOME = os.path.expanduser("~/.codex")

# ponytail: heuristic only — the model does the real reading. Flags turns that
# look like the user correcting/redirecting, a proxy for an under-specified goal.
CORRECTION_RE = re.compile(
    r"\b(нет|не так|не то|стоп|подожди|я( же)? (просил|говорил|сказал)|зачем|"
    r"почему ты|верни|откати|отмени|это не|опять|снова|no,? |wrong|revert|undo|"
    r"that's not|not what|why did you|stop|i said|i asked)\b",
    re.IGNORECASE,
)
NOISE_MARKERS = ("<command-name>", "<local-command", "caveat", "<system-reminder",
                 "tool_result", "[request interrupted", "<command-message>",
                 "<recommended_plugins", "<user_instructions", "<environment_context")

# Injected context blocks that agents prepend to the user's real message. The
# real prompt lives AFTER them, so strip the blocks before deciding noise/real.
# Codex wraps ambient UI state; Claude injects system reminders and hooks.
INJECTED_TAGS = ("in-app-browser-context", "recommended_plugins", "environment_context",
                 "user_instructions", "system-reminder", "user-prompt-submit-hook",
                 "ide_context", "ide_opened_file", "local-command-caveat")


def clean_injected(text):
    if not text:
        return ""
    for tag in INJECTED_TAGS:
        text = re.sub(rf"<{tag}\b.*?(</{tag}>|\Z)", "", text, flags=re.S | re.I)
    # Codex prefixes the real ask with this marker
    text = re.sub(r"^\s*#+\s*My request for Codex:\s*", "", text)
    return text.strip()


def detect_agent(path):
    base = os.path.basename(path)
    return "codex" if base.startswith("rollout-") or "/.codex/" in path else "claude"


def latest_transcript(agent, exclude=None):
    if agent == "codex":
        files = glob.glob(f"{CODEX_HOME}/sessions/**/rollout-*.jsonl", recursive=True)
        files += glob.glob(f"{CODEX_HOME}/archived_sessions/rollout-*.jsonl")
    else:
        files = [f for f in glob.glob(f"{CLAUDE_PROJECTS}/**/*.jsonl", recursive=True)
                 if "/subagents/" not in f and "/tool-results/" not in f]
    if exclude:
        files = [f for f in files if exclude not in f]
    return max(files, key=os.path.getmtime) if files else None


def is_real_prompt(text):
    if not text or not text.strip():
        return False
    head = text.lstrip()[:40].lower()
    if head.startswith("[") or head.startswith("<"):
        return False
    return not any(m in text.lower() for m in NOISE_MARKERS)


EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _blank(path, agent):
    return dict(path=path, agent=agent, meta={}, models=set(), skills=set(),
                prompts=[], tools=Counter(), out_tok=0, in_tok=0, cache_tok=0,
                corrections=0, interrupts=0, refusals=0, tool_errors=0,
                first_ts=None, last_ts=None, files=set(), edits=0)


def parse_claude(path):
    d = _blank(path, "claude")
    for line in open(path, encoding="utf-8"):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = o.get("timestamp")
        if ts:
            d["first_ts"] = d["first_ts"] or ts
            d["last_ts"] = ts
        if o.get("cwd") and "cwd" not in d["meta"]:
            d["meta"]["cwd"] = o["cwd"]
        if o.get("gitBranch"):
            d["meta"]["branch"] = o["gitBranch"]
        if o.get("attributionSkill"):
            d["skills"].add(o["attributionSkill"])
        if o.get("interruptedMessageId"):
            d["interrupts"] += 1
        if o.get("apiRefusalCategory"):
            d["refusals"] += 1
        m = o.get("message")
        if not isinstance(m, dict):
            continue
        if m.get("model"):
            d["models"].add(m["model"])
        u = m.get("usage")
        if isinstance(u, dict):
            d["out_tok"] += u.get("output_tokens", 0) or 0
            d["in_tok"] += u.get("input_tokens", 0) or 0
            d["cache_tok"] += (u.get("cache_read_input_tokens", 0) or 0) + \
                              (u.get("cache_creation_input_tokens", 0) or 0)
        role = m.get("role")
        if role == "user" and not o.get("isMeta"):
            c = m.get("content")
            raw = c if isinstance(c, str) else "\n".join(
                b["text"] for b in c if isinstance(b, dict) and b.get("type") == "text"
            ) if isinstance(c, list) else ""
            t = clean_injected(raw)
            if is_real_prompt(t):
                d["prompts"].append(t)
                if CORRECTION_RE.search(t):
                    d["corrections"] += 1
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        d["tool_errors"] += 1
        elif role == "assistant":
            c = m.get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        d["tools"].update([name])
                        if name in EDIT_TOOLS:
                            d["edits"] += 1
                            fp = (b.get("input") or {}).get("file_path")
                            if fp:
                                d["files"].add(fp)
    return d


def parse_codex(path):
    d = _blank(path, "codex")
    last_usage = None
    for line in open(path, encoding="utf-8"):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = o.get("timestamp")
        if ts:
            d["first_ts"] = d["first_ts"] or ts
            d["last_ts"] = ts
        p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        pt = p.get("type")
        if o.get("type") == "session_meta":
            if p.get("cwd"):
                d["meta"].setdefault("cwd", p["cwd"])
        elif o.get("type") == "turn_context":
            if p.get("cwd") and "cwd" not in d["meta"]:
                d["meta"]["cwd"] = p["cwd"]
            if p.get("model"):
                d["models"].add(p["model"])
        elif pt == "user_message":
            t = clean_injected(p.get("message", ""))
            if is_real_prompt(t):
                d["prompts"].append(t)
                if CORRECTION_RE.search(t):
                    d["corrections"] += 1
        elif pt == "custom_tool_call":
            d["tools"].update([p.get("name", "?")])
        elif pt == "function_call":
            d["tools"].update([p.get("name", "function_call")])
        elif pt == "mcp_tool_call_end":
            d["tools"].update(["mcp_tool"])
        elif pt == "web_search_end":
            d["tools"].update(["web_search"])
        elif pt == "patch_apply_end":
            d["edits"] += 1
            fp = p.get("path") or (p.get("changes") or {})
            if isinstance(fp, str):
                d["files"].add(fp)
            elif isinstance(fp, dict):
                d["files"].update(fp.keys())
        elif pt == "custom_tool_call_output":
            out = p.get("output")
            if isinstance(out, dict) and (out.get("exit_code") not in (0, None)):
                d["tool_errors"] += 1
        elif pt == "token_count":
            info = p.get("info") or {}
            tot = info.get("total_token_usage") or {}
            if tot:
                last_usage = tot  # cumulative — keep the latest
    if last_usage:
        d["out_tok"] = (last_usage.get("output_tokens", 0) or 0) + \
                       (last_usage.get("reasoning_output_tokens", 0) or 0)
        d["in_tok"] = last_usage.get("input_tokens", 0) or 0
        d["cache_tok"] = last_usage.get("cached_input_tokens", 0) or 0
    return d


def parse(path, agent=None):
    agent = agent or detect_agent(path)
    return parse_codex(path) if agent == "codex" else parse_claude(path)


def duration(a, b):
    try:
        d = datetime.fromisoformat(b[:19]) - datetime.fromisoformat(a[:19])
        mins = int(d.total_seconds() // 60)
        return f"{mins//60}h {mins%60}m" if mins >= 60 else f"{mins}m"
    except Exception:
        return "?"


def render(d):
    tot_tools = sum(d["tools"].values())
    top = ", ".join(f"{n}×{c}" for n, c in d["tools"].most_common(12))
    L = [f"# Session digest [{d['agent']}] — {os.path.basename(d['path'])}\n"]
    L.append(f"- cwd: `{d['meta'].get('cwd')}`  branch: `{d['meta'].get('branch','n/a')}`")
    L.append(f"- model(s): {', '.join(sorted(d['models'])) or '?'}")
    L.append(f"- span: {d['first_ts']} → {d['last_ts']}  ({duration(d['first_ts'], d['last_ts'])})")
    if d["agent"] == "claude":
        L.append(f"- skills invoked: {', '.join(sorted(d['skills'])) or 'none'}")
    L.append("\n## Metrics")
    L.append(f"- user prompts: **{len(d['prompts'])}**")
    L.append(f"- tool calls: **{tot_tools}** ({top})")
    L.append(f"- output tokens: **{d['out_tok']:,}**  |  input(+cache): {d['in_tok']+d['cache_tok']:,}")
    L.append(f"- correction-like prompts (heuristic): **{d['corrections']}**  |  "
             f"interrupts: {d['interrupts']}  |  tool errors: {d['tool_errors']}  |  refusals: {d['refusals']}")
    L.append("\n## User prompts (verbatim — analyze HOW goals were framed)")
    for i, p in enumerate(d["prompts"], 1):
        L.append(f"{i}. {p[:500].replace(chr(10), ' ')}")
    return "\n".join(L)


def _self_check():
    import tempfile
    claude_rows = [
        {"type": "user", "timestamp": "2026-07-19T01:00:00Z", "cwd": "/tmp", "gitBranch": "main",
         "message": {"role": "user", "content": "сделай X с чётким критерием"}},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-fable-5",
         "usage": {"output_tokens": 100, "input_tokens": 5, "cache_read_input_tokens": 10},
         "content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "user", "message": {"role": "user", "content": "нет, я же просил не так"}},
        # real prompt hidden behind an injected reminder block — must still be extracted
        {"type": "user", "message": {"role": "user",
         "content": "<system-reminder>bg noise</system-reminder>\nисправь баг в файле"}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"}},
    ]
    codex_rows = [
        {"type": "session_meta", "timestamp": "2026-07-19T01:00:00Z",
         "payload": {"type": "session_meta", "cwd": "/tmp/proj"}},
        {"type": "turn_context", "payload": {"type": "turn_context", "model": "gpt-5.6-sol", "cwd": "/tmp/proj"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "собери данные и покажи"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "input": "ls"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "no, wrong, revert that"}},
        # the real ask sits AFTER an ambient block and the Codex marker
        {"type": "event_msg", "payload": {"type": "user_message", "message":
         "\n<in-app-browser-context source=\"ambient-ui-state\">\nstate\n</in-app-browser-context>\n\n## My request for Codex:\nсделай спиннеры поменьше"}},
        {"type": "response_item", "payload": {"type": "patch_apply_end", "path": "/tmp/proj/app.ts"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "apply_patch"}},
        {"type": "event_msg", "timestamp": "2026-07-19T01:05:00Z", "payload": {"type": "token_count",
         "info": {"total_token_usage": {"input_tokens": 200, "output_tokens": 50,
                  "reasoning_output_tokens": 20, "cached_input_tokens": 30}}}},
    ]
    for rows, agent in [(claude_rows, "claude"), (codex_rows, "codex")]:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            path = fh.name
        d = parse(path, agent)
        os.unlink(path)
        if agent == "claude":
            assert len(d["prompts"]) == 3, d["prompts"]       # incl. the reminder-wrapped one
            assert "исправь баг в файле" in d["prompts"], d["prompts"]
            assert d["corrections"] == 1 and d["out_tok"] == 100
            assert d["tools"]["Bash"] == 1 and "claude-fable-5" in d["models"]
        else:
            assert len(d["prompts"]) == 3, d["prompts"]       # ambient-wrapped prompt recovered
            assert "сделай спиннеры поменьше" in d["prompts"], d["prompts"]
            assert d["corrections"] == 1, d["corrections"]    # "no, wrong, revert"
            assert d["out_tok"] == 70, d["out_tok"]           # 50 + 20 reasoning
            assert d["in_tok"] == 200 and d["cache_tok"] == 30
            assert d["tools"]["exec"] == 1 and d["tools"]["apply_patch"] == 1
            assert d["edits"] == 1 and "/tmp/proj/app.ts" in d["files"]
            assert "gpt-5.6-sol" in d["models"]
        assert "Session digest" in render(d)
    print("self-check OK (claude + codex)")


def main(argv):
    if "--self-check" in argv:
        return _self_check()
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv else None
    exclude = argv[argv.index("--exclude") + 1] if "--exclude" in argv else None
    skip = {exclude, agent}
    target = next((a for a in argv[1:] if not a.startswith("--") and a not in skip), None)
    if target in (None, "last"):
        path = latest_transcript(agent or "claude", exclude)
        if not path:
            sys.exit(f"no {agent or 'claude'} transcript found")
    else:
        path = os.path.expanduser(target)
        if not os.path.exists(path):
            sys.exit(f"not found: {path}")
    print(render(parse(path, agent)))


if __name__ == "__main__":
    main(sys.argv)
