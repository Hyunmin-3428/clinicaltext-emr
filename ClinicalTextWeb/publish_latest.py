"""Render real saved synthetic results, retaining incomplete and failed checks."""
import html
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).parent
SOURCE=ROOT/".runtime/server-final-results"
DEST=ROOT/"docs/evaluation-2026-10-28-final"


def esc(value):return html.escape(str(value),quote=True)
def verdict(value):return '<b class="ok">통과</b>' if value is True else '<b class="fail">미충족</b>' if value is False else '<b class="unknown">미완료</b>'


def publish():
    DEST.mkdir(parents=True,exist_ok=True)
    for p in SOURCE.iterdir():
        if p.is_file():shutil.copyfile(p,DEST/p.name)
    stage=json.loads((DEST/"stage-audit.json").read_text(encoding="utf8"))
    comparison=json.loads((DEST/"comparison.json").read_text(encoding="utf8"))
    status=json.loads((DEST/"status.json").read_text(encoding="utf8"))
    names={"surrogate":"모식도 · 날짜와 과거력 보존","surrogate_exact_age":"같은 기록 · 환자 정확한 나이 보존", "bilingual":"한영 혼용", "surgery":"수술", "neonatal":"신생아 진단명", "direct_fields":"식별정보·임상 수치"}
    methods={"ner_tokens":"ko-pii·Presidio 규칙 치환", "hybrid_spans":"규칙·LLM 후보 병합 (KatherLab 구조 참고)",
             "two_pass":"local-llm-deid 원본 프로토콜·모듈", "utility_feedback":"활용성·개인정보 피드백 (RUPTA 구조 참고)", "purpose_guarded":"현재 목적별 보존·결합 검증"}
    faults={"negation":"발열 부정 반전","interval":"3일→5일","experiencer":"부친→환자","unsupported_recency":"근거 없는 최근 추가",
            "date_removed":"수술일을 토큰으로 치환","date_shifted":"수술 날짜 변경","history_year_shifted":"과거력 연도 변경","identity_leak":"이름 재삽입"}
    s=['''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClinicalText · 최종 합성 시험 결과</title><link rel="stylesheet" href="report.css"></head><body><main>
<p class="eyebrow">ClinicalText · 서버 GPU 실제 실행 · 2026-10-28</p><h1>임상 날짜와 의미를 보존하는 가명처리</h1>
<p>새 학습 없이 qwen3:30b를 서버 RTX 3090에서 실행했다. 아래는 직접 작성한 합성 기록의 실제 처리·검증·승인·서명 반출 결과다.</p>
<aside>실제 환자자료·병원 CDS·임상 성능 평가 결과가 아니다. 개발 중 예제를 보며 보완한 탐색 시험이다. 외부 데이터베이스 연계 재식별 위험이나 익명성을 측정·보증하지 않는다.</aside>''']
    s.append(f'<p>정책: <code>{esc(stage.get("policy_version"))}</code> · GPU 전체 적재: {verdict(stage.get("fully_on_gpu"))} · 서버 작업: <strong>{esc(status["stage"])}</strong></p>')
    browser=DEST/'browser-verification.json'
    if browser.exists():
        b=json.loads(browser.read_text(encoding='utf8'))
        s.append(f'<p>브라우저에서도 실제 처리 → 검토 승인 → 다운로드 → 공개키 서명 확인을 완료했다. 추가 실행은 재처리 1회를 포함해 {b["ui_seconds"]}초, {b["llm_calls"]}회 호출이었다. 아래 서버 단일 실행값보다 시간이 늘어날 수 있다.</p>')
    s.append('<h2>단계별 실제 시험</h2><div class="scroll"><table><tr><th>사례</th><th>검증·승인·서명 반출</th><th>처리 시간</th></tr>')
    for name,case in stage["cases"].items():
        s.append(f'<tr><td>{esc(names[name])}</td><td>{verdict(case.get("passed"))}</td><td>{case.get("seconds",0):.2f}초</td></tr>')
    s.append('</table></div><p>승인 전 반출·검토 해시 변조·중복 반출 거부, 반출 파일 변조 검출, 내부 원문 조회의 일치와 권한 없는 조회 거부를 함께 시험했다. 시간은 이번 짧은 기록의 단일 실행값이다. 아래 반출 파일은 보관용 시험 산출물이며 수신 유효기간이 지나면 정상 수신 검사에서 거부된다. 서명·원문 일치 검사는 유효기간 내 시험 시각과 만료 후 거부를 구분해 확인했다.</p>')
    s.append('<h2>의도적으로 손상한 결과</h2><div class="scroll"><table><tr><th>변경</th><th>차단</th><th>별도 LLM 지적</th><th>코드 검출</th></tr>')
    for key,r in stage["faults"].items():
        llm='응답 미완료' if not r.get('model_completed',True) else ('지적함' if r.get('llm_flagged') else '놓침')
        s.append(f'<tr><td>{esc(faults[key])}</td><td>{verdict(r.get("passed"))}</td><td>{llm}</td><td>{"검출" if r.get("independent_code_flagged") else "미검출"}</td></tr>')
    s.append('</table></div><p>LLM이 놓친 오류와 코드가 검출한 오류를 구분했다. 모델 호출 오류는 의미를 올바르게 판정한 것으로 합산하지 않는다.</p>')
    s.append('<h2>실제 원문과 변환문</h2>')
    for key,r in stage['cases'].items():
        view=r.get('view',{})
        s.append(f'<section><h3>{esc(names[key])}</h3><p><strong>연구 목적</strong> {esc(r["purpose"])}</p><div class="pair"><div><h4>합성 원문</h4><pre>{esc(r["source"])}</pre></div><div><h4>실제 변환문</h4><pre>{esc(view.get("output","결과 없음"))}</pre></div></div>')
        s.append('<details><summary>임상정보 보존 근거</summary><table><tr><th>항목</th><th>원문 근거</th><th>변환문 근거</th><th>판정</th></tr>')
        for row in view.get('validation',{}).get('rows',[]):
            s.append(f'<tr><td>{esc(row["label"])}</td><td>{esc(row["source"])}</td><td>{esc(row["output"])}</td><td>{esc(row["status"])}</td></tr>')
        s.append('</table></details>')
        if view.get('reasons'):s.append('<p class="fail">'+esc(' · '.join(view['reasons']))+'</p>')
        if (DEST/f'{key}.public.json').is_file():
            s.append(f'<p><a href="{key}.public.json">서명 반출 JSON</a> · <a href="{key}.verification.key">서명 검증용 공개키</a></p>')
        s.append('</section>')
    s.append('<h2>5개 방식 비교</h2><p>동일 모델과 합성 기록 3개를 사용했다. local-llm-deid는 MIT 원본 프로토콜·모듈을 실제 호출했고, KatherLab·RUPTA는 아이디어를 적용한 별도 구현이다. 원본 프로젝트 성능 재현이나 공식 순위가 아니다. 원본의 개인정보 정책과 이번 연구용 보존 조건이 서로 다르다.</p><div class="scroll"><table><tr><th>사례</th><th>방식</th><th>사례별 개인정보 조건</th><th>필수 임상정보</th></tr>')
    for key,case in comparison['cases'].items():
        for method,r in case['results'].items():
            score=r.get('evaluation',{})
            s.append(f'<tr><td>{esc(names.get(key,key))}</td><td>{esc(methods[method])}</td><td>{verdict(score.get("privacy_contract_met"))}</td><td>{verdict(score.get("clinical_relations_passed"))}</td></tr>')
    s.append('</table></div><p>개인정보 조건은 사전에 정한 합성 식별 단서의 잔존 여부다. 임상 조건은 공통 관계 검증과 날짜·연도·핵심 수치 확인이다. LLM 판정 자체도 오류가 있으며 소규모 합성 결과를 일반 정확도로 확대할 수 없다.</p>')
    s.append('<h2>원본 실행 근거</h2><ul>')
    for filename,title in [('stage-audit.json','전체 단계·모델 응답·오류 시험'),('comparison.json','비교 출력과 평가 응답'),('functional-tests.log','서버 기능시험 로그'),('status.json','서버 작업 상태'),('job-source.json','실행 소스 묶음 해시와 작업 정보'),('prior-v2-stage-failed.json','이전 날짜 정책의 실패 기록'),('prior-v3-stage-failed.json','이전 개인정보 분류의 보류 기록'),('privacy-role-checks.json','개인정보 분류 별도 실제 모델 확인'),('local-signature-checks.json','보관 서명과 만료 거부 확인')]:
        s.append(f'<li><a href="{filename}">{title}</a></li>')
    s.append('</ul><p>이름·기관·행사는 처리하되 수술 날짜, 상대 경과일, 증상 과거력 연도를 보존한다. 생년월일·행정 날짜는 구분한다. 날짜 역할이나 검증 근거가 불명확하면 보류한다. 실제 기관 인증과 반출 정책 연결은 별도 개발·승인이 필요하다.</p></main></body></html>')
    (DEST/'report.html').write_text('\n'.join(s),encoding='utf8')
    (DEST/'report.css').write_text('''body{margin:0;background:#f3f6fa;color:#24364e;font:16px/1.7 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:32px 24px}h1{font-size:32px;line-height:1.3}h2{margin-top:32px}h4{margin:8px 0}.eyebrow{color:#337a77}aside{padding:16px 20px;background:#e8eef7;border-left:4px solid #496b9d}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}section{padding:22px;background:white;border:1px solid #dbe3ef;border-radius:12px;margin:20px 0}pre{font:15px/1.8 system-ui,sans-serif;white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f6f8;padding:16px;border-radius:7px}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{border:1px solid #d8e2ed;text-align:left;padding:10px;vertical-align:top}th{background:#e6edf5}.ok{color:#147253}.fail{color:#ac3636}.unknown{color:#946720}.scroll{overflow:auto}summary{cursor:pointer;color:#275e87}a{color:#225f96}code{overflow-wrap:anywhere}@media(max-width:760px){.pair{grid-template-columns:1fr}}''',encoding='utf8')
    print(str(DEST/'report.html'))


if __name__=='__main__':publish()
