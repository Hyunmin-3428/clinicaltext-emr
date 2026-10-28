"""Source-only server deployment; AGENT_TOKEN is accepted on stdin, never printed."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--ssh',default='server')
    parser.add_argument('--site',required=True)
    args=parser.parse_args()
    import getpass
    token=(getpass.getpass('Private agent token (hidden): ') if sys.stdin.isatty() else sys.stdin.readline()).strip()
    if len(token)<40:raise RuntimeError('Private agent token required on stdin')
    runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True)
    archive=runtime/'agent-source.zip'
    excluded={'.runtime','.git','__pycache__','.venv','node_modules','.openai'}
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(ROOT.rglob('*')):
            rel=path.relative_to(ROOT)
            if path.is_file() and not any(v in excluded for v in rel.parts) and path.suffix not in {'.pyc','.zip'}:
                z.writestr(rel.as_posix(),path.read_bytes())
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    subprocess.run(['ssh','-o','BatchMode=yes',args.ssh,'mkdir -p "$HOME/clinicaltext"'],check=True,timeout=20)
    subprocess.run(['scp','-q',str(archive),args.ssh+':clinicaltext/source.zip'],check=True,timeout=90)
    script='''import pathlib,json,hashlib,os,subprocess,signal,time,zipfile
home=pathlib.Path.home();root=home/"clinicaltext";web=root/"web";runtime=root/"runtime";scripts=root/"scripts"
for path in [web,runtime,scripts]:path.mkdir(exist_ok=True)
runtime.chmod(0o700)
archive=root/"source.zip";assert hashlib.sha256(archive.read_bytes()).hexdigest()==DIGEST
with zipfile.ZipFile(archive) as z:
 for name in z.namelist():assert (web/name).resolve().is_relative_to(web.resolve())
 z.extractall(web)
old=home/".local/share/clinicaltext-emr"
if old.is_dir() and not old.is_symlink():
 for name,needle in [("api.pid",b"demo_server.py"),("tunnel.pid",b"cloudflared")]:
  p=old/"runtime"/name
  if p.is_file():
   pid=int(p.read_text());proc=pathlib.Path('/proc')/str(pid)
   if proc.exists():
    assert needle in proc.joinpath('cmdline').read_bytes()
    assert proc.joinpath('cwd').resolve().is_relative_to(old.resolve())
    os.kill(pid,signal.SIGTERM)
 snapshot=root/"initial-web-snapshot"
 assert not snapshot.exists()
 old.rename(snapshot)
for name,target in [("gpu-runtime",home/".local/share/clinicaltext-gpu"),("evaluations",home/".local/share/clinicaltext-gpu/jobs")]:
 link=root/name
 if not link.exists():link.symlink_to(target,target_is_directory=True)
tokenfile=runtime/"agent.token";tokenfile.write_text(TOKEN);tokenfile.chmod(0o600)
(runtime/"agent-config.json").write_text(json.dumps({"site":SITE,"source_sha256":DIGEST},indent=2))
python=home/".local/share/clinicaltext-gpu/web-venv/bin/python";assert python.is_file()
for action in ['start','status','stop']:
 command='#!/bin/sh\\nset -eu\\ncd "$(dirname "$0")/../web"\\nexec '+str(python)+' process_control.py '+action+'\\n'
 path=scripts/(action+'.sh');path.write_text(command);path.chmod(0o755)
(root/"README.md").write_text("# ClinicalText server workspace\\n\\nPublic site: "+SITE+"\\n\\nAll new web code is in web/. Logs, private token and temporary sessions are in runtime/.\\nThe existing GPU runtime and evaluation jobs are linked here without changing their paths or restarting them.\\n\\nWebsite worker only:\\n  ./scripts/status.sh\\n  ./scripts/start.sh\\n  ./scripts/stop.sh\\n\\nThese commands never reboot the shared host and never stop Ollama or unrelated processes.\\nA host reboot may require starting the website worker again; no system-level boot service was installed.\\n")
result=subprocess.run([str(python),'process_control.py','start'],cwd=web,capture_output=True,text=True,check=True)
print(json.dumps({'root':str(root),'site':SITE,'source_sha256':DIGEST,'worker':json.loads(result.stdout),'gpu_runtime_unchanged':True}))
'''.replace('DIGEST',repr(digest)).replace('TOKEN',repr(token)).replace('SITE',repr(args.site))
    result=subprocess.run(['ssh','-o','BatchMode=yes',args.ssh,'python3 -'],input=script,text=True,encoding='utf8',capture_output=True,timeout=60)
    if result.returncode:raise RuntimeError(result.stderr[-3000:])
    data=json.loads(result.stdout.strip().splitlines()[-1]);(runtime/'agent-deployment.json').write_text(json.dumps(data,indent=2))
    print(json.dumps(data))

if __name__=='__main__':main()
