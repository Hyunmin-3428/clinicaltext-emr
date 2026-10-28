# Long synthetic example

This separate example uses a generated 7,200-codepoint note to exercise the
3,500-codepoint windows and 400-codepoint overlap. It does not change the original
three-note fixture or its expected scores. All text is invented.

```bash
python3 fixtures/long_synthetic/build_fixture.py --out-dir out/long-fixture

PYTHONPATH=src python3 -m deid.run_model \
    --model-id long-stub --notes-dir out/long-fixture/notes \
    --out-dir out/long-example/predictions \
    --offline-stub --name-file out/long-fixture/name_gazetteer.txt

PYTHONPATH=src python3 -m deid.metrics \
    --pred-dir out/long-example/predictions \
    --gold-dir out/long-fixture/gold --out-dir out/long-example
```

Use a fresh output directory, or explicitly replace the stub output with
`--overwrite-model-output` on a repeat run.

The reference includes four spans, containing 29 characters. `Dana Kim` crosses
the first window's end at 3,500; `01/02/2021` crosses the second window's end at
6,600. Each is available in full in the next overlapping window. The note also
contains non-ASCII text, so byte positions differ from the codepoint offsets.

`May` occurs once as a clinician's surname and once in `May improve with rest.`
The second occurrence is not a reference identifier. Literal propagation removes
it anyway after `May` is accepted as a name. The offline stub therefore covers
all 29 reference characters but also removes 3 nonreference characters:
character recall is 1.0 and precision is 29/32. This illustrates why finding text
in the input does not confirm that every matching occurrence is identifying.

The regression tests also use a staged mock response that withholds `Rae Cole`
until Pass 2. They check that its coordinates map back correctly after earlier
redactions, that Pass 1 findings persist, and that the segment plans are written
before the requests. Staged responses are test cases, not model performance data.

```bash
python3 -m unittest discover -s tests -p test_run_records.py -v
```
