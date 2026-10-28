# Data dictionary

The scorer writes `metrics_long.csv`, `per_doc_long.csv`, `model_manifest.csv`,
and `run_manifest.json`. Accuracy calculations read the reference annotations
and prediction offsets. Operational and response-format calculations also read
response telemetry, validation records, and available expected-segment plans.

Outputs omit identifier literals but can contain sensitive document IDs, hashes,
counts, and provenance. Keep them under the controls governing the source data.
In the legacy column names below, **PHI means the identifier characters marked in
the reference annotations**, not every potentially identifying fact in a note.
Zero residual reference characters does not establish anonymity.

Offsets are Unicode codepoints, half-open `[start, end)`, relative to the reference
`text`. An empty cell means undefined, not applicable, or unavailable; unknown
request coverage is not represented as zero requests.

## Correspondence to the manuscript

`char_recall`, `char_precision`, and `over_redaction_rate` are the manuscript's
character recall, character precision, and share of redacted characters outside
the reference spans. `relaxed_span_f1` is the reported boundary-tolerant span F1.
`zero_residual_note_rate` is annotation-based note-level completeness.

The manuscript reports `all_expected` accuracy, micro-averaged within each
execution and averaged across three separate locked stability executions. The
runner and scorer process one execution at a time. The `operational_complete`
cohort, macro averages, and `finish_stop_rate` are additional diagnostics.
Usable-response and strict-schema-valid rates on cumulative Pass 2 rows are
conditional on available Pass 2 validation records, not full-pipeline success.

Confidence intervals are computed separately by `scripts/bootstrap_ci.py` from
matched per-note outputs, with across-execution averaging before 10,000 note-level
resamples (seed `20260801`). See [the manuscript map](docs/MANUSCRIPT_MAP.md) and
[analysis instructions](docs/ANALYSIS.md).

## `metrics_long.csv` (70 columns)

One row per model, pass, cohort, and reference category. The `ALL` row contains
note-wide metrics. Per-category rows contain recall/coverage, type-matched
precision, and span recall. Their note-level, operational, macro, and
type-agnostic precision fields are blank.

### Model identity and metadata

Optional metadata come from `model_config.json` beside the predictions. A run
label does not verify the checkpoint loaded by the server.

| Column | Meaning |
| --- | --- |
| `model_id` | Run label, also the model directory name. |
| `hf_link` | Hugging Face link derived from `hf_gguf_repo`. |
| `hf_gguf_repo` | Recorded GGUF repository. |
| `developer` | Recorded model developer. |
| `family` | Model family. |
| `params_total_B` | Total parameters in billions. |
| `params_effective_B` | Effective parameters where distinguished from the total, such as Gemma-4 E2B/E4B. |
| `params_active_B` | Active parameters per token for mixture-of-experts models. |
| `moe` | Whether the recorded architecture is mixture-of-experts. |
| `is_medical` | Whether the model is recorded as medically domain-adapted. |
| `is_reasoning` | Whether the configuration labels it a dedicated reasoning model. |
| `quant` | Recorded checkpoint quantization, such as `Q5_K_M`. |
| `n_docs` | Number of notes in the reference set, including notes with missing predictions. |
| `doc_provenance` | Reserved provenance label; currently blank. |

### Row keys

| Column | Meaning |
| --- | --- |
| `pass` | `pass1` or `cumulative_pass2`, the union of Pass 1 and applied Pass 2 predictions. |
| `surface` | Always `final`: the predicted coverage mask. It does not imply a saved redacted text file. |
| `cohort` | `all_expected`: every reference note. `operational_complete`: only passes with verified record coverage and a `stop`, usable response for every expected segment. |
| `phi_type` | `ALL`, or a study category such as `01_NAME`. The 18-category schema is based on HIPAA Safe Harbor with study-specific conventions. |

### Totals

