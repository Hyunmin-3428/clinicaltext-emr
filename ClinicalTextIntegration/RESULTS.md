# 두 구현의 조사·개발 결과

확인 기준일: 2026-10-08. 두 개의 독립 작업 디렉터리에 실행 가능한 프로토타입을 만들고 실제로 연결했다. [전체 사용법](README.md)과 [고정 코드 검증 기록](reports/validation_manifest.json)에서 재현 방법과 코드 해시를 확인할 수 있다. 데이터는 모두 합성이다.

## 구현 결과

**Task 1 — 한영 혼용 임상 문장 비식별화.** 한국어는 MIT ko-pii 고정 코드를 번들해 실행하고 영어는 설치된 Presidio/spaCy 모델로 탐지한다. 임상 보완 규칙과 Unicode 원문 위치 처리를 추가했다. 직접 식별자 치환과 제한 문법의 문맥 일반화를 분리했다. 이름으로 성별을 추론하지 않고, 원 예시의 정확나이·운동 시점·부친·MI·사망·52세를 기본으로 유지한다. 일반화로 잃는 항목은 명시하고 원 UTF-8 전체를 내부 AES-256-GCM 캡슐에서 복원한다. 공개문장·정책·감사·평가·문서/목적/공개회차 문맥의 변경은 복원 인증에 결합했다. [구현과 실행](../ClinicalTextDeid/README.md), [자체 56문장 평가](../ClinicalTextDeid/EVALUATION.md).

**Task 2 — 의료 Cross-Domain 보안 게이트웨이.** 변형과 반출 승인을 구분한다. 서버의 목적·수신처·등급 정책, 변형 후 별도 잔여정보·임상사실 검사, 제출/검토/반출 역할, 검토한 문서의 해시, 목적·수신처·정책판·문서/회차·만료 조건을 적용한다. 승인 내용을 Ed25519로 서명하고 SQLite에서 한 번만 소비한 뒤 실제 로컬 모의 외부 파일을 만든다. 원문·키·캡슐·내부 검사결과는 최소 공개 스키마에서 제외한다. Task 1의 실제 hybrid 출력이 이 경로에 들어가는 연계를 구현했다. [구현과 실행](../ClinicalCDSGateway/README.md), [기준과 통제 대응표](../ClinicalCDSGateway/docs/CONTROL_MATRIX.md).

## 실행 증거

| 검사 | 확인 결과 | 범위 |
|---|---|---|
| 자동 테스트 | 총 99개 중 **98개 통과, 1개 제외** | Task 1 44통과/1제외, Task 2 47통과, 독립 연계·회귀 7통과 |
| 제외 항목 | 디렉터리 symlink 검사 | Windows에서 생성 권한이 없어 미실행; 성공으로 계산하지 않음 |
| 코드 고정 | 시험 전후 소스 해시 동일 | [검증 manifest](reports/validation_manifest.json) |
| 실제 연결 | **13개 확인, 9개 거부 시나리오** | 공개 hybrid 변형 → 별도 승인 → 실제 로컬 파일 생성 |
| 동시 반출 | 성공 1건, 소비된 승인 거부 1건 | 외부 완성 JSON 파일 1개 |
| 독립 작성 PHI 정답 | 24문장, 27구간 | 힌트 없이 실행; 문자 단위 정답 |
| 전체 PHI 문자 점수 | Precision **99.54%**, Recall **81.95%**, F1 **89.90%** | 이름·주소 등을 완전히 가리지 못한 실패가 있음 |
| **한영 혼용 부분** | 11문장, 18구간; 문자 Recall **71.26%** | 이 성능을 범용 임상 자동 반출 근거로 삼을 수 없음 |
| 정확 PHI 구간 | 22/27; 구간·유형 모두 일치 21/27 | 문자 F1와 다른 엄격 지표 |
| 선택 임상 표현 | **57/57 보존** | 용량·부정·관계 등의 지정 표현; 전체 의료 의미 검증 아님 |
| 원문 복원 | **24/24 UTF-8 바이트 일치** | BOM/CRLF/NFD/제로폭/이모지 포함; 새 프로세스 복원도 별도 검사 |
| 복원 변조 | 평가 7개 공격 거부 | 결과 필드·목적·키 변경; 자체 테스트에서 추가 조건 검사 |

독립 작성 정답은 개발 중 오류를 수정하는 데도 사용했으므로 **합성 회귀 평가**다. 봉인된 holdout 또는 실제 환자 평가가 아니다. 원문/변환문과 개별 누락 위치는 합성 [상세 지표](reports/deid_independent.json)에, 실제 공개 payload와 반출 통제는 [연계 결과](reports/connected_report.json)에 기록했다. 자체 56문장 평가와 이 24문장의 분모·정답을 혼합하지 않는다.

