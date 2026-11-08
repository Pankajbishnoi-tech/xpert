# Cold Start: Xpert winter internship submission

Reads a few weeks of one person's WhatsApp and writes `output.json`: their people and circles,
organisations, which chat belongs to whom, goals, and open loops with where an assistant should help.
See [WRITEUP.md](WRITEUP.md) for the approach, cost and expected errors.

## Run

```bash
export LLM_BASE_URL=https://openrouter.ai/api/v1
export LLM_API_KEY=...            # any OpenAI-compatible endpoint
export LLM_MODEL=deepseek/deepseek-v3.2

./run.sh <input_dir> <output.json>
python3 check.py <input_dir> <output.json>   # scores it if the folder has answers/
```

Python 3.9+, standard library only, nothing to install. Without a model it still writes a valid file
(rules only). For local work, copy `.env.example` to `.env`; `run.sh` fills in any variable not already set.

| Variable | Default | What it does |
|---|---|---|
| `XPERT_REASONING` | `off` | `off`, `low`, `medium`, `high`, or `none` to send nothing. `off` is ~15x faster on reasoning models |
| `XPERT_WORKERS` | 8 | parallel model calls |
| `XPERT_CACHE` | unset | folder to cache replies in, so reruns during development are free |
| `XPERT_MIN_REPLY_TOKENS` | 0 | floor on `max_tokens`, for models that think before answering |

## Layout

| File | Stage |
|---|---|
| `solution.py` | runs the stages, derives links and chat ties, writes the file |
| `xpert/data.py` | loading, indexes, compact one-line message format (IST times) |
| `xpert/llm.py` | OpenAI-compatible client: retries, rate limits, JSON repair, cache, `usage.jsonl` |
| `xpert/orgs.py` | organisations + which group chat belongs to which (1 call) |
| `xpert/people.py` | circles from short person profiles (batches of 70) |
| `xpert/loops.py` | open loops, status and help over the last two weeks (packed chunks + 1 dedupe call) |
| `xpert/goals.py` | goals from the user's own messages (1-2 calls) |
| `tools/diagnose.py` | shows which circles, chats and loops disagree with the dev answers |
