"""Loading the input folder and the small helpers every stage shares."""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def ist(ts):
    return datetime.fromtimestamp(ts, IST)


def short_time(ts):
    return ist(ts).strftime("%a %d %b %H:%M")


def clip(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


class Data:
    """Everything read from the input folder, plus indexes built once."""

    def __init__(self, folder):
        folder = Path(folder)
        self.chats = []
        for c in read_jsonl(folder / "chats.jsonl"):
            if not c.get("chat_id"):
                continue
            c["participants"] = [p for p in (c.get("participants") or []) if p]
            c["is_group"] = bool(c.get("is_group", len(c["participants"]) > 2))
            c["name"] = c.get("name") or ""
            self.chats.append(c)
        known = {c["chat_id"] for c in self.chats}
        msgs = []
        for m in read_jsonl(folder / "messages.jsonl"):
            if not m.get("id") or m.get("chat_id") not in known:
                continue  # a message we cannot place cannot be cited
            try:
                m["ts"] = float(m.get("ts") or 0)
            except (TypeError, ValueError):
                m["ts"] = 0.0
            m["sender_id"] = m.get("sender_id") or "unknown"
            if not isinstance(m.get("body"), str):
                m["body"] = "" if m.get("body") is None else str(m["body"])
            msgs.append(m)
        self.messages = sorted(msgs, key=lambda m: (m["ts"], m["id"]))
        seed_path = folder / "seed.json"
        self.seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.exists() else {}
        self.seed.setdefault("circles", {})
        self.seed.setdefault("organisations", [])
        self.seed.setdefault("goals", [])

        self.chat = {c["chat_id"]: c for c in self.chats}
        self.msg = {m["id"]: m for m in self.messages}
        self.by_chat = defaultdict(list)
        for m in self.messages:
            self.by_chat[m["chat_id"]].append(m)

        self.people = sorted({p for c in self.chats for p in c["participants"]} - {"ME"})
        self.names = defaultdict(Counter)  # person -> saved names they appear under
        for m in self.messages:
            if m["sender_id"] != "ME" and m.get("sender_name"):
                self.names[m["sender_id"]][m["sender_name"].strip()] += 1
        self.dm_of = {}  # person -> their 1:1 chat with Arjun
        for c in self.chats:
            if not c["is_group"]:
                others = [p for p in c["participants"] if p != "ME"]
                if len(others) == 1:
                    self.dm_of.setdefault(others[0], c["chat_id"])
        self.groups_of = defaultdict(list)
        for c in self.chats:
            if c["is_group"]:
                for p in c["participants"]:
                    if p != "ME":
                        self.groups_of[p].append(c["chat_id"])
        self.sent = Counter(m["sender_id"] for m in self.messages)
        me_names = Counter(m["sender_name"] for m in self.messages if m["sender_id"] == "ME" and m.get("sender_name"))
        self.me_name = me_names.most_common(1)[0][0] if me_names else "the user"
        self.start_ts = self.messages[0]["ts"] if self.messages else 0
        self.end_ts = self.messages[-1]["ts"] if self.messages else 0

    def name(self, pid):
        if pid == "ME":
            return self.me_name.split()[0] if self.me_name else "Arjun"
        if pid in self.dm_of:
            dm_name = clip(self.chat[self.dm_of[pid]].get("name", ""), 40).strip("‎ ")
            if dm_name:
                return dm_name
        if self.names.get(pid):
            return self.names[pid].most_common(1)[0][0]
        return pid

    def fmt(self, m, width=400):
        """One message as one compact line: id, time, sender, text."""
        body = m.get("body") or ""
        if m.get("kind") == "document":
            body = "[document] " + body
        elif m.get("kind") == "voice_transcript":
            body = "[voice] " + body
        return f"{m['id']} [{short_time(m['ts'])}] {self.name(m['sender_id'])}: {clip(body, width)}"
