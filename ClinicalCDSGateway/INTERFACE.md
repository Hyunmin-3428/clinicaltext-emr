# Gateway 입출력 계약 v1

Python 3.11, cryptography 50.0.2. `gateway.engine.Gateway`를 이 폴더가 Python module path에 있는 환경에서 import한다. 루트 부모 통합 코드에서는 이 폴더를 `sys.path`에 추가하면 된다. 임상 원문은 외부 API나 stdout에 전달하지 않는다.

```python
from gateway.engine import Gateway
from gateway.fixtures import request
from gateway.deid_adapter import ClinicalTextDeidAdapter

gateway = Gateway.initialize("new-empty-state-directory")
gateway.adapter = ClinicalTextDeidAdapter("hybrid")  # "rules" also supported
submit_token = (gateway.internal / "submitter.token").read_text()
review_token = (gateway.internal / "reviewer.token").read_text()
export_token = (gateway.internal / "exporter.token").read_text()
result = gateway.submit(request("connected-hybrid"), submit_token)
review = gateway.inspect(result["release"], review_token)  # internal-only text
gateway.approve(result["release"], review_token, review["text_sha256"])
egress = gateway.export(result["release"], export_token)  # actual file write
```

`Gateway(root, adapter=None, clock=time.time)`는 영속 상태를 재개한다. synthetic adapter는 사전작성 4개 fixture의 연결 테스트용 fallback이다. **실제 공개 변환 연결은 `ClinicalTextDeidAdapter("hybrid")`**이며, `connected-hybrid` fixture에서 호출·검사·승인·파일 반출 전체를 검증했다. 나머지 upstream 출력도 독립 검사하지만 사전 승인 출력 정답과 다르면 BLOCK/REVIEW이고 임의로 canonical fixture로 바꿔치기하지 않는다.

요청의 정확한 필드: `text`, `document`, `purpose`, `source`, `source_grade`, `destination_grade`, `recipient`, `media_type`, `project`. 누락/추가/중복 JSON key, NaN/Infinity, 미지원 Unicode 제어·비정규화·형식·크기는 거부한다. `text`는 최대 UTF-8 32768 bytes이며 현재 canonical plain-text subset만 허용한다(BOM/CRLF/NFD도 모호성 때문에 거부). 문자열·원본 bytes의 넓은 복원 범위는 형제 deid 기능과 구별한다.

```json
{"text":"synthetic text","document":"doc-001","purpose":"scientific-research","source":"synthetic-corpus","source_grade":"S","destination_grade":"O","recipient":"local-research","media_type":"text/plain","project":"demo-research"}
```

`document` 등 메타데이터는 제한된 opaque 문법이고 외부 문서ID는 서버 HMAC으로 생성한다. 요청의 source/grade는 서버 등록과 대조한다. synthetic provenance는 서버 등록 fixture의 정확한 입력 hash까지 확인하는 모의 출처 검사다. 실제 병원 원천 시스템의 서명·전송자 인증을 구현한 것은 아니다.

| 함수 | 반환/실패 |
|---|---|
| submit(request, token) | `{release,state,codes}`. state=REVIEW/BLOCK. 자동 APPROVED 없음 |
| inspect(release, reviewer_token) | 내부 `{release,state,codes,candidate,text_sha256}`. 원문은 반환하지 않지만 후보에도 잔여PHI 가능 |
| approve(release, reviewer_token, expected_text_sha256) | `{release,state:APPROVED,manifest_sha256}`. 등록 합성·보호사실·잔여검사·최신 문서·TTL·별도 주체 모두 필요 |
| export(release, exporter_token) | `{release,state:EXPORTED,file}`. 실제 `external/<server-release>.json` 생성. 재전송·미승인·변경·취소·만료 거부 |
| revoke(release, reviewer_token) | REVIEW/APPROVED→REVOKED. 소비가 선행하면 NOT_REVOCABLE |
| audit_export(reviewer_token) | PHI·키·raw hash 없는 최소 이벤트들. 내부 변조/중간 삭제 검증, suffix rollback는 범위 밖 |
| restore_internal(release, reviewer_token) | 내부 원 UTF-8 bytes. 외부 전송용이 아님 |
| verify_external(envelope, public_key, now=None, expected_recipient='local-research', expected_policy_digest=None) | schema/hash/Ed25519/TTL/scope 확인. 전달받은 public_key는 별도 신뢰 채널에서 고정해야 함. 수신자의 replay ledger는 별도 필요 |

실패는 `Denied(code)`만 외부 표면에 노출한다. CLI는 `{state: DENIED, code: predefined-code}`와 exit2를 내보내고 임의 예외·원문·키·원문 경로를 출력하지 않는다. 인증 실패도 원문 없는 DENIED 사유코드로 감사하며 감사 저장소 장애는 외부 반출을 허용하지 않는다. 감사 장애 자체의 원격 통보·재처리 큐는 범위 밖이다.

외부 envelope는 정확히 `payload`, `signature`만 갖는다. payload 필드는 `schema_version`, `text`, `document`, `release`, `purpose`, `recipient`, `policy_version`, `policy_digest`, `text_sha256`, `expires_at`이다. signature는 이 payload canonical JSON bytes의 Ed25519 signature(base64)다. 원문 hash, 원문, capsule, 대응표, 키, detector audit, assessment는 포함하지 않는다.

adapter 경계는 `transform(text)->Candidate` 또는 `transform_bound(text,key,context)->Candidate`. 후자의 context는 서버 생성 `project/document/release/purpose(research)`이며 key는 내부 vault key다. 반환 Candidate의 `supported`나 upstream `assessment`는 승인 근거가 아니다. 엔진은 원문 fixture 등록·후보 정답·잔여검사·보호사실·사람 승인·현재 상태를 직접 확인한다. upstream 전체 결과·캡슐은 바깥 AES-GCM 캡슐 안에만 저장한다. 자유서술의 새로운 지원 영역은 기관별 독립 검증 없이 추가하지 않는다.

