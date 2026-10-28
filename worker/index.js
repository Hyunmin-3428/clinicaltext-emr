// All public sources are authored synthetic fixtures. The GPU agent pulls jobs over HTTPS.
const JSON_HEADERS = {"Content-Type":"application/json; charset=utf-8","Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer"};
const reply=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:JSON_HEADERS});
const now=()=>Math.floor(Date.now()/1000);
async function hash(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),x=>x.toString(16).padStart(2,'0')).join('');}
const failure=(message,status=400)=>{throw Object.assign(new Error(message),{status});};
async function body(request,max=5000){if(Number(request.headers.get('Content-Length')||0)>max)failure('요청이 너무 큽니다.');const text=await request.text();if(text.length>max)failure('요청이 너무 큽니다.');try{return JSON.parse(text);}catch{failure('입력 형식을 확인해 주세요.');}}
function exact(value,fields){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join('|')!==[...fields].sort().join('|'))failure('허용되지 않은 입력입니다.');}
async function session(request,env){const token=request.headers.get('Authorization')||'';if(!/^Bearer [a-f0-9-]{36}\.[a-f0-9-]{36}$/.test(token))failure('체험 연결이 만료됐습니다. 새로고침해 주세요.',403);const found=await env.DB.prepare('SELECT id FROM sessions WHERE token_hash=? AND expires>?').bind(await hash(token.slice(7)),now()).first();if(!found)failure('체험 연결이 만료됐습니다. 새로고침해 주세요.',403);return found.id;}
function agent(request,env){const supplied=request.headers.get('Authorization')||'';if(!env.AGENT_TOKEN||supplied!==`Bearer ${env.AGENT_TOKEN}`)failure('접근할 수 없습니다.',403);}
async function online(env){const row=await env.DB.prepare("SELECT value FROM control WHERE name='heartbeat'").first();return row&&now()-Number(row.value)<45;}
async function enqueue(env,owner,kind,payload){
  const id=crypto.randomUUID(),stamp=now();
  const result=await env.DB.prepare(`INSERT INTO jobs(id,owner,kind,payload,state,created) SELECT ?,?,?,?,'queued',? WHERE (SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running'))<8 AND (SELECT COUNT(*) FROM jobs WHERE owner=? AND state IN ('queued','running'))=0 AND (SELECT COUNT(*) FROM jobs WHERE owner=? AND kind='analyze' AND created>?)<8 AND (SELECT COUNT(*) FROM jobs WHERE kind='analyze' AND created>?)<180`).bind(id,owner,kind,JSON.stringify(payload),stamp,owner,owner,stamp-600,stamp-3600).run();
  if(!result.meta.changes)failure('체험 대기열 또는 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',429);
  return reply({job_id:id,state:'queued',position:0},202);
}
export default {async fetch(request,env){
  try{
    const url=new URL(request.url),path=url.pathname;
    if(path.startsWith('/internal/')){
      agent(request,env);
      if(request.method==='POST'&&path==='/internal/heartbeat'){
        await env.DB.prepare("INSERT INTO control(name,value) VALUES('heartbeat',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value").bind(String(now())).run();return reply({ok:true});
      }
      if(request.method==='POST'&&path==='/internal/claim'){
        await env.DB.prepare("INSERT INTO control(name,value) VALUES('heartbeat',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value").bind(String(now())).run();
        await env.DB.prepare("UPDATE jobs SET state='error',result=? WHERE state='running' AND claimed<?").bind(JSON.stringify({message:'서버 처리 시간이 초과됐습니다. 다시 실행해 주세요.'}),now()-1200).run();
        const job=await env.DB.prepare("UPDATE jobs SET state='running',claimed=? WHERE id=(SELECT id FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1) AND state='queued' RETURNING id,owner,kind,payload").bind(now()).first();
        return reply({job:job?{...job,payload:JSON.parse(job.payload)}:null});
      }
      if(request.method==='POST'&&path==='/internal/complete'){
        const data=await body(request,700000);exact(data,['id','ok','result']);
        const result=JSON.stringify(data.result);if(result.length>650000)failure('결과 크기를 초과했습니다.');
        await env.DB.prepare("UPDATE jobs SET state=?,result=? WHERE id=? AND state='running'").bind(data.ok===true?'done':'error',result,data.id).run();return reply({ok:true});
      }
      return reply({error:'NOT_FOUND'},404);
    }
    if(path.startsWith('/api/')){
      if(!env.DB)return reply({message:'체험 저장소를 준비하고 있습니다.'},503);
      const origin=request.headers.get('Origin');if(origin&&origin!==url.origin)failure('허용되지 않은 접속입니다.',403);
      if(request.method==='GET'&&path==='/api/health')return reply({status:'ok',server_online:await online(env),scope:'authored-synthetic-only',model:'qwen3:30b'});
      if(request.method==='GET'&&path==='/api/bootstrap'){
        const stamp=now(),visitor=await hash((env.AGENT_TOKEN||'')+(request.headers.get('CF-Connecting-IP')||'unknown'));
        await env.DB.batch([env.DB.prepare('DELETE FROM jobs WHERE created<?').bind(stamp-7200),env.DB.prepare('DELETE FROM sessions WHERE expires<?').bind(stamp)]);
        const counts=await env.DB.prepare('SELECT COUNT(*) AS total,SUM(CASE WHEN visitor=? AND created>? THEN 1 ELSE 0 END) AS recent FROM sessions').bind(visitor,stamp-600).first();
        if(counts.total>=128||counts.recent>=40)failure('현재 체험 접속이 많습니다. 잠시 후 다시 시도해 주세요.',429);
        const id=crypto.randomUUID(),token=crypto.randomUUID()+'.'+crypto.randomUUID();
        await env.DB.prepare('INSERT INTO sessions(id,token_hash,visitor,created,expires) VALUES(?,?,?,?,?)').bind(id,await hash(token),visitor,stamp,stamp+7200).run();
        return reply({...CATALOG,token,scope:'synthetic-only',server_online:await online(env),expires_in:7200});
      }
      const owner=await session(request,env);
      if(request.method==='GET'&&/^\/api\/jobs\/[a-f0-9-]{36}$/.test(path)){
        const job=await env.DB.prepare('SELECT id,kind,state,result,created,claimed FROM jobs WHERE id=? AND owner=?').bind(path.split('/').pop(),owner).first();if(!job)failure('요청을 찾을 수 없습니다.',403);
        const ahead=await env.DB.prepare("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('queued','running') AND created<?").bind(job.created).first();
        const result=job.result?JSON.parse(job.result):null;
        return reply({job_id:job.id,state:job.state,position:ahead.n,elapsed:now()-job.created,...(job.state==='done'?(job.kind==='analyze'?{view:result}:{result}):job.state==='error'?{message:result?.message||'처리를 완료하지 못했습니다.'}:{})});
      }
      if(request.method==='POST'&&path==='/api/analyze'){
        if(!await online(env))failure('GPU 체험 서버에 연결 중입니다. 잠시 후 다시 시도해 주세요.',503);
        const data=await body(request);exact(data,['record_id','purpose_id','purpose']);
        if(!CATALOG.records.some(r=>r.id===data.record_id)||!CATALOG.purposes.some(p=>p.id===data.purpose_id)||typeof data.purpose!=='string'||!data.purpose.trim()||data.purpose.length>800)failure('연구 목적과 기록을 확인해 주세요.');
        return await enqueue(env,owner,'analyze',data);
      }
      if(request.method==='POST'&&['/api/approve','/api/export'].includes(path)){
        if(!await online(env))failure('GPU 체험 서버 연결을 확인해 주세요.',503);
        const data=await body(request);exact(data,['analysis_id','review_digest','confirmed']);
        if(data.confirmed!==true||typeof data.analysis_id!=='string'||typeof data.review_digest!=='string'||data.analysis_id.length>100||data.review_digest.length>100)failure('검토 결과를 확인해 주세요.');
        return await enqueue(env,owner,path.slice(5),data);
      }
      return reply({error:'NOT_FOUND'},404);
    }
    if(request.method!=='GET'&&request.method!=='HEAD')return reply({error:'NOT_FOUND'},404);
    const asset=ASSETS[path];if(!asset)return new Response('Not found',{status:404});
    return new Response(request.method==='HEAD'?null:asset.text,{headers:{'Content-Type':asset.type,'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}});
  }catch(error){return reply({message:error.status?error.message:'요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'},error.status||500);}
}};
