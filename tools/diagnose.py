#!/usr/bin/env python3
"""Show where an output.json disagrees with the dev answers, part by part.

    python3 tools/diagnose.py data/dev out.json [circles|orgs|chats|loops|usage]
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check  # noqa: E402
from xpert.data import Data  # noqa: E402


def main(folder, out_path, parts):
    d = Data(folder)
    ans = json.loads((Path(folder) / "answers" / "answers.json").read_text())
    out = json.loads(Path(out_path).read_text())
    seed = d.seed["circles"]

    if "circles" in parts:
        mine = {p["id"]: p for p in out["people"]}
        conf = Counter()
        print("== circles wrong (truth -> mine)")
        for p in ans["people"]:
            if p["id"] in seed:
                continue
            got = mine.get(p["id"], {}).get("circle")
            conf[(p["circle"], got)] += 1
            if got != p["circle"]:
                print(f'  {p["id"]:6} {d.name(p["id"])[:28]:28} {p["circle"]:>10} -> {got}')
        print("  links truth vs mine:")
        for p in ans["people"]:
            if p.get("links") or mine.get(p["id"], {}).get("links"):
                print(f'   {p["id"]:6} {d.name(p["id"])[:24]:24} {[l["organisation"] + ":" + l["link"] for l in p.get("links") or []]} | {[l["organisation"] + ":" + l["link"] for l in mine.get(p["id"], {}).get("links") or []]}')

    if "orgs" in parts or "chats" in parts:
        matched, _, _ = check.match_orgs(out, ans)
        print("== organisations (ours -> yours, jaccard)")
        names = {o["id"]: o["name"] for o in out["organisations"]}
        for o in ans["organisations"]:
            y = matched.get(o["id"])
            print(f'  {o["id"]} {o["name"]:12} -> {names.get(y[0]) if y else None} {round(y[1], 2) if y else ""}')
        print("  unmatched yours:", [o["name"] for o in out["organisations"] if o["id"] not in {v[0] for v in matched.values()}])
        tie = {c["chat_id"]: c for c in out["chats"]}
        inv = {v[0]: k for k, v in matched.items()}
        print("== chats wrong (truth -> mine)")
        for c in ans["chats"]:
            m = tie.get(c["chat_id"], {})
            want = c.get("person") or c.get("organisation")
            got = m.get("person") or inv.get(m.get("organisation"), "unmatched:" + str(m.get("organisation")))
            if want != got:
                print(f'  {c["chat_id"]:5} {(d.chat[c["chat_id"]].get("name") or "")[:35]:35} {want} -> {got}')

    if "loops" in parts:
        srcs = {}
        for j, a in enumerate(ans["open_loops"]):
            for s in a["source_msgs"]:
                srcs.setdefault(s, []).append(j)
        hit = set()
        print("== my loops")
        for l in out["open_loops"]:
            js = srcs.get(l["source_msg"], [])
            st = ans["open_loops"][js[0]]["status"] if js else ""
            mark = ("HIT " + ("ok " if st == l["status"] else f"STATUS want {st} ")) if js else "xtra"
            hit.update(js)
            h = (l.get("help") or {}).get("action", "")
            print(f'  {mark:20} {l["chat_id"]:5} {l["source_msg"]} {l["status"]:7} {h:6} {l["type"]:10} {l["title"][:70]}')
        print("== missed")
        seen = set()
        for j, a in enumerate(ans["open_loops"]):
            if j in hit or a["source_msgs"][0] in seen:
                continue
            seen.add(a["source_msgs"][0])
            print(f'  {a["chat_id"]:5} {a["source_msgs"][0]} {a["status"]:6} {a["title"][:70]}')

    if "usage" in parts and Path("usage.jsonl").exists():
        rows = [json.loads(x) for x in open("usage.jsonl")]
        by = Counter()
        for r in rows:
            by[(r.get("tag"), "in")] += r.get("prompt_tokens") or 0
            by[(r.get("tag"), "out")] += r.get("completion_tokens") or 0
            by[(r.get("tag"), "calls")] += 1
        for tag in sorted({t for t, _ in by}, key=str):
            print(f'  {tag:12} calls {by[(tag, "calls")]:3}  in {by[(tag, "in")]:8}  out {by[(tag, "out")]:7}')


if __name__ == "__main__":
    parts = set(sys.argv[3:]) or {"circles", "orgs", "chats", "loops", "usage"}
    main(sys.argv[1], sys.argv[2], parts)
