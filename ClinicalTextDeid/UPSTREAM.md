# 공개 구현·모델 통합 기록

확인 날짜: 2026-10-08 (Asia/Seoul). 실제 환자자료는 내려받거나 입력하지 않았다. 합성 문장만 로컬에서 처리했다. 탐지 결과는 안전성 인증 또는 반출 승인으로 해석하지 않는다.

## 1. ko-pii: 실제 보존·호출한 공개 코드

- 공식 저장소: [Marker-Inc-Korea/ko-pii](https://github.com/Marker-Inc-Korea/ko-pii).
- GitHub commits/main API로 확인한 최신 main: `9516cabd6f582935cae1536ccbd8c5448afae679`, 커미터 시각 `2026-09-09T05:50:11Z`, 버전 `1.16.0`.
- 보존 범위: `vendor/ko-pii/src/ko_pii/` 원본 전체와 `vendor/ko-pii/LICENSE`. 136개 파일, 803,073 bytes. 소스에 포함된 `dictionaries/legal_dongs.txt.gz`는 런타임 사전으로 함께 보존했다. 저장소의 별도 data 폴더, 환자자료, 학습자료, 가중치, 테스트셋은 받지 않았다.
- `vendor/ko-pii/MANIFEST.json`: 모든 파일의 pinned raw URL, SHA256, Git blob SHA1, 길이, 커밋 및 조회 시각. 다운로드 직후 모든 Git blob 해시를 GitHub tree API 값과 대조했고, 호출 후 136개 SHA256을 재검증했다. vendored 원본을 수정하지 않았다.
- 라이선스: [고정 커밋 MIT LICENSE](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/LICENSE), `Copyright (c) 2026 Marker Inc.`. 사용·수정·배포가 가능하나 저작권 및 허락 고지를 사본/중요 부분에 유지해야 하며 무보증이다. 동봉 LICENSE 유지가 이 귀속 조건을 충족하도록 설계했다.
- [고정 pyproject.toml](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/pyproject.toml): Python >=3.10, core dependencies 없음. 모델 연동 코드는 optional이며 이 시제품의 ko-pii core 호출은 외부 API/LLM을 사용하지 않는다.

### 탐지 호출과 자료 분리

```python
import sys
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "vendor" / "ko-pii" / "src"))
from ko_pii import detect_all, Anonymizer, ProcessingMode

clinical_exclude = {"AGE", "HEIGHT", "WEIGHT", "EDI_DRUG"}
detections = detect_all(
    text,
    exclude=clinical_exclude,
    person_exclusions={"메트포르민", "이부프로펜"},
)
```

`DetectionResult`의 실제 필드는 `label, text, start, end, risk_level, confidence, evidence, legal_basis, extra`이다. start/end는 원문 Python 문자 인덱스의 반개구간이고 UTF-8 byte offset이 아니다. 기본 Unicode 정규화 후 원문 구간으로 역매핑한다. `text`, `evidence`, `extra`에 원문/개인정보가 들어갈 수 있으므로 공개 JSON이나 로그로 그대로 직렬화하지 않는다. 공개 감사값은 유형·원문 없는 offset·카운트·검증 상태로 제한한다.

AGE/HEIGHT/WEIGHT는 임상 분석값이며 EDI_DRUG는 약품코드다. 해당 분석에서는 이들을 ko-pii 탐지 대상으로 제외하고 필요 시 별도 정책으로 처리한다. 처방번호 `PRESCRIPTION_ID`는 약물명/용량과 다르며 식별자 대상으로 유지한다. 정확한 나이/운동 시점이 필요한 분석은 앱의 문맥 일반화도 끈다. `POSITION`/`EDUCATION`은 직책/학교 준식별자이며 일반화·치환 여부는 정책으로 결정한다. BMI/DISEASE/MEDICATION/FAMILY_HISTORY 등의 ko-pii 라벨은 존재하지 않는다.

`Anonymizer(mode=ProcessingMode.STRICT, strategy="tokenize", exclude=clinical_exclude).process(text)`도 실제로 호출했다. 반환값은 `.text`, `.detections`(DetectionRecord), `.vault`, `.summary`, `.combined_risk`이다. `.detections`의 각 항목에는 `.detection`, `.action`, `.token`이 있다. STRICT는 risk>=MEDIUM, confidence>=0.7만 BLOCK 치환하고 REVIEW는 원문을 남긴다. PARANOID도 정해진 confidence/risk 임계 아래는 치환하지 않으며 미탐을 보장하지 않는다. 앱에서는 탐지 결과와 전체 치환 정책을 분리하고 잔여 위험을 별도 보고한다.

`tokenize` Vault는 이름·전화 등 치환 구간의 원문 매핑만 보관하며 전체 문맥 일반화의 손실 정보는 저장하지 않는다. `vault.reveal(token)`은 원문 값을 반환한다. `vault.dumps()/save()`는 평문 개인정보이므로 공개/로그 경로에 쓰지 않는다. upstream `save_encrypted`는 AES-256-GCM 및 PBKDF2-HMAC-SHA256(480,000 iterations)을 지원하지만 그것만으로 인증·인가·키관리가 구현되지는 않는다. 앱의 원문 전체 암호화 복원 캡슐은 별도 보호 경계로 구현한다.

### 합성 smoke 실행 관찰

이는 4개 문장의 기능 관찰이며 성능 측정/임상 검증을 대체하지 않는다.

| 합성 경우 | 실제 관찰 |
|---|---|
| `환자 홍길동, John Smith, 연락처 010-1234-5678, smith@example.com; metformin 500 mg bid.` | 홍길동·전화·email 검출 및 STRICT 치환. `John Smith`는 미검출. 용량·투약 빈도 유지. |
| NFD 한글 `환자 홍길동 연락처 010-1234-5678` | 이름·전화 검출, 모든 `source[start:end] == detection.text` 확인. 이름 구간이 앞 공백까지 포함되는 경계 오류 관찰. 앱 평가에서 문자열 경계를 별도 확인해야 함. |
| 전각 전화·zero-width 삽입 email | `０１０-１２３４-５６７８`, `smith\u200b@example.com` 전체 검출. 반환 text는 변형된 원문 그대로. |
| `47세 남성, 아버지가 52세에 MI로 사망. 체중 70kg, 키 175cm. 이부프로펜 400mg 하루 3회. 약품코드 640001300` + 위 제외 설정 | 이 문장에서는 탐지 없음, 원문 그대로 유지. 이는 일반 임상 의미 보존 보장이 아님. |

기존 일반화 함수의 실제 실행: `generalize_age(47) == "40대"`, `generalize_occupation("교수") == "연구/학계"`, `generalize_occupation("professor") == "professor"`. 기본 Anonymizer에는 영어 직업·운동 사건·가족력 문장 전체 일반화가 연결되어 있지 않다. 앱의 문맥 일반화는 제한된 규칙이며 임의 자유서술 이해/LLM/의학 추론이라고 주장하지 않는다. 이름에서 성별을 추론하지 않는다.

## 2. Presidio + spaCy 영어 NLP: 설치 버전과 조건

- [Presidio upstream](https://github.com/data-privacy-stack/presidio) (`microsoft/presidio`가 현재 해당 저장소로 redirect). `presidio-analyzer==2.2.360` 배포가 PyPI에 존재함을 조회했다. 현재 최신과 별개로 재현성을 위해 이 버전을 선택했다. 대응 tag `2.2.360` commit: `af1c524460ad62e17313520a3cbb618b062b75cb`.
- [고정 Presidio MIT](https://github.com/data-privacy-stack/presidio/blob/af1c524460ad62e17313520a3cbb618b062b75cb/LICENSE): 이 배포 커밋의 실제 저작권자는 `Microsoft Corporation. All rights reserved.`이다. 현재 main LICENSE의 Presidio Contributors 표기와 구분한다. 고정 URL 및 `vendor/licenses/Presidio-MIT.txt` 원문을 재대조했으며, 동봉 파일은 처음부터 해당 배포본의 Microsoft 고지를 정확히 보존했다.
- Presidio wheel 공식 배포 URL: `https://files.pythonhosted.org/packages/04/dd/df0d00ff44977868878f391b08feb65b8269904262d0f1387de827c13c7d/presidio_analyzer-2.2.360-py3-none-any.whl`, SHA256 `aa6e83779b8f23587000ccfa3ef47aa34356a60b51284742e0d2a96b33205c4e` ([PyPI 고정 메타데이터](https://pypi.org/pypi/presidio-analyzer/2.2.360/json)).
- `spacy==3.8.11`, MIT ([PyPI 고정 메타데이터](https://pypi.org/pypi/spacy/3.8.11/json)).
- 최신 Typer 의존성 구성에서 Click이 설치되지 않아 spaCy import가 실패함을 실제로 확인했다. `click==8.1.8`을 명시 설치하고 요구사항에 고정해 해결했으며 `pip check`에서 깨진 의존성이 없음을 확인했다.
- [공식 en_core_web_sm 3.8.0 release](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0), MIT, Explosion 귀속. 공식 wheel URL 및 SHA256은 `requirements.txt`에 고정했다. wheel SHA256은 `1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85`. spaCy >=3.8.0,<3.9.0 호환. 모델 크기 12,806,118 bytes.
- 영어 일반 NER 모델이며 한국어 또는 임상 전용 검증 모델이 아니다. OntoNotes 5/ClearNLP/WordNet을 출처로 고지하지만 이 시제품은 학습 원자료를 내려받지 않는다. 배포 모델은 MIT 표시이며 원자료에 모델 MIT 조건을 자동 적용하지 않는다.
- 실제 wheel의 `LICENSE`, `LICENSES_SOURCES`도 `vendor/licenses/`에 복사했다. 후자는 OntoNotes 5를 commercial(licensed by Explosion), ClearNLP를 참고 인용(모델에 code 미포함), WordNet 3.0을 별도 고지/면책/Princeton 명칭 광고 금지 조건으로 표시한다. 배포 시 이 원문 고지를 유지한다. spaCy/cryptography 원문 LICENSE와 cryptography의 Apache/BSD 고지도 함께 보존했다.
- `en_core_web_sm`에는 정적 word vector 테이블이 없으나 학습된 tok2vec 신경망 표현과 NER가 있다. hybrid를 'ML/임베딩 없이'라고 부르면 부정확하다. 문맥 일반화는 규칙, 영어 탐지는 로컬 학습 모델, LLM/외부 inference API 사용은 없음으로 구분한다.
- 암호화 라이브러리 `cryptography==50.0.1`은 부모 작업에서 설치되었고 요구사항에 고정했다. 배포 라이선스 표현은 `Apache-2.0 OR BSD-3-Clause`이며 설치된 원문 고지를 유지한다. 핵심 버전 외의 설치 환경은 `vendor/INSTALL_REPORT.json`과 `vendor/CLICK_INSTALL_REPORT.json`에 설치 시점 URL·해시·버전으로 기록한다. 요약 `vendor/RUNTIME_MANIFEST.json`, 라이선스 SHA256 `vendor/licenses/MANIFEST.json`, 실제 전체 의존성 버전 잠금 `requirements-lock.txt`도 함께 제공한다. lock은 Windows/Python 3.11에서 확인한 환경이며 다른 플랫폼의 binary wheel 검증을 대신하지 않는다.

영어 분석은 설치된 모델을 명시적으로 로드한다. 기본 AnalyzerEngine 생성만 호출하면 기본 모델 이름에 따른 자동 다운로드가 발생할 수 있다. 애플리케이션은 필수 패키지/모델이 없으면 hybrid 요청을 실패시키고 규칙 모드로 자동 fallback하지 않는다.

```python
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
provider = NlpEngineProvider(nlp_configuration={
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
})
engine = provider.create_engine()
analyzer = AnalyzerEngine(nlp_engine=engine, supported_languages=["en"])
results = analyzer.analyze(
    text=text,
    language="en",
    entities=["PERSON", "LOCATION", "PHONE_NUMBER"],
    score_threshold=0.35,
)
```

`RecognizerResult`는 `.entity_type`, `.start`, `.end`, `.score`를 제공한다. 반환 객체/설명 경로를 그대로 공개 로그에 출력하지 않는다. DATE_TIME을 전부 지우면 시점/기간 분석 정보가 손실되므로 위 구성에서 제외하고 생년월일과 임상 시점을 앱 정책으로 구분한다. Presidio 기본 spaCy recognizer는 ORG를 기본 대상에 포함하지 않으므로 기관명은 별도 ORG 처리 또는 명시 문맥 규칙이 필요하다. 일반 NER 탐지가 누락/오탐/범위 오류를 발생할 수 있으며 문서 반출 적합성을 의미하지 않는다.

Presidio의 EMAIL_ADDRESS 검사기는 `tldextract`를 통해 Public Suffix List를 최초 자동 다운로드할 수 있다. 소켓 접속을 차단한 합성 smoke에서 실제 이 경로의 실패를 확인했다. 이메일은 ko-pii 로컬 탐지를 사용하고 위 Presidio 호출은 EMAIL_ADDRESS/URL 대상에서 제외한다. 기본 suffix-list 조회에 환자문장을 전송하는 것은 아니지만, 완전 오프라인 계약에서는 이 자동 조회도 허용하지 않는다. 별도 이메일 recognizer가 필요하면 다운로드 없는 `TLDExtract(suffix_list_urls=(), cache_dir=None)`을 명시한 로컬 구현을 추가 검증해야 한다.

### 영어 모델의 실제 실패 관찰 및 앱 오프라인 확인

`vendor/ENGLISH_SMOKE.json`은 위 제한된 Presidio entity 목록으로 합성 9문장을 실행한 원시 관찰이다. 이름/장소 탐지 성능 평가셋이 아니며 정상 처리된 몇 개 이름을 임의 인명 보장으로 해석하지 않는다. 추론 중 socket 연결을 모두 차단했다.

| 경우 | 결과·한계 |
|---|---|
| 예시 `John Smith` 및 혼합문장 내 `(John Smith)` | PERSON 정확 구간 검출. |
| `Amina Yusuf`, 소문자 `john smith`, `J. Smith` | 이 합성 문맥에서 PERSON 검출. |
| `Xochitl Quispe` | PERSON 미탐. spaCy는 ORG로 분류. |
| `Hodgkin lymphoma` | `Hodgkin`을 PERSON으로 오탐해 무조건 치환하면 질환명 손상. |
| 한국어 `환자 김철수는 당뇨가 없고 ...` | 영어 모델이 첫 절 전체를 PERSON으로 오탐. 영어 recognizer 반환에 대한 언어·임상표현 보호가 필요. |
| `MI`, `BID` | spaCy raw ORG로 분류. 모든 ORG 치환은 가족력/투약빈도를 손상시킬 수 있으므로 기관/행사 보호 정책과 임상 검증을 분리해야 함. |
| `last Sunday`, aspirin 100 mg daily | 지정 entity 목록에서는 시점·용량을 치환하지 않음. |

이 표와 `vendor/ENGLISH_SMOKE.json`은 **추가 보호 전 공개 영어 모델/recognizer의 원시 출력**이다. 현재 앱은 영어 모델이 반환한 span에 한글·CJK·히라가나·가타카나 문자가 있으면 해당 영어 탐지 후보를 제거하고, 한국어 탐지 및 명시 anchor 규칙에 맡긴다. 이는 해당 영어 모델이 임의 한영혼용 구간을 안전하게 이해한다는 뜻이 아니며, mixed span에 포함된 실제 영어 이름도 이 필터에서 누락될 수 있다. 모델 원본은 변경하지 않았다.

현재 `clinical_guards.py`는 특정 인명 목록만으로 처리하지 않고 제한된 **일반 eponym 문맥**, **측정값**, **가족관계** 패턴을 보호한다. eponym 문맥은 대문자로 시작하는 1~3개 단어 뒤 `disease/syndrome/lymphoma/brace/thyroiditis`가 오는 형태다. 측정값은 `Cr/creatinine/Hb/hemoglobin/HbA1c/eGFR/Na/K/Cl/AST/ALT/WBC/platelet/BP/HR/SpO2`와 뒤 숫자·지원 단위 형식이며, kinship은 명시된 영어·한국어 가족관계 단어다. 이는 자유서술 의료 엔티티 인식이나 모든 약물명·질환명 사전이 아니다. 앱에 남아 있는 일부 개별 eponym 보완 외에도 이 일반 패턴이 detector 후보의 최종 보호 구간에 적용된다.

보호 규칙은 보호 구간 **전체 안에 포함된** PERSON/ADDRESS 후보만 억제하며, 더 넓은 구간이나 다른 PHI 유형까지 임의 삭제하지 않는다. 호출자가 제공한 PHI hint는 우선한다. 변환 후 API는 `clinical_lexical_guard_checks`에 `measurement`, `kinship`, `medical_eponym_context`별 원문 보호 표현의 잔존 여부를 검사하고, 손상 시 `protected_clinical_fact_changed`를 표시한다. 동일 문자열 잔존 확인은 주체·부정·관계의 전체 의미 검증을 대신하지 않는다. 가족력의 주체·부정·나이·사망/발병 경과 및 용량 등의 별도 합성 의무와 함께 평가하며 `all_free_text_semantics_validated`는 false로 유지한다. 보호된 질환 표현과 같은 형태에 실제 이름이 들어오는 경우도 별도 검토가 필요하므로, 보호 규칙을 적용한 앱 성능과 이 표의 raw 모델 실패를 구분한다.

앱의 실제 `ClinicalTextDeid.api.transform`, `restore`, `public_payload`, hybrid `detect`도 엔진 import/생성 전 socket 연결을 차단하고 3개 합성 입력×2개 mode로 실행했다. 6회 모두 외부 연결 없이 완료했고 UTF-8 byte-exact restore가 일치했으며 공개 payload 필드는 `schema_version,text,validation_status`뿐이었다. 내부 키는 메모리에만 두고 로그/파일에 출력하지 않았다. 원문·키 없이 결과 요약만 `vendor/HYBRID_OFFLINE_SMOKE.json`에 기록했다. 이 검사는 네트워크·복원·필드분리 관찰이고 임상 의미 보존이나 미탐 부재를 확인하는 검사는 아니다. 영어 raw smoke의 오탐/미탐은 앱 합성 평가 및 문서의 잔여 위험 보고에 계속 반영해야 한다. 설치 report는 긴 패키지 설명 문단만 제거한 compact provenance이며 버전·해시·다운로드 URL·라이선스 metadata·의존성·환경 필드를 보존했다. 설치 report/lock에는 원문·PHI·환자키·복원 캡슐을 저장하지 않았다.
