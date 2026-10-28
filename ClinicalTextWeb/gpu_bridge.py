"""Restore the explicitly configured SSH tunnel; no local inference fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.request import urlopen

RUNTIME = Path(__file__).with_name(".runtime")


def ready(url):
    try:
        with urlopen(url + "/api/version", timeout=2) as response:
            return bool(json.loads(response.read(1000)).get("version"))
    except (OSError, ValueError):
        return False


def ensure():
    settings = RUNTIME / "gpu-connection.json"
    if not settings.is_file():
        return
    cfg = json.loads(settings.read_text(encoding="utf-8"))
    host, local_port, remote_port = cfg["ssh_host"], cfg["local_port"], cfg["remote_port"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", host) or any(type(p) is not int or not 1024 <= p <= 65535 for p in (local_port, remote_port)):
        raise RuntimeError("GPU 연결 설정을 확인해 주세요.")
    url = f"http://127.0.0.1:{local_port}"
    if ready(url):
        return
    # Only this project's existing unprivileged server may be started.
    common = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    check = subprocess.run(common + [host, f"curl -fsS http://127.0.0.1:{remote_port}/api/version || bash ~/clinicaltext-gpu-setup.sh"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, creationflags=flags)
    if check.returncode:
        raise RuntimeError("GPU 서버에 연결하지 못했습니다. 서버·SSH 연결을 확인해 주세요. 로컬 CPU로 대체하지 않았습니다.")
    with (RUNTIME / "gpu-tunnel.log").open("ab") as output, (RUNTIME / "gpu-tunnel-error.log").open("ab") as error:
        process = subprocess.Popen(common + ["-N", "-T", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", host], stdin=subprocess.DEVNULL, stdout=output, stderr=error, creationflags=flags)
    (RUNTIME / "gpu-tunnel.pid").write_text(str(process.pid), encoding="ascii")
    for _ in range(30):
        if ready(url):
            return
        if process.poll() is not None:
            break
        time.sleep(0.25)
    raise RuntimeError("GPU 서버 연결을 열지 못했습니다. 로컬 CPU로 대체하지 않았습니다.")


if __name__ == "__main__":
    ensure()
