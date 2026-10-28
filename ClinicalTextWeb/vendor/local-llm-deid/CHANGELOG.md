# Changelog

## Unreleased

* Shorten the README and separate usage, annotation, checkpoint, analysis, and provenance documentation.
* Clarify the relationship to the study software, actual saved outputs, exact-string propagation, and conditional response-format metrics.
* Update model downloads to the Hugging Face `hf` command.
* Record run settings, code and protocol hashes, and available checkpoint/server provenance for new executions.
* Save expected segment plans before requests and check record coverage when reporting operational completeness. Legacy outputs without plans retain accuracy scoring but have unknown expected request counts and unverified coverage.
* Add a separate long synthetic example and regression tests for boundaries, second-pass mapping, non-identifying string matches, and missing request records.

The frozen protocol, study results, and cited `v1.0.1` tag are unchanged. No clinical inference or reported study analyses were rerun for this update.

## 1.0.1 - 2026-08-25

Reliability and reproducibility release:

* makes the gold set, rather than produced output, define the scoring denominator;
* rejects stale/extra documents and malformed or mismatched prediction artifacts;
* fixes conditional validation denominators and separates format usability from finish reason;
* fails closed on reused model-output directories and malformed server responses;
* strips persisted model text by default, uses private output permissions, and requires explicit acknowledgement for raw-content retention or non-loopback endpoints;
* reproduces the manuscript's three-execution bootstrap preparation for recall and completeness;
* aligns Gemma-4 E2B/E4B checkpoint metadata and the resolving E2B GGUF repository with the manuscript; and
* pins GitHub Actions dependencies and expands the regression suite.

No study inference or analysis results were rerun or changed in this release.
