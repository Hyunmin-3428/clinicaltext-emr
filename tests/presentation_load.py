"""Opt-in public cached-lane load test. Never submits live model work."""
import argparse,concurrent.futures,json,statistics,time,sys
from pathlib import Path
from urllib.request import Request,urlopen
API='https://clinicaltext-emr.alexchoi676.chatgpt.site'

def call(path,data=None,token=None):
    headers={'User-Agent':'ClinicalText-50-cached-validation'}
    if token:headers['Authorization']='Bearer '+token
    if data is not None:headers['Content-Type']='application/json'
    request=Request(API+path,data=None if data is None else json.dumps(data,ensure_ascii=False).encode(),headers=headers)
    with urlopen(request,timeout=30) as response:return json.load(response)

def wait_job(job,token):
    started=time.monotonic()
    while time.monotonic()-started<180:
        result=call('/api/jobs/'+job['job_id'],token=token)
        if result['state']=='done':return result
        if result['state']=='error':raise RuntimeError(result.get('message'))
        time.sleep(2)
    raise TimeoutError('cached queue')

def visitor(i):
    session=call('/api/bootstrap');token=session['token']
    assert len(session['presentation']['cache_ready'])>=len(session['records'])
    record=session['records'][i%len(session['records'])]
    started=time.monotonic()
    job=call('/api/analyze',{'record_id':record['id'],'purpose_id':'record-default','purpose':record['purpose']},token)
    assert job['execution']=='cached',job
    view=wait_job(job,token)['view']
    assert view['llm']['cached'] is True and view['llm']['calls']==0,view['llm']
    assert view['state']=='REVIEW_READY' and view['validation']['failed']==0 and view['validation']['unknown']==0,view['state']
    seconds=round(time.monotonic()-started,3)
    print(f"visitor {i+1}: cached result in {seconds}s",flush=True)
    return {'record_id':record['id'],'seconds':seconds,'passed':view['validation']['passed'],'new_llm_calls':0},(token,view)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--visitors',type=int,default=50);parser.add_argument('--report',required=True);args=parser.parse_args()
    assert 1<=args.visitors<=50
    started=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.visitors) as pool:values=list(pool.map(visitor,range(args.visitors)))
    summary=[v[0] for v in values]
    token,view=values[0][1]
    payload={'analysis_id':view['analysis_id'],'review_digest':view['review_digest'],'confirmed':True}
    summary_path=Path(args.report);summary_path.parent.mkdir(parents=True,exist_ok=True)
    summary_path.write_text(json.dumps({'visitors':args.visitors,'completed':len(summary),'new_llm_calls':0,'cases':summary},ensure_ascii=False,indent=2),encoding='utf8')
    approved=wait_job(call('/api/approve',payload,token),token)['result']
    exported=wait_job(call('/api/export',payload,token),token)['result']
    import base64,io,zipfile
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from ClinicalCDSGateway.gateway.engine import verify_external
    # Store only authored example artifacts, never visitor credentials.
    folder=Path(args.report).parent;folder.mkdir(parents=True,exist_ok=True)
    for key in ['text_b64','xlsx_b64']:assert exported.get(key)
    assert zipfile.is_zipfile(io.BytesIO(base64.b64decode(exported['xlsx_b64'])))
    verified=verify_external(json.loads(base64.b64decode(exported["artifact_b64"])),base64.b64decode(exported["verification_key_b64"]))
    assert verified["text"]==view["output"]
    report={'visitors':args.visitors,'completed':len(summary),'new_llm_calls':sum(v['new_llm_calls'] for v in summary),'total_seconds':round(time.monotonic()-started,3),'analysis_p50_seconds':statistics.median(v['seconds'] for v in summary),'analysis_max_seconds':max(v['seconds'] for v in summary),'approval_completed':True,'export_completed':True,'signature_verified':True,'export_fields':sorted(exported),'cases':summary}
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
