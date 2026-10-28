# 한국어·영어 혼용 임상 텍스트 비식별화 시제품

MIT 공개 ko-pii 실제 코드에 임상용 규칙 어댑터와 제한 문법의 문맥 일반화를 추가하고, 손실되는 정보를 포함한 UTF-8 원문을 별도 AES-256-GCM 캡슐에 보존하는 로컬 시제품. 합성자료만 사용. 임의 자유서술을 이해하는 LLM/임베딩 또는 반출 안전성 인증 시스템이 아님.

연동 함수와 반환값은 [INTERFACE.md](INTERFACE.md)에 먼저 확정. 기본 숫자 나이·운동시점·가족력 주체/부정/나이/치명적 경과·용량 보존. 성별은 입력에 명시된 경우만 사용.

다른 프로젝트 폴더는 변경하지 않습니다. 실행은 프로젝트 상위 디렉터리 `idea fair`에서 합니다. Windows Python 3.11로 검증했습니다.

## 실행

```powershell
python -m venv ClinicalTextDeid/.venv
ClinicalTextDeid/.venv/Scripts/python.exe -m pip install -r ClinicalTextDeid/requirements-lock.txt
ClinicalTextDeid/.venv/Scripts/python.exe -m ClinicalTextDeid.demo
ClinicalTextDeid/.venv/Scripts/python.exe -m unittest discover -s ClinicalTextDeid/tests -v
ClinicalTextDeid/.venv/Scripts/python.exe -m ClinicalTextDeid.evaluate
```

`requirements.txt`는 핵심 의존성/공식 모델 URL·SHA256, `requirements-lock.txt`는 실행한 전체 환경입니다. 최초 패키지·모델 설치에는 인터넷이 필요하고 추론·일반화·복원에는 외부 모델 API를 사용하지 않습니다. 기본 `hybrid`는 공개 Presidio 2.2.360 + spaCy 3.8.11 + 영어 모델 en_core_web_sm 3.8.0을 실제 호출합니다. 모델이 없으면 오류로 멈추며 규칙으로 조용히 대체하지 않습니다. `detector="rules"`는 ko-pii와 자체 규칙만, `detector="ko_pii"`는 임상 선택 라벨/제외어·Unicode 어댑터가 있는 ko-pii 경로입니다. 평가의 `upstream`은 어댑터 없는 원본 detect_all 호출입니다. 규칙 실행에는 `cryptography==50.0.1`만 별도 설치하면 됩니다.

**hybrid 탐지는 학습 모델/내부 tok2vec 표현을 사용합니다.** 문맥 일반화는 LLM·임베딩 모델을 사용하지 않는 규칙입니다. Presidio 이메일 recognizer의 자동 Public Suffix List 다운로드를 피하기 위해 해당 recognizer는 호출하지 않고 이메일은 ko-pii가 탐지합니다. 소켓 연결을 금지한 추론 실행을 검증했습니다.

## 예시와 보존 정책

합성 입력:
```
47-year-old John Smith, professor at Yonsei University, ran Seoul Marathon last Sunday, father died from MI at 52.
```

기본 direct 출력:
```
47-year-old <PERSON_1>, professor at <ORGANIZATION_1>, ran <EVENT_1> last Sunday, father died from MI at 52.
```

`policy={"mode":"context_rules"}` 출력:
```
47-year-old <PERSON_1>, works in an academic occupation, participated in endurance exercise last Sunday, positive family history of premature MI (father; died at 52).
```

정확 나이/운동시점 손실을 명시적으로 허용한 경우:
```
middle-aged <PERSON_1>, works in an academic occupation, recently participated in endurance exercise, positive family history of premature MI (father; died at 52).
```

마지막 출력은 `preserve_numeric_age=false, preserve_exercise_time=false`를 지정해야 합니다. 원래 숫자와 시점은 암호화 캡슐에 남습니다. `male`은 생성하지 않습니다. 명시된 male/female/남성/여성은 보존 검사 대상입니다. 가족력은 주체·부정·나이·사망 여부를 보존하고 사망 나이를 별도 발병 나이로 바꾸지 않습니다. premature는 부/형제 <55, 모/자매 <65라는 **시제품 규칙**이며 현행 임상지침 전체 판정이 아닙니다.

지원 문법은 [generalize.py](generalize.py)에 정의되어 있습니다. 영어 `professor at ... University`, `ran ... Marathon last Sunday/last Saturday/yesterday/last week`, 소문자 `father/mother/brother/sister died from/had/did not have MI at N` 및 제한된 한국어 대학·마라톤·가족력 표현을 지원합니다. 문장 앞뒤의 denies/not/possible/suspected/if/의심/부인/조건부 단서는 보수적인 **문장 범위 veto 규칙**으로 일반화를 보류하고 `requires_review`를 기록합니다. 이는 부정·시점·인과를 이해하는 문맥 모델이 아닙니다. 대문자 친족 표현, 복잡한 시점·발병과 사망의 서로 다른 원인, 간접 인용, 대명사, 문장 간 부정은 미지원일 수 있습니다.

