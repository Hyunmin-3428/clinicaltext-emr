"use strict";
const $ = (id) => document.getElementById(id);
const state = { csrf: "", examples: [], current: null, busy: false, ready: false, artifact: null, llm: {configured:false} };
const statusLabels = { passed: "보존 확인", failed: "손상 의심", unknown: "확인 필요", not_present: "해당 없음" };

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function notify(message = "", error = false) {
  $("notice").textContent = message;
  $("notice").className = `notice${error ? " error" : ""}`;
  $("notice").hidden = !message;
}

async function api(path, data) {
  const response = await fetch(path, data === undefined ? { credentials: "same-origin" } : {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
    body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "요청을 완료하지 못했습니다. 다시 시도해 주세요.");
  return result;
}

function updateActions() {
  $("research-form").querySelectorAll("input, textarea, select, button").forEach((control) => { control.disabled = state.busy || !state.ready; });
  const current = state.current;
  $("review-confirm").disabled = state.busy || !current?.can_approve || current.state !== "REVIEW_READY";
  $("approve").disabled = state.busy || current?.state !== "REVIEW_READY" || !current?.can_approve || !$("review-confirm").checked;
  $("export").disabled = state.busy || current?.state !== "APPROVED";
  $("shutdown").disabled = state.busy || !state.ready;
  $("model-settings").disabled = state.busy || !state.ready;
  $("analyze").classList.toggle("is-loading", state.busy);
}

function setBusy(busy, label = "가명처리·검증") {
  state.busy = busy;
  $("analyze-label").textContent = busy ? label : "가명처리·검증";
  $("research-form").setAttribute("aria-busy", String(busy));
  updateActions();
}

function paintText(node, text, spans = [], className = "phi-mark") {
  node.replaceChildren();
  node.classList.remove("empty");
  if (!$("highlight").checked) { node.textContent = text; return; }
  let cursor = 0;
  for (const span of [...spans].sort((a, b) => a.start - b.start)) {
    if (span.start < cursor || span.end > text.length || span.end <= span.start) continue;
    node.append(document.createTextNode(text.slice(cursor, span.start)));
    node.append(element("mark", text.slice(span.start, span.end), className));
    cursor = span.end;
  }
  node.append(document.createTextNode(text.slice(cursor)));
}

function comparison() {
  const current = state.current;
  // Python reports Unicode code-point offsets, while browser strings use UTF-16.
  const points = current ? Array.from(current.source) : [];
  const sourceSpans = (current?.source_spans || []).map((span) => ({ start: points.slice(0, span.start).join("").length, end: points.slice(0, span.end).join("").length }));
  paintText($("source-preview"), current ? current.source : $("source").value, sourceSpans);
  if (current) {
    const outputPoints = Array.from(current.output);
    const spans = current.output_spans ? current.output_spans.map((span)=>({start:outputPoints.slice(0,span.start).join("").length,end:outputPoints.slice(0,span.end).join("").length})) : Array.from(current.output.matchAll(/<[A-Z_]+_\d+>/g), (match) => ({ start: match.index, end: match.index + match[0].length }));
    paintText($("output-preview"), current.output, spans, "token-mark");
  } else {
    $("output-preview").replaceChildren(element("div", "↔", "empty-icon"), element("strong", "변환 결과를 비교해 보세요"), element("p", "가명처리·검증을 실행하면 결과가 표시됩니다."));
    $("output-preview").classList.add("empty");
  }
  $("char-count").textContent = `${$("source").value.length.toLocaleString()}자`;
}

function badge(node, text, type = "neutral") {
  node.textContent = text;
  node.className = `badge ${type}`;
}

function render(current) {
  state.current = current;
  comparison();
  $("step-input").className = current ? "done" : "current";
  $("step-validation").className = current ? current.state === "EXPORTED" ? "done" : "current" : "";
  $("step-export").className = ["APPROVED", "EXPORTED"].includes(current?.state) ? "current" : "";
  $("validation-empty").hidden = Boolean(current);
  $("validation-table-wrap").hidden = !current;
  $("privacy-summary").hidden = !current;
  $("events").hidden = !current;
  $("hold-reasons").hidden = !current?.reasons?.length;
  $("download-again").hidden = !state.artifact;
  $("validation-rows").replaceChildren();
  $("hold-reasons").replaceChildren();
  $("events").replaceChildren();
  if (!current) {
    $("metric-phi").replaceChildren(document.createTextNode("—"), element("small", "개"));
    $("metric-preserved").replaceChildren(document.createTextNode("—"), element("small", "항목"));
    $("metric-state").textContent = "검사 전";
    $("metric-state").className = "metric-state";
    $("candidate-status").textContent = "처리 대기";
    $("release-message").className = "release-message";
    $("release-message").replaceChildren(element("strong", "검증 결과를 먼저 확인해 주세요."), element("p", "등록된 합성 예제의 검사 결과를 검토한 후 반출을 승인할 수 있습니다."));
    badge($("validation-badge"), "검사 대기");
    badge($("approval-badge"), "승인 전");
    updateActions(); return;
  }
  const v = current.validation;
  const total = v.rows.filter((row) => row.status !== "not_present").length;
  $("metric-phi").replaceChildren(document.createTextNode(String(current.phi_count)), element("small", "개"));
  $("metric-preserved").replaceChildren(document.createTextNode(`${v.passed}/${total}`), element("small", "항목"));
  const labels = { REVIEW_READY: "승인 대기", HELD: "반출 보류", APPROVED: "승인 완료", EXPORTED: "반출 완료", INVALIDATED: "재검사 필요" };
  $("metric-state").textContent = labels[current.state] || "확인 필요";
  $("metric-state").className = `metric-state ${current.state === "HELD" ? "held" : "ready"}`;
  $("candidate-status").textContent = current.llm ? `LLM · ${current.llm.calls}회 호출` : "규칙 처리 완료";
  $("validation-scope").textContent = current.validation.scope;
  badge($("validation-badge"), v.failed ? "손상 의심" : v.unknown || !v.passed ? "확인 필요" : "선택 표현 보존 확인", v.failed ? "failed" : v.unknown || !v.passed ? "unknown" : "passed");
  for (const row of v.rows) {
    const tr = document.createElement("tr");
    const item = element("td", row.label);
    if (row.kind.startsWith("llm") || row.status === "failed" || row.status === "unknown") item.append(element("small",row.detail,"row-detail"));
    tr.append(item);
    for (const value of [row.source, row.output]) {
      const cell = document.createElement("td");
      cell.append(value ? document.createTextNode(value) : element("span", "근거 없음", "no-evidence"));
      tr.append(cell);
    }
    const cell = document.createElement("td");
    const label = element("span", statusLabels[row.status], `badge ${row.status === "not_present" ? "neutral" : row.status}`);
    label.title = row.detail;
    cell.append(label); tr.append(cell); $("validation-rows").append(tr);
  }
  const residual = current.privacy.residual_count;
  $("privacy-summary").className = `privacy-summary${current.privacy.status === "passed" ? "" : " review"}`;
  $("privacy-summary").textContent = `잔여 식별정보 후보 ${residual}건 탐지 · ${current.registered_example ? "등록된 합성 예제" : "추가 검토가 필요한 기록"} · 재식별 위험의 완전 제거를 의미하지 않습니다.`;
  let heading, description;
  const message = $("release-message");
  if (current.state === "EXPORTED") {
    heading = "승인된 가명처리 파일을 반출했습니다.";
    description = "JSON 파일이 다운로드됩니다. 아래에서 같은 파일을 다시 저장할 수 있습니다.";
    badge($("approval-badge"), "반출 완료", "passed"); message.className = "release-message ready";
  } else if (current.state === "APPROVED") {
    heading = "검토 결과가 승인되었습니다.";
    description = "승인 후 2분 이내에 반출해 주세요. 반출 직전 정책과 내용을 다시 검사합니다.";
    badge($("approval-badge"), "승인 완료", "passed"); message.className = "release-message ready";
  } else if (current.can_approve) {
    heading = "시연 반출을 승인할 수 있습니다.";
    description = "가명처리문과 근거별 판정을 검토하고, 확인란을 선택한 뒤 승인해 주세요.";
    badge($("approval-badge"), "검토 대기", "unknown"); message.className = "release-message ready";
  } else {
    heading = "검토가 필요하여 반출을 보류합니다.";
    description = "표현 보존 검사를 통과하더라도 지원 범위 밖의 기록은 자동 반출하지 않습니다.";
    badge($("approval-badge"), "반출 보류", "unknown"); message.className = "release-message held";
  }
  message.replaceChildren(element("strong", heading), element("p", description));
  for (const reason of current.reasons) $("hold-reasons").append(element("li", reason));
  for (const event of current.events) {
    const line = element("div", undefined, "event");
    const stamp = new Date(event.time * 1000);
    const when = element("time", stamp.toLocaleTimeString("ko-KR", { timeZone: "Asia/Seoul", hour12: false }));
    when.dateTime = stamp.toISOString();
    line.append(element("span", event.event), when); $("events").append(line);
  }
  updateActions();
}

function dirty() {
  if (state.busy) return;
  const previous = state.current;
  state.current = null; state.artifact = null;
  $("review-confirm").checked = false;
  $("processing-time").textContent = "";
  if (previous) {
    api("/api/invalidate", { analysis_id: previous.analysis_id }).catch(() => {});
    notify("입력 조건이 변경되었습니다. 다시 가명처리·검증한 뒤 승인해 주세요.");
  }
  render(null);
}

function loadExample() {
  const selected = state.examples.find((item) => item.id === $("example-select").value);
  if (!selected) return;
  dirty();
  $("purpose").value = selected.purpose;
  $("source").value = selected.text;
  $("required-phrases").value = "";
  document.querySelectorAll("[name=preserve]").forEach((checkbox) => { checkbox.checked = selected.checks.includes(checkbox.value); });
  $("example-note").textContent = selected.note;
  comparison(); notify();
}

function modelStatus() {
  const usingLLM = $("engine").value === "llm";
  const config = state.llm;
  $("llm-status").textContent = usingLLM ? config.configured ? `처리: ${config.model} · 검증: ${config.verifier_model} (별도 호출)` : "LLM 미연결 — 연결 설정 후 실행할 수 있습니다." : "기존 탐지기·표현 규칙만 사용합니다. LLM은 호출하지 않습니다.";
  $("remote-consent-row").hidden = !(usingLLM && config.configured && config.remote);
  $("remote-consent-text").textContent = config.remote ? `이 기록이 합성 문서임을 확인하며, 연구 목적과 원문을 ${config.base_url}의 ${config.model}·${config.verifier_model}로 전송하는 데 동의합니다.` : "";
  $("processing-location").textContent = !usingLLM ? "이 컴퓨터의 기존 규칙으로 처리합니다." : !config.configured ? "LLM 연결이 필요합니다. 규칙 처리로 자동 대체하지 않습니다." : config.remote ? "선택한 외부 LLM API로 원문이 전송됩니다." : "선택한 모델 서버에서 처리합니다. SSH로 연결한 경우 추론은 서버에서 실행됩니다.";
}

$("engine").addEventListener("change",modelStatus);
$("model-settings").addEventListener("click",()=>$("model-dialog").showModal());
$("model-close").addEventListener("click",()=>$("model-dialog").close());
$("model-provider").addEventListener("change",()=>{
  const kind=$("model-provider").value;
  $("model-base-url").value=kind==="openai"?"https://api.openai.com/v1":kind==="ollama"?"http://127.0.0.1:11434":"http://127.0.0.1:1234/v1";
});
async function configureModel() {
  const settings={provider:$("model-provider").value,base_url:$("model-base-url").value,model:$("model-name").value,verifier_model:$("verifier-model-name").value,api_key:$("model-api-key").value};
  const configured=await api("/api/model/configure",settings);
  dirty(); state.llm=configured; $("remote-consent").checked=false; modelStatus();
  return configured;
}
$("model-form").addEventListener("submit",async(event)=>{
  event.preventDefault(); $("model-save").disabled=true;
  try {await configureModel(); $("model-api-key").value=""; $("model-dialog").close(); notify("LLM 연결 설정을 적용했습니다. 연결 테스트 또는 가명처리·검증을 실행해 주세요.");}
  catch(error){$("model-feedback").textContent=error.message;}
  finally{$("model-save").disabled=false;}
});
$("model-probe").addEventListener("click",async()=>{
  $("model-probe").disabled=true; $("model-feedback").textContent="합성 연결 확인 요청을 보내고 있습니다…";
  try {await configureModel(); await api("/api/model/probe",{}); $("model-api-key").value=""; $("model-feedback").textContent="모델 연결과 구조화 응답을 확인했습니다.";}
  catch(error){$("model-feedback").textContent=error.message;}
  finally{$("model-probe").disabled=false;}
});

function reviewRequest() {
  return { analysis_id: state.current.analysis_id, review_digest: state.current.review_digest, confirmed: $("review-confirm").checked };
}

function saveFile(base64, name, mime) {
  const raw = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([raw], { type: mime }));
  const link = element("a"); link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

$("research-form").addEventListener("input", (event) => {
  if (event.target.id === "example-select") return;
  dirty();
  if (!["engine","remote-consent"].includes(event.target.id)) $("example-note").textContent = "직접 입력한 기록입니다. 등록된 합성 예제 외의 반출은 보류합니다.";
  comparison();
});
$("load-example").addEventListener("click", loadExample);
$("highlight").addEventListener("change", comparison);
$("review-confirm").addEventListener("change", updateActions);
$("research-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (state.busy || !state.ready) return;
  if ($("engine").value === "llm" && !state.llm.configured) { notify("LLM 연결 설정이 필요합니다.",true); $("model-dialog").showModal(); return; }
  if ($("engine").value === "llm" && state.llm.remote && !$("remote-consent").checked) {notify("합성 기록과 모델 전송 목적지를 확인한 뒤 동의란을 선택해 주세요.",true);return;}
  const checks = Array.from(document.querySelectorAll("[name=preserve]:checked"), (checkbox) => checkbox.value);
  if (!checks.length) { notify("보존할 임상정보를 하나 이상 선택해 주세요.", true); return; }
  const phrases = $("required-phrases").value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (phrases.length > 8 || phrases.some((phrase) => phrase.length > 100)) { notify("필수 구절은 100자 이내로, 최대 8개까지 입력해 주세요.", true); return; }
  const previous = state.current;
  if (previous) await api("/api/invalidate", { analysis_id: previous.analysis_id }).catch(() => {});
  state.artifact = null; $("review-confirm").checked = false; render(null);
  const started = performance.now();
  const usingLLM=$("engine").value==="llm";
  setBusy(true, usingLLM?"LLM 처리·별도 검증 중…":"기록 처리·근거 확인 중…"); notify(usingLLM?"LLM이 목적별 조건과 수정 구간을 정하고, 별도 호출로 임상관계를 검증합니다. 최대 2회 시도합니다.":"기존 규칙으로 개인정보 탐지와 표현 보존 검사를 진행하고 있습니다.");
  const progressTimer = setInterval(() => { $("processing-time").textContent = `처리 중 · ${Math.floor((performance.now()-started)/1000)}초 경과`; }, 1000);
  try {
    const result = await api("/api/analyze", { purpose: $("purpose").value, text: $("source").value, checks, required_phrases: phrases,engine:$("engine").value,synthetic_consent:$("remote-consent").checked });
    render(result); $("processing-time").textContent = `${((performance.now() - started) / 1000).toFixed(1)}초`;
    notify(result.can_approve ? "가명처리·검증이 완료되었습니다. 결과를 검토한 후 승인해 주세요." : "가명처리·검증이 완료되었습니다. 반출 보류 사유를 확인해 주세요.");
  } catch (error) {
    $("processing-time").textContent = "처리 실패";
    $("candidate-status").textContent = "처리 실패";
    notify(error.message, true);
  }
  finally { clearInterval(progressTimer); setBusy(false); }
});
$("approve").addEventListener("click", async () => {
  if (!state.current || state.busy) return;
  setBusy(true, "승인 확인 중…");
  try { render(await api("/api/approve", reviewRequest())); notify("승인이 완료되었습니다. 승인 후 반출 버튼으로 파일을 받으세요."); }
  catch (error) { notify(error.message, true); }
  finally { setBusy(false); }
});
$("export").addEventListener("click", async () => {
  if (!state.current || state.busy) return;
  setBusy(true, "반출 조건 재검증 중…");
  try {
    state.artifact = await api("/api/export", reviewRequest());
    render(state.artifact.view);
    saveFile(state.artifact.artifact_b64, state.artifact.filename, "application/json");
    notify("승인된 가명처리 파일을 반출했습니다. 원문과 복원 정보는 반출 파일에 포함되지 않습니다.");
  } catch (error) {
    if (state.current) { state.current.can_approve = false; state.current.state = "HELD"; state.current.reasons = [error.message]; render(state.current); }
    notify(error.message, true);
  } finally { setBusy(false); }
});
$("save-again").addEventListener("click", () => { if (state.artifact) saveFile(state.artifact.artifact_b64, state.artifact.filename, "application/json"); });
$("save-public-key").addEventListener("click", () => { if (state.artifact) saveFile(state.artifact.verification_key_b64, "clinical-demo-verification.key", "application/octet-stream"); });
$("shutdown").addEventListener("click", async () => {
  $("shutdown-dialog").showModal();
});
$("cancel-shutdown").addEventListener("click", () => $("shutdown-dialog").close());
$("confirm-shutdown").addEventListener("click", async () => {
  $("shutdown-dialog").close();
  try {
    await api("/api/shutdown", {});
    state.ready = false; state.current = null; state.artifact = null;
    $("connection").classList.remove("online");
    $("connection").replaceChildren(document.createElement("i"), document.createTextNode("시연 종료됨"));
    render(null);
    notify("시연을 종료했습니다. 다시 사용하려면 start-demo.cmd를 실행해 주세요.");
  } catch (error) { notify(error.message, true); }
});

async function initialize() {
  updateActions();
  try {
    const bootstrap = await api("/api/bootstrap");
    state.csrf = bootstrap.csrf; state.examples = bootstrap.examples; state.llm=bootstrap.llm||{configured:false};
    for (const [value, label] of Object.entries(bootstrap.checks)) {
      const wrapper = element("label", undefined, "check-chip");
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.name = "preserve"; checkbox.value = value;
      wrapper.append(checkbox, document.createTextNode(label)); $("check-options").append(wrapper);
    }
    for (const example of state.examples) { const option = element("option", example.label); option.value = example.id; $("example-select").append(option); }
    state.ready = true; loadExample(); modelStatus();
    if(state.llm.configured){$("model-provider").value=state.llm.provider;$("model-base-url").value=state.llm.base_url;$("model-name").value=state.llm.model;$("verifier-model-name").value=state.llm.verifier_model;}
    $("connection").replaceChildren(document.createElement("i"), document.createTextNode("웹 연결됨"));
    $("connection").classList.add("online");
  } catch (error) { $("connection").textContent = "연결 확인 필요"; notify("프로그램에 연결할 수 없습니다. 시연 서버를 실행한 뒤 새로고침해 주세요.", true); }
  updateActions();
}
initialize();
