"""Bound this project's inference; no shared daemon or unrelated process changes."""
import contextlib
import csv
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.request import Request,urlopen
from ClinicalCDSGateway.gateway.common import Denied
from .llm_client import StructuredClient

ROOT=Path(__file__).resolve().parent.parent
LIMITS=json.loads((ROOT/'event_limits.json').read_text())

class ResourceGuard:
    def __init__(self,runtime,model_url,model):
        self.runtime=Path(runtime);self.runtime.mkdir(parents=True,exist_ok=True)
        self.model_url,self.model=model_url,model
        self.lock=threading.Lock()
        self.deadline=None
        self.runtime_gpu=Path.home()/'.local/share/clinicaltext-gpu'
    def usage(self):
        path=self.runtime/'inference-budget.json'
        try:values=json.loads(path.read_text())
        except FileNotFoundError:values=[]
        return [v for v in values if time.time()-v['time']<LIMITS['budget_window_seconds']]
    def remaining(self):return max(0,LIMITS['llm_seconds_per_hour']-sum(v['seconds'] for v in self.usage()))
    def charge(self,seconds):
        values=self.usage()+[{'time':time.time(),'seconds':round(seconds,3)}]
        p=self.runtime/'inference-budget.json';tmp=p.with_suffix('.pending');tmp.write_text(json.dumps(values));tmp.replace(p)
    def gpu_available(self):
        try:
            selected=(self.runtime_gpu/'gpu.uuid').read_text().strip()
            daemon=int((self.runtime_gpu/'server.pid').read_text().strip())
            if not (Path('/proc')/str(daemon)).exists():return False
            inventory=subprocess.run(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],capture_output=True,text=True,timeout=5,check=True)
            if selected not in inventory.stdout.split():return False
            def ours(pid):
                for _ in range(20):
                    if pid==daemon:return True
                    try:pid=int((Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[1])
                    except (OSError,ValueError,IndexError):return False
                    if pid<=1:return False
                return False
            r=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5,check=True)
            for row in csv.reader(r.stdout.splitlines()):
                if len(row)>=2 and row[0].strip()==selected and not ours(int(row[1].strip())):return False
            return True
        except (OSError,ValueError,subprocess.SubprocessError):return False
    def ready(self):
        if (self.runtime/'live-paused.json').exists():return False
        return self.remaining()>10 and self.gpu_available()
    def before_call(self):
        if self.deadline is None or time.monotonic()>=self.deadline:raise Denied('DEMO_LIVE_TIME_LIMIT')
        if self.remaining()<=1:raise Denied('DEMO_GPU_BUDGET')
        if not self.gpu_available():raise Denied('DEMO_GPU_BUSY')
    @contextlib.contextmanager
    def lease(self):
        import fcntl
        if not self.lock.acquire(blocking=False):raise Denied('DEMO_GPU_BUSY')
        file=(self.runtime/'inference.lock').open('a')
        try:
            try:fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Denied('DEMO_GPU_BUSY')
            if not self.ready():raise Denied('DEMO_GPU_BUSY')
            self.deadline=time.monotonic()+min(LIMITS['live_deadline_seconds'],self.remaining())
            yield
        finally:
            if self.deadline is not None:
                # Only our isolated Ollama endpoint/model is addressed. No kill,
                # host reboot, GPU reset or shared-service restart is used.
                try:
                    req=Request(self.model_url+'/api/generate',data=json.dumps({'model':self.model,'keep_alive':0,'stream':False}).encode(),headers={'Content-Type':'application/json'})
                    with urlopen(req,timeout=10) as response:response.read(10000)
                    with urlopen(self.model_url+'/api/ps',timeout=5) as response:loaded=json.load(response)
                    if any(m.get('name')==self.model or m.get('model')==self.model for m in loaded.get('models',[])):
                        raise RuntimeError('model still loaded')
                except Exception:
                    (self.runtime/'live-paused.json').write_text(json.dumps({'time':time.time(),'reason':'model_unload_unconfirmed'}))
            self.deadline=None
            fcntl.flock(file,fcntl.LOCK_UN);file.close();self.lock.release()

class GuardedClient(StructuredClient):
    def __init__(self,config,guard):
        super().__init__(config);self.guard=guard;self.keep_alive=LIMITS['keep_alive']
    def complete(self,*args,**kwargs):
        self.guard.before_call()
        self.request_timeout=max(.1,min(self.guard.deadline-time.monotonic(),self.guard.remaining()))
        started=time.monotonic()
        try:return super().complete(*args,**kwargs)
        finally:self.guard.charge(time.monotonic()-started)
