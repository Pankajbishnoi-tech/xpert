"""People: one circle per person, plus which organisations they run or support.

People who never wrote a message and have no 1:1 chat with Arjun carry no evidence beyond the groups
they sit in, so a rule places them. Everyone else gets a short profile (saved name, 1:1 chat, groups,
a few of their own lines) and the model classifies them in batches."""
import os
import re
from collections import Counter

from . import llm
from .data import clip

BATCH = int(os.environ.get("XPERT_PEOPLE_BATCH", "70"))
CIRCLES = ["spouse", "child", "parent", "in-law", "sibling", "relative", "friend", "colleague", "client", "vendor", "partner", "staff"]

SYSTEM = """You are a sharp new personal assistant on your first day, working out who is who in your boss's life
from his WhatsApp. Your boss is {me_full}. For every person listed, choose exactly ONE circle: their
relationship to {me}:

spouse   - his wife
child    - his own child
parent   - his own father or mother (not his wife's)
in-law   - his wife's parents and siblings, or a sibling's spouse
sibling  - his own brother or sister (cousins are "relative")
relative - any other family: cousins, uncles, aunts, nephews, extended family
friend   - personal friends, sports/hobby buddies, old classmates
colleague- people who work with him in his own companies: employees, co-founders' teams, people he manages
client   - customers/prospects of his businesses (incl. people from client companies in client groups)
vendor   - people he buys services/goods from: suppliers, agencies, contractors, teachers, coaches, brokers, shops
partner  - business partners, co-founders, investors, people he co-runs or co-invests with
staff    - household or office help: cook, maid, driver, guard, caretaker, office boy/cleaner

What you know about his organisations:
{orgs}

Clues: there is exactly ONE spouse, usually the 1:1 chat with by far the most messages about home, kids and
money. His parents may share his surname too: they write like elders (blessings, "beta", asking after
grandchildren). His children are usually young; a same-surname adult who chats like a peer is a sibling or relative.
The saved contact name often says the role (e.g. "Ramesh Driver", "Sita Maid Home", "Amit Nexus",
"Probir sir yoga"). Sharing his surname ({surname}) suggests family; his wife's family name suggests in-laws.
Family groups imply relatives. Members of a client group who are not on his team are clients. A person who sells to or serves him
(supplier, contractor, broker, coach, consultant) is a vendor; a person who buys from his company is a client.
His own employees/team (often saved with the company name, e.g. "Neha PMA") are colleagues. Someone who looks
after a property (caretaker, gardener, office cleaner) is staff.
People he told us about himself (treat as ground truth and use them to calibrate):
{seed}

For each person also give:
- "orgs": for colleague / partner: keys of the organisations above that they help run (may be several);
  for staff: the one place they work, either an organisation key above or a new place such as
  "home", "farmhouse", "office". Otherwise [].

Reply with JSON only: {{"people": [{{"id": "P..", "circle": "...", "orgs": []}}, ...]}} covering every listed person."""


def _samples(msgs, k, width=110):
    """Up to k informative messages, spread across the list."""
    good = [m for m in msgs if len((m.get("body") or "").strip()) >= 12 and not (m.get("body") or "").startswith("http")]
    if not good:
        good = [m for m in msgs if (m.get("body") or "").strip()]
    if len(good) <= k:
        pick = good
    else:
        step = len(good) / k
        pick = [good[int(i * step)] for i in range(k)]
    return [clip(m["body"], width) for m in pick]


def profile(data, pid, chat_org, org_name):
    parts = [pid]
    names = [n for n, _ in data.names[pid].most_common(2)]
    dm = data.dm_of.get(pid)
    if dm:
        dm_name = (data.chat[dm].get("name") or "").strip("‎ ")
        if dm_name and dm_name not in names:
            names.insert(0, dm_name)
    parts.append("saved as " + " / ".join(f'"{clip(n, 40)}"' for n in names) if names else "no saved name")
    if dm:
        msgs = data.by_chat.get(dm, [])
        theirs = [m for m in msgs if m["sender_id"] == pid]
        mine = [m for m in msgs if m["sender_id"] == "ME"]
        parts.append(f"1:1 chat: {len(theirs)} msgs from them, {len(mine)} from {data.me_name.split()[0]}")
        s = _samples(theirs, 4)
        if s:
            parts.append("they say: " + " · ".join(f'"{x}"' for x in s))
        s = _samples(mine, 3)
        if s:
            parts.append("he says: " + " · ".join(f'"{x}"' for x in s))
    groups = data.groups_of.get(pid, [])
    if groups:
        gl = []
        for g in sorted(groups, key=lambda g: -len(data.by_chat.get(g, [])))[:6]:
            tag = f" [{org_name.get(chat_org.get(g), '')}]" if chat_org.get(g) else ""
            gl.append(clip(data.chat[g].get("name") or g, 35) + tag)
        more = f" +{len(groups) - 6} more" if len(groups) > 6 else ""
        parts.append("groups: " + "; ".join(gl) + more)
        if not dm:
            theirs = [m for g in groups for m in data.by_chat.get(g, []) if m["sender_id"] == pid]
            s = _samples(theirs, 3, 90)
            if s:
                parts.append("in groups they say: " + " · ".join(f'"{x}"' for x in s))
    return " | ".join(parts)


