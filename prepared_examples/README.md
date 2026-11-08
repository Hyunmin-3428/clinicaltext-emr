# Prepared synthetic examples

These two files contain actual earlier Qwen3:30b runs on the project's authored fictional postoperative records, not real patient data. The original inputs are already public fixtures in `ClinicalTextWeb/llm_service.py`.

`provenance.json` records original run timestamps, model digest, implementation fingerprint, and cache keys. `PreparedCache.get()` checks the expected input/purpose/model key and revalidates clinical evidence on each use. Changing the bound clinical implementation invalidates the bundle. No model inference or approval is fabricated, and no previous approval/signature is reused.

The model's own validation and selected code checks can miss errors. These fixtures are development demonstrations, not a clinical accuracy benchmark or a guarantee of anonymity. New synthetic notes require an explicitly connected local model and are held outside the first registered study scope.

`release_grades.json` records separate Qwen3:30b grade recommendations for the seven public synthetic examples. A recommendation is matched to the transformed text and research purpose. The server's project policy determines whether O-grade file generation is available.
