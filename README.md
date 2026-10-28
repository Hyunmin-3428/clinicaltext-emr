# ClinicalText · EMR research export demo

**[Try the live synthetic demo](https://hyunmin-3428.github.io/clinicaltext-emr/)** · [QR code](dist/qr.png)

The website and GPU worker run independently of the developer laptop. The shared GPU host is not rebooted or reconfigured by this project.

An EMR-style prototype for **research-purpose-specific clinical information preservation** during free-text pseudonymization. Uses a pretrained Qwen3 model through Ollama; no fine-tuning or new training.

> All public-demo notes are author-created synthetic examples. This prototype is not connected to a hospital EMR and does not certify anonymity, clinical correctness, or N2SF/CDS compliance.

## Experience

1. Select an outpatient, surgical, inpatient or progress note from the simulated chart, or use **새 문서** to type a new synthetic note or import a UTF-8 `.txt` file (presentation mode: 1,000 characters / 16 KB). No actual patient records should be entered into this public demo.
2. Choose a research purpose and preservation conditions. For the main example, compare generalizing patient age with retaining exact age for age-stratified research.
3. Use clearly labelled, pre-executed real model results for the six default examples, or bounded live server inference for a new synthetic note; examine the natural surrogate note and source-grounded preservation evidence.
4. Review and approve an eligible result; download TXT, a real Excel `.xlsx`, or the gateway-signed JSON artifact. Held results cannot be exported.

Actual surgery dates, elapsed days, relevant history years, negation and family/patient roles are checked. Privacy protection can conflict with research utility; unresolved results are held. The same LLM is called separately for processing, clinical checking and privacy checking. Code checks add evidence grounding and selected clinical guards; these are not independent statistical validation models.

## Architecture

```text
Mobile / desktop browser (hosted EMR interface)
  → HTTPS synthetic-job queue
  ← GPU server pulls jobs over outbound HTTPS (no laptop relay)
  → single-worker inference
  → actual ko-pii + Presidio / spaCy + clinical rules
  → Qwen3 purpose-directed source-anchored edits
  → separate clinical and candidate-only privacy LLM calls
  → code checks: evidence, diagnoses, dates/events, elapsed days, negation, family roles
  → reviewer confirmation → signed gateway export → TXT / XLSX / JSON
```

The public API exposes no model configuration, original restoration, administrative shutdown, raw Ollama proxy, or filesystem routes. Custom notes require an explicit synthetic-record confirmation and bounded plain text. Unregistered custom notes can be processed and inspected; their export remains held for institutional review. This confirmation does not establish that a note is truly synthetic or safe. Visitor tokens are kept only in browser memory. Notes and approval records are temporary; restart discards session access. Queue, visitor rate and session limits bound GPU usage. A public demo approval simulates the workflow; real institutional identity and role separation remain future work.

## Run locally

Python 3.11+ and Ollama are required. Install dependencies and the pretrained model:

```sh
python -m venv .venv
# Activate the environment for your operating system.
python -m pip install -r requirements.txt
ollama pull qwen3:30b
python demo_server.py --origin http://127.0.0.1:8771 --model-url http://127.0.0.1:11434
```

In a second terminal:

```sh
python -m http.server 8771 --bind 127.0.0.1 --directory dist
```

Set `dist/config.js` to your local API origin (`http://127.0.0.1:8770`) and open `http://127.0.0.1:8771`. A smaller model can be selected with `--model`, but its outputs need separate evaluation; the validated demonstration used `qwen3:30b` Q4_K_M on an RTX 3090.

## Deploy

The live deployment uses a hosted HTTPS interface and queue, plus `gpu_agent.py` running on the shared GPU server. The agent only makes outgoing HTTPS calls. It exposes no inbound port and requires no firewall, nginx or host restart. All current website code and controls live under `~/clinicaltext`; the existing model runtime and evaluation jobs are linked without changing their active paths. See `queue-server-layout.md`. The laptop is not required after deployment.

`node build.mjs` embeds the UI assets in the Worker ESM. The Sites hosting manifest declares the D1 queue; schema-only migrations are in `drizzle/`. Set the private `AGENT_TOKEN` in the hosting environment and in the server runtime token file, never in browser code or Git. `deploy_agent.py` accepts it via hidden terminal input or stdin.

A direct HTTP API is also available for local development:

`dist/` is a buildless static site. Run the Python API on the GPU server, bound to loopback, and place an HTTPS reverse proxy or tunnel in front of this **demo API only**. Keep Ollama and the local administrative app private. Set the allowed frontend `--origin` and the public API address in `dist/config.js`. Use `--protocol http2` if UDP tunnel egress is blocked. A Quick Tunnel is temporary: its hostname changes when restarted and it has no uptime guarantee. For a stable event deployment, use a named tunnel/domain and update the API URL accordingly. Do not expose the unrelated applications on a shared GPU server.

Generate a QR code for the public **frontend** URL, not a laptop loopback URL. The QR stays valid when the backend address changes as long as the frontend is updated.

## Tests and evaluation limits

```sh
node build.mjs
node --test tests/worker.test.mjs
python -m unittest discover -s tests -v
python -m unittest discover -s ClinicalTextWeb/tests -v
```

The underlying development evaluation covered five synthetic clinical cases and eight injected errors, plus functional checks. These are development fixtures, not an unseen expert-annotated clinical corpus; they do not establish general sensitivity, specificity, safety, novelty or superiority over other approaches. Public-demo UI and API checks are documented in `docs/VALIDATION.md`.

## Open-source bases and licensing

- **ko-pii**: actual vendored detector code, unchanged and hash-checked; upstream license and commit/file manifest included in `ClinicalTextDeid/vendor/ko-pii/`.
- **Microsoft Presidio / spaCy**: actual detection calls through pinned dependencies, with their own licenses.
- **local-llm-deid**: unchanged pinned core included for the development comparison only; not the default public web processing path. License and manifest retained under `ClinicalTextWeb/vendor/`.
- **RUPTA** and **KatherLab/deidentifier**: ideas for privacy/utility feedback and source-grounded edits informed our own adaptation; their full pipelines were not reproduced and KatherLab code is not copied.
- **Qwen3 / Ollama**: external model/runtime, installed separately; weights are not redistributed here.

Project-authored code is provided under the MIT license. Bundled third-party files remain under their original licenses. See `THIRD_PARTY_NOTICES.md`.

## Public website and GPU isolation

The visitor-facing website is GitHub Pages. A separate HTTPS API accepts bounded synthetic jobs; the GPU server pulls those jobs outward over HTTPS using a private worker token. It does not expose Ollama or its SSH/admin interface through the website. GitHub Pages receives no worker credentials. Public anonymous requests still consume GPU resources, so queue and rate limits are enforced.

Run `python build_pages.py` after frontend changes to update the static `docs/` site. GitHub Pages uses `main:/docs`; set the backend API and exact allowed frontend origin together. QR codes point to the GitHub Pages URL. A custom domain can later be attached to Pages through verified DNS ownership.

Compatible Unicode typography (full-width digits/letters, compatibility units and Windows line endings) is normalized at the web ingress boundary. Detection, source-anchored edits and clinical checks use the normalized representation; the original input is kept in private session memory. Hidden control characters and active HTML/script content remain rejected.

## Presentation mode: 50 visitors

`event_limits.json` is the common public queue / GPU agent policy. Six authored example-purpose pairs are precomputed with the real model, version-bound and revalidated on every use. The UI explicitly labels **사전 실행 결과**. A cache miss never falls back to inference. Approvals and signed exports are generated separately for each visitor; cached approvals or expired signatures are not reused.

- 256 visitor sessions (headroom for refreshes) and 128 lightweight pending jobs; shared venue Wi-Fi allowed (200 session starts per IP / 10 min, within the global cap).
- One live GPU inference at a time, at most 2 pending live jobs, minimum 120 seconds between admissions, 1 per visitor / 10 min, 12 total / hour.
- At most 90 seconds of model-request time per document; a persistent rolling budget permits 600 seconds of model calls per hour. Calls stop when time/budget expires; uncertain unload pauses further live inference.
- Live notes: 1,000 characters. Use only synthetic records. Live eligibility still does not authorize exporting unregistered custom sources.
- CPU worker priority is lowered and numerical-library threads limited to one. Only the selected GPU is inspected; foreign processes on it prevent live inference. Other GPUs, jobs and the model daemon are not stopped or restarted.
- Model weights are unloaded after a live document (30-second keep-alive fallback). Cached viewing, approval and download run in a separate lane and do not load the model.

This is application-level admission and time control, not a physical GPU partition or a guarantee of zero host contention. GPU occupancy is rechecked before each model call. If another job begins between checks, the project cannot preempt or manage it. For an event requiring strict hardware isolation, leave live inference paused and serve the prepared examples only, or reserve a GPU through the server's normal scheduler.

Prepare once, using the same guard and hourly budget: `python warm_event_cache.py --runtime ~/clinicaltext/runtime/sessions`. The deployed worker is `gpu_agent.py`; the optional standalone `demo_server.py` is a development path with separate limits.