## Python API

```python
import os
from ClinicalTextDeid.api import transform, restore, public_payload

context = {"project": "synthetic", "document": "doc-1", "release": "release-1", "purpose": "research"}
key = os.urandom(32)  # 내부 메모리 또는 별도 키 저장소; JSON/로그에 넣지 않음
source = "환자 김철수, John Smith; insulin 10 U SC."
result = transform({"text": source, "context": context}, key=key)
candidate = public_payload(result)  # downstream gateway가 별도로 검토·반출 판단
assert restore(result, key=key, context=context) == source.encode("utf-8")
```

원문 byte-stream 입력은 `text` 대신 `utf8_b64`를 사용합니다. UTF-8 유효성/200,000 bytes 상한을 검사하며 BOM/CRLF/NFD/emoji/trailing spaces를 복원합니다. `hints=[{"start":...,"end":...,"type":"PERSON"}]`는 내부 검토자가 확인한 원문 문자 구간을 추가합니다. 힌트로 성공한 결과는 자동 탐지 점수에 넣지 않습니다. 잘못된 힌트가 용량·나이·알려진 의료표현을 지우면 좁은 보존검사는 `failed`를 기록합니다.

## CLI와 파일 경계

서로 포함되지 않는 **sibling public/internal 디렉터리**를 사용합니다. CLI는 기존 파일 덮어쓰기를 거부하고, resolve한 경로가 양방향 포함관계거나 키가 내부 디렉터리 밖이면 실패합니다. 공개 폴더만 공유해야 하며 두 폴더의 공통 상위 디렉터리는 공유하지 않습니다. 더 넓은 공유 루트, Windows ACL, 사용자 권한·클라우드 동기화는 CLI가 추론하거나 통제하지 않습니다.

합성 입력과 무작위 키를 준비하는 예(출력 파일은 `.cache` 아래로 버전관리에서 제외):
```powershell
ClinicalTextDeid/.venv/Scripts/python.exe -c 'import json,os,pathlib; p=pathlib.Path("ClinicalTextDeid/.cache/demo/internal"); p.mkdir(parents=True,exist_ok=True); c={"project":"synthetic","document":"cli-1","release":"local-1","purpose":"research"}; (p/"key.bin").write_bytes(os.urandom(32)); (p/"context.json").write_text(json.dumps(c),encoding="utf-8"); (p/"request.json").write_text(json.dumps({"text":"Patient: John Smith; insulin 10 U SC.","context":c}),encoding="utf-8")'
ClinicalTextDeid/.venv/Scripts/python.exe -m ClinicalTextDeid.cli transform --input ClinicalTextDeid/.cache/demo/internal/request.json --key-file ClinicalTextDeid/.cache/demo/internal/key.bin --public-out ClinicalTextDeid/.cache/demo/public/note.json --internal-out ClinicalTextDeid/.cache/demo/internal/result.json
ClinicalTextDeid/.venv/Scripts/python.exe -m ClinicalTextDeid.cli restore --input ClinicalTextDeid/.cache/demo/internal/result.json --key-file ClinicalTextDeid/.cache/demo/internal/key.bin --context-file ClinicalTextDeid/.cache/demo/internal/context.json --output ClinicalTextDeid/.cache/demo/internal/restored.txt
```

CLI stdout/stderr는 일반 상태만 출력하고 원문/키/캡슐/임의 예외 내용을 출력하지 않습니다. 파일 경로명과 context에는 이름·주민번호 등 개인정보를 넣지 않아야 합니다. 내부 키파일·원문 입력·복원 결과·암호문은 모두 내부 민감 자료입니다. 이 예의 키파일은 데모 전달 방식이며 운영 키관리 서비스가 아닙니다.

## 복원·권한과 공개 payload

전체 원문 바이트를 AES-256-GCM(32-byte caller key, 새 12-byte nonce)으로 암호화합니다. 공개 문장뿐 아니라 schema/transformation_id/policy/audit/assessment와 caller context 및 format을 AAD로 결합합니다. 매 변환의 무작위 transformation_id는 같은 context와 동일한 공개 모양인 다른 문서의 캡슐 교체도 거부하도록 결합합니다. 단일 AESGCM ciphertext에는 인증 tag가 포함되어 있습니다. 평문 매핑이나 원문 hash를 감사 로그에 남기지 않습니다.

