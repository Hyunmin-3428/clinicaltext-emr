# Third-party notices

Project-authored files use the root MIT license. The following bundled source retains upstream notices and license terms; the root license does not override them.

| Component | Source | Included license / integrity |
| --- | --- | --- |
| ko-pii | https://github.com/Marker-Inc-Korea/ko-pii | `ClinicalTextDeid/vendor/ko-pii/LICENSE`, `MANIFEST.json` |
| local-llm-deid | https://github.com/JamesWeatherhead/local-llm-deid | `ClinicalTextWeb/vendor/local-llm-deid/LICENSE`, adjacent manifest |

Installed dependencies (Presidio, spaCy, cryptography and transitive dependencies), the spaCy English model, Ollama, cloudflared and Qwen3 have their own licenses. Their binaries/model weights are not redistributed in this source repository. RUPTA and KatherLab/deidentifier are conceptual references; no KatherLab AGPL implementation is bundled.

Synthetic people, contact information and clinical stories in fixtures are authored examples, not patient records. Some university, event and clinical term names intentionally serve as contextual identification examples.
