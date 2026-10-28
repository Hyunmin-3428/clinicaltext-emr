# Synthetic fixture

The three short discharge-style notes in this directory were written for the
repository. All identifiers are invented; the notes are not derived from the
study corpus. They provide an execution example, not a clinical benchmark.

## Files

`notes/` contains `TEST001.txt`, `TEST002.txt`, and `TEST003.txt`. `gold/` contains
reference annotations in the PubAnnotation format: source `text`, typed
`denotations`, and `identifier_type` attributes. `name_gazetteer.txt` lists the
invented names recognized by the offline stub. `build_gold.py` rebuilds the
reference files from the notes and its explicit annotation list:

```bash
python3 fixtures/synthetic/build_gold.py
```

The builder annotates every occurrence of each listed literal. Keep the
annotation list and note text consistent when editing this fixture.

## Expected offline result

There are 26 reference spans containing 335 characters. TEST001 and TEST003
include facility and city names; TEST002 contains only categories targeted by
the stub. Each note fits into one default segment, so this fixture does not
exercise overlap. Use [the long example](../long_synthetic/README.md) for that.

The `StubExtractor` matches names from the gazetteer, dates, ages over 89,
telephone numbers, email addresses, and MRNs. It does not target geography.

| Metric, cumulative Pass 2, all three notes | Value |
| --- | --- |
| Character recall | 253/335, approximately 0.755 |
| Character precision | 1.000 on this fixture |
| Remaining annotated identifier characters | 82, all geographic |
| Notes with no remaining annotated identifier characters | 1 of 3, TEST002 |

Category-specific recall is 1.0 for each targeted category and 0.0 for geography.
`tests/test_end_to_end.py` checks these values. Precision is not guaranteed by
verbatim grounding: the long example shows that a grounded string can also occur
in a non-identifying context and be over-redacted there.

```bash
make selftest
```

For direct Python commands, see [Usage](../../docs/USAGE.md). The stub was not used
for the manuscript's LLM results, and a note with no remaining reference
characters is not thereby proven anonymous.
