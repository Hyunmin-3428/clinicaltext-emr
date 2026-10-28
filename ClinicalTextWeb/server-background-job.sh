#!/usr/bin/env bash
# Run entirely on the selected server, detached from SSH and the laptop.
set -euo pipefail
umask 077
job_root=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$job_root"
mkdir -p results ClinicalTextWeb/.runtime
status() {
  python3 - "$1" "${2:-}" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
p=Path('results/status.json')
d=json.loads(p.read_text()) if p.exists() else {'policy_version':'clinical-dates-v4','synthetic_only':True,'history':[]}
step={'stage':sys.argv[1],'detail':sys.argv[2],'at_utc':datetime.now(timezone.utc).isoformat()}
d.update(step);d['history'].append(step)
p.write_text(json.dumps(d,indent=2),encoding='utf8')
PY
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then status FAILED "exit=$rc; see job.log"; fi' EXIT
status INSTALLING 'Creating isolated Python environment; no system packages changed'
venv_root="$HOME/.local/share/clinicaltext-gpu/web-venv"
if [ ! -x "$venv_root/bin/python" ]; then
  python3 -m venv --without-pip "$venv_root"
fi
if ! "$venv_root/bin/python" -m pip --version; then
  curl -fLsS --retry 2 --connect-timeout 10 https://bootstrap.pypa.io/get-pip.py -o results/get-pip.py
  sha256sum results/get-pip.py > results/get-pip.sha256
  timeout 180 "$venv_root/bin/python" results/get-pip.py
fi
timeout 900 "$venv_root/bin/python" -m pip install --disable-pip-version-check -r ClinicalTextDeid/requirements.txt
"$venv_root/bin/python" -m pip freeze > results/installed-packages.txt
"$venv_root/bin/python" - <<'PY'
import json
from pathlib import Path
Path('ClinicalTextWeb/.runtime/local-model.json').write_text(json.dumps({
 'provider':'ollama','base_url':'http://127.0.0.1:11435','model':'qwen3:30b','verifier_model':'qwen3:30b','api_key':''}),encoding='utf8')
PY
status CHECKING_MODEL 'Uses existing GPU Ollama on server loopback only'
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:11435/api/tags > results/model-tags.json
status RUNNING_FUNCTIONAL_TESTS
set +e
timeout 180 "$venv_root/bin/python" -m unittest discover -s ClinicalTextWeb/tests -q > results/functional-tests.log 2>&1
functional_exit=$?
set -e
printf '%s\n' "$functional_exit" > results/functional-tests.exit
web_port=${CLINICAL_WEB_PORT:-8765}
status STARTING_WEB "Server loopback only; port $web_port"
if ! curl -fsS --max-time 2 "http://127.0.0.1:$web_port/api/health" > results/web-health.json; then
  nohup setsid "$venv_root/bin/python" -u -m ClinicalTextWeb.server --port "$web_port" </dev/null > results/web.log 2>&1 &
  printf '%s\n' "$!" > results/web.pid
fi
status RUNNING_GPU_AUDIT '5 clinical cases, 8 faulty outputs, dates/history, approval and signed export'
set +e
timeout 900 "$venv_root/bin/python" -u -m ClinicalTextWeb.audit_pipeline --out-dir results/date-policy-audit > results/date-policy-audit.log 2>&1
audit_exit=$?
set -e
printf '%s\n' "$audit_exit" > results/date-policy-audit.exit
status RUNNING_OPEN_SOURCE_COMPARISON '3 synthetic cases x 5 adapted approaches; failures retained'
set +e
timeout 900 "$venv_root/bin/python" -u -m ClinicalTextWeb.compare_approaches --out-dir results/opensource-comparison > results/opensource-comparison.log 2>&1
comparison_exit=$?
set -e
printf '%s\n' "$comparison_exit" > results/opensource-comparison.exit
if [ "$functional_exit" -eq 0 ] && [ "$audit_exit" -eq 0 ] && [ "$comparison_exit" -eq 0 ]; then
  status FINISHED 'Functional checks and live GPU audit passed; comparisons retained, including baseline failures'
else
  status FINISHED_WITH_FAILURES "functional_exit=$functional_exit audit_exit=$audit_exit comparison_exit=$comparison_exit; inspect reports"
fi
