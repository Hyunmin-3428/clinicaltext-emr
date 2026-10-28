#!/usr/bin/env bash
# Run one local model, score its predictions, and draw example result charts.
# Defaults use the three synthetic notes. Real clinical inputs require approved
# storage and local handling. No final redacted text files are written.
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GGUF=""
HF_REPO=""
HF_FILE=""
MODEL_ID=""
NOTES_DIR="$REPO_ROOT/fixtures/synthetic/notes"
GOLD_DIR="$REPO_ROOT/fixtures/synthetic/gold"
OUT_DIR="$REPO_ROOT/out"
HOST="127.0.0.1"
PORT="8081"
CTX_SIZE="8192"
NGL="999"
LOAD_TIMEOUT="600"
MODEL_CONFIG=""
RETAIN_RAW=""
ACK_PHI_RISK=""
OVERWRITE_MODEL_OUTPUT=""

usage() {
  cat <<'HELP'
Usage: scripts/run_local_end_to_end.sh --gguf PATH [options]
       scripts/run_local_end_to_end.sh --hf-repo REPO --hf-file FILE [options]

  --gguf PATH                  Local GGUF (first part for a split checkpoint)
  --hf-repo REPO --hf-file FILE Download one file with the Hugging Face hf CLI
  --model-id ID                Run label; use a model_configs filename stem
  --model-config PATH          Optional model metadata; defaults to matching ID
  --notes-dir DIR              UTF-8 notes (default: fixtures/synthetic/notes)
  --gold-dir DIR               Reference JSON (default: fixtures/synthetic/gold)
  --out-dir DIR                Output root (default: out)
  --host HOST                  127.0.0.1 or localhost (default: 127.0.0.1)
  --port N                     Server port (default: 8081)
  --ctx-size N                 Server context size (default: 8192)
  --n-gpu-layers N             Layers to offload (default: 999; CPU-only: 0)
  --overwrite-model-output     Replace only this model's existing output
  --strip-raw-content          Omit generated response text (the default)
  --retain-raw-content         Retain response text; also requires the next flag
  --acknowledge-phi-risk       Acknowledge approved storage for retained text
  -h, --help                   Show this help

Requires bash, python3, curl, and llama-server. Automatic download also needs hf.
Download all parts of a split checkpoint before supplying its first part.
The launcher records checkpoint hashes, server version, and the launch command.
HELP
}

while [ $# -gt 0 ]; do
  case "$1" in
    --gguf|--hf-repo|--hf-file|--model-id|--model-config|--notes-dir|--gold-dir|--out-dir|--host|--port|--ctx-size|--n-gpu-layers)
      [ $# -ge 2 ] || { echo "missing value for $1" >&2; exit 1; } ;;
  esac
  case "$1" in
    --gguf) GGUF="$2"; shift 2 ;;
    --hf-repo) HF_REPO="$2"; shift 2 ;;
    --hf-file) HF_FILE="$2"; shift 2 ;;
    --model-id) MODEL_ID="$2"; shift 2 ;;
    --model-config) MODEL_CONFIG="$2"; shift 2 ;;
    --notes-dir) NOTES_DIR="$2"; shift 2 ;;
    --gold-dir) GOLD_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --ctx-size) CTX_SIZE="$2"; shift 2 ;;
    --n-gpu-layers) NGL="$2"; shift 2 ;;
    --strip-raw-content) RETAIN_RAW=""; shift ;;
    --retain-raw-content) RETAIN_RAW="1"; shift ;;
    --acknowledge-phi-risk) ACK_PHI_RISK="1"; shift ;;
    --overwrite-model-output) OVERWRITE_MODEL_OUTPUT="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

case "$HOST" in
  127.0.0.1|localhost) ;;
  *) echo "refusing non-loopback llama-server bind: $HOST" >&2; exit 1 ;;
esac
if [ -n "$RETAIN_RAW" ] && [ -z "$ACK_PHI_RISK" ]; then
  echo "--retain-raw-content also requires --acknowledge-phi-risk" >&2
  exit 1
