# 한영 혼용 임상 자유서술 비식별화: 1차 출처 조사

확인일: **2026-10-08, Asia/Seoul**. GitHub 기본 브랜치 HEAD는 API로 재확인했고 아래 SHA를 기록했다. HEAD 조사 버전과 실제 설치 배포 버전은 구분한다. **조사 담당자는** 문서·코드·모델 메타데이터만 읽었다. **전체 프로젝트의 통합 단계에서는** MIT 영어 모델 `en_core_web_sm==3.8.0`을 다운로드·설치해 오프라인 탐지에 연결했다. 환자 원문 데이터와 SNUBH/MIDRC/CliniDeID 가중치는 내려받지 않았다. 구현 성능은 별도 합성 평가 산출물을 따르며 이 조사로 성능이나 반출 안전성을 인증하지 않는다. 기계 판독 출처는 [SOURCES.json](SOURCES.json)이다.

## 결론과 통합 선택

**한국어 구조적 PII 탐지의 공개 코드 기반으로 ko-pii를 통합하고, 영어 탐지는 Presidio+spaCy NER를 실제 연결했다.** MIT 조건에 따라 원 저작권·허가문을 포함하고 버전을 고정한다. 기본 `hybrid` 탐지는 ko-pii 규칙, 임상 보완 규칙, 학습된 영어 NER를 결합한다. 영어와 임상 문맥의 빈틈은 별도 평가로 명시해야 한다. `rules` 구성은 모델 없이 실행하지만 `hybrid`에는 학습 모델과 `tok2vec` 신경망 표현이 포함되므로 전체 탐지를 “ML/임베딩 없이”라고 부르지 않는다. **문맥 일반화만은 LLM·임베딩을 사용하지 않는 제한된 정규식/사전 변환**이다. SNUBH 코드는 참고로 남기고 허가 없이 복사·재배포하지 않는다. MIDRC/CliniDeID는 비교 대상으로 두며 기본 의존성으로 채택하지 않는다.

