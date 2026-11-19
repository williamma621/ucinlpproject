# Call analytics pipeline

Turn customer-service transcripts into validated labels and queryable records.
This is a public sample of a call-labeling workflow, rebuilt around fictional data.
It runs locally or in Google Colab without API credentials.

**Quick start (Python 3.10+):** download and extract the ZIP, open a terminal in
`call-analytics-sample`, and run:

```sh
python -m call_pipeline demo
python -m unittest discover -s tests -v
```

No installation is needed for the offline demo. It processes 16 fictional calls
and writes a SQLite database, JSON/CSV labels, and an evaluation report to `outputs/`.
Run the same command again: all 16 completed calls should be reused.

**Prefer a notebook?** Open [notebooks/demo.ipynb](notebooks/demo.ipynb).
Its saved outputs show the redaction, labeling results, SQL analysis, and two
example disagreements without running any code.

## What the demo does

The original project involved transcribing company calls and classifying the
resulting text for analysis. This sample focuses on the transcript-to-label stage:

1. Read JSONL records and reject duplicate IDs or malformed inputs.
2. Replace recognized identifiers locally with placeholders.
3. Obtain structured labels from a fixture or, optionally, Gemini.
4. Validate labels, redact notes, and checkpoint each result in SQLite.
5. Export results and compare selected fields against synthetic reference labels.

For example, the synthetic input `My name is Morgan Vale ... 202-555-0142`
becomes `My name is [PERSON_1] ... [PHONE_1]` before the provider is called.
One sample result has `primary_intent=tax_debt_help` and
`call_outcome=consultation_scheduled`. These are different questions: what the
customer wanted, and what happened by the end of the call.

### Data provenance

All calls, names, company details, and reference labels were authored for this
sample with AI assistance. They are not customer records. `Cedar Tax Support` is
a fictional company. The SSN-like example is deliberately invalid; email addresses
use `example.com` and phone examples use the fictional 555-01xx range.

**The bundled model responses are hand-authored fixtures, not recorded Gemini
output.** Two intentional mistakes exercise the evaluation report. Their scores
demonstrate the evaluation code, not model accuracy. No live model comparison
results are claimed. The live workflow can save actual responses for later replay.

The original notebook's external taxonomy files were not available, so this
sample defines its own documented taxonomy in `schemas.py`. It retains the
original categories but is not a verbatim reproduction of the business rules.
Module organization, redaction, persistence, evaluation, and tests were added
for this public sample with AI assistance. Tests and limitations are included
alongside the implementation.

## Run in Google Colab

