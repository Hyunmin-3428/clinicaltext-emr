"""Control only this project's polling worker, never the shared host or Ollama."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

root=Path(__file__).resolve().parent.parent
runtime=root/'runtime'
runtime.mkdir(exist_ok=True)
pidfile=runtime/'supervisor.pid'

def existing():
    if not pidfile.is_file():return None
    pid=int(pidfile.read_text())
    proc=Path('/proc')/str(pid)
    if not proc.exists():return None
    if proc.joinpath('cwd').resolve()!=(root/'web').resolve() or b'server-supervisor.py' not in proc.joinpath('cmdline').read_bytes():
        raise RuntimeError('PID ownership check failed; no process was changed.')
    return pid

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','status','stop'])
    action=parser.parse_args().action
    pid=existing()
    if action=='start' and not pid:
        with (runtime/'supervisor.log').open('ab') as log:
            child=subprocess.Popen([sys.executable,'-u','server-supervisor.py'],cwd=root/'web',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        pid=child.pid;pidfile.write_text(str(pid))
    elif action=='stop' and pid:
        if os.getpgid(pid)!=pid:raise RuntimeError('Process group mismatch; nothing stopped.')
        os.killpg(pid,signal.SIGTERM);pid=None
    print(json.dumps({'website_worker':'running' if pid else 'stopped','pid':pid,'scope':'this project only; host and model unchanged'}))

if __name__=='__main__':main()