직접식별자 치환, 토큰 매핑과 가역 복원, 나이·직업 등의 일반화, 간접식별자 대응은 모두 선행기술이 존재한다. **이 조사는 신규성, 특허 가능성, 선행기술 부재를 판정하지 않는다.** ko-pii에는 나이 구간화와 직업 범주화 코드가 이미 있고, Presidio에는 encryption/decryption operator가 있다. [ko-pii age 코드](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/src/ko_pii/generalization/age.py), [occupation 코드](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/src/ko_pii/generalization/occupation.py), [Presidio Encrypt 코드](https://github.com/data-privacy-stack/presidio/blob/d8847904621733f4eaad4f9bd977b96a11325c90/presidio-anonymizer/presidio_anonymizer/operators/encrypt.py).

| 후보 | 확인한 코드 조건 | 모델·데이터 조건 | 본 작업에서의 판단 |
|---|---|---|---|
| ko-pii | MIT, `9516cabd6f582935cae1536ccbd8c5448afae679` | 코어는 룰·사전·체크섬, 모델 불필요. optional ML/classifier는 별도 | 재배포 가능한 탐지 코어로 실제 통합하고 임상 한계 별도 측정 |
| Presidio + spaCy | MIT. 조사 HEAD `d8847904621733f4eaad4f9bd977b96a11325c90`; 실제 설치 analyzer `2.2.360` | 실제 `spacy==3.8.11`, MIT 영어 NER `en_core_web_sm==3.8.0`; 배포 모델 다운로드·오프라인 실행 | hybrid 탐지에 실제 ML 연결. 문맥 일반화는 규칙. 영어 일반 NER 점수를 임상 PHI 성능으로 적용 금지 |
| SNUBH_deid | LICENSE 및 명시적 코드 재사용 허가 미확인, `cc1e727750032cc2d4c69bc42acc857a30b17e99` | 논문 환자 자료는 IRB 정책상 비공개. 저장소 가중치의 별도 허가도 미확인 | 참고만 사용. 원 코드·정규식·가중치 재배포 대상에서 제외 |
| Stanford/Penn MIDRC | Apache-2.0, `897fc0c0b9fcc3c0345228124a571a862cefa1d5` | 공식 `StanfordAIMI/stanford-deidentifier-base` 카드 `license: mit`; 영어 모델. README는 연구 목적 명시 | 논문·코드·모델 조건을 구분한 비교. 가중치 미다운로드 |
| CliniDeID | GPL-3.0-or-later, `4eaaf3ca691ebcce1e5cb5f225b4679cd4db2097` | 사전학습 모델은 **비상업용만**. Java 17, 모델/부속자료 조건 별도 | 모델 조건과 GPL 배포 의무 검토 전 기본 통합 대상에서 제외 |

## 1. ko-pii: 실제 기능과 경계

HEAD 커밋 일시 `2026-09-09T05:50:11Z`; 패키지 선언 버전 `1.16.0`, Python ≥3.10, 코어 `dependencies=[]`. 한국어 사전·규칙·체크섬 기반이며 한국어 조사 분리, 로마자로 표기한 한국 이름 매칭, 유니코드 정규화 후 원문 offset 유지 등을 제공한다. 이는 임의 영어 인명, 임상 전체 의미, 모든 한국어 이름 검출의 보장이 아니다. 프로젝트 자체도 일반 NER·법적 익명성·모든 도메인의 동일 정확도를 비보장 범위로 적었다. 임상 영역은 AGE/HEIGHT/WEIGHT 오탐과 약품·일반명사의 PERSON 오탐을 별도로 고려해야 한다. [공식 README](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/README.md), [pyproject](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/pyproject.toml).

`Anonymizer.process()`는 detect→정책 판단→치환을 수행하고 `text`, `detections`, `vault`, `summary`, `combined_risk`를 반환한다. `tokenize` 전략은 검출 원문과 토큰의 Vault 매핑을 남긴다. 결과의 `DetectionResult.text`, Vault와 원문 offset 주변 문맥은 내부 개인정보로 취급해야 한다. `combined_risk`와 코드의 ALLOW/REVIEW/BLOCK은 라이브러리 자체 휴리스틱 정책이므로 사용자 프로젝트의 반출 승인이나 인증으로 승격하면 안 된다. [Anonymizer 코드](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/src/ko_pii/anonymizer.py).

별도 `generalize_age(47)`은 `40대`를 반환하며 영어 `middle-aged`나 문장 파싱 기능은 아니다. `generalize_occupation('교수')`는 `연구/학계`를 반환하지만 영어 `professor`는 사전에 없어 원문을 그대로 반환한다. 이 모듈들은 기본 Anonymizer에서 문장 전체 운동·가족력 일반화로 자동 연결되지 않는다. [age 구현](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/src/ko_pii/generalization/age.py), [occupation 구현](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/src/ko_pii/generalization/occupation.py), [Anonymizer 구현](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/src/ko_pii/anonymizer.py).

`vault/encrypted.py`는 optional `cryptography`의 AESGCM을 사용하고 32-byte 키, 12-byte nonce, PBKDF2-HMAC-SHA256 480,000회, 무작위 salt로 Vault 파일을 암호화한다. 파일 암호화는 기본 메모리 Vault 자체의 접근권한을 제공하지 않는다. 키 관리·권한·보관기간은 호출 앱 책임이며 일반화로 사라진 전체 원문의 보존은 별도 설계가 필요하다. 본 작업은 전체 UTF-8 원문을 내부 인증 암호화 캡슐에 두는 정책으로 이 문제를 다룬다. [암호 Vault 코드](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/src/ko_pii/vault/encrypted.py). MIT의 재배포 시 원 저작권·허가문 보존 조건을 따른다. [LICENSE](https://github.com/Marker-Inc-Korea/ko-pii/blob/9516cabd6f582935cae1536ccbd8c5448afae679/LICENSE).

## 2. Presidio: 영어 탐지의 구성 의존성

Microsoft의 기존 링크는 현재 `data-privacy-stack/presidio`로 이동한다. 조사 HEAD 커밋 일시 `2026-10-04T11:07:17Z`, SHA `d8847904621733f4eaad4f9bd977b96a11325c90`는 현재 코드 조사 기준이며 실행 설치 버전과 같다는 뜻이 아니다. **실제 환경에서 `presidio-analyzer==2.2.360`, `spacy==3.8.11`, `en_core_web_sm==3.8.0` 설치를 확인했다.** Presidio 2.2.360 공식 PyPI 배포 provenance가 가리키는 커밋은 `af1c524460ad62e17313520a3cbb618b062b75cb`이고 wheel SHA256은 `aa6e83779b8f23587000ccfa3ef47aa34356a60b51284742e0d2a96b33205c4e`이다. 이 배포의 MIT LICENSE는 Microsoft Corporation 귀속이며 조사 HEAD의 Presidio Contributors 귀속과 구분해 원문을 보존한다. [PyPI 2.2.360](https://pypi.org/project/presidio-analyzer/2.2.360/), [배포 커밋 LICENSE](https://github.com/data-privacy-stack/presidio/blob/af1c524460ad62e17313520a3cbb618b062b75cb/LICENSE), [spaCy 3.8.11 배포](https://pypi.org/project/spacy/3.8.11/).

공식 언어 문서는 기본 구성에 영어 모델·recognizer가 있고 다른 언어에는 NLP 엔진과 recognizer/문맥어 조정이 필요하다고 설명한다. 설치 2.2.360과 조사 HEAD의 default YAML은 spaCy `en_core_web_lg`를 사용하며 `ORGANIZATION`을 오탐 우려로 무시한다. 본 앱은 모델 이름을 **`en_core_web_sm`으로 명시적으로 변경**했으며 소속·기관명은 별도 규칙으로 처리한다. `hybrid`는 실제 영어 NER와 구조적 PII recognizer를 실행하지만 모든 `John Smith` 문맥 검출을 보장하지 않는다. 정규식 `PatternRecognizer.analyze()`의 모델 없는 직접 호출은 가능한 별도 구성이고 현재 hybrid 전체의 동작을 설명하는 말은 아니다. [언어 문서](https://github.com/data-privacy-stack/presidio/blob/d8847904621733f4eaad4f9bd977b96a11325c90/docs/analyzer/languages.md), [2.2.360 default YAML](https://github.com/data-privacy-stack/presidio/blob/af1c524460ad62e17313520a3cbb618b062b75cb/presidio-analyzer/presidio_analyzer/conf/default.yaml), [패턴 공식 예제](https://presidio.dataprivacystack.org/tutorial/02_regex/).

NLP 모델 라이선스는 Presidio 라이선스와 별개다. **실제 선택 모델 3.8.0의 공식 release**는 MIT, spaCy `>=3.8.0,<3.9.0` 호환, 영어 CPU 파이프라인, `tok2vec` 및 NER를 명시한다. 배포 wheel은 12,806,118 bytes이며 공식 SHA256은 `1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85`이다. 고정 정적 word-vector 테이블이 0개여도 학습된 `tok2vec` 표현과 NER 신경망이 있으므로 “임베딩/ML 없음”으로 해석하지 않는다. 출처는 OntoNotes 5, ClearNLP, WordNet이며 학습 원자료는 받지 않았다. OntoNotes 원자료 조건을 모델 MIT로 덮어쓰지 않는다. 일반 NER 점수는 한영 임상 PHI 성능이 아니다. 모델 설치 후 추론은 로컬에서 실행하며 외부 모델 API로 기록을 전송하지 않는다. [공식 3.8.0 release](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0), [공식 모델 metadata](https://raw.githubusercontent.com/explosion/spacy-models/master/meta/en_core_web_sm-3.8.0.json).

## 3. SNUBH2025: 논문과 공개 정규식의 구분

An et al., *De-identification of clinical notes with pseudo-labeling using regular expression rules and pre-trained BERT*, BMC Medical Informatics and Decision Making 25:82, 2025-02-17, DOI `10.1186/s12911-025-02913-z`. 한국어·영어 혼용 SNUBH 방사선 기록으로 51개 정규식을 만들고 pseudo-label로 KoBERT-NER를 미세조정했다. 검증은 방사선 342건과 타 진료과 12건이다. 논문은 규칙 P97.2/R93.7/F1 96.2%, 32,000 pseudo-label 학습 KoBERT P96.5/R97.6/F1 97.1%를 보고한다. **논문 수치를 현재 코드의 합성 문자 단위 F1에 대입하거나 직접 비교하지 않는다.** 원문은 토큰 수준 라벨 검증을 설명하고 PHI 단어 통계를 보고한다. 이름·날짜·기관·지역·번호·ETC 6종이며, ETC에는 나이·성별·국적이 포함된다. 임상 유용성 정책은 원 논문의 마스킹 정책을 그대로 따르지 않고 본 작업에서 별도 정의한다. 원 자료는 IRB 정책상 공유 불가다. [논문 전문](https://link.springer.com/article/10.1186/s12911-025-02913-z).

HEAD 커밋 일시 `2026-06-15T03:30:42Z`. 현재 `tagging.py`는 문맥을 이용해 이름 구간을 좁히지만 기본 CLI `print_output()`은 `idx/origin/transformed`를 **평문 CSV**에 함께 기록한다. 원문 저장 경로를 임상 처리 경로로 사용하면 안 된다. 저장소의 공개 코드와 가중치 파일의 존재는 재사용 허가가 아니다. 확인한 README·전체 파일 목록·GitHub 라이선스 메타데이터에서 LICENSE나 코드 재배포 허가를 찾지 못했다. 논문 open-access 조건을 저장소 코드·학습자료·가중치 허가로 확장하지 않는다. [tagging.py](https://github.com/leelabsg/SNUBH_deid/blob/cc1e727750032cc2d4c69bc42acc857a30b17e99/tagging.py), [저장소](https://github.com/leelabsg/SNUBH_deid/tree/cc1e727750032cc2d4c69bc42acc857a30b17e99).

GitHub 공식 안내도 명시적 라이선스가 없으면 기본 저작권이 적용된다고 설명한다. 공개 저장소의 열람·fork 가능성과 파생 코드 재배포 허가는 구분해야 한다. 본 시제품에는 SNUBH 코드를 복사·배포하지 않는 선택을 적용한다. [GitHub 라이선스 안내](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository).

## 4. MIDRC와 CliniDeID

MIDRC HEAD 커밋 일시 `2025-11-24T12:38:53Z`. Stanford/Penn 프로젝트는 transformer PHI 탐지와 hide-in-plain-sight 대체값 생성을 결합하며 README는 연구 목적을 명시한다. 관련 논문 DOI `10.1093/jamia/ocac219`가 있다. 코드 LICENSE는 Apache-2.0이고 배포 시 라이선스·귀속·해당 NOTICE·변경 고지를 보존한다. 공식 모델 카드 `StanfordAIMI/stanford-deidentifier-base`는 영어와 MIT를 표시하며 확인 모델 revision은 `661b9c1c717d3165512d440abc3700c386aefab6`이다. 코드 조건, 모델 카드 표시, 학습 원자료 이용 조건은 따로 확인해야 한다. 영어 모델의 발표 점수로 한국어/혼용 정확도를 주장하지 않으며 HIPS surrogate 생성은 byte-exact 가역 복원을 자동 제공한다는 뜻이 아니다. [MIDRC README](https://github.com/MIDRC/Stanford_Penn_MIDRC_Deidentifier/blob/897fc0c0b9fcc3c0345228124a571a862cefa1d5/README.md), [LICENSE](https://github.com/MIDRC/Stanford_Penn_MIDRC_Deidentifier/blob/897fc0c0b9fcc3c0345228124a571a862cefa1d5/LICENSE), [공식 모델 카드](https://huggingface.co/StanfordAIMI/stanford-deidentifier-base).

CliniDeID HEAD 커밋 일시 `2023-08-09T12:21:53Z`. README는 GPL version 3 or later와 사전학습 ML 모델의 **non-commercial applications only**를 명시한다. Java JDK17 이상, Maven, 모델 파일들이 필요하고 README의 beta 버전에서는 RNN 컴포넌트가 비활성이다. GUI/CLI와 태그 또는 환자 기록에 걸친 일관된 surrogate 치환을 지원한다고 설명하지만 그 설명이 본 시제품의 성능·한국어 지원·반출 승인을 증명하지는 않는다. GPL 코드의 배포 의무와 사전학습 모델의 비상업 조건을 분리해 검토해야 한다. [고정 README](https://github.com/Clinacuity/CliniDeID/blob/4eaaf3ca691ebcce1e5cb5f225b4679cd4db2097/README.md), [LICENSE](https://github.com/Clinacuity/CliniDeID/blob/4eaaf3ca691ebcce1e5cb5f225b4679cd4db2097/LICENSE).

## 5. 간접식별자·문맥 일반화의 선행연구와 구현 정책

Baroud et al., PrivateNLP 2025는 의료 텍스트 간접식별자 9범주, MIMIC-III 퇴원요약 100건과 6,199개 annotation, 간접식별자 검출 baseline을 제안한다. 지인·가족·의료진 등 공격자의 보조 지식까지 고려하므로 이름 제거 후 직업·활동·희귀 경과가 남는 문제는 이미 연구 주제다. 본 작업은 환자 원문 MIMIC 자료를 가져오지 않는다. [ACL 1차 논문](https://aclanthology.org/2025.privatenlp-main.7/).

Larbi et al., EACL 2023는 여러 익명화 방법의 5개 downstream NLP 과제 영향과 재식별 공격을 분석한다. PHI 탐지 점수와 문맥 보존/분석 유용성, 잔여 재식별 위험은 분리해야 한다는 연구 근거로 사용한다. 이 논문 결과로 현재 한영 hybrid 시제품의 의료 성능을 주장하지 않는다. [ACL 1차 논문](https://aclanthology.org/2023.eacl-srw.11/).

아래는 **본 작업의 설계 권고**이며 위 도구들이 모두 제공하는 기능이라는 뜻이 아니다.

| 변환 요소 | 허용 범위와 정보 보존 요구 |
|---|---|
| `47-year-old` → 연령대 | 이용 목적이 coarse age를 허용할 때만. 숫자 나이를 사용하는 분석에서는 원 숫자를 공개 분석 payload에 보존하거나 반출을 보류해야 한다. 일반화 결과로 원 나이를 계산할 수 있다고 주장하지 않는다. |
| 이름 → 토큰 | 이름만으로 성별을 추론하지 않는다. `John Smith`여도 `male`을 생성할 근거가 없다. 명시된 입력만 이용한다. |
| 교수/소속 → 학술 직군 | 한영 직업 사전 및 정의된 문법에 한정한다. 드문 소속·직함·경력 조합은 잔여 식별 단서가 될 수 있다. |
| 대회/시점 → 지구력 운동/최근 | 실제 참가·완주·취소·부정 여부를 구분하고 숫자 거리/운동량/시점이 필요한 분석이면 보존한다. 이 규칙이 임의 자유서술의 의미를 이해한다고 주장하지 않는다. |
| `father died from MI at 52` | 주체 father, 긍정/부정, MI, 52세, 사망 여부를 보존하는 것이 기본이다. `positive family history`만으로 축약하면 주체·정확한 나이·치명적 경과를 잃는다. premature 기준은 명시적 버전 정책과 입력으로만 판정한다. |
| 복원 | 모든 변경·일반화 전 UTF-8 원문을 내부 AES-GCM 캡슐에 보존하고 공개 텍스트와 결합 검증한다. Unicode NFC/NFD, CRLF/LF, BOM, emoji, 제로폭 문자를 포함해 UTF-8 바이트 일치 검증을 따로 시행한다. |

사용자 예시의 목표 문장은 성별 생성과 가족력 정보 소실 때문에 그대로 구현 계약으로 채택할 수 없다. 정해진 한영 표현에 대한 규칙 일반화는 구현 가능하지만 임의 자유서술의 주체·인과·부정·시간·희귀 경력 조합까지 자동 이해하는 범위는 별도 모델·데이터·평가가 필요하다. 미지원 문장은 변환 없음/잔여 위험/검토 필요로 명시하고, 변화가 없다는 것을 안전하다는 뜻으로 해석하지 않는다.

## 6. 평가 계약

합성 평가에서는 (1) 유형별 PHI recall/precision과 TP/FP/FN, (2) 범위가 고정된 문맥 일반화의 의미 보존, (3) 전체 원문의 UTF-8 byte-exact 복원, (4) 공개 payload에 원문·키·capsule·평문 mapping이 섞이지 않았는지를 별도 산출한다. 한영·문장내혼용·조사·NFD·전각·제로폭·주체·부정·용량·기간·가족력·운동 및 알려진 실패 예를 포함한다. 평가셋은 합성이며 정규식 개발 규칙과 유사할 수 있으므로 실환자 성능·독립 외부 검증·안전성 인증으로 해석하지 않는다. 한 유형의 성공을 임의 이름이나 희귀 간접식별자까지 일반화하지 않는다.

코드가 직접 호출한 공개 의존성, 실행 당시 버전/해시, 선택 모델 유무를 기록한다. 외부 모델 API는 사용하지 않고 키·원문·복원 캡슐은 공개 문장/로그/저장소에 남기지 않는다. 복원 자료는 암호문이어도 내부 민감 자료로 분류한다. 후속 gateway가 공개 payload와 정책·위험·유용성·자료분리를 검증해야 하며 탐지/변환 함수 자체를 반출 승인으로 부르지 않는다.