완전 미탐은 `Minjun Kim`, 한글 조사와 붙은 `John Brown`, 혼용 문장의 `Naomi Brooks`, `25 Willow Street` 등 4구간이다. 별도 드문 이름 `Xochitl Quispe`도 미탐이 재현됐다. 영어 단독 예제의 좋은 점수를 혼용 성능으로 대체하지 않는다. 표현 보호용 좁은 임상 사전과 실제 이름의 동음이의 문제, 별칭·같은 환자 연결은 남는 연구 대상이다.

## 조사 결과가 설계에 준 수정

- **N2SF:** 2025년 1.0과 생성형 AI 모델2·망간 전송 모델11, 2026.5.1 기본지침을 확인했다. 모델11은 이미 비식별화·마스킹·정보축소와 승인·검사를 다룬다. S→O 배치 위치만으로 신규성을 주장할 수 없다. 비식별화 결과가 자동으로 O등급이 되거나 반출권한이 생기는 것도 아니다. 기관의 분류·허용 통제와 병원별 적용 근거가 필요하다. [독립 N2SF 조사](research/N2SF_INDEPENDENT.md), [공식 최신 기본지침](https://www.ncsc.go.kr/template/resources/file/nis_guide_lines_2023_1_31.pdf).
- **한국 법·지침:** 현행 개인정보보호법·시행령·의료법, 2026.3. 가명정보 처리 가이드와 2025.12. 보건의료데이터 활용 가이드를 확인했다. 연구 목적·적정성·추가정보 분리·제공/위탁/국외이전·의료기록/윤리 검토를 각각 통제 조건으로 정리했다. 로컬 gateway 승인은 법무·DRB/IRB 또는 지정 결합전문기관의 판단을 대신하지 않는다. [법령·조건 대응 조사](../ClinicalCDSGateway/docs/LEGAL_RESEARCH.md), [개인정보보호위원회 현재 가이드](https://pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=I030010000&nttId=11931), [보건의료 공식 개정 공지](https://www.khis.kr/board.es?act=view&bid=0001&list_no=2267&mid=a10301000000&tag=).
- **직접 선행:** Claritrics·Anonos·MD Aware·CN 특허, Redacta·Trylon·FFI/Isode 등에서 가까운 구성과 공개 코드를 확인했다. MD Aware의 CDS는 clinical decision support이며 EHR write-back 승인이고, Redacta의 release는 내부 복원이다. 이번 송신 전 반출 승인과 방향·대상을 구별했다. 제한된 조사에서 완전히 같은 통합 구현은 확인하지 못했지만 **선행 없음·특허 신규성 확정은 아니다**. [8개 직접 선행 비교](research/DIRECT_PRIOR_REVIEW.md), [MD Aware 공식 특허](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/20260221245), [Redacta 공개 코드](https://github.com/nickjlamb/redacta).
- **공개 코드 재사용:** 재사용 조건을 확인한 ko-pii와 Presidio/spaCy를 실제 통합했다. SNUBH 2025 논문은 비교 근거로 활용했으나 저장소의 코드 라이선스가 확인되지 않아 새 구현에 복사·재배포하지 않았다. 조사 HEAD와 실제 설치 버전, 코드·모델·학습자료의 조건을 구분했다. [출처와 라이선스](../ClinicalTextDeid/RESEARCH.md), [실제 설치 manifest](../ClinicalTextDeid/vendor/RUNTIME_MANIFEST.json).

## 현재 사용 가능한 범위

합성 노트 변형·정확 복원, 등록된 합성 문서의 승인·취소·만료·변조·동시성·한 번 반출을 재현할 수 있다. **일반 임상 자유서술과 실환자 문서의 반출은 허용하지 않는다.** 실데이터 승인 경로·재식별 적정성 검토·병원망 연결·OS/네트워크 우회 차단·기관 키관리·법정 감사 보존과 사고대응은 이 로컬 구현의 완료 범위에 포함하지 않는다.

HMAC 감사체인은 중간 기록 변조를 검사하지만 마지막 기록 삭제·전체 DB rollback을 막는 외부 checkpoint가 없다. 서명 검증도 수신 측 신뢰키 배포와 별도 replay 저장을 전제로 한다. TXT 제한은 일반 HWP/PDF/DOCX CDR 구현이 아니다. [독립 감사와 수정 증거](research/EVALUATION_CONTRACT.md)에 초기 결함, 수정 확인, 남은 한계를 함께 보존했다.
