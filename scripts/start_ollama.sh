#!/usr/bin/env bash
# Start Ollama with an explicit SocialEase capacity and residency contract.

set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${repo_dir}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${repo_dir}/.env"
  set +a
fi

slots="${LOCAL_LLM_OLLAMA_SLOTS:-${OLLAMA_NUM_PARALLEL:-1}}"
queue_size="${LOCAL_LLM_SERVER_MAX_QUEUE:-${LOCAL_LLM_MAX_QUEUE_SIZE:-4}}"
context_size="${LOCAL_LLM_CONTEXT_SIZE:-4096}"

if [[ ! "${slots}" =~ ^[1-9][0-9]*$ || ! "${queue_size}" =~ ^[0-9]+$ ]]; then
  echo "Ollama slots must be positive and queue size must be non-negative." >&2
  exit 2
fi

export OLLAMA_NUM_PARALLEL="${slots}"
export OLLAMA_MAX_QUEUE="${queue_size}"
export OLLAMA_CONTEXT_LENGTH="${context_size}"
export OLLAMA_KEEP_ALIVE="${LOCAL_LLM_KEEP_ALIVE:--1}"
export OLLAMA_HOST="${LOCAL_LLM_OLLAMA_HOST:-127.0.0.1:11434}"

exec ollama serve
