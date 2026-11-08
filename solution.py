#!/usr/bin/env python3
"""Cold Start: read one person's WhatsApp export and write output.json.

    python3 solution.py <input_dir> <output.json>

Stages (each falls back to rules if the model is unavailable or a call fails, so a valid file is
always written):
  1. organisations  - one model call over the group-chat list
  2. people         - circles in batches of 30 short profiles; links derived from circles + orgs
  3. chats          - 1:1 chats to the person, groups to their organisation or their main voice
  4. open loops     - one call per chunk of the last two weeks, in chats Arjun is part of
  5. goals          - Arjun's own messages, two small passes
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xpert import goals as goals_mod  # noqa: E402
from xpert import llm, loops as loops_mod, orgs as orgs_mod, people as people_mod  # noqa: E402
from xpert.data import Data  # noqa: E402
from xpert.orgs import slug, top_sender  # noqa: E402

PLACE_TYPES = [
    ("farm", "Farmhouse", "vacation home"),
    ("holiday", "Holiday home", "vacation home"),
    ("vacation", "Holiday home", "vacation home"),
    ("office", "Office", "workspace"),
    ("home", "Home", "residence"),
    ("house", "Home", "residence"),
]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def resolve_place(text, orgs):
    """Map a staff member's workplace (an org key or a free word like 'farmhouse') to an org id,
    creating the place if it is new."""
    key = slug(text)
    for o in orgs:
        if key in (o["id"], o.get("_key"), slug(o["name"])):
            return o["id"]
    for word, name, typ in PLACE_TYPES:
        if word in key:
            for o in orgs:
                if o["type"] == typ and (typ != "residence" or o["side"] == "home"):
                    return o["id"]
            oid = slug(name)
            orgs.append({"id": oid, "name": name, "type": typ, "side": "home" if typ != "workspace" else "work",
                         "role": "owner", "_key": oid, "_about": ""})
            return oid
    return None


def build(input_dir):
    data = Data(input_dir)
    log(f"{len(data.messages)} messages, {len(data.chats)} chats, {len(data.people)} people; model={'on' if llm.available() else 'off'}")

    orgs, chat_org = guarded("organisations", lambda: orgs_mod.find_orgs(data), lambda: orgs_mod.rule_orgs(data))
    log(f"organisations: {[o['name'] for o in orgs]}")

    circles = people_mod.classify(data, orgs, chat_org)
    log("circles done")

    people = []
    for pid in data.people:
        c = circles[pid]
        entry = {"id": pid, "circle": c["circle"]}
        links = []
        if c["circle"] in ("colleague", "partner"):
            ids = {o["id"] for o in orgs}
            named = [resolve_place(x, orgs) for x in c["orgs"]]
            named = [x for x in named if x in ids and next(o for o in orgs if o["id"] == x)["side"] == "work"]
            if not named:  # fall back to the work organisations whose chats they are in
                counts = {}
                for g in data.groups_of.get(pid, []):
                    o = chat_org.get(g)
                    if o and next((x for x in orgs if x["id"] == o), {}).get("side") == "work":
                        counts[o] = counts.get(o, 0) + 1
                named = sorted(counts, key=lambda k: -counts[k])[:1]
            links = [{"organisation": o, "link": "runs"} for o in dict.fromkeys(named)]
        elif c["circle"] == "staff":
            place = None
            for x in c["orgs"]:
                place = resolve_place(x, orgs)
                if place:
                    break
            if not place:
                for g in data.groups_of.get(pid, []):
                    if chat_org.get(g):
                        place = chat_org[g]
                        break
            if not place:
                place = resolve_place("home", orgs)
            links = [{"organisation": place, "link": "supports"}]
        # Only people he deals with one to one (or that he named at signup) get linked: a team
        # member who only shows up in big groups is not someone he would list as running a company.
        if links and (data.dm_of.get(pid) or pid in data.seed["circles"]):
            entry["links"] = links
        people.append(entry)

    ties = []
    for c in data.chats:
        cid = c["chat_id"]
        others = [p for p in c["participants"] if p != "ME"]
        if not c["is_group"] and len(others) == 1:
            ties.append({"chat_id": cid, "person": others[0]})
        elif chat_org.get(cid):
            ties.append({"chat_id": cid, "organisation": chat_org[cid]})
        else:
            p = top_sender(data, cid)
            if p:
                ties.append({"chat_id": cid, "person": p})
            elif orgs:
                ties.append({"chat_id": cid, "organisation": orgs[0]["id"]})
    log("chats tied")

    loops = guarded("open loops", lambda: loops_mod.extract(data, circles), [])
    log(f"open loops: {len(loops)}")

    goals = guarded("goals", lambda: goals_mod.find_goals(data), [])
    log(f"goals: {len(goals)}")

    org_out = [{k: v for k, v in o.items() if not k.startswith("_")} for o in orgs]
    return {"people": people, "organisations": org_out, "chats": ties, "goals": goals, "open_loops": loops}


def guarded(name, fn, fallback):
    """Run one stage; if it crashes, log it and use the fallback so the rest still gets written."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log(f"{name} failed, using fallback: {e!r}")
        return fallback() if callable(fallback) else fallback


def main(input_dir, output_path):
    started = time.time()
    try:
        out = build(input_dir)
    except Exception as e:  # noqa: BLE001 - last resort: rules only, so the file is still valid
        log(f"run failed ({e!r}); writing a rules-only file")
        os.environ["LLM_API_KEY"] = ""
        out = build(input_dir)
    Path(output_path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"wrote {output_path} in {time.time() - started:.0f}s")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
