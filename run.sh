#!/usr/bin/env bash
# The one command:  ./run.sh <input_dir> <output.json>
# Needs only python3 (3.9+, standard library). Reads LLM_BASE_URL, LLM_API_KEY, LLM_MODEL from the
# environment. For local testing, a .env file next to this script fills in any that are not set.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo "usage: ./run.sh <input_dir> <output.json>" >&2
  exit 2
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HERE/.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in LLM_BASE_URL|LLM_API_KEY|LLM_MODEL|XPERT_CACHE|XPERT_WORKERS|XPERT_MIN_REPLY_TOKENS|XPERT_REASONING)
      if [ -z "${!k:-}" ]; then export "$k=$v"; fi ;;
    esac
  done < <(grep -E '^[A-Z_]+=' "$HERE/.env")
fi
python3 "$HERE/solution.py" "$1" "$2"
