# ClinicalTextDeid 연동 계약 v1

Python: `from ClinicalTextDeid.api import transform, restore, detect, public_payload`.

`transform(request: dict, *, key: bytes) -> dict` 입력:
```json
{"text":"synthetic UTF-8 text","context":{"project":"opaque-id","document":"opaque-id","release":"opaque-id","purpose":"research"},"policy":{"mode":"direct","preserve_numeric_age":true,"preserve_exercise_time":true},"detector":"hybrid","hints":[]}
```
`text` 또는 `utf8_b64` 중 하나만. bytes 입력을 base64로 전달하면 BOM/CRLF/NFD까지 그대로 복원. key는 호출자가 전달하는 무작위 32 bytes이며 JSON에 넣지 않음. purpose는 research/external_ai. policy mode는 direct/context_rules. context_rules는 명시된 제한 문법만 일반화하며 임의 자유서술 이해, LLM/임베딩 기능이 아님. hints는 내부 입력의 `{start,end,type}` Unicode code-point 위치로 확인된 PHI를 추가하는 옵션 (보편 탐지 성능과 구별).

반환 스키마:
```json
{"schema_version":"clinical-text-deid/v1","transformation_id":"32-lowercase-hex-chars","public":{"text":"transformed text"},"policy":{"mode":"direct","preserve_numeric_age":true,"preserve_exercise_time":true},"audit":{"detector":"hybrid","processed_spans":[{"start":0,"end":5,"type":"PERSON","method":"ko_pii"}],"applied_edits":[{"start":0,"end":5,"type":"PERSON","method":"ko_pii","replacement":"<PERSON_1>"}],"generalizations":[]},"assessment":{"validation_status":"requires_downstream_review","residual_risk":"unquantified","unsupported":[],"warnings":[],"semantic_checks":{},"residual_candidates":[],"withheld_generalizations":[],"generalization_coverage":"not_requested"},"internal":{"capsule":{"format":"clinical-text-deid-aes256gcm/v1","nonce_b64":"...","ciphertext_b64":"..."}}}
```
audit에는 원문 substring/PHI 값/원문 hash를 반환하지 않음. 위치는 원문 위치이며 내부 검토용 메타데이터. public text 역시 잔여 개인정보 가능성이 있고 반출 승인이나 안전성 인증을 뜻하지 않음.

`public_payload(result: dict) -> dict`: 명시적 allowlist로 `{schema_version, text, validation_status}`만 반환. audit/assessment/internal/context/key는 외부 payload에 포함하지 않음. 부모 gateway가 반출을 별도로 결정해야 함.

`restore(result: dict, *, key: bytes, context: dict) -> bytes`: 공개 text와 policy/audit/assessment/schema/transformation_id/context 및 format을 AAD로 인증, full original UTF-8 bytes가 암호화된 capsule을 복호화. 어느 결과 필드나 context 변조, 다른 key, capsule 변조는 실패. 원문을 문자열로 재직렬화하지 않고 bytes 반환. transformation_id는 매 호출 새 무작위값이며 key/nonce와 다른 필드. 같은 context·동일 공개문장/offset의 다른 변환 사이 capsule swap도 거부.

`detect(text: str, hints: list | None = None, *, detector="hybrid") -> list[dict]`: PHI substring 없는 `{start,end,type,method}`. detection 자체는 내부 기능. 일반화 정책/임상검증은 별도. `hybrid`=ko-pii+local Presidio/spaCy+clinical_rules, `rules`=ko-pii+clinical_rules, `ko_pii`=ko-pii+임상 라벨/제외어·Unicode 어댑터. hybrid 모델 미설치 시 오류, 자동 fallback 없음. 200,000 UTF-8 bytes 상한.

`assessment.semantic_checks.status`는 `passed_narrow_checks`, `failed`, `requires_review`. 전체 free-text 동등성 검증은 항상 false. scoped negation/uncertain/conditional veto는 withheld_generalizations의 원문 없는 위치/유형/reason으로 보고. generic unsupported에는 미탐 이름·주소/희귀조합/숫자와날짜/CJK 영어모델 제외/한정 의료사전 동음이의 위험이 있고, 별칭 단서 시 동인성 미해결도 보고. residual_candidates는 최종 공개문장에 다시 같은 detector를 실행한 원문 없는 후보일 뿐 같은모델의 미탐까지 찾아낸 것이 아님. 가족 fact는 relation/negated/fatal/age/disease/premature_rule, age는 fatal일 때 사망나이이며 별도 MI 발병나이를 추정하지 않음.

CLI: `python -m ClinicalTextDeid.cli transform --input request.json --public-out public.json --internal-out internal/result.json --key-file internal/key.bin`; `restore --input internal/result.json --output internal/restored.txt --key-file internal/key.bin --context-file internal/context.json`. CLI stdout에는 상태만, 원문/키/capsule은 출력하지 않음. 상세 구현 후 README에 실행 예시 제공.

기본 정책: 숫자 나이/운동 시점/가족관계/가족력의 부정/발병·사망 구분 및 나이/용량 보존. context_rules에서 preserve_numeric_age=false 또는 preserve_exercise_time=false를 명시하면 해당 일반화에 따른 손실을 audit에 기록하고 원문을 암호화 캡슐에 보관. 가족력 부정·주체·나이·치명적 경과는 context_rules에서도 보존. 이름으로 성별을 추론하지 않음. 가족력 premature 기준은 시제품의 명시 규칙(부/형제 <55, 모/자매 <65), 임상지침 판정이 아님.
