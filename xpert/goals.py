"""Goals: what Arjun is working towards, from what he himself writes.

Goals show in his own words, so only his messages (and the chat each was sent in) are read. A first
pass over chunks proposes candidates with evidence; a second, small call merges them and keeps only
those with real support, because an invented goal costs points."""
from . import llm
from .data import clip, short_time

AREAS = {"self", "spouse", "kids", "parents", "work", "friends", "experiences", "finances", "health", "home"}
TYPES = {"habit", "milestone", "experience"}
HORIZONS = {"3mo", "1yr", "3yr", "5yr"}
CHUNK = 500
MAX_GOALS = 6

FIND = """You read {me}'s own WhatsApp messages (with the chat each was sent in) and find the GOALS he is working towards.
A goal is something he is actively pursuing over weeks or months, not a one-off errand:
- habit: a rhythm he keeps (exercise three times a week, daily yoga, weekly date night)
- milestone: a target (grow a company to N clients, launch a product, raise money, finish a renovation, buy a house)
- experience: something to plan and do (a trip, a celebration, an event)
Areas: self, spouse, kids, parents, work, friends, experiences, finances, health, home.
Horizon: 3mo, 1yr, 3yr or 5yr.

A goal is an AIM, not a topic he chats about. Good: "Grow OurCo's ad product to paying clients", "Play
pickleball three times a week", "Finish the new flat's interiors", "Family safari trip in February",
"Build a long-term stock portfolio". Not goals: being ill for a day, errands, one-off purchases, admin,
logistics, helping someone once, general "manage the household".
Only report goals with clear support in several messages, or one explicit statement of intent. No generic
guesses ("be a good father"). Cite the message ids that show the aim most directly (2 to 6), preferring
messages where he states the plan, target or routine over operational details.
Reply with JSON only: {{"goals": [{{"title": "...", "area": "...", "type": "...", "horizon": "...", "evidence": ["M.."]}}]}}"""

MERGE = """Below are candidate goals for {me}, found in different parts of his chats, each with evidence quotes.
Merge duplicates (keep the union of their evidence ids), drop weak, generic or one-off ones (errands, an illness, admin are not goals), and return the
{n} best-supported goals at most, strongest first. Never invent evidence ids: use only ids shown.
Reply with JSON only: {{"goals": [{{"title": "...", "area": "...", "type": "...", "horizon": "...", "evidence": ["M.."]}}]}}"""


def find_goals(data):
    if not llm.available():
        return []
    me = data.me_name.split()[0]
    mine = [m for m in data.messages if m["sender_id"] == "ME" and len((m.get("body") or "").strip()) >= 15]
    lines = [f'{m["id"]} [{short_time(m["ts"])}] ({clip(data.chat[m["chat_id"]].get("name") or "", 30)}) {clip(m["body"], 220)}' for m in mine]
    parts = [lines[i : i + CHUNK] for i in range(0, len(lines), CHUNK)]
    replies = llm.run_parallel(lambda p: llm.ask_json(FIND.format(me=me), "\n".join(p), max_tokens=2000, tag="goals"), parts)
    cands = []
    for r in replies:
        for g in (r or {}).get("goals", []) or []:
            if isinstance(g, dict) and g.get("title"):
                ev = [e for e in g.get("evidence") or [] if e in data.msg]
                if ev:
                    cands.append({**g, "evidence": ev})
    if not cands:
        return []
    if len(parts) > 1:
        text = []
        for i, g in enumerate(cands):
            quotes = " / ".join(f'{e}: "{clip(data.msg[e]["body"], 100)}"' for e in g["evidence"][:4])
            text.append(f'{i + 1}. {g.get("title")} [{g.get("area")}, {g.get("type")}, {g.get("horizon")}] evidence: {quotes} | all ids: {g["evidence"]}')
        r = llm.ask_json(MERGE.format(me=me, n=MAX_GOALS), "\n".join(text), max_tokens=2000, tag="goals_merge")
        merged = []
        for g in (r or {}).get("goals", []) or []:
            if isinstance(g, dict) and g.get("title"):
                ev = [e for e in g.get("evidence") or [] if e in data.msg]
                if ev:
                    merged.append({**g, "evidence": ev})
        if merged:
            cands = merged
    out = []
    # A goal shown by a single message is usually a passing remark, and an invented goal costs points.
    cands = [g for g in cands if len(set(g["evidence"])) >= 2]
    for g in cands[:MAX_GOALS]:
        out.append({
            "title": clip(str(g["title"]), 120),
            "area": g.get("area") if g.get("area") in AREAS else "self",
            "type": g.get("type") if g.get("type") in TYPES else "milestone",
            "horizon": g.get("horizon") if g.get("horizon") in HORIZONS else "1yr",
            "evidence": list(dict.fromkeys(g["evidence"]))[:8],
        })
    return out
