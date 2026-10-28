# Validation — 2026-10-28

- Public demo API and ID-label checks: 30 tests passed (queue limits, visitor isolation, origin checks, forbidden administration/restoration routes, approval/export sequence, actual XLSX XML).
- Hosted queue: 7 tests passed using real SQLite semantics (source allowlist, session isolation, one-time claim/completion, limits, offline failures, private worker credentials).
- Local browser: actual Qwen3 GPU processing of the figure example preserved the surgery date, elapsed days, history year and family role. 12/12 selected evidence rows passed; this is a development example, not an overall accuracy metric.
- Prior core pipeline evaluation: five synthetic clinical cases and eight injected failures. No actual patient data or unseen expert corpus was used.

Public deployment succeeded at https://clinicaltext-emr.alexchoi676.chatgpt.site. The public browser flow ran on the GPU server in 17.0 seconds for the main synthetic example (not a latency guarantee). It returned 12/12 selected preservation rows and zero remaining candidates in this example, then completed reviewer approval and actual XLSX/JSON/public-key downloads. The downloaded JSON signature verified on the laptop. XLSX was a valid ZIP/XML workbook and preserved surgery date, three-day interval, history year, family role and MI age while removing the authored name, university and named event. See `public-browser-verification.json`.

The server supervisor was verified with parent PID 1 and an independent session. The loaded Qwen3 model reported equal model and VRAM size (20,091,167,046 bytes), demonstrating GPU execution. This app does not rely on the local preview or SSH tunnel to serve visitors.

Desktop and phone breakpoint checks used the actual public page. The shared GPU host, existing Ollama service and unrelated jobs were not restarted or reconfigured.

Gateway regression: 47 tests passed after narrowing the ID-field-label false positive. All five prepared public examples reached REVIEW_READY across the recorded runs; the medication example was rechecked after preserving its explicitly required patient age and resolving the fully replaced ID label. Original held results are retained for comparison. New custom notes remain held for export by design.

Public-source and server checks found no tracked runtime credentials or private server addresses. Ollama and the previous local admin app still listen on loopback only. The project worker is detached from the laptop and makes outbound HTTPS requests.
