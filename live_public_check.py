"""Exercise authored synthetic examples through the deployed, anonymous web API."""
import argparse
import json
from pathlib import Path
import time
from urllib.request import Request,urlopen

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--site',default='https://clinicaltext-emr.alexchoi676.chatgpt.site')
    parser.add_argument('--output',default='.runtime/public-cases.json')
    parser.add_argument('--record',default='')
    parser.add_argument('--purpose',default='')
    args=parser.parse_args()
    token=''
    def api(path,data=None):
        headers={'User-Agent':'ClinicalText-public-evaluation'}
        if token:headers['Authorization']='Bearer '+token
        if data is not None:headers['Content-Type']='application/json'
        request=Request(args.site+path,headers=headers,data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None)
        with urlopen(request,timeout=25) as response:return json.load(response)
    bootstrap=api('/api/bootstrap');token=bootstrap['token'];rows=[]
    def wait(job):
        deadline=time.time()+900
        while time.time()<deadline:
            result=api('/api/jobs/'+job['job_id'])
            if result['state'] in {'done','error'}:return result
            time.sleep(1.5)
        raise RuntimeError('Public job timeout')
    for record in bootstrap['records']:
        if args.record and record['id']!=args.record:continue
        purpose=args.purpose or record['purpose']
        result=wait(api('/api/analyze',{'record_id':record['id'],'purpose_id':'record-default','purpose':purpose}))
        if result['state']=='done':
            view=result['view']
            rows.append({'case':record['id'],'request_state':'done','release_state':view['state'],'passed':view['validation']['passed'],'failed':view['validation']['failed'],'unknown':view['validation']['unknown'],'residual':view['privacy']['residual_count'],'model_calls':view['llm']['calls'],'seconds':result['elapsed'],'output':view['output'],'reasons':view['reasons']})
        else:rows.append({'case':record['id'],'request_state':'error','message':result.get('message')})
        output=Path(args.output);output.parent.mkdir(exist_ok=True,parents=True);output.write_text(json.dumps({'scope':'authored synthetic examples, not held-out validation','cases':rows},ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({key:value for key,value in rows[-1].items() if key not in {'output','reasons'}},ensure_ascii=True),flush=True)
    print('Completed live public-route checks.',flush=True)

if __name__=='__main__':main()