def rule_circle(data, pid, chat_org, orgs_by_id):
    """For people with no messages of their own: go by the groups they sit in."""
    votes = Counter()
    for g in data.groups_of.get(pid, []):
        o = orgs_by_id.get(chat_org.get(g))
        name = (data.chat[g].get("name") or "").lower()
        if o and o["side"] == "work":
            votes["client"] += 1
        elif o and o["side"] == "home":
            votes["staff"] += 1
        elif re.search(r"famil|khandan|parivar|cousin|sibling|bro|sis|wedding|shaadi", name):
            votes["relative"] += 1
        else:
            votes["friend"] += 1
    return votes.most_common(1)[0][0] if votes else "friend"


def classify(data, orgs, chat_org):
    """Returns {pid: {"circle": .., "orgs": [..]}} for every person."""
    org_name = {o["id"]: o["name"] for o in orgs}
    orgs_by_id = {o["id"]: o for o in orgs}
    seed = data.seed["circles"]
    result = {}
    ask = []
    for pid in data.people:
        if data.sent[pid] == 0 and pid not in data.dm_of:
            result[pid] = {"circle": seed.get(pid) or rule_circle(data, pid, chat_org, orgs_by_id), "orgs": []}
        else:
            ask.append(pid)

    if llm.available() and ask:
        me_full = data.me_name
        org_lines = "\n".join(f'- key "{o["id"]}": {o["name"]} ({o["type"]}, {o["side"]} side, he is {o["role"]}) {o.get("_about", "")}' for o in orgs) or "- none known"
        seed_lines = "\n".join(f'- {pid} "{data.name(pid)}": {c}' for pid, c in seed.items()) or "- none"
        system = SYSTEM.format(me_full=me_full, me=me_full.split()[0], surname=me_full.split()[-1], orgs=org_lines, seed=seed_lines)
        # Batch people who share chats together, so the model sees a family or a team at once.
        def anchor(pid):
            if pid in data.dm_of and not data.groups_of.get(pid):
                return ("~", pid)
            g = min(data.groups_of.get(pid, ["~"]), key=lambda g: len(data.chat[g]["participants"]) if g in data.chat else 999)
            return (g, pid)
        ask.sort(key=anchor)
        batches = [ask[i : i + BATCH] for i in range(0, len(ask), BATCH)]

        def run(batch):
            text = "\n".join(profile(data, pid, chat_org, org_name) for pid in batch)
            return llm.ask_json(system, "People:\n" + text, max_tokens=3000, tag="circles")

        for batch, reply in zip(batches, llm.run_parallel(run, batches)):
            got = {}
            for p in (reply or {}).get("people", []) or []:
                if isinstance(p, dict) and p.get("id") in batch and p.get("circle") in CIRCLES:
                    got[p["id"]] = p
            for pid in batch:
                p = got.get(pid)
                if p:
                    o = p.get("orgs") or []
                    result[pid] = {"circle": p["circle"], "orgs": [str(x) for x in (o if isinstance(o, list) else [o])]}
                else:
                    result[pid] = {"circle": rule_circle(data, pid, chat_org, orgs_by_id), "orgs": []}
    else:
        for pid in ask:
            result[pid] = {"circle": rule_circle(data, pid, chat_org, orgs_by_id), "orgs": []}

    # Exactly one spouse: keep the one he talks to most, demote the rest.
    spouses = [p for p in result if result[p]["circle"] == "spouse" and p not in seed]
    if any(c == "spouse" for c in seed.values()):
        keep = None
    else:
        keep = max(spouses, key=lambda p: len(data.by_chat.get(data.dm_of.get(p), [])), default=None)
    surname = data.me_name.split()[-1].lower()
    for p in spouses:
        if p != keep:
            same = any(surname in n.lower() for n in list(data.names[p]) + [data.name(p)])
            result[p]["circle"] = "parent" if same else "relative"

    for pid, c in seed.items():  # what Arjun told us wins
        if pid in result:
            result[pid]["circle"] = c
    return result
