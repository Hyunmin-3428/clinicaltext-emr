# 두 임상 텍스트 구현과 통합 평가

한국어·영어 임상 문장의 비식별 후보를 만드는 `ClinicalTextDeid`와, 그 후보를 검토·승인한 뒤 한 번만 로컬 모의 외부 저장소에 내보내는 `ClinicalCDSGateway`를 별도 구현했다. 이 디렉터리는 두 구현을 실제 연결하는 실행 예와 독립 작성 합성 정답 평가를 담당한다. 실제 환자자료·병원망·외부 AI 서비스는 사용하지 않는다.

| 작업 | 실제 구현 | 설명과 조사 |
|---|---|---|
| 한영 혼용 임상 문장 비식별화 | MIT ko-pii + 설치된 Presidio/spaCy 영어 모델, 임상 보완 규칙, 제한된 문맥 일반화, AES-256-GCM 원문 바이트 복원 | [Task 1 사용법](../ClinicalTextDeid/README.md), [출처·라이선스·논문](../ClinicalTextDeid/RESEARCH.md), [연동 계약](../ClinicalTextDeid/INTERFACE.md) |
| 의료 Cross-Domain 보안 게이트웨이 | 서버의 목적·수신처·등급 정책, 별도 잔여정보/임상사실 검사, 역할 분리, 정확한 검토본 승인, Ed25519 서명, SQLite 일회 반출 상태, 실제 로컬 파일 반출 | [Task 2 사용법](../ClinicalCDSGateway/README.md), [N2SF](../ClinicalCDSGateway/docs/N2SF_RESEARCH.md), [법령](../ClinicalCDSGateway/docs/LEGAL_RESEARCH.md), [직접 선행·공급망](../ClinicalCDSGateway/docs/PRIOR_AND_SUPPLY_CHAIN.md) |

## 연결 실행

현재 작업 폴더에서 아래 명령을 실행한다. 각 작업이 준비한 `.venv`를 사용하며 모델은 설치되어 있어야 한다. 이 실행은 외부 모델을 호출하거나 실행 중 모델을 다운로드하지 않는다.

```powershell
& '.\ClinicalTextDeid\.venv\Scripts\python.exe' -m ClinicalTextIntegration.connected_demo
```

입력은 서버가 등록한 합성 한영 문장이다.

```text
성명: 김민준. Patient name: John Smith. No evidence of MI. BP 120/80 mmHg.
```

실제 ko-pii/영어 모델 호출로 만든 후보를 서버가 보관한다. 이름 두 개는 별도 토큰으로 처리하고, `No evidence of MI`와 `BP 120/80 mmHg`는 유지한다. 검토자가 본 문서 해시로 승인한 뒤 반출한다. 동시 두 요청 중 하나만 반출하고, 원문과 키·복원 캡슐은 외부 payload에 포함하지 않는다. 등록되지 않은 한영 문장은 같은 변환기를 거쳐도 반출되지 않는다.

임시 내부 저장소는 실행 종료 시 정리한다. 남는 산출물은 합성 공개문서 [connected_public_example.json](reports/connected_public_example.json)과 내용 없는 통제 결과 [connected_report.json](reports/connected_report.json)이다. 공개 JSON의 서명 검증은 별도의 신뢰 경로로 제공한 공개키를 전제로 한다. 예제 파일만으로 기관 반출 승인을 증명하는 것은 아니다.

## 별도 합성 평가

```powershell
& '.\ClinicalTextDeid\.venv\Scripts\python.exe' -m ClinicalTextIntegration.run_validation
```

이 명령은 두 작업의 자체 테스트, 이 디렉터리의 7개 회귀·연계 테스트, 실제 반출 예, 힌트 없는 24개 문장 평가를 실행하고 [validation_manifest.json](reports/validation_manifest.json)에 코드 해시와 실행 결과를 기록한다. 코드가 시험 도중 바뀌면 결과를 고정 스냅샷 검증으로 인정하지 않는다.