| Column | Meaning |
| --- | --- |
| `n_notes` | Number of notes in this pass/cohort. |
| `doc_chars_total` | Total source characters in those notes. |
| `gt_phi_chars` | Reference-identifier character count. |
| `pred_phi_chars` | Characters covered by predicted redactions. |
| `gt_span_count` | Reference identifier spans. |
| `pred_span_count` | Unique predicted start/end intervals, ignoring type. |

### Type-agnostic character metrics

A reference character counts as covered by any redaction, regardless of the
predicted type. Overlapping intervals do not count a character more than once.

| Column | Meaning |
| --- | --- |
| `char_tp` | Reference characters covered by a redaction. |
| `char_fp` | Redacted characters outside the reference spans. |
| `char_fn` | Reference characters not covered; equals `residual_phi_chars`. |
| `char_tn` | Characters outside reference spans left intact. |
| `char_recall` | `char_tp / gt_phi_chars`. |
| `char_precision` | `char_tp / pred_phi_chars`. |
| `char_f1` | Harmonic mean of character precision and recall. |
| `char_specificity` | `char_tn / (char_tn + char_fp)`. |
| `over_redaction_rate` | `char_fp / pred_phi_chars`, or `1 - char_precision`; not `FP/(FP+TN)`. |
| `residual_phi_chars` | Uncovered reference characters. |
| `residual_phi_char_rate` | `residual_phi_chars / gt_phi_chars`, or `1 - char_recall`. |

### Type-matched character metrics

Per-category matches require agreement with the reference category's two-digit
code. On the `ALL` row these three fields mirror the type-agnostic metrics; they
are not a separate overall classification score.

| Column | Meaning |
| --- | --- |
| `char_recall_typematched` | Reference characters covered by a same-type redaction / reference characters of that type. |
| `char_precision_typematched` | Same-type-covered reference characters / characters predicted as that type, within the contributing notes. |
| `char_f1_typematched` | Harmonic mean of the two type-matched rates. |

### Span metrics

Any-overlap and full-coverage metrics measure removal of reference characters.
Strict and relaxed metrics measure boundary agreement. Both ignore identifier
type. Relaxed matching allows each endpoint to differ by at most two codepoints.
Matches are non-exclusive: one span may qualify more than one nearby span on the
other side. These are not one-to-one entity-assignment scores.

| Column | Meaning |
| --- | --- |
| `any_span_tp` | Reference spans with at least one character covered. |
| `any_span_recall` | `any_span_tp / gt_span_count`. |
| `full_span_tp` | Reference spans with every character covered. |
| `full_span_recall` | `full_span_tp / gt_span_count`. |
| `strict_span_tp` | Reference spans with an exact predicted start/end match. |
| `strict_span_recall` | `strict_span_tp / gt_span_count`. |
| `strict_span_precision` | `strict_span_tp / pred_span_count`, as implemented. |
| `strict_span_f1` | Harmonic mean of strict precision and recall. |
| `relaxed_span_tp` | Reference spans matched within two codepoints at both endpoints. |
| `relaxed_span_recall` | `relaxed_span_tp / gt_span_count`. |
| `relaxed_span_precision` | Predicted spans within two codepoints of some reference span / `pred_span_count`. |
| `relaxed_span_f1` | Harmonic mean of relaxed precision and recall. |

### Note-level metrics (`ALL` rows)

| Column | Meaning |
| --- | --- |
| `zero_residual_notes` | Notes with no remaining annotated identifier characters. |
| `zero_residual_note_rate` | `zero_residual_notes / n_notes`. |
| `notes_with_leak` | Notes with at least one uncovered reference character. |
| `notes_with_leak_rate` | `notes_with_leak / n_notes`. |
| `mean_residual_per_note` | Mean uncovered reference characters per note. |
| `median_residual_per_note` | Median uncovered reference characters per note. |
| `max_residual_per_note` | Largest uncovered-reference-character count in one note. |

### Operational and response-format metrics (`ALL` rows)

