"""Restart only this project's web/GPU-agent process; never reboot the shared host."""
import json
from pathlib import Path
import subprocess
import sys
import time

root=Path(__file__).resolve().parent.parent
runtime=root/'runtime'
runtime.mkdir(exist_ok=True)
config=json.loads((runtime/'agent-config.json').read_text())
command=[sys.executable,'-u','gpu_agent.py','--site',config['site'],'--token-file',str(runtime/'agent.token'),'--runtime',str(runtime/'sessions')]
while True:
    with (runtime/'agent.log').open('ab') as log:
        child=subprocess.Popen(command,cwd=root/'web',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        (runtime/'agent.pid').write_text(str(child.pid))
        child.wait()
    time.sleep(5)