`public_payload()`는 schema_version/text/validation_status 3필드 allowlist만 반환합니다. audit/assessment/policy/transformation_id/internal capsule/key/context는 공개 payload에 포함하지 않습니다. `public.text`도 미탐이나 희귀 조합에 의한 잔여 PHI가 있을 수 있습니다. 평가/상태 필드의 이름은 인증·법적 비식별 판정·반출 허가가 아닙니다.

라이브러리 `restore()`는 키·context를 가진 호출자에게 원문 bytes를 반환하는 기능입니다. 인증·인가, 복원 역할 분리, 키 서비스, 접근감사, 보관·삭제, 실제 환자정보 이용·재식별 행위의 적법한 권한을 구현하거나 증명하지 않습니다. 이 작업에서 복원은 합성자료에만 실행했고 운영서비스의 권한/승인은 후속 경계에서 따로 구현해야 합니다.

## 한계와 평가

한국어는 룰·사전·체크섬, 영어는 일반 영어 NER와 공개 구조적 recognizer, 문맥 일반화는 한정 문법입니다. 임의 이름·서술형 주소·희귀 활동·기관·직업·날짜 조합의 완전 탐지는 지원하지 않습니다. 영어 모델이 한글 문장 전체를 PERSON으로 오탐하므로 CJK가 포함된 영어 모델 구간을 제외합니다. 일부 한국어 임상 제외어, measurement/kinship/eponym-context 보호는 한정 규칙이며 범용 의료 NER가 아닙니다. 같은 단어가 이름으로 쓰이는 경우 미탐 위험도 남습니다. 폰트 동형문자/OCR/띄어쓴 이름/다국어·별칭 동인성은 미지원일 수 있습니다. 토큰은 구간별 번호이며 일관된 환자 개체 ID나 별칭 연결을 보장하지 않습니다.

알려진 실제 실패에는 `Minjun Kim` 별칭, `Xochitl Quispe`, 한글 조사가 붙은 일부 영어 이름, 이름·주소의 비정형 표현이 있습니다. 실패를 합성 보고서에 남기며 `assessment.unsupported`와 잔여위험 `unquantified`, `all_free_text_semantics_validated=false`를 반환합니다. 숫자 나이/상대시간을 보존하므로 이들이 준식별자로 남는 위험도 보고합니다. 좁은 용량·성별·나이·운동시점·의료어휘 검사의 통과로 문장 전체 의미 동등성이나 잔여 PHI 부재를 판정하지 않습니다.

최종 수치는 [evaluation/report.md](evaluation/report.md), 원문·키·캡슐 없는 기계판독 결과는 [evaluation/metrics.json](evaluation/metrics.json), 방법/실패/한계는 [EVALUATION.md](EVALUATION.md)에 있습니다. 자체 수동 합성 개발셋으로 외부 임상자료의 성능이 아닙니다. 부모의 독립 통합 평가와 표본·분모·의무가 다르므로 지표를 섞지 않습니다.

최종 실행: **합성 56문장**. 힌트 없는 hybrid 직접식별자 문자 TP/FP/FN=391/6/106, Precision 98.5%, Recall 78.7%, F1 87.5%. 기관·행사 포함 all 문자 F1=89.0%. 정확 유형·구간 direct TP/FP/FN=32/4/9로 문자 점수와 구별합니다. rules와 hybrid는 각각 UTF-8 원문 복원112/112, 별도로 정한 유한 임상 의무134/134입니다. 자동검사45개 중44개 통과, Windows symlink 생성권한 부족으로1개 skip이며 성공에 합산하지 않습니다. 실패 사례는 보고서에 남아 있고 이 점수로 모든 PHI 탐지나 전체 임상 의미 보존을 주장하지 않습니다.

## 1차 출처·라이선스

[RESEARCH.md](RESEARCH.md), [SOURCES.json](SOURCES.json), [UPSTREAM.md](UPSTREAM.md)에 확인 날짜·고정 SHA·논문과 사용 조건을 기록했습니다. 공개 코드는 실제 통합했으며 ko-pii 원본은 vendor에 변경 없이 보존하고 런타임 SHA256을 확인합니다. ko-pii/Presidio/spaCy/영어 모델은 MIT, cryptography는 동봉 Apache/BSD 조건을 따르며 저작권·허가문을 [vendor/licenses](vendor/licenses)에 보존합니다. 일반 영어 모델의 학습 원자료 조건과 배포 모델 조건은 구분했습니다. 라이선스가 없는 SNUBH 코드를 이 패키지에 복사하지 않았고 원 논문 수치를 현재 합성 문자 F1과 비교하지 않습니다. 직접식별자 치환·가역 복원·문맥 일반화는 모두 선행기술이며 신규성·특허 가능성·선행기술 부재를 주장하지 않습니다.
