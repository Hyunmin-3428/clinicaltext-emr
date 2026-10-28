"""Pull synthetic jobs over HTTPS. No inbound ports or changes to the GPU host services."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
from urllib.request import Request, urlopen
from ClinicalCDSGateway.gateway.common import Denied
from ClinicalTextWeb.service import MESSAGES
from demo_server import PublicDemo

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--site',required=True)
    parser.add_argument('--token-file',required=True)
    parser.add_argument('--model-url',default='http://127.0.0.1:11435')
    parser.add_argument('--model',default='qwen3:30b')
    parser.add_argument('--runtime',required=True)
    args=parser.parse_args()
    if not args.site.startswith('https://'):parser.error('HTTPS site required')
    token=Path(args.token_file).read_text().strip()
    runtime=Path(args.runtime);runtime.mkdir(parents=True,exist_ok=True)
    def post(path,data):
        request=Request(args.site.rstrip('/')+path,data=json.dumps(data,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'ClinicalText-GPU-agent'})
        with urlopen(request,timeout=25) as response:return json.load(response)
    with tempfile.TemporaryDirectory(prefix='sessions-',dir=runtime) as directory:
        app=PublicDemo(directory,args.model_url,args.model)
        sessions={}
        def heartbeat():
            while True:
                try:post('/internal/heartbeat',{})
                except Exception:pass
                time.sleep(10)
        threading.Thread(target=heartbeat,daemon=True).start()
        pending=None
        print('Synthetic GPU agent started; only outbound HTTPS is used.',flush=True)
        while True:
            try:
                if pending:
                    post('/internal/complete',pending)
                    (runtime/'last-result.json').write_text(json.dumps({'job_id':pending['id'],'ok':pending['ok'],'finished':time.time()}))
                    pending=None
                job=post('/internal/claim',{})['job']
                if not job:
                    time.sleep(2);continue
                owner=job['owner']
                if owner not in sessions:
                    sessions[owner]=app.service.new_session()[0]
                local=sessions[owner]
                try:
                    data=job['payload']
                    if job['kind']=='analyze':
                        record=app.records[data['record_id']]
                        result=app.service.analyze(local,{'engine':'llm','purpose':data['purpose'],'text':record['text'],'checks':record['checks'],'required_phrases':[],'synthetic_consent':True})
                    elif job['kind']=='approve':result=app.approve(local,data)
                    elif job['kind']=='export':result=app.export(local,data)
                    else:raise Denied('WEB_INPUT_INVALID')
                    pending={'id':job['id'],'ok':True,'result':result}
                except Exception as error:
                    code=error.code if isinstance(error,Denied) else 'WEB_INTERNAL_ERROR'
                    pending={'id':job['id'],'ok':False,'result':{'message':MESSAGES.get(code,'서버 처리를 완료하지 못했습니다. 다시 실행해 주세요.'),'error':code}}
            except Exception as error:
                # Never print URLs with parameters, credentials, note contents or model responses.
                print('Connection waiting: '+type(error).__name__,flush=True)
                time.sleep(5)

if __name__=='__main__':main()
