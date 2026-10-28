"use strict";
const $ = id => document.getElementById(id);
const cfg = window.CLINICAL_DEMO || {};
const state = { token:"", records:[], purposes:[], selected:null, current:null, artifact:null, busy:false, generation:0, ready:false,limits:null };
const labels = { passed:"보존", failed:"손상 의심", unknown:"확인 필요", not_present:"해당 없음" };
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function message(text="",error=false){$("notice").textContent=text;$("notice").className="message"+(error?" error":"");$("notice").hidden=!text;}
async function api(path,data){
  const headers={};if(state.token)headers.Authorization=`Bearer ${state.token}`;
  if(data!==undefined)headers["Content-Type"]="application/json";
  let response;try{response=await fetch((cfg.apiBase||"")+path,{method:data===undefined?"GET":"POST",headers,body:data===undefined?undefined:JSON.stringify(data),credentials:"omit",signal:AbortSignal.timeout(20000)});}catch{throw new Error("서버에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.");}
  let result;try{result=await response.json();}catch{throw new Error("서버 응답을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.");}
  if(!response.ok)throw new Error(result.message||"요청을 완료하지 못했습니다.");return result;
}
function highlight(target,text,spans=[],cls="phi-mark"){
  target.replaceChildren();const chars=Array.from(text);let cursor=0;
  for(const span of [...spans].sort((a,b)=>a.start-b.start)){if(span.start<cursor||span.end>chars.length||span.end<=span.start)continue;target.append(document.createTextNode(chars.slice(cursor,span.start).join("")),node("mark",chars.slice(span.start,span.end).join(""),cls));cursor=span.end;}
  target.append(document.createTextNode(chars.slice(cursor).join("")));
}
function status(id,text,kind="neutral"){$(id).textContent=text;$(id).className="status-tag "+kind;}
function actions(){
  const v=state.current;
  $("process-open").disabled=!state.ready||state.busy||!state.selected;
  $("new-record").disabled=!state.ready||state.busy;
  $("run").disabled=state.busy||!$("purpose").value.trim();
  $("review").disabled=state.busy||!v?.can_approve||v.state!=="REVIEW_READY";
  $("approve").disabled=state.busy||v?.state!=="REVIEW_READY"||!v?.can_approve||!$("review").checked;
  const approved=v?.state==="APPROVED"||v?.state==="EXPORTED";
  $("copy").disabled=state.busy||!approved;$("format").disabled=state.busy||!approved;$("download").disabled=state.busy||!approved;
  $("download").textContent=state.artifact?"↓ 파일 다운로드":"↓ 승인 후 다운로드";
  document.querySelectorAll(".record").forEach(x=>x.disabled=state.busy);
}
function renderList(){
  const from=$("date-from").value,to=$("date-to").value,query=$("record-search").value.trim().toLocaleLowerCase();const records=state.records.filter(r=>(!from||r.date>=from)&&(!to||r.date<=to)&&(!query||`${r.category} ${r.department} ${r.title}`.toLocaleLowerCase().includes(query))).sort((a,b)=>b.date.localeCompare(a.date));
  $("record-count").textContent=`${records.length}건`;
  $("records").replaceChildren();
  const table=node("table",undefined,"record-table"),head=node("thead"),header=node("tr"),body=node("tbody");
  for(const label of ["","작성일","서식명","진료과"])header.append(node("th",label));head.append(header);table.append(head,body);
  for(const r of records){const selected=state.selected?.id===r.id,tr=node("tr",undefined,selected?"selected":""),check=node("td",selected?"▣":"□","record-selector"),date=node("td",r.date,"record-date"),title=node("td"),department=node("td",r.department,"record-department");check.setAttribute("aria-hidden","true");const b=node("button",r.category,"record"+(selected?" selected":""));b.type="button";b.title=r.title;b.setAttribute("aria-pressed",String(selected));b.setAttribute("aria-label",`${r.category} · ${r.department}`);b.onclick=()=>selectRecord(r);title.append(b);tr.append(check,date,title,department);body.append(tr);}$("records").append(table);
  if(!records.length)$("records").append(node("p","해당 기간의 기록이 없습니다.","loading-text"));actions();
}
function selectRecord(record){
  if(state.busy)return;state.generation++;state.selected=record;state.current=null;state.artifact=null;
  $("document-title").textContent=record.category;$("doc-department").textContent=`진료과 ${record.department}`;$("doc-date").textContent=`작성일 ${record.date}`;
  $("source").textContent=record.text;$("source-length").textContent=`${Array.from(record.text).length}자`;
  $("result").hidden=true;$("progress").hidden=true;$("empty").hidden=false;$("review").checked=false;status("output-status","처리 전");
  $("purpose-preset").value=record.id==="surrogate"?"clinical":"record-default";choosePurpose();renderList();message();
}
function choosePurpose(){const p=state.purposes.find(x=>x.id===$("purpose-preset").value);$("purpose").value=p?.id==="record-default"?state.selected?.purpose||"":p?.purpose||"";actions();}
function renderView(view,elapsed){
  state.current=view;$("empty").hidden=true;$("progress").hidden=true;$("result").hidden=false;
  highlight($("source"),view.source,view.source_spans);highlight($("output"),view.output,view.output_spans,"surrogate-mark");
  $("used-purpose").textContent=view.purpose;$("used-purpose").title=view.purpose;
  const v=view.validation,total=v.rows.filter(r=>r.status!=="not_present").length;
  $("phi-count").textContent=`${view.phi_count}개`;
  $("preserve-count").textContent=`${v.passed} / ${total}`;
  $("residual-count").textContent=`${view.privacy.residual_count}개`;
  if(elapsed!==undefined)$("duration").textContent=view.llm?.cached?'사전 실행 결과':`${elapsed.toFixed(1)}초`;
  const states={REVIEW_READY:["가명처리·검증 완료","success"],HELD:["확인 필요 · 반출 보류","warning"],APPROVED:["검토·승인 완료","success"],EXPORTED:["반출 완료","success"]};
  const [text,kind]=states[view.state]||["재검사 필요","warning"];status("output-status",view.llm?.cached&&view.state==='REVIEW_READY'?'사전 실행 결과 · 검증 완료':text,kind);
  status("release-status",{REVIEW_READY:"승인 대기",HELD:"반출 보류",APPROVED:"승인 완료",EXPORTED:"반출 완료"}[view.state]||"재검사 필요",kind);
  $("validation-rows").replaceChildren();
  for(const r of v.rows){const tr=node("tr"),label=node("td",r.label),pair=node("td"),wrap=node("div",undefined,"evidence-pair");for(const [key,name] of [["source","원문"],["output","변환"]]){const line=node("div");line.append(node("span",name),document.createTextNode(r[key]||"근거 없음"));wrap.append(line);}pair.append(wrap);const verdict=node("td");verdict.append(node("span",labels[r.status]||r.status,"status-tag "+(r.status==="passed"?"success":r.status==="failed"?"error":"warning")));tr.append(label,pair,verdict);$("validation-rows").append(tr);}
  $("hold-reasons").replaceChildren(...view.reasons.map(reason=>node("p",reason)));$("hold-reasons").hidden=!view.reasons.length;
  $("review").checked=["APPROVED","EXPORTED"].includes(view.state);actions();
}
async function processRecord(){
  if(state.busy||!state.selected||!$("purpose").value.trim())return;
  $("purpose-dialog").close();state.busy=true;state.current=null;state.artifact=null;state.generation++;const generation=state.generation;
  $("empty").hidden=true;$("result").hidden=true;$("progress").hidden=false;$("progress-title").textContent="서버에서 임상기록을 처리하고 있습니다";$("progress-detail").textContent="연구 목적별 가명처리와 임상정보 보존 검증을 진행합니다.";status("output-status","처리 중","neutral");message();actions();
  const start=Date.now();$("elapsed").textContent="0초 경과";const timer=setInterval(()=>$("elapsed").textContent=`${Math.floor((Date.now()-start)/1000)}초 경과`,1000);
  try{
    const input={record_id:state.selected.id,purpose_id:$("purpose-preset").value,purpose:$("purpose").value.trim()};
    if(state.selected.custom)input.custom_note={title:state.selected.category,text:state.selected.text,synthetic_confirmed:true};
    const submitted=await api("/api/analyze",input);
    const deadline=Date.now()+15*60*1000;
    while(generation===state.generation){
      const job=await api(`/api/jobs/${submitted.job_id}`);
      if(job.state==="done"){renderView(job.view,job.elapsed);message(job.view.can_approve?"가명처리와 검증이 완료됐습니다. 보존 근거를 확인하고 승인해 주세요.":"검토가 필요한 항목이 남아 반출을 보류했습니다.",!job.view.can_approve);break;}
      if(job.state==="error")throw new Error(job.message);
      if(Date.now()>deadline)throw new Error("처리가 오래 걸리고 있습니다. 서버 상황을 확인한 뒤 다시 시도해 주세요.");
      $("progress-title").textContent=job.execution==='cached'?'사전 실행 예제의 검증 결과를 불러오고 있습니다':job.state==="queued"?`서버 처리 대기 중 · 앞에 ${job.position}건`:"서버 LLM이 가명처리·검증 중입니다";
      $("progress-detail").textContent=job.state==="queued"?"한 번에 하나의 요청을 GPU로 처리합니다.":"진단, 날짜, 경과일과 문맥을 원문에 대조하고 있습니다.";
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }catch(error){message(error.message,true);$("progress").hidden=true;$("empty").hidden=false;status("output-status","처리 실패","error");}
  finally{clearInterval(timer);state.busy=false;actions();}
}
function reviewPayload(){return{analysis_id:state.current.analysis_id,review_digest:state.current.review_digest,confirmed:true};}
async function command(path,data){const response=await api(path,data);if(!response.job_id)return response;const deadline=Date.now()+120000;while(Date.now()<deadline){const job=await api(`/api/jobs/${response.job_id}`);if(job.state==="done")return job.result;if(job.state==="error")throw new Error(job.message);await new Promise(resolve=>setTimeout(resolve,1200));}throw new Error("서버 응답을 기다리고 있습니다. 잠시 후 다시 시도해 주세요.");}
async function approve(){if(state.busy||!$("review").checked)return;state.busy=true;actions();try{renderView(await command("/api/approve",reviewPayload()));message("시연 반출을 승인했습니다. 원하는 형식으로 다운로드할 수 있습니다.");}catch(e){message(e.message,true);}finally{state.busy=false;actions();}}
function decode(b64){return Uint8Array.from(atob(b64),c=>c.charCodeAt(0));}
function save(bytes,name,type){const url=URL.createObjectURL(new Blob([bytes],{type}));const a=node("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
async function download(){
  if(state.busy||!state.current)return;state.busy=true;actions();
  try{
    if(!state.artifact){state.artifact=await command("/api/export",reviewPayload());renderView(state.artifact.view);}
    const a=state.artifact,id=state.selected.id,format=$("format").value;
    if(format==="txt")save(decode(a.text_b64),`clinicaltext-${id}.txt`,"text/plain;charset=utf-8");
    else if(format==="xlsx")save(decode(a.xlsx_b64),`clinicaltext-${id}.xlsx`,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    else if(format==="json")save(decode(a.artifact_b64),a.filename,"application/json");
    else save(decode(a.verification_key_b64),"clinicaltext-verification.key","application/octet-stream");
    message("승인된 가명처리 기록을 다운로드했습니다. 다른 형식도 선택해 저장할 수 있습니다.");
  }catch(e){message(e.message,true);}finally{state.busy=false;actions();}
}
async function copyText(text,success){try{await navigator.clipboard.writeText(text);message(success);}catch{message("복사할 수 없습니다. 기록을 선택해 직접 복사하거나 파일로 다운로드해 주세요.",true);}}
document.querySelectorAll(".close-dialog").forEach(b=>b.onclick=()=>b.closest("dialog").close());
document.querySelectorAll("dialog").forEach(d=>d.addEventListener("click",e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
$("process-open").onclick=()=>$("purpose-dialog").showModal();$("purpose-cancel").onclick=()=>$("purpose-dialog").close();
$("purpose-preset").onchange=choosePurpose;$("purpose").oninput=actions;$("run").onclick=processRecord;$("review").onchange=actions;$("approve").onclick=approve;$("download").onclick=download;
$("copy").onclick=()=>copyText(state.current.output,"승인된 가명처리 기록을 복사했습니다.");
$("date-from").onchange=renderList;$("date-to").onchange=renderList;
$("record-search-run").onclick=renderList;$("record-search").oninput=renderList;
function newActions(){$("new-add").disabled=!$("new-title").value.trim()||!$("new-source").value.trim()||!$("new-synthetic").checked;}
$("new-record").onclick=()=>$("new-dialog").showModal();$("new-cancel").onclick=()=>$("new-dialog").close();
for(const id of ["new-title","new-source","new-synthetic"])$(id).oninput=newActions;
$("new-file").onchange=async()=>{const file=$("new-file").files[0];if(!file)return;try{if(!file.name.toLowerCase().endsWith('.txt')||file.size>16000)throw new Error("16KB 이하의 UTF-8 텍스트(.txt) 파일을 선택해 주세요.");const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(text.length>(state.limits?.live_max_chars||4000))throw new Error(`실시간 체험 기록은 ${state.limits?.live_max_chars||4000}자 이내로 입력해 주세요.`);$("new-source").value=text;$("new-title").value=file.name.replace(/\.txt$/i,'').slice(0,60);$("file-message").textContent=file.name;newActions();}catch(e){$("file-message").textContent=e.message;}};
$("new-add").onclick=()=>{if($("new-add").disabled)return;const record={id:'custom-'+crypto.randomUUID(),category:$("new-title").value.trim(),department:'직접 입력',title:'새 합성 기록',date:new Date().toLocaleDateString('sv-SE'),text:$("new-source").value,purpose:'임상 경과 연구',custom:true};state.records.unshift(record);$("date-from").value='';$("date-to").value='';$("record-search").value='';$("new-dialog").close();selectRecord(record);$("new-title").value='새 외래기록';$("new-source").value='';$("new-synthetic").checked=false;$("new-file").value='';$("file-message").textContent='';newActions();};
$("share").onclick=()=>$("share-dialog").showModal();
$("copy-url").onclick=()=>copyText(cfg.publicUrl||location.origin,"체험 주소를 복사했습니다.");
async function init(){
  if(cfg.githubUrl)$("github").href=cfg.githubUrl;
  $("share-url").textContent=cfg.publicUrl||location.origin;$("share-url").href=cfg.publicUrl||location.origin;
  try{const result=await api("/api/bootstrap");state.token=result.token;state.limits=result.event_limits||null;$("new-source").maxLength=state.limits?.live_max_chars||4000;state.records=result.records;state.purposes=result.purposes;state.ready=true;$("purpose-preset").replaceChildren(...result.purposes.map(p=>{const option=node("option",p.label);option.value=p.id;return option;}));const online=result.server_online!==false;$("connection").className="connection "+(online?"online":"offline");$("connection").replaceChildren(node("i"),document.createTextNode(online?"서버 연결됨":"서버 연결 대기"));selectRecord(state.records[0]);if(!online)message("GPU 서버에 연결 중입니다. 잠시 후 새로고침해 주세요.");}
  catch(e){$("connection").className="connection offline";$("connection").replaceChildren(node("i"),document.createTextNode("서버 연결 확인 필요"));$("records").replaceChildren(node("p","기록을 불러오지 못했습니다. 새로고침해 다시 연결해 주세요.","loading-text"));message(e.message,true);}
}
init();
