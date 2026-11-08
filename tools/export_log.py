#!/usr/bin/env python3
"""Turn a Claude Code session transcript (.jsonl) into a readable Markdown log for ai-logs/.

Keeps what shows how the work was done: the user's messages, Claude's replies, and every tool call
(commands run, files written). Drops tool OUTPUT, because it printed challenge messages, which must not
be committed, and drops hidden reasoning and app plumbing. Redacts API keys, email addresses and the
home folder path.

    python3 tools/export_log.py <session.jsonl> <out.md>
"""
import json
import re
import sys

KEY = re.compile(r"sk-or-v1-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{32,}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
HOME = re.compile(r"/Users/[^/\s]+")
SCRATCH = re.compile(r"~/Library/Application Support/Claude/scratch-workspaces/[^\s\"']*?/scratch-[\w-]+")


def scrub(text):
    text = REMINDER.sub("", text or "")
    text = KEY.sub("[API KEY REDACTED]", text)
    text = EMAIL.sub("[email]", text)
    text = HOME.sub("~", text)
    text = SCRATCH.sub("<session folder>", text)
    return text.strip()


def tool_call(block):
    name, inp = block.get("name", ""), block.get("input") or {}
    if name == "Bash":
        body = inp.get("command", "")
        return f"**Bash** — {scrub(inp.get('description', ''))}\n```bash\n{scrub(body)}\n```"
    if name in ("Write",):
        content = inp.get("content", "")
        lines = content.splitlines()
        shown = "\n".join(lines[:60]) + (f"\n… ({len(lines) - 60} more lines)" if len(lines) > 60 else "")
        return f"**Write** `{scrub(inp.get('file_path', ''))}`\n```\n{scrub(shown)}\n```"
    if name == "Edit":
        return (f"**Edit** `{scrub(inp.get('file_path', ''))}`\n```diff\n"
                + "\n".join("- " + l for l in scrub(inp.get("old_string", "")).splitlines()[:30]) + "\n"
                + "\n".join("+ " + l for l in scrub(inp.get("new_string", "")).splitlines()[:40]) + "\n```")
    if name == "Read":
        return f"**Read** `{scrub(inp.get('file_path', ''))}`"
    if name.startswith("mcp__ccd_turn") or name in ("ToolSearch",):
        return None  # app plumbing (task list, wrap-up boxes)
    short = json.dumps(inp, ensure_ascii=False)
    return f"**{name}** `{scrub(short[:300])}`"


def main(src, dst):
    out = ["# Claude Code session: Cold Start challenge", "",
           "Exported from the session transcript with `tools/export_log.py`. Tool outputs are omitted "
           "because they printed challenge data; API keys, emails and home paths are redacted.", ""]
    for line in open(src, encoding="utf-8"):
        r = json.loads(line)
        if r.get("type") not in ("user", "assistant") or r.get("isMeta"):
            continue
        msg = r.get("message") or {}
        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        if r["type"] == "user":
            texts = [scrub(b.get("text", "")) for b in blocks if b.get("type") == "text"]
            texts = [t for t in texts if t and not t.startswith("[SYSTEM NOTIFICATION") and "<task-notification>" not in t]
            if r.get("isCompactSummary") or any(t.startswith("This session is being continued") for t in texts):
                out += ["---", "*[Context was summarised here; the conversation continues.]*", ""]
                continue
            if texts:
                out += ["---", "### User", "", *texts, ""]
        else:
            for b in blocks:
                if b.get("type") == "text" and scrub(b.get("text")):
                    out += ["### Claude", "", scrub(b["text"]), ""]
                elif b.get("type") == "tool_use":
                    t = tool_call(b)
                    if t:
                        out += [t, "*(output omitted)*", ""]
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
