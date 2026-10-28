# Validation — 2026-10-28

- Public demo API: 23 tests passed (queue limits, visitor isolation, origin checks, forbidden administration/restoration routes, approval/export sequence, actual XLSX XML).
- Hosted queue: 5 tests passed using real SQLite semantics (source allowlist, session isolation, one-time claim/completion, limits, offline failures, private worker credentials).
- Local browser: actual Qwen3 GPU processing of the figure example preserved the surgery date, elapsed days, history year and family role. 12/12 selected evidence rows passed; this is a development example, not an overall accuracy metric.
- Prior core pipeline evaluation: five synthetic clinical cases and eight injected failures. No actual patient data or unseen expert corpus was used.

Public deployment and mobile/browser verification are recorded after release.