fi
command -v llama-server >/dev/null || { echo "llama-server not found; install llama.cpp." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found." >&2; exit 1; }
command -v curl >/dev/null || { echo "curl not found." >&2; exit 1; }

if [ -z "$GGUF" ]; then
  if [ -n "$HF_REPO" ] && [ -n "$HF_FILE" ]; then
    command -v hf >/dev/null || { echo "hf not found; install it with: python3 -m pip install -U huggingface_hub" >&2; exit 1; }
    dest="$REPO_ROOT/models/$(basename "$HF_REPO")"
    hf download "$HF_REPO" "$HF_FILE" --local-dir "$dest"
    GGUF="$dest/$HF_FILE"
  else
    echo "provide --gguf PATH, or --hf-repo REPO --hf-file FILE" >&2
    exit 1
  fi
fi
[ -f "$GGUF" ] || { echo "model file not found: $GGUF" >&2; exit 1; }
[ -n "$MODEL_ID" ] || MODEL_ID="$(basename "$GGUF" .gguf)"
if [ -z "$MODEL_CONFIG" ] && [ -f "$REPO_ROOT/model_configs/$MODEL_ID.json" ]; then
  MODEL_CONFIG="$REPO_ROOT/model_configs/$MODEL_ID.json"
fi

mkdir -p "$OUT_DIR"
SERVER_LOG="$OUT_DIR/llama-server.log"
if curl -sf "http://$HOST:$PORT/health" >/dev/null 2>&1; then
  echo "a server already answers on http://$HOST:$PORT; stop it or use --port N" >&2
  exit 1
fi
SERVER_VERSION="$(llama-server --version 2>&1)" || SERVER_VERSION=""
SERVER_COMMAND=(llama-server -m "$GGUF" --host "$HOST" --port "$PORT"
    --ctx-size "$CTX_SIZE" --n-gpu-layers "$NGL" --jinja)
echo "Starting $MODEL_ID (server log: $SERVER_LOG)"
"${SERVER_COMMAND[@]}" > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

for i in $(seq 1 "$LOAD_TIMEOUT"); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "llama-server exited; inspect $SERVER_LOG" >&2; exit 1
  fi
  if curl -sf "http://$HOST:$PORT/health" >/dev/null 2>&1; then break; fi
  if [ "$i" -eq "$LOAD_TIMEOUT" ]; then
    echo "model loading timed out; inspect $SERVER_LOG" >&2; exit 1
  fi
  sleep 1
done

RUN_ARGS=(--model-id "$MODEL_ID" --notes-dir "$NOTES_DIR"
    --out-dir "$OUT_DIR/predictions" --protocol-dir "$REPO_ROOT/protocol"
    --api-base "http://$HOST:$PORT" --checkpoint-file "$GGUF"
    "--server-version=$SERVER_VERSION")
[ -n "$MODEL_CONFIG" ] && RUN_ARGS+=(--model-config "$MODEL_CONFIG")
[ -n "$RETAIN_RAW" ] && RUN_ARGS+=(--retain-raw-content)
[ -n "$ACK_PHI_RISK" ] && RUN_ARGS+=(--acknowledge-phi-risk)
[ -n "$OVERWRITE_MODEL_OUTPUT" ] && RUN_ARGS+=(--overwrite-model-output)
# --server-command consumes the rest of the arguments; keep it last.
PYTHONPATH="$REPO_ROOT/src" python3 -m deid.run_model "${RUN_ARGS[@]}" \
    --server-command "${SERVER_COMMAND[@]}"
PYTHONPATH="$REPO_ROOT/src" python3 -m deid.metrics \
    --pred-dir "$OUT_DIR/predictions" --gold-dir "$GOLD_DIR" --out-dir "$OUT_DIR"
python3 "$REPO_ROOT/scripts/make_results_table.py" --metrics "$OUT_DIR/metrics_long.csv"
python3 "$REPO_ROOT/scripts/make_figures.py" --metrics "$OUT_DIR/metrics_long.csv" --out-dir "$OUT_DIR/figures"
echo "Outputs: $OUT_DIR"