| Column | Meaning |
| --- | --- |
| `expected_requests` | Sum of independently planned segments, not surviving response files. Blank if any included note lacks a plan for that pass. |
| `usable_requests` | Usable responses among available validation records. |
| `usable_rate` | `usable_requests /` available validation records. |
| `json_valid_requests` | Strict-schema-valid responses among available validation records. |
| `json_valid_rate` | `json_valid_requests /` available validation records. |
| `finish_stop_rate` | Observed `stop` responses / known `expected_requests`; blank when the expected count is unknown. |
| `prompt_tokens` | Sum of recorded prompt tokens in parseable response artifacts. |
| `completion_tokens` | Sum of recorded completion tokens in parseable response artifacts. |

The cumulative Pass 2 rows describe **Pass 2 requests**, not the combined token
cost or request-success rate of both passes. Missing response or validation
records prevent verified operational completeness but do not erase saved
predictions. Conditional format rates may remain 1.0 despite missing records.
Unknown token usage is not recovered by these sums. The stub's token fields are
simple word-count stand-ins, not measured model tokenization or inference cost.

### Macro averages (`ALL` rows)

Micro-averaging pools counts before computing each rate. Macro fields are means
of per-note rates, skipping notes whose relevant denominator is zero.

| Column | Meaning |
| --- | --- |
| `char_recall_macro` | Mean per-note character recall. |
| `char_precision_macro` | Mean per-note character precision. |
| `char_f1_macro` | Mean per-note character F1. |
| `strict_span_recall_macro` | Mean per-note strict-span recall. |
| `relaxed_span_recall_macro` | Mean per-note relaxed-span recall. |

## `per_doc_long.csv`

One row per model, note, and pass, including all reference notes.

| Column | Meaning |
| --- | --- |
| `model_id`, `n_docs` | As above. |
| `doc` | Note ID. |
| `pass_` | `pass1` or `cumulative_pass2`. |
| `doc_chars` | Source-note length in codepoints. |
| `gt_phi_chars`, `pred_phi_chars` | Reference and predicted character counts. |
| `char_tp`, `char_fp`, `char_fn` | Per-note confusion counts. |
| `residual_phi_chars` | Uncovered reference characters. |
| `zero_residual` | 1 when no reference character remains uncovered; otherwise 0. |
| `gt_spans`, `any_span_hits` | Reference spans and spans with at least one character covered. |
| `n_segments` | Observed parseable response-artifact count for this pass. |
| `usable_segments` | Usable responses in available validation records. |
| `finish_stop` | Observed responses with finish reason `stop`. |
| `expected_segments` | Independently planned count; blank for missing or legacy plans. |
| `segment_coverage_verified` | 1 when a valid plan and matching, consistent response/validation record sets exist; otherwise 0. |
| `operational_complete` | 1 when coverage is verified and every expected segment has a `stop`, usable response; otherwise 0. |

`expected_segments` and `segment_coverage_verified` were added after the cited
`v1.0.1` snapshot. Older outputs remain scoreable for accuracy, but without a
plan they cannot establish operational completeness. They are excluded from that
diagnostic cohort; the `all_expected` accuracy denominator is unchanged.

## Model and run manifests

`model_manifest.csv` contains the model metadata plus `gold_types_present`
(category labels joined by `;`) and `model_dir` (the output subdirectory).

`run_manifest.json` describes scoring: package version, `scorer_sha256`, Python
and platform, reference-directory basename, offset units, metric definitions,
cohorts, and output counts. The scorer hash distinguishes source revisions even
when the last released package version has not changed.

The runner separately writes `inference_manifest.json` inside each model's
output. It records actual run settings, source/protocol hashes, and available
checkpoint/server provenance. Per-pass `expected_segments/<doc>/manifest.json`
files record the pre-request segment plans without literal note text. Full
provenance and legacy-compatibility details are in
[Reproducibility](docs/REPRODUCIBILITY.md).
