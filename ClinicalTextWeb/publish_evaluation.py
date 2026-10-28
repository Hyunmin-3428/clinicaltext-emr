"""Publish only authored synthetic evaluation artifacts, with failures retained."""
from __future__ import annotations

import html
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).parent
DEST = ROOT / "docs/evaluation-2026-10-28"
NAMES = {
    "ner_tokens": "ko-pii + Presidio + 규칙 치환",
    "hybrid_spans": "규칙·LLM 후보 병합 (KatherLab 구조 참고)",
    "two_pass": "local-llm-deid 원본 모듈·프로토콜 2단계",
    "utility_feedback": "활용성·개인정보 피드백 (RUPTA 구조 참고)",
    "purpose_guarded": "현재 목적별 편집·임상 검증·코드 검사",
}
CASES = {"surrogate": "모식도 사례", "neonatal": "신생아 진단 기록", "direct_fields": "이름·연락처·식별번호와 임상 수치"}


def esc(value):
    return html.escape(str(value), quote=True)


def badge(value):
    return '<b class="pass">통과</b>' if value is True else '<b class="fail">미충족</b>' if value is False else '<b class="unknown">평가 미완료</b>'


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for src, dest in [
        ("oss-comparison/report.json", "oss-comparison.json"),
        ("oss-comparison/report-initial.json", "oss-comparison-initial.json"),
        ("stage-audit-final/report.json", "stage-audit.json"),
        ("detection-audit.json", "detection-audit.json"),
        ("connectivity-status.json", "connectivity-status.json"),
    ]:
        shutil.copyfile(ROOT / ".runtime" / src, DEST / dest)
    report = json.loads((DEST / "oss-comparison.json").read_text(encoding="utf-8"))
    audit = json.loads((DEST / "stage-audit.json").read_text(encoding="utf-8"))
    connectivity = json.loads((DEST / "connectivity-status.json").read_text(encoding="utf-8"))
    parts = ["""<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>임상기록 가명처리 · 오픈소스 아이디어 비교</title>
<style>
body{font:16px/1.7 system-ui,sans-serif;color:#20324b;background:#f3f6fa;margin:0}
main{max-width:1120px;margin:auto;padding:38px 24px}h1{font-size:30px;line-height:1.3}
h2{margin-top:38px}h3{margin-top:28px}section,.notice{padding:22px;background:white;border:1px solid #d9e2ed;border-radius:12px;margin:18px 0}
.notice{border-left:5px solid #3c679d}table{border-collapse:collapse;width:100%;font-size:14px;background:white}
td,th{border:1px solid #d9e2ed;padding:10px;text-align:left;vertical-align:top}th{background:#eaf0f8}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:15px;border-radius:7px;font:14px/1.8 system-ui,sans-serif}
.pass{color:#117155}.fail{color:#b33e3e}.unknown{color:#876321}summary{cursor:pointer;font-weight:650}a{color:#275f9d}
.small{font-size:14px;color:#586a80}.scroll{overflow-x:auto}code{overflow-wrap:anywhere}
</style><main>
<p class="small">ClinicalText / 2026-10-28 / 작성한 합성 기록만 사용 / 새 학습 없음</p>
<h1>어떤 오픈소스 아이디어가<br>연구 목적별 의미 보존에 도움이 되는가?</h1>
<p>같은 서버 GPU 모델로 3개 합성 기록과 5개 처리 방식을 실행했다. 원문·출력과 검증 응답을 남겼으며 실패를 제외하지 않았다.</p>
<div class="notice"><strong>해석 범위</strong><br>이것은 탐색적 구현 비교다. KatherLab·RUPTA 원본 성능 재현이나 프로젝트 간 공식 순위가 아니다.
한국어 적용 코드, 요구 정책, 모델이 원본 연구와 다르다. 표의 개인정보 조건은 이 사례에서 미리 정한 식별정보·일반화 대상의 잔존 여부이며 익명성이나 법적 반출 적합성 판정이 아니다.</div>
<p>서버 Ollama <strong>qwen3:30b (30B-A3B)</strong>, temperature 0. 정상 단계 시험에서 RTX 3090 전체 GPU 적재를 확인했다.
핵심은 원문에서 확인한 구간만 편집하고, 연구 목적의 관계를 원문·결과 근거로 비교한 뒤 수치·부정·가족 주체를 별도 검사하는 것이다.</p>
<h2>실행한 방식</h2><ul>
<li><a href="https://github.com/JamesWeatherhead/local-llm-deid">local-llm-deid</a>: MIT 원본 추출 프로토콜과 분할·원문 근거 검사·좌표 복구·치환 모듈을 실제 사용했다. 웹 기본 경로가 아니라 비교 경로다.</li>
<li><a href="https://github.com/KatherLab/deidentifier">KatherLab/deidentifier</a>: 규칙과 LLM의 후보 구간을 합쳐 코드로 치환하는 아이디어를 한국어 어댑터로 시험했다. 원본 앱은 실행하지 않았다.</li>
<li><a href="https://github.com/UKPLab/acl2025-rupta">RUPTA</a>: 가명처리·개인정보 평가·활용성 평가의 분리와 피드백 반복을 임상관계 과제로 바꾸어 시험했다. 원본 학습·데이터·전체 실행기를 재현하지 않았다.</li>
<li>ko-pii·Presidio는 실제 호출했고 현재 목적별 처리에도 후보 탐지기로 사용한다.</li></ul>
<h2>공통 조건으로 평가한 결과</h2>
<p class="small">임상 조건은 생성 모델이 선택한 조건이 아니라 사례마다 미리 정한 공통 관계다. LLM 인용 근거와 별도 수치 확인을 함께 사용했다. 검증 모델도 같은 모델이므로 독립성을 보증하지 않는다.</p>
<div class="scroll"><table><tr><th>사례</th><th>처리 방식</th><th>사례별 개인정보 조건</th><th>필수 임상관계</th><th>생성+내부 검사</th></tr>"""]
    if not connectivity["connected"]:
        notice = '<p class="notice"><strong>현재 연결 상태 재확인: SSH 접속 시간 초과</strong><br>아래는 앞서 완료하여 저장한 실제 GPU 실행 결과다. 현재 새로운 GPU 실행은 서버 접속 복구가 필요하다. 로컬 CPU로 대체하지 않았다. 확인 시각: '+esc(connectivity["checked_at_kst"])+'.</p>'
        parts[0] = parts[0].replace('<h1>', notice + '<h1>', 1)
    parts[0] = parts[0].replace('<h1>', '<p class="notice"><strong>이 보고서는 임상 날짜 정책 수정 전 기록이다.</strong><br>사용자 피드백을 반영한 현재 코드는 실제 수술 날짜와 경과일을 함께 보존한다. 아래 날짜 치환 출력과 평가 점수는 이전 정책의 역사적 결과이며, 새 정책의 실제 모델 시험은 서버 접속 복구 후 별도로 수행해야 한다.</p><h1>', 1)
    for name, case in report["cases"].items():
        for method, result in case["results"].items():
            e = result.get("evaluation", {})
            timing = f'{result["method_seconds"]:.2f}초' if result.get("completed") else "평가 오류로 분리 불가"
            parts.append(f'<tr><td>{esc(CASES[name])}</td><td>{esc(NAMES[method])}</td><td>{badge(e.get("privacy_contract_met"))}</td><td>{badge(e.get("clinical_relations_passed"))}</td><td>{timing}</td></tr>')
    parts.append("""</table></div>
<p class="small">공통 추가 평가는 위 시간에 포함하지 않았다. 피드백 방식과 현재 방식은 내부 검사 호출이 포함된다. 단일 실행의 짧은 기록이므로 속도 벤치마크가 아니다. 모식도 2단계 방식은 후보 생성 후 공통 평가 응답이 토큰 한도로 끝나지 않아 미완료로 표시했다. 원본 후보는 아래와 JSON에 남아 있다.</p>
<h2>이번에 드러난 문제와 반영한 검사</h2>
<ul><li>기본 이름·연락처 치환은 모식도의 정확한 나이·직업·날짜·행사명을 모두 처리하지 못했다. 임상정보를 모두 지우는 대신 목적에 따른 일반화 조건이 필요했다.</li>
<li>원본 추출 프로토콜은 89세 초과 나이를 대상으로 하지만, 해당 모델의 한국어 출력은 부친의 52세도 AGE_OVER_89로 잘못 분류했다. 생성 모델의 분류 오류이며 원본 정책이 52세 삭제를 요구한 것이 아니다.</li>
<li>LLM 공통 평가도 그 나이 손실을 통과시킨 적이 있어 평가에 원문 수치 검사를 추가했다. 변경 전 JSON도 보존했다. 사전 등록한 통계 실험이 아니다.</li>
<li>활용성 피드백 방식에서는 개인정보 평가가 잔여 날짜 등을 놓쳐 추가 수정을 유도하지 못했다. 해당 어댑터·모델에서 관찰된 결과이며 RUPTA 전체의 성능 결론이 아니다.</li>
<li>현재 경로는 임상 보존 검사와 함께 원문 이름 잔존, 목적별 일반화, 원문 수치·부정·가족 주체를 코드로 확인한다. 오류를 해결하지 못하면 승인을 보류한다.</li></ul>
<h2>실제 시제품 단계 시험</h2><div class="scroll"><table><tr><th>사례</th><th>처리 시간</th><th>검증·승인·서명 반출·내부 원문 조회</th></tr>""")
    stage_names = {**CASES, "surrogate_exact_age": "같은 원문, 환자 정확한 나이 보존", "bilingual": "한영 혼용", "surgery": "수술"}
    for name, case in audit["cases"].items():
        parts.append(f'<tr><td>{esc(stage_names[name])}</td><td>{case["seconds"]:.2f}초</td><td>{badge(case["passed"])}</td></tr>')
    parts.append('</table></div><p>의도적 오류: ')
    labels = {"negation":"발열 부정 반전","interval":"경과일 변경","experiencer":"부친→환자","unsupported_recency":"근거 없는 최근 추가","identity_leak":"원문 이름 재삽입"}
    parts.append(" · ".join(esc(labels[n]) + (" 차단" if r["passed"] else " 차단 실패") for n,r in audit["faults"].items()))
    parts.append(". 이름 재삽입은 개인정보 LLM이 놓쳤고 원문 식별정보 잔존 검사가 차단했다. 이 시험은 실제 병원 반출이나 임상 안전성 검증을 대신하지 않는다.</p><h2>원문과 각 출력</h2>")
    for name, case in report["cases"].items():
        parts.append(f'<section><h3>{esc(CASES[name])}</h3><p><strong>연구 목적</strong>: {esc(case["purpose"])}</p><strong>원문</strong><pre>{esc(case["source"])}</pre>')
        for method, result in case["results"].items():
            e=result.get("evaluation",{})
            parts.append(f'<details><summary>{esc(NAMES[method])}</summary><pre>{esc(result.get("output","결과 없음"))}</pre>')
            if not result.get("completed"):
                parts.append(f'<p class="unknown">평가 미완료: {esc(result.get("error"))}. 생성된 문장의 완전성·개인정보 적합성을 통과로 합산하지 않았다.</p>')
            else:
                parts.append('<p>남은 직접 식별정보: '+esc(", ".join(e.get("direct_literals_left",[])) or "사전 지정 항목 없음")+'<br>남은 일반화 대상: '+esc(", ".join(e.get("context_literals_left",[])) or "사전 지정 항목 없음")+'</p>')
            parts.append('<details><summary>원본 실행·검증 응답</summary><pre>'+esc(json.dumps(result,ensure_ascii=False,indent=2))+'</pre></details></details>')
        parts.append('</section>')
    parts.append("""<h2>근거 파일과 한계</h2><ul>
<li><a href="oss-comparison.json">5방식 원본 JSON</a> · <a href="oss-comparison-initial.json">초기 평가 JSON</a></li>
<li><a href="stage-audit.json">실제 HTTP·GPU 단계 시험</a> · <a href="detection-audit.json">실제 탐지기 시험</a></li>
<li><a href="connectivity-status.json">마지막 서버 접속 재확인 상태</a></li>
<li>기능 시험과 적용 범위의 상세 설명은 프로젝트 docs/VALIDATION.md와 docs/OPEN_SOURCE_BASES.md에 남겼다.</li></ul>
<p>현재 정상 처리 5개는 모두 통과했으나 학습·확증용 표본 분리나 반복 실행, 전문가 정답 검수, 한국어 임상 코퍼스 평가를 수행하지 않았다.
예제를 보면서 수정한 개발 결과다. 긴 기록과 처음 보는 문맥의 성능은 별도 검증이 필요하다. 소규모 합성 결과로 범용 탐지율·임상 정확도·익명성을 주장할 수 없다.</p>
<p class="small">local-llm-deid 핵심 모듈 시험 4개 통과. 전체 CLI는 Windows의 os.fchmod 미지원·파일 잠금 오류로 실패했다. 이 웹은 원본 CLI를 실행하지 않는다.</p></main></html>""")
    document = "\n".join(parts)
    css = re.search(r"<style>(.*?)</style>", document, re.S).group(1)
    (DEST / "comparison.css").write_text(css, encoding="utf-8")
    document = re.sub(r"<style>.*?</style>", '<link rel="stylesheet" href="comparison.css">', document, count=1, flags=re.S)
    (DEST / "comparison.html").write_text(document, encoding="utf-8")
    print(str(DEST / "comparison.html"))


if __name__ == "__main__":
    main()
