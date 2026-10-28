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
async function capabilities(env){const row=await env.DB.prepare("SELECT value FROM control WHERE name='presentation'").first();try{return row?JSON.parse(row.value):{cache_ready:[],live_ready:false};}catch{return{cache_ready:[],live_ready:false};}}
async function enqueue(env,owner,kind,payload){
  const id=crypto.randomUUID(),stamp=now();
  let execution='control';
  if(kind==='analyze'){
    const purpose=payload.purpose.normalize('NFKC').replace(/\r\n?/g,'\n').trim();payload={...payload,purpose};
    const record=CATALOG.records.find(r=>r.id===payload.record_id);
    const known=record&&!payload.custom_note&&(purpose===record.purpose||(record.id==='surrogate'&&CATALOG.purposes.some(p=>p.purpose&&p.purpose===purpose)));
    const caps=await capabilities(env);
    if(known){
      if(!caps.cache_ready?.some(c=>c.record_id===record.id&&c.purpose===purpose))failure('예제 실행 결과를 준비하고 있습니다. 잠시 후 다시 시도해 주세요.',503);
      execution='cached';
    }else{
      execution='live';
      if(!caps.live_ready)failure('다른 서버 작업을 보호하기 위해 실시간 분석을 잠시 중지했습니다. 기본 예제를 이용해 주세요.',503);
    }
  }
  let sql="INSERT INTO jobs(id,owner,kind,payload,state,created,execution) SELECT ?,?,?,?,'queued',?,? WHERE (SELECT COUNT(*) FROM jobs WHERE owner=? AND state IN ('queued','running'))=0";
  const values=[id,owner,kind,JSON.stringify(payload),stamp,execution,owner];
  if(execution==='live'){
    sql+=" AND (SELECT COUNT(*) FROM jobs WHERE execution='live' AND state IN ('queued','running'))<? AND (SELECT COUNT(*) FROM jobs WHERE owner=? AND execution='live' AND created>?)<? AND (SELECT COUNT(*) FROM jobs WHERE execution='live' AND created>?)<? AND (SELECT COUNT(*) FROM jobs WHERE execution='live' AND created>?)=0";
    values.push(EVENT_LIMITS.live_pending,owner,stamp-600,EVENT_LIMITS.live_per_session_10min,stamp-3600,EVENT_LIMITS.live_per_hour,stamp-EVENT_LIMITS.live_accept_interval_seconds);
  }else{sql+=" AND (SELECT COUNT(*) FROM jobs WHERE execution!='live' AND state IN ('queued','running'))<?";values.push(EVENT_LIMITS.fast_pending);}
  const result=await env.DB.prepare(sql).bind(...values).run();
  if(!result.meta.changes)failure(execution==='live'?'실시간 체험은 사용자당 10분에 1회, 전체 시간당 12회로 제한합니다. 잠시 후 다시 시도하거나 기본 예제를 이용해 주세요.':'체험 요청이 많거나 이미 처리 중인 요청이 있습니다. 잠시 후 다시 시도해 주세요.',429);
  return reply({job_id:id,state:'queued',position:0,execution},202);
}
async function handle(request,env){
  try{
    const url=new URL(request.url),path=url.pathname;
    if(path.startsWith('/internal/')){
      agent(request,env);
      if(request.method==='POST'&&path==='/internal/heartbeat'){
        const info=await body(request,8000);
        await env.DB.batch([env.DB.prepare("INSERT INTO control(name,value) VALUES('heartbeat',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value").bind(String(now())),env.DB.prepare("INSERT INTO control(name,value) VALUES('presentation',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value").bind(JSON.stringify(info))]);return reply({ok:true});
      }
      if(request.method==='POST'&&path==='/internal/claim'){
        await env.DB.prepare("INSERT INTO control(name,value) VALUES('heartbeat',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value").bind(String(now())).run();
        const data=await body(request);const lane=data.lane||'all';if(!['fast','live','all'].includes(lane))failure('Unknown lane');
        await env.DB.prepare("UPDATE jobs SET state='error',result=? WHERE state='running' AND claimed<?").bind(JSON.stringify({message:'서버 처리 시간이 초과됐습니다. 다시 실행해 주세요.'}),now()-180).run();
        const job=await env.DB.prepare("UPDATE jobs SET state='running',claimed=? WHERE id=(SELECT id FROM jobs WHERE state='queued' AND (?='all' OR (?='live' AND execution='live') OR (?='fast' AND execution!='live')) ORDER BY CASE WHEN kind IN ('approve','export') THEN 0 ELSE 1 END,created,id LIMIT 1) AND state='queued' RETURNING id,owner,kind,payload,execution").bind(now(),lane,lane,lane).first();
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
      const origin=request.headers.get('Origin');if(origin&&origin!==url.origin&&origin!=='https://hyunmin-3428.github.io')failure('허용되지 않은 접속입니다.',403);
      if(request.method==='GET'&&path==='/api/health')return reply({status:'ok',server_online:await online(env),scope:'authored-synthetic-only',model:'qwen3:30b'});
      if(request.method==='GET'&&path==='/api/bootstrap'){
        const stamp=now(),visitor=await hash((env.AGENT_TOKEN||'')+(request.headers.get('CF-Connecting-IP')||'unknown'));
        await env.DB.batch([env.DB.prepare('DELETE FROM jobs WHERE created<?').bind(stamp-7200),env.DB.prepare('DELETE FROM sessions WHERE expires<?').bind(stamp)]);
        const counts=await env.DB.prepare('SELECT COUNT(*) AS total,SUM(CASE WHEN visitor=? AND created>? THEN 1 ELSE 0 END) AS recent FROM sessions').bind(visitor,stamp-600).first();
        if(counts.total>=EVENT_LIMITS.max_sessions||counts.recent>=EVENT_LIMITS.bootstrap_per_ip_10min)failure('현재 체험 접속이 많습니다. 잠시 후 다시 시도해 주세요.',429);
        const id=crypto.randomUUID(),token=crypto.randomUUID()+'.'+crypto.randomUUID();
        const admitted=await env.DB.prepare('INSERT INTO sessions(id,token_hash,visitor,created,expires) SELECT ?,?,?,?,? WHERE (SELECT COUNT(*) FROM sessions)<? AND (SELECT COUNT(*) FROM sessions WHERE visitor=? AND created>?)<?').bind(id,await hash(token),visitor,stamp,stamp+7200,EVENT_LIMITS.max_sessions,visitor,stamp-600,EVENT_LIMITS.bootstrap_per_ip_10min).run();
        if(!admitted.meta.changes)failure('현재 체험 접속이 많습니다. 잠시 후 다시 시도해 주세요.',429);
        return reply({...CATALOG,token,scope:'synthetic-only',server_online:await online(env),expires_in:7200,event_limits:EVENT_LIMITS,presentation:await capabilities(env)});
      }
      const owner=await session(request,env);
      if(request.method==='GET'&&/^\/api\/jobs\/[a-f0-9-]{36}$/.test(path)){
        const job=await env.DB.prepare('SELECT id,kind,state,result,created,claimed,execution FROM jobs WHERE id=? AND owner=?').bind(path.split('/').pop(),owner).first();if(!job)failure('요청을 찾을 수 없습니다.',403);
        const ahead=await env.DB.prepare("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('queued','running') AND created<?").bind(job.created).first();
        const result=job.result?JSON.parse(job.result):null;
        return reply({job_id:job.id,state:job.state,execution:job.execution,position:ahead.n,elapsed:now()-job.created,...(job.state==='done'?(job.kind==='analyze'?{view:result}:{result}):job.state==='error'?{message:result?.message||'처리를 완료하지 못했습니다.'}:{})});
      }
      if(request.method==='POST'&&path==='/api/analyze'){
        if(!await online(env))failure('GPU 체험 서버에 연결 중입니다. 잠시 후 다시 시도해 주세요.',503);
        const data=await body(request,24000);
        if(data.custom_note!==undefined){
          exact(data,['record_id','purpose_id','purpose','custom_note']);exact(data.custom_note,['title','text','synthetic_confirmed']);
          if(typeof data.record_id!=='string'||!/^custom-[a-f0-9-]{36}$/.test(data.record_id)||data.custom_note.synthetic_confirmed!==true||typeof data.custom_note.title!=='string'||!data.custom_note.title.trim()||data.custom_note.title.length>60||typeof data.custom_note.text!=='string'||!data.custom_note.text.trim()||data.custom_note.text.length>EVENT_LIMITS.live_max_chars||new TextEncoder().encode(data.custom_note.text).length>16000)failure('발표용 실시간 체험은 직접 작성한 합성 기록 1,000자 이내로 입력해 주세요.');
        }else{exact(data,['record_id','purpose_id','purpose']);if(!CATALOG.records.some(r=>r.id===data.record_id))failure('기록을 확인해 주세요.');}
        if(!CATALOG.purposes.some(p=>p.id===data.purpose_id)||typeof data.purpose!=='string'||!data.purpose.trim()||data.purpose.length>800)failure('연구 목적을 확인해 주세요.');
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
}
export default {async fetch(request,env){
  const origin=request.headers.get('Origin'),url=new URL(request.url);
  const allowed=origin===url.origin||origin==='https://hyunmin-3428.github.io';
  if(request.method==='OPTIONS'){
    if(!allowed||!url.pathname.startsWith('/api/'))return reply({message:'허용되지 않은 접속입니다.'},403);
    return new Response(null,{status:204,headers:{'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Methods':'GET, POST, OPTIONS','Access-Control-Allow-Headers':'Authorization, Content-Type','Access-Control-Max-Age':'600','Vary':'Origin'}});
  }
  const response=await handle(request,env);
  if(allowed){response.headers.set('Access-Control-Allow-Origin',origin);response.headers.set('Vary','Origin');}
  return response;
}};
