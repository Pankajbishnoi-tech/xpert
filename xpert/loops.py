"""Open loops and where to help.

Only the last two weeks are scored, so only chats with activity in that window are read, and only
chats where a loop for Arjun can plausibly arise: his 1:1 chats, small groups, and groups he himself
writes in. Each chat is cut into chunks; every chunk carries some earlier messages as context and some
later ones so the model can tell whether a loop got closed. One model call per chunk finds the loops,
their status, and the help decision together, which keeps the cost to one pass over the window."""
import os
import re
from datetime import datetime, timedelta

from . import llm
from .data import IST, clip, ist

LOOP_TYPES = {"commitment", "followup", "deadline", "question"}
CHUNK = int(os.environ.get("XPERT_LOOP_CHUNK", "250"))     # messages the model extracts loops from
BEFORE = 25      # earlier messages shown as context only
AFTER = 120      # later messages shown only to judge status

SYSTEM = """You are {me}'s personal assistant. You read his WhatsApp and keep a log of every LOOP raised in it: a task,
request, promise, question or date that {me} has to act on, or that he is waiting on someone else for.
Log every loop raised in the MAIN sections, INCLUDING loops that were handled or closed later (status "done"
or "dropped"): we need the full history, not only what is still pending. Many loops are closed within
minutes or hours (a request to pay, then a payment screenshot; a question, then an answer).
{me} is the user (shown as "{me}"). Today is {today}. Below are one or more chats, each with its own list of
people and sections.

Loop types:
- commitment: {me} promised or agreed to do something ("will send tomorrow", "ill buy it", "sure, will connect"),
  or someone close asked him to do something and it is now his to do ("pls pay 2800", "pls order one more").
- followup: {me} asked someone else for something and is waiting on them (owner = that person's id).
- deadline: something with a date that {me} must meet (a payment due, an RSVP/confirmation by a date).
- question: {me} must decide or answer something put to him ("can we meet tomorrow 11-2?", "should I get the bigger one?").

What counts: requests, promises, decisions and asks that involve {me} directly and need a real action.
What does NOT count: chit-chat, greetings, forwards, news, jokes, FYIs, things done in the same breath,
tasks between other people that do not need {me}, broadcasts not addressed to him, marketing messages.
In groups, only take items addressed to {me} or that {me} took on himself. In work and client groups, routine
work his team does (adding creatives, pausing or editing campaigns, sending invoices, granting access, team members
attending client calls) is NOT his loop unless {me} personally promised it or was personally asked. A close
friend's or relative's birthday announced in a chat he is in IS a loop: wish them (owner ME, due that day). One loop per real-world task:
do not repeat the same task. Typical yield: a busy 1:1 chat with family gives a loop every few dozen
messages; most work groups give few.

For each loop give:
- "chat": the chat id the loop is in.
- "source": the id of the ONE message that makes the loop concrete: the message with the actual ask, promise or
  question in it, not the context around it. Examples: after "Dev's bday party 27th sept" / "Invite to be made",
  the source is "Invite to be made"; after a link and "we should build X" / "Please discuss with me tomorrow",
  it is "Please discuss with me tomorrow"; when someone asks "any update?" and {me} answers "reverting tomorrow",
  the loop is his promise, so the source is "reverting tomorrow"; when a plan is re-fixed ("lets fix a call for
  tomorrow", "3 pm ill reach"), the source is the message that sets the current plan. Never a link, photo or
  bare "ok". It MUST be one of the ids in that chat's MAIN section.
- "type", "owner" ("ME" or the person id who owes it), "title" (short imperative, e.g. "Pay Nayna ₹2,800"),
- "weight": 1 = trivial (banter, a one-word ask done instantly, a joke "rule", small talk about plans),
  2 = a real task, 3 = important (money, kids, health, clients, deadlines).
- "due": "YYYY-MM-DD" if a date is stated or clearly implied (tomorrow, Saturday), else null.
- "status" at the END of everything you can see (MAIN and LATER):
  "done" when the messages show it happened: a payment screenshot or "paid"/"sent"/"ordered"/"done"/"booked",
  the item delivered, the meeting held or its time agreed, the question answered, the asker saying thanks
  or "ok" to the result, or the date passed with the event clearly having taken place.
  "dropped" if it was cancelled, refused, superseded, or plainly abandoned.
  Otherwise "open". When unsure, say "open": a false "done" hides real work.
- "closed_by": for done/dropped, the id of the message that shows it, else null.
- "help", only when status is "open", choose one:
  {{"action": "assist", "next_step": "one line", "needs_yes": true/false}}: the assistant can move it forward
     itself: find options, compare prices, draft a reply, book or order something, set up a meeting, look
     something up. needs_yes is true if the step sends a message, pays, buys or books (leaves his phone or
     cannot be undone); false if it is private prep work.
  {{"action": "remind", "remind_at": "YYYY-MM-DDTHH:MM:00+05:30"}}: only {me} can do it (a call, a personal
     decision, a meeting, something physical); remind him once at a sensible time before it matters.
  {{"action": "leave", "reason": "..."}}: stay out: someone else owns it and is handling it, it is staff's job,
     it is personal or emotional (his wife's feelings, family matters), or a reminder would only annoy him.

Reply with JSON only: {{"loops": [{{"chat": "C..", "source": "M..", "type": "...", "owner": "...", "title": "...", "weight": 2, "due": null,
"status": "open", "closed_by": null, "help": {{...}}}}]}}. If there are none, reply {{"loops": []}}."""