24개 문장의 PHI 구간과 보호할 임상 표현은 구현의 검출 결과에서 가져오지 않고 [cases.py](cases.py)에 따로 작성했다. 평가 중 발견한 오류를 개발자에게 전달해 수정했으므로, 최종 수치는 독립 작성 정답을 사용한 **합성 회귀 평가**다. 봉인된 외부 검증자료 또는 실제 병원 성능으로 해석하지 않는다. 힌트를 주어 맞춘 탐지는 이 지표에 포함하지 않는다.

[deid_independent.json](reports/deid_independent.json)은 문자 단위 precision/recall/F1, 정확 구간·타입 recall, 언어별 집계, 전체/부분 누락, 선택 임상 표현 손실, 원문 바이트 복원, 변조 거부를 분리해 기록한다. 선택 표현 57개가 남는 것은 전체 의료 의미·공동참조·인과관계 검증과 다르다. 합성 문장에는 임상수치와 NFD·제로폭문자·BOM·CRLF·이모지가 포함되어 있다.

## 연구에서 수정한 전제

N2SF 모델11에는 이미 망연계의 비식별화·마스킹·정보 축소와 승인·검사 통제가 있다. 따라서 망연계에 배치했다는 사실만으로 신규성을 주장하지 않는다. 모델2의 외부 O AI 사용과 2026.5.1 기본지침의 하위 등급 처리 조건도 별도로 확인했다. 병원별 적용 근거는 설립·공공기관 지정·상급 규정을 확인해야 하며 모든 민간 병원의 직접 의무로 확대하지 않는다. [독립 원문 검토](research/N2SF_INDEPENDENT.md), [공식 기본지침](https://www.ncsc.go.kr/template/resources/file/nis_guide_lines_2023_1_31.pdf).

개인정보보호법·의료법, 2026.3. 가명정보 가이드와 2025.12. 보건의료데이터 가이드를 반출 조건과 대응시켰다. 가명처리 성공은 공개정보 전환·기관 반출권한·해외 AI 전송 허가를 자동 생성하지 않는다. 실제 연구 목적, 제공/위탁/국외이전, 적정성, 의료·윤리 검토는 기관이 확인해야 한다. [법령 조사와 조건 대응표](../ClinicalCDSGateway/docs/LEGAL_RESEARCH.md).

의료 PHI 변형·복원·정책·검증·게이트의 가까운 특허와 공개코드가 존재한다. Redacta의 `release`는 내부 복원이며, MD Aware의 CDS는 clinical decision support와 EHR write-back 승인이다. 이번 송신 전 반출 승인과 구별해서 비교했다. “선행 없음”을 확인한 것으로 표현하지 않는다. [직접 선행 독립 검토](research/DIRECT_PRIOR_REVIEW.md).

## 구현 범위

- 비식별화 모듈은 임의 텍스트의 후보를 만들 수 있지만 모든 이름·주소·희귀 조합 탐지를 보장하지 않는다. 기본 판정은 후속 검토 필요다.
- 게이트웨이의 현재 반출 허용 대상은 검증용으로 등록한 합성 문장이다. 일반 임상 자유서술은 REVIEW/BLOCK이며 실데이터 반출은 꺼져 있다.
- 임상 일반화는 제한 문법이다. 원 예시의 정확나이·운동 시점·부친·MI·사망·52세를 기본으로 보존하고 이름으로 성별을 추론하지 않는다.
- 키와 복원 캡슐은 내부에 유지한다. 라이브러리의 키 보유자 복원 기능과 실제 서비스의 정당한 권한/재식별 제한은 구별한다.
- 현재 반출은 로컬 파일 모의 경계다. 공유 Windows 사용자 계정의 임의 파일쓰기·직접 HTTP·다른 망 경로를 차단한 OS/네트워크 배포는 구현·검증 범위에 포함하지 않는다.
- HMAC 감사체인은 중간 변경을 검사한다. 외부 신뢰 checkpoint가 없어 마지막 기록 삭제·전체 DB rollback을 검출한다고 주장하지 않는다. 실패 후 반출 승인은 소비되며 자동 재시도하지 않는다.

[독립 평가 계약과 실행 감사](research/EVALUATION_CONTRACT.md)에 재현된 수정사항과 남은 실패를 보존한다. 실제 임상 적용을 판단하려면 기관 자료의 별도 검증과 운영 경계 시험이 필요하다.
