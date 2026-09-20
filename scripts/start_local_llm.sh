#!/usr/bin/env bash
# Start the development llama-server with an explicit capacity contract.

set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${repo_dir}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${repo_dir}/.env"
  set +a
fi

default_bin="${repo_dir}/.local/llama.cpp/build-socialease-gcc12/bin/llama-server"
server_bin="${LLAMA_SERVER_BIN:-${default_bin}}"
model_path="${LOCAL_LLM_MODEL_PATH:-}"
slots="${LOCAL_LLM_SERVER_SLOTS:-4}"
context_size="${LOCAL_LLM_CONTEXT_SIZE:-4096}"
host="${LOCAL_LLM_SERVER_HOST:-127.0.0.1}"
port="${LOCAL_LLM_SERVER_PORT:-8080}"
api_key="${LOCAL_LLM_API_KEY:-local}"
model_alias="${LOCAL_LLM_MODEL:-qwen3.5:4b}"

if [[ ! -x "${server_bin}" ]]; then
  echo "llama-server not executable: ${server_bin}" >&2
  echo "Set LLAMA_SERVER_BIN to the installed llama-server path." >&2
  exit 2
fi

if [[ -z "${model_path}" || ! -f "${model_path}" ]]; then
  echo "Set LOCAL_LLM_MODEL_PATH to the local GGUF model file." >&2
  exit 2
fi

if [[ ! "${slots}" =~ ^[1-9][0-9]*$ || ! "${context_size}" =~ ^[1-9][0-9]*$ ]]; then
  echo "LOCAL_LLM_SERVER_SLOTS and LOCAL_LLM_CONTEXT_SIZE must be positive integers." >&2
  exit 2
fi

exec "${server_bin}" \
  --model "${model_path}" \
  --alias "${model_alias}" \
  --ctx-size "${context_size}" \
  --parallel "${slots}" \
  --cont-batching \
  --gpu-layers all \
  --host "${host}" \
  --port "${port}" \
  --api-key "${api_key}" \
  --reasoning off \
  --reasoning-budget 0 \
  --metrics \
  --no-webui
