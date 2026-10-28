"""Deploy only this bounded synthetic API into a user-owned directory over SSH."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--ssh",default="server")
    parser.add_argument("--origin",required=True)
    args=parser.parse_args()
    runtime=ROOT/".runtime"
    runtime.mkdir(exist_ok=True)
    archive=runtime/"public-source.zip"
    excluded={".runtime",".git","__pycache__",".venv","node_modules",".openai"}
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
        for path in sorted(ROOT.rglob("*")):
            rel=path.relative_to(ROOT)
            if not path.is_file() or any(p in excluded for p in rel.parts) or path.suffix in {".pyc",".zip"}:continue
            z.writestr(rel.as_posix(),path.read_bytes())
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    remote="$HOME/.local/share/clinicaltext-emr"
    subprocess.run(["ssh","-o","BatchMode=yes",args.ssh,"mkdir -p \""+remote+"\""],check=True,timeout=25)
    subprocess.run(["scp","-q",str(archive),args.ssh+":.local/share/clinicaltext-emr/public-source.zip"],check=True,timeout=90)
    script='''import hashlib,json,os,pathlib,subprocess,time,urllib.request,zipfile,re
root=pathlib.Path.home()/".local/share/clinicaltext-emr"
archive=root/"public-source.zip"
assert hashlib.sha256(archive.read_bytes()).hexdigest()==DIGEST
app=root/"app"; app.mkdir(exist_ok=True)
with zipfile.ZipFile(archive) as z:
 for name in z.namelist():
  assert (app/name).resolve().is_relative_to(app.resolve())
 z.extractall(app)
logs=root/"runtime";logs.mkdir(exist_ok=True)
python=pathlib.Path.home()/".local/share/clinicaltext-gpu/web-venv/bin/python"
assert python.is_file()
subprocess.run([str(python),"-m","unittest","discover","-s","tests","-v"],cwd=app,stdout=(logs/"tests.log").open("w"),stderr=subprocess.STDOUT,check=True,timeout=90)
pidfile=logs/"api.pid"
if pidfile.exists():
 pid=int(pidfile.read_text())
 proc=pathlib.Path("/proc")/str(pid)
 if proc.exists():
  assert proc.joinpath("cwd").resolve()==app.resolve()
  assert b"demo_server.py" in proc.joinpath("cmdline").read_bytes()
  os.kill(pid,15);time.sleep(1)
with (logs/"api.log").open("ab") as log:
 p=subprocess.Popen([str(python),"-u","demo_server.py","--port","8770","--origin",ORIGIN],cwd=app,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
pidfile.write_text(str(p.pid))
for _ in range(30):
 try:
  health=json.load(urllib.request.urlopen("http://127.0.0.1:8770/api/health",timeout=3));break
 except Exception:time.sleep(1)
else:raise RuntimeError("API not ready")
binary=root/"bin/cloudflared";binary.parent.mkdir(exist_ok=True)
metadata={}
if not binary.is_file():
 request=urllib.request.Request("https://api.github.com/repos/cloudflare/cloudflared/releases/latest",headers={"User-Agent":"ClinicalText-demo-setup"})
 release=json.load(urllib.request.urlopen(request,timeout=30))
 asset=next(x for x in release["assets"] if x["name"]=="cloudflared-linux-amd64")
 raw=urllib.request.urlopen(asset["browser_download_url"],timeout=90).read()
 expected=asset.get("digest","")
 assert expected.startswith("sha256:") and hashlib.sha256(raw).hexdigest()==expected[7:]
 binary.write_bytes(raw);binary.chmod(0o755)
 metadata={"release":release["tag_name"],"sha256":expected[7:]}
 (logs/"cloudflared-source.json").write_text(json.dumps(metadata))
tunnelpid=logs/"tunnel.pid"
reuse=False
if tunnelpid.exists():
 pid=int(tunnelpid.read_text());proc=pathlib.Path("/proc")/str(pid)
 reuse=proc.exists() and str(binary).encode() in proc.joinpath("cmdline").read_bytes()
if not reuse:
 with (logs/"tunnel.log").open("wb") as log:
  p=subprocess.Popen([str(binary),"tunnel","--no-autoupdate","--protocol","http2","--url","http://127.0.0.1:8770"],cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 tunnelpid.write_text(str(p.pid))
for _ in range(35):
 text=(logs/"tunnel.log").read_text(errors="replace")
 urls=re.findall(r"https://[a-z0-9-]+\\.trycloudflare\\.com",text)
 if urls:break
 time.sleep(1)
else:raise RuntimeError("No public tunnel URL; inspect tunnel log")
result={"api_url":urls[-1],"api_pid":int(pidfile.read_text()),"tunnel_pid":int(tunnelpid.read_text()),"root":str(root),"source_sha256":DIGEST,"health":health,"temporary_tunnel":True}
(logs/"deployment.json").write_text(json.dumps(result,indent=2))
print(json.dumps(result))
'''.replace("DIGEST",repr(digest)).replace("ORIGIN",repr(args.origin))
    output=subprocess.run(["ssh","-o","BatchMode=yes",args.ssh,"python3 -"],input=script,text=True,encoding="utf8",capture_output=True,timeout=240)
    if output.returncode:
        raise RuntimeError(output.stderr[-3500:]+output.stdout[-1000:])
    result=json.loads(output.stdout.strip().splitlines()[-1])
    (runtime/"deployment.json").write_text(json.dumps(result,indent=2),encoding="utf8")
    print(json.dumps(result))

if __name__=="__main__":main()
