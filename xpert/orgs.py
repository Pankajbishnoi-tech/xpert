"""Organisations: find the companies, home and places Arjun runs or belongs to, and which group
chats belong to each. One model call over a compact list of group chats."""
import re
from collections import Counter

from . import llm
from .data import clip

ORG_TYPES = {"company", "residence", "vacation home", "workspace", "society", "school", "other"}
ROLES = {"owner", "partner", "helper", "resident", "parent", "member", "other"}

SYSTEM = """You map out the organisations in one person's life from their WhatsApp group chats.
The user is {me}. An organisation is something {me} runs or belongs to, with its own people and chats:
his companies, his home (household staff, cook, driver, the kids' carpool, building/society),
a holiday or farm house, an office, a housing society, the kids' school.

Rules:
- {me} told us at signup that he has these organisations: {seed}. Always include them.
- A client's company is NOT an organisation. A group with a client or prospect, named after the client
  and {me}'s company (e.g. "Acme: OurCo - Ads"), belongs to {me}'s company.
- Communities, forums, partner and vendor groups that a company of his runs or uses for its business
  belong to that company. Abbreviations and initials count (e.g. "XYZ" for "Xavier Yellow Zinc").
  Industry circles and forums he runs or sits in for business (founder or marketer circles, "D2C Circle North",
  "D2B Connect East", a regional community) belong to the company whose customers they are, even when the
  name does not mention it: if his company sells to D2C brands, the D2C/D2B circles belong to it.
- A company run by his spouse or a family member where {me} only helps out (a group named after a business,
  with his wife in it, talking about her orders/operations): its own organisation, role "helper".
- A second company or product line of his (e.g. "OurCo Labs", "OurCo DaaS") with its own team is its own organisation.
- Home side: groups that run the household (cook, kitchen, staff, groceries, kids' carpool, a house help or
  driver coordinating with him) belong to a "Home" residence. Family groups, cousins' groups, friends' groups named
  after a house, a season or a feeling ("Summer", "Home is where the heart is") are NOT Home: map them to null. A farm house / holiday home is its own organisation (type "vacation home").
- A group named after a building, house or club where friends meet socially is NOT an organisation.
- Friends' groups, family groups, hobby/sports groups, alumni, generic news/investing groups and
  unrelated communities belong to NO organisation: map them to null.
- Do not invent organisations without chats to show for them, except the seeded ones.
- Keep the list short: usually 3 to 8 organisations.

Reply with JSON only:
{{"organisations": [{{"key": "short_id", "name": "...", "type": "company|residence|vacation home|workspace|society|school|other",
   "side": "work|home", "role": "owner|partner|helper|resident|parent|member|other", "about": "one line"}}],
 "chats": {{"<chat_id>": "<organisation key or null>", ...every group chat listed...}}}}"""


def group_line(data, c):
    msgs = data.by_chat.get(c["chat_id"], [])
    me_n = sum(m["sender_id"] == "ME" for m in msgs)
    samples, seen = [], set()
    for m in msgs:
        body = (m.get("body") or "").strip()
        if len(body) < 25 or body.startswith("http") or m["sender_id"] in seen:
            continue
        seen.add(m["sender_id"])
        samples.append(f'{data.name(m["sender_id"])}: "{clip(body, 90)}"')
        if len(samples) == 3:
            break
    name = clip(c.get("name") or "", 60)
    return f'{c["chat_id"]} "{name}" | {len(c["participants"])} members | {len(msgs)} msgs, {data.me_name.split()[0]} wrote {me_n} | ' + " / ".join(samples)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:30] or "org"