def window_start(data):
    """Midnight IST, 14 days before the day the data ends, minus one day of slack."""
    end = ist(data.end_ts)
    day = datetime(end.year, end.month, end.day, tzinfo=IST) - timedelta(days=15)
    return day.timestamp()


def wanted(data, c, msgs_in_window):
    if not msgs_in_window:
        return False
    if not c["is_group"]:
        allm = data.by_chat[c["chat_id"]]
        if any(m["sender_id"] == "ME" for m in allm):
            return True
        return len(msgs_in_window) <= 40  # a 1:1 chat he never answers and that is long is a broadcast
    if len(c["participants"]) <= 4:
        return True
    return any(m["sender_id"] == "ME" for m in msgs_in_window)


def chunks(data, since):
    out = []
    for c in data.chats:
        allm = data.by_chat.get(c["chat_id"], [])
        first = next((i for i, m in enumerate(allm) if m["ts"] >= since), None)
        if first is None:
            continue
        if not wanted(data, c, allm[first:]):
            continue
        i = first
        while i < len(allm):
            main = allm[i : i + CHUNK]
            out.append((c, allm[max(0, i - BEFORE) : i], main, allm[i + CHUNK : i + CHUNK + AFTER]))
            i += CHUNK
    return out


def people_line(data, c, circles):
    parts = []
    for p in c["participants"]:
        if p == "ME":
            continue
        circ = circles.get(p, {}).get("circle", "?")
        parts.append(f"{data.name(p)} ({p}, {circ})")
    if len(parts) > 25:
        talkers = {m["sender_id"] for m in data.by_chat.get(c["chat_id"], [])}
        parts = [x for x, p in zip(parts, [p for p in c["participants"] if p != "ME"]) if p in talkers][:25]
    return "; ".join(parts) or "none"


PACK = int(os.environ.get("XPERT_LOOP_PACK", "500"))  # messages per request, small chats packed together


def block(data, c, circles, before, main, after):
    kind = "group chat" if c["is_group"] else "1:1 chat"
    parts = [f'=== Chat {c["chat_id"]}: "{clip(c.get("name") or "", 60)}" ({kind}). People: {people_line(data, c, circles)}']
    if before:
        parts.append("EARLIER (context only, do not take loops from here):\n" + "\n".join(data.fmt(m, 200) for m in before))
    parts.append("MAIN (take loops from here):\n" + "\n".join(data.fmt(m) for m in main))
    if after:
        parts.append("LATER (only to judge status):\n" + "\n".join(data.fmt(m, 200) for m in after))
    return "\n".join(parts)


def extract(data, circles):
    since = window_start(data)
    me = data.me_name.split()[0]
    today = ist(data.end_ts).strftime("%A %d %B %Y")
    system = SYSTEM.format(me=me, today=today)
    work = chunks(data, since)
    packs, cur, size = [], [], 0
    for item in work:
        n = len(item[1]) + len(item[2]) + len(item[3])
        if cur and size + n > PACK:
            packs.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += n
    if cur:
        packs.append(cur)

    def run(pack):
        text = "\n\n".join(block(data, *it[:1], circles, *it[1:]) for it in pack)
        return llm.ask_json(system, text, max_tokens=4000, tag="loops")

    results = llm.run_parallel(run, packs) if (llm.available() and packs) else []
    loops, seen = [], set()
    persons = set(data.people)
    for pack, reply in zip(packs, results):
        where = {}
        for c, before, main, after in pack:
            visible = {m["id"] for m in main} | {m["id"] for m in after}
            for m in main:
                where[m["id"]] = (c, visible)
        for l in (reply or {}).get("loops", []) or []:
            if not isinstance(l, dict):
                continue
            src = str(l.get("source") or "")
            if src not in where or src in seen:
                continue
            if str(l.get("weight")) == "1":
                continue
            seen.add(src)
            c, visible = where[src]
            loops.append(clean(l, c, src, visible, persons, data))
    return dedupe(data, loops)


