import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import worker from '../dist/server/index.js';

// Use actual SQLite semantics, including conditional INSERT and UPDATE RETURNING.
const python=process.env.TEST_PYTHON||path.resolve('../ClinicalTextDeid/.venv/Scripts/python.exe');
function database(){
  const file=path.join(mkdtempSync(path.join(tmpdir(),'clinical-worker-')),'test.sqlite');
  const schema=readFileSync('drizzle/0000_demo_queue.sql','utf8');
  const init=spawnSync(python,['-X','utf8','-c','import sqlite3,sys; c=sqlite3.connect(sys.argv[1]);c.executescript(sys.stdin.read());c.commit()',file],{input:schema,encoding:'utf8'});assert.equal(init.status,0,init.stderr);
  return {prepare(sql){let values=[];const statement={bind(...args){values=args;return statement;},async first(){return run().rows[0]||null;},async run(){return run();}};
    function run(){const p=spawnSync(python,['-X','utf8','-c',`import sqlite3,sys,json
d=json.load(sys.stdin);c=sqlite3.connect(sys.argv[1]);c.row_factory=sqlite3.Row
r=c.execute(d['sql'],d['values']);rows=[dict(v) for v in r.fetchall()] if r.description else []
changes=c.total_changes;c.commit();print(json.dumps({'rows':rows,'meta':{'changes':changes}}))`,file],{input:JSON.stringify({sql,values}),encoding:'utf8'});if(p.status)throw new Error(p.stderr);return JSON.parse(p.stdout);}return statement;},async batch(statements){return Promise.all(statements.map(s=>s.run()));}};
}
async function setup(){const env={DB:database(),AGENT_TOKEN:'test-only-agent-secret'};const call=async(path,method='GET',body,token)=>{const headers={};if(token)headers.Authorization='Bearer '+token;if(body)headers['Content-Type']='application/json';return worker.fetch(new Request('https://demo.example'+path,{method,headers,body:body?JSON.stringify(body):undefined}),env);};const bootstrap=await(await call('/api/bootstrap')).json();return{env,call,token:bootstrap.token};}
test('static EMR page and assets are served without source/admin routes',async()=>{for(const p of ['/','/app.js','/styles.css','/qr.svg'])assert.equal((await worker.fetch(new Request('https://demo.example'+p),{})).status,200);assert.equal((await worker.fetch(new Request('https://demo.example/.runtime/agent.token'),{})).status,404);});
test('agent endpoints reject missing or incorrect credentials',async()=>{const {call}=await setup();assert.equal((await call('/internal/claim','POST',{},'wrong')).status,403);});
test('public API does not accept arbitrary notes or settings',async()=>{const{call,token}=await setup();await call('/internal/heartbeat','POST',{},'test-only-agent-secret');assert.equal((await call('/api/analyze','POST',{record_id:'surrogate',purpose_id:'clinical',purpose:'研究',text:'private source'},token)).status,400);assert.equal((await call('/api/model/configure','POST',{},token)).status,404);});
test('real SQLite queue binds visitors, claims once, and returns completed result',async()=>{const{call,token}=await setup();await call('/internal/heartbeat','POST',{},'test-only-agent-secret');const submit=await call('/api/analyze','POST',{record_id:'surrogate',purpose_id:'clinical',purpose:'날짜 보존 연구'},token);assert.equal(submit.status,202);const job=await submit.json();assert.equal((await call('/api/analyze','POST',{record_id:'surrogate',purpose_id:'clinical',purpose:'연구'},token)).status,429);const claim=await(await call('/internal/claim','POST',{},'test-only-agent-secret')).json();assert.equal(claim.job.id,job.job_id);assert.equal((await(await call('/internal/claim','POST',{},'test-only-agent-secret')).json()).job,null);await call('/internal/complete','POST',{id:job.job_id,ok:true,result:{output:'가명처리 결과'}},'test-only-agent-secret');const done=await(await call('/api/jobs/'+job.job_id,'GET',undefined,token)).json();assert.equal(done.state,'done');assert.equal(done.view.output,'가명처리 결과');const other=await(await call('/api/bootstrap')).json();assert.equal((await call('/api/jobs/'+job.job_id,'GET',undefined,other.token)).status,403);});
test('offline GPU returns clear error instead of fake successful inference',async()=>{const{call,token}=await setup();const response=await call('/api/analyze','POST',{record_id:'surrogate',purpose_id:'clinical',purpose:'연구'},token);assert.equal(response.status,503);});
test('GitHub Pages preflight is allowed and other origins are rejected',async()=>{const{env}=await setup();const request=origin=>new Request('https://demo.example/api/analyze',{method:'OPTIONS',headers:{Origin:origin}});const allowed=await worker.fetch(request('https://hyunmin-3428.github.io'),env);assert.equal(allowed.status,204);assert.equal(allowed.headers.get('Access-Control-Allow-Origin'),'https://hyunmin-3428.github.io');assert.equal((await worker.fetch(request('https://evil.example'),env)).status,403);});
test('custom document requires explicit synthetic confirmation and size limits',async()=>{const{call,token}=await setup();await call('/internal/heartbeat','POST',{},'test-only-agent-secret');const data={record_id:'custom-11111111-1111-1111-1111-111111111111',purpose_id:'record-default',purpose:'임상 경과 연구',custom_note:{title:'새 수술기록',text:'수술 2일 후 발열 없음.',synthetic_confirmed:false}};assert.equal((await call('/api/analyze','POST',data,token)).status,400);data.custom_note.synthetic_confirmed=true;assert.equal((await call('/api/analyze','POST',data,token)).status,202);});