def find_orgs(data):
    """Returns (organisations, {group chat_id: org id or None})."""
    groups = [c for c in data.chats if c["is_group"]]
    me = data.me_name.split()[0]
    seed = ", ".join(data.seed["organisations"]) or "none"
    reply = None
    if llm.available() and groups:
        lines = "\n".join(group_line(data, c) for c in groups)
        reply = llm.ask_json(SYSTEM.format(me=me, seed=seed), "Group chats:\n" + lines, max_tokens=6000, tag="orgs")

    orgs, chat_org = [], {}
    if reply and isinstance(reply.get("organisations"), list):
        used = set()
        for o in reply["organisations"]:
            if not isinstance(o, dict) or not o.get("name"):
                continue
            oid = slug(o.get("key") or o["name"])
            while oid in used:
                oid += "_x"
            used.add(oid)
            orgs.append({
                "id": oid,
                "name": str(o["name"])[:60],
                "type": o.get("type") if o.get("type") in ORG_TYPES else "company",
                "side": o.get("side") if o.get("side") in ("home", "work") else "work",
                "role": o.get("role") if o.get("role") in ROLES else "owner",
                "_key": slug(o.get("key") or ""),
                "_about": clip(str(o.get("about") or ""), 120),
            })
        keymap = {}
        for o in orgs:
            keymap[o["id"]] = o["id"]
            if o["_key"]:
                keymap.setdefault(o["_key"], o["id"])
            keymap.setdefault(slug(o["name"]), o["id"])
        for cid, k in (reply.get("chats") or {}).items():
            if cid in data.chat and data.chat[cid]["is_group"] and k:
                chat_org[cid] = keymap.get(slug(str(k)))
    else:
        orgs, chat_org = rule_orgs(data)

    # A business group the model left out ("Radiant operations", "Acme team"): a company of its own.
    # If he never writes there, he is helping someone else run it.
    biz = re.compile(r"^\s*([A-Za-z][\w&.' -]{1,30}?)\s*[:\-]?\s+(operations|ops|team|orders|sales|accounts|admin)\s*$", re.I)
    for c in groups:
        m = biz.match(c.get("name") or "")
        if not m:
            continue
        name = m.group(1).strip()
        oid = slug(name)
        cur = next((o for o in orgs if o["id"] == chat_org.get(c["chat_id"])), None)
        if cur and _same_name(name, cur["name"]):
            continue  # "PMA Operations" already sits under PostMyAd
        if not any(oid in (o["id"], slug(o["name"])) for o in orgs):
            wrote = any(x["sender_id"] == "ME" for x in data.by_chat.get(c["chat_id"], []))
            orgs.append({"id": oid, "name": name, "type": "company", "side": "work",
                         "role": "owner" if wrote else "helper", "_key": oid, "_about": ""})
        chat_org[c["chat_id"]] = oid

    # Seeded organisations must exist even if the model dropped them.
    for name in data.seed["organisations"]:
        if not any(slug(name) in (o["id"], slug(o["name"])) for o in orgs):
            orgs.append({"id": slug(name), "name": name, "type": "company", "side": "work", "role": "owner", "_key": slug(name), "_about": ""})
            for c in groups:
                if name.lower() in (c.get("name") or "").lower() and not chat_org.get(c["chat_id"]):
                    chat_org[c["chat_id"]] = slug(name)
    return orgs, chat_org


def _initials(name):
    return "".join(w[0] for w in re.findall(r"[A-Z][a-z]*|\d+", name)).lower()


def _same_name(short, full):
    a, b = slug(short), slug(full)
    return a in b or b in a or a == _initials(full)


def rule_orgs(data):
    """No model: seeded companies matched by group name, nothing else."""
    orgs, chat_org = [], {}
    for name in data.seed["organisations"]:
        oid = slug(name)
        orgs.append({"id": oid, "name": name, "type": "company", "side": "work", "role": "owner", "_key": oid, "_about": ""})
        initials = "".join(w[0] for w in re.findall(r"[A-Z][a-z]*", name)) if re.search(r"[a-z]", name) else ""
        for c in data.chats:
            n = (c.get("name") or "")
            if c["is_group"] and (name.lower() in n.lower() or (len(initials) >= 2 and re.search(r"\b" + initials + r"\b", n))):
                chat_org[c["chat_id"]] = oid
    return orgs, chat_org


def top_sender(data, chat_id):
    counts = Counter(m["sender_id"] for m in data.by_chat.get(chat_id, []) if m["sender_id"] != "ME")
    if counts:
        return counts.most_common(1)[0][0]
    others = [p for p in data.chat[chat_id]["participants"] if p != "ME"]
    return others[0] if others else None