MERGE = """Below is a log of tasks ("loops") found in someone's WhatsApp chats, one per line:
index | chat | date of first message | status | title.
The log was built from overlapping pieces, so the same real-world task often appears several times
(e.g. "Book Hyderabad ticket", "Confirm Hyderabad ticket preference", "Book Hyderabad flight for Nayna").
Group the indices that are the SAME task in the SAME chat. Different tasks stay separate, even if similar
(paying 2,800 and later paying 4,500 are two tasks).
Reply with JSON only: {"groups": [[3, 7, 9], [12, 15]]} listing only groups of 2 or more."""


def dedupe(data, loops):
    """Merge repeats of one task: keep the earliest source, take status and help from the latest."""
    if len(loops) < 2 or not llm.available():
        return loops
    loops.sort(key=lambda l: (l["chat_id"], data.msg[l["source_msg"]]["ts"]))
    lines = [f'{i} | {l["chat_id"]} | {ist(data.msg[l["source_msg"]]["ts"]).strftime("%d %b")} | {l["status"]} | {l["title"]}' for i, l in enumerate(loops)]
    reply = llm.ask_json(MERGE, "\n".join(lines), max_tokens=3000, tag="loops_merge")
    drop = set()
    for g in (reply or {}).get("groups", []) or []:
        try:
            idx = sorted({int(x) for x in g if 0 <= int(x) < len(loops)})
        except (TypeError, ValueError):
            continue
        idx = [i for i in idx if i not in drop and loops[i]["chat_id"] == loops[idx[0]]["chat_id"]] if idx else []
        if len(idx) < 2:
            continue
        first, last = loops[idx[0]], loops[idx[-1]]
        for k in ("status", "closed_by", "help"):
            first[k] = last[k]
        if first["due"] is None:
            first["due"] = last["due"]
        drop.update(idx[1:])
    return [l for i, l in enumerate(loops) if i not in drop]


def _iso_ok(s):
    try:
        datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def clean(l, c, src, visible, persons, data):
    typ = l.get("type") if l.get("type") in LOOP_TYPES else "commitment"
    owner = str(l.get("owner") or "ME")
    if owner != "ME" and owner not in persons:
        owner = "ME"
    due = l.get("due")
    due = due if isinstance(due, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) else None
    status = l.get("status") if l.get("status") in ("open", "done", "dropped") else "open"
    closed_by = l.get("closed_by")
    if status != "open":
        if closed_by not in visible or closed_by == src:
            # no message to show it closed: a false "done" costs more than a missed one
            status, closed_by = "open", None
    else:
        closed_by = None
    out = {
        "title": clip(str(l.get("title") or "Follow up"), 120),
        "type": typ,
        "owner": owner,
        "chat_id": c["chat_id"],
        "source_msg": src,
        "due": due,
        "status": status,
        "closed_by": closed_by,
    }
    out["help"] = clean_help(l.get("help"), out, data) if status == "open" else None
    return out


def clean_help(h, loop, data):
    h = h if isinstance(h, dict) else {}
    action = h.get("action")
    if action not in ("assist", "remind", "leave"):
        action = "leave" if loop["owner"] != "ME" else "remind"
    if action == "assist":
        return {"action": "assist", "next_step": clip(str(h.get("next_step") or loop["title"]), 160), "needs_yes": bool(h.get("needs_yes", True))}
    if action == "remind":
        at = h.get("remind_at")
        if not at or not _iso_ok(at):
            base = ist(data.end_ts) + timedelta(days=1)
            if loop["due"]:
                try:
                    base = datetime.fromisoformat(loop["due"]).replace(tzinfo=IST) - timedelta(days=1)
                except ValueError:
                    pass
            at = base.replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
        return {"action": "remind", "remind_at": str(at)}
    return {"action": "leave", "reason": clip(str(h.get("reason") or "someone else is handling it"), 160)}
