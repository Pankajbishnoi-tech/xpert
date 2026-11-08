"""A small client for any OpenAI-compatible chat API, standard library only.

Reads LLM_BASE_URL, LLM_API_KEY and LLM_MODEL. Adds what a 30-minute unattended run needs on top of
the starter kit's client: retries with backoff, a JSON reply parser that copes with models that wrap
JSON in prose or fences, a fallback when a model rejects response_format, parallel calls, and an
optional on-disk cache (XPERT_CACHE=dir) so development reruns cost nothing. Every real call appends
its token counts to usage.jsonl.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_lock = threading.Lock()
_json_mode_ok = {"value": True}
USAGE_FILE = os.environ.get("XPERT_USAGE", "usage.jsonl")
# The grader stops us at 30 minutes. After this many seconds no new model call starts, so the rules
# still get to write a complete file in time.
_START = time.time()
BUDGET = float(os.environ.get("XPERT_BUDGET_SECONDS", "1380"))


def _left():
    return BUDGET - (time.time() - _START)


def available():
    return bool(os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY") and os.environ.get("LLM_MODEL"))


def _post(body, timeout):
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"), strict=False)


def chat(messages, temperature=0, max_tokens=4000, json_mode=True, timeout=180, tag=""):
    model = os.environ["LLM_MODEL"]
    # reasoning models spend reply tokens on thinking first; give them room
    max_tokens = max(max_tokens, int(os.environ.get("XPERT_MIN_REPLY_TOKENS", "0")))
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    effort = os.environ.get("XPERT_REASONING", "off")  # OpenRouter: off (default) | low | medium | high | none (send nothing)
    if effort == "off":
        body["reasoning"] = {"enabled": False}
    elif effort and effort != "none":
        body["reasoning"] = {"effort": effort}
    cache_dir = os.environ.get("XPERT_CACHE")
    key = hashlib.sha256(json.dumps([model, messages, max_tokens, os.environ.get("XPERT_REASONING", "off")], sort_keys=True).encode()).hexdigest()
    if cache_dir:
        path = os.path.join(cache_dir, key + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)["content"]

    last_err = None
    if _json_mode_ok.get("daily_cap"):
        return None
    for attempt in range(int(os.environ.get("XPERT_RETRIES", "6"))):
        if _left() < 20:
            last_err = "time budget used up"
            break
        timeout = min(timeout, max(20, _left()))
        send = dict(body)
        if json_mode and _json_mode_ok["value"]:
            send["response_format"] = {"type": "json_object"}
        started = time.time()
        try:
            data = _post(send, timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code == 400 and "response_format" in send and _json_mode_ok["value"]:
                _json_mode_ok["value"] = False  # model does not take json mode; ask in the prompt only
                continue
            if e.code in (400, 401, 403, 404):
                break
            if e.code == 429 and "per-day" in detail:  # a daily cap will not lift within this run
                _json_mode_ok["daily_cap"] = True
                break
            if e.code == 429:  # rate limited (free tiers): wait as told, or a good while
                wait = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(90, float(wait)) if wait and wait.replace('.', '', 1).isdigit() else 20 * (attempt + 1))
                continue
            time.sleep(max(0, min(30, 2 ** attempt * 2, _left() - 20)))
            continue
        except Exception as e:  # network, timeout, bad JSON from the server
            last_err = repr(e)
            time.sleep(max(0, min(30, 2 ** attempt * 2, _left() - 20)))
            continue
        if "error" in data and not data.get("choices"):
            last_err = str(data["error"])[:300]
            time.sleep(max(0, min(30, 2 ** attempt * 2, _left() - 20)))
            continue
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            last_err = "no choices in reply"
            time.sleep(2)
            continue
        usage = data.get("usage") or {}
        row = json.dumps({
            "tag": tag,
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost": usage.get("cost"),
            "seconds": round(time.time() - started, 2),
        })
        with _lock:
            try:
                with open(USAGE_FILE, "a", encoding="utf-8") as f:
                    f.write(row + "\n")
            except OSError:  # read-only folder: the log is only for us
                pass
        if not content.strip():
            last_err = "empty reply"
            continue
        if cache_dir:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(os.path.join(cache_dir, key + ".json"), "w", encoding="utf-8") as f:
                    json.dump({"content": content}, f)
            except OSError:
                pass
        return content
    print(f"[llm] giving up on a call ({tag}): {last_err}", file=sys.stderr)
    return None


def parse_json(text):
    """The first JSON object in a reply, or None."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text, strict=False)
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1], strict=False)
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def ask_json(system, user, max_tokens=4000, tag=""):
    reply = chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        tag=tag,
    )
    return parse_json(reply)


def run_parallel(fn, items, workers=None):
    workers = workers or int(os.environ.get("XPERT_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))