1. Extract the ZIP on your computer.
2. Visit [Google Colab](https://colab.research.google.com/) and use **File → Upload
   notebook** to upload `notebooks/demo.ipynb`.
3. Choose **Runtime → Run all**. When the first cell prompts, upload the original
   `call-analytics-sample.zip` so Colab has the Python modules and data as well.
4. Leave `RUN_LIVE = False`. The entire demo runs without credentials or API calls.

No GitHub repository URL is assumed. After publishing this folder to your own
repository, you can add a Colab badge that points to its real notebook URL.
On a local Jupyter installation, open the notebook from inside this folder.

## Read the code

| File | Responsibility |
| --- | --- |
| `call_pipeline/pipeline.py` | Processing order, retries, resuming, and export |
| `call_pipeline/redaction.py` | Local identifier detection and placeholders |
| `call_pipeline/labeling.py` | Fixture and Gemini providers |
| `call_pipeline/schemas.py` | Label meanings, JSON schema, local validation |
| `call_pipeline/storage.py` | SQLite schema and per-call transactions |
| `call_pipeline/evaluation.py` | Reference matching, coverage, and disagreements |
| `call_pipeline/prompts/` | Baseline, explicit-rule, and example-based prompts |
| `call_pipeline/data/` | Synthetic calls, reference labels, and fixture responses |
| `tests/test_pipeline.py` | Failure handling, privacy boundaries, reruns, and evaluation |

Start with `run_pipeline()` to follow one call through the system. The core uses
the Python standard library. Only live labeling needs an external package.

## Live Gemini labeling (optional)

Live runs send redacted transcripts to Google and may incur charges. Use only
the included fictional data until you have reviewed the redaction limitations.

```sh
python -m pip install ".[live]"
```

Set `GEMINI_API_KEY` privately and set `GEMINI_MODEL` to a model available to your
account that supports structured JSON output. No model ID is hardcoded because
availability changes. The notebook uses a hidden input prompt for the key.
`.env.example` documents the variable names; the application does not load `.env`
files automatically. Do not paste keys into source files, notebook cells, or the README.

```sh
python -m call_pipeline live --output outputs/live --record
```

`--record` writes validated, redacted responses to
`outputs/live/recorded_responses.json`. You can replay them without an API key:

```sh
python -m call_pipeline demo --fixtures outputs/live/recorded_responses.json --output outputs/replay
```

Replay verifies the prompt hash and redacted input hash, so editing a transcript
or prompt cannot silently reuse an unrelated response. Review a recording before
publishing it, even when the inputs were fictional. Failed responses are excluded.

The adapter follows the [Google Gen AI SDK](https://googleapis.github.io/python-genai/)
structured-output interface. Live API calls were not executed during preparation;
adapter tests use a fake client. The SDK could not be installed in the preparation
environment because package downloads were unavailable. The dependency range is
therefore a compatibility target, not a tested lockfile.

## Compare prompts

```sh
python -m call_pipeline compare --output outputs/comparison --record
```

This is a **live** command: it requests all 16 calls with each of three prompts
(48 logical requests before retries). Use a separate output directory for a fresh
experiment. Rerunning against the same database reuses successful results.

| Prompt | Change being examined |
| --- | --- |
| `baseline` | Cleaned-up version of the original category-by-category approach |
| `explicit` | More precise rules for appointment offers, stop-contact requests, and ambiguity |
| `few_shot` | Separate development examples showing how to apply the labels |

The report measures accuracy on four fields, reference coverage, valid response
rate, latency for newly processed calls, available token counts, and individual
disagreements. It also reports accuracy with failures counted as incorrect, so
failed calls cannot inflate the score simply by disappearing from the denominator.
Token totals cover the final returned attempt, not all potentially billable retry
activity; they are not a billing estimate. Cached calls are excluded from fresh
latency and token totals.

The 16 cases are a small authored evaluation set, not an independently annotated
benchmark. Prompt examples are separate from those calls, but the rules and cases
were designed together. Treat this as a development evaluation, not evidence of
generalization. Model confidence is self-reported and is not calibrated accuracy.

To use another synthetic dataset:

```sh
python -m call_pipeline live --input path/to/calls.jsonl --references path/to/references.json --output outputs/custom
```

Each JSONL record must contain exactly `call_id` and `transcript`. Use opaque IDs,
never customer names or phone numbers. Reference JSON has a top-level `labels`
object keyed by call ID, with the four fields listed in `EVALUATED_FIELDS`.

## Reliability choices

- **Explicit IDs:** every provider result stays attached to the call that produced
  it. Evaluation and SQL joins use IDs, not the position of a response in a list.
- **Idempotency:** a work key includes the call ID, source hash, prompt/schema hash,
  redactor version, pipeline version, and provider identity. Successful work is reused.
- **Per-call checkpoints:** one failed request does not discard other results.
  A rerun retries unfinished or failed calls and preserves prior failed observations.
- **Bounded retries:** transient HTTP and network failures get at most three attempts
  with backoff and jitter. Invalid JSON and permanent errors are not blindly retried.
- **Validation:** required fields, enum membership, note length, confidence bounds,
  duplicate JSON keys, and contradictory primary/secondary intents are checked locally.
- **Minimal logging:** provider error bodies and raw model responses are not saved.
  Error codes identify the failure category without copying request content.

This is a sequential batch processor, not a distributed service or a Gemini Batch
API implementation. SQLite keeps review setup small. A crash after an API response
but before its database commit can still cause a repeat request and charge on a
rerun. Concurrent workers and exactly-once remote execution are outside this sample.

## Privacy boundary and limits

The redactor handles common email and US phone formats, SSN-like patterns,
contextual account IDs, labeled numeric dates of birth, street addresses, and
names introduced by phrases such as `My name is`. Repeated values get consistent
placeholders within each call. Only aggregate detection counts are persisted;
the temporary replacement map is discarded.

It **will miss** some names, city/ZIP fragments, international formats, spoken-out
identifiers, and personal context. It may also over-redact. Tests document both
supported cases and known misses. This is a demonstration of where redaction
belongs, not a guarantee of anonymization or regulatory compliance. Model notes
are checked by the same imperfect redactor.

Original input stays on disk; raw transcript hashes are stored for cache invalidation.
The database stores redacted text and labels, which can still be sensitive if the
detector misses information. These files are not encrypted by this sample.
The default public data is entirely fictional.

Audio transcription is upstream of this sample. If audio is sent to a cloud
transcription provider, transcript redaction happens **after** that provider has
received the audio. No audio, cloud bucket credentials, or real customer data
is bundled here. A next extension would add fictional audio and a separate
transcription adapter, keeping this boundary explicit.

## Verification and possible next steps

Run `python -m unittest discover -s tests -v`. The tests exercise shuffled inputs,
interruption/resume, failed-call isolation, retry limits, schema errors, PII checks,
record/replay, and CSV formula escaping. A fixture test blocks socket creation to
verify the offline path does not access the network.

Useful next improvements are independent review of reference labels, a larger
held-out evaluation set, local entity recognition for names and addresses, and
a bounded worker pool for higher throughput. Before adding cloud orchestration,
measure where processing time is actually spent.

To build a clean submission ZIP from this folder:

```sh
python scripts/package_sample.py
```

The packager includes only the public source, synthetic data, tests, docs, and
notebook. Runtime outputs, credentials, caches, and the original private notebook
are excluded. A detected Google API key or private-key block stops packaging.

