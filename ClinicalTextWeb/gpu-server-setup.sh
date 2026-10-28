#!/usr/bin/env bash
# Isolated, unprivileged Ollama runtime. No system service or public listener.
set -euo pipefail
demo_root="$HOME/.local/share/clinicaltext-gpu"
demo_version="0.40.11"
demo_sha="7bedab90dd122661be78798e9f0edf37fccf0cccf2699d2c58ea1a3afdc04b6d"
mkdir -p "$demo_root/install" "$demo_root/runtime" "$demo_root/models"
if [ ! -x "$demo_root/runtime/bin/ollama" ]; then
  curl -fL --retry 2 "https://github.com/ollama/ollama/releases/download/v${demo_version}/ollama-linux-amd64.tar.zst" -o "$demo_root/install/ollama-linux-amd64.tar.zst"
  printf '%s  %s\n' "$demo_sha" "$demo_root/install/ollama-linux-amd64.tar.zst" | sha256sum --check
  tar --zstd -xf "$demo_root/install/ollama-linux-amd64.tar.zst" -C "$demo_root/runtime"
fi
if ! curl -fsS http://127.0.0.1:11435/api/version; then
  demo_gpu_uuid=$(nvidia-smi --query-gpu=uuid --format=csv,noheader | sed -n '2p')
  test -n "$demo_gpu_uuid"
  nohup env CUDA_VISIBLE_DEVICES="$demo_gpu_uuid" OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS="$demo_root/models" OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_KEEP_ALIVE=5m "$demo_root/runtime/bin/ollama" serve </dev/null >"$demo_root/server.log" 2>&1 &
  printf '%s\n' "$!" >"$demo_root/server.pid"
  printf '%s\n' "$demo_gpu_uuid" >"$demo_root/gpu.uuid"
fi
for demo_attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:11435/api/version; then break; fi
  sleep 1
done
env OLLAMA_HOST=127.0.0.1:11435 "$demo_root/runtime/bin/ollama" pull qwen3:4b-instruct
curl -fsS http://127.0.0.1:11435/api/tags
