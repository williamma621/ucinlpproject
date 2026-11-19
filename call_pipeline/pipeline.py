"""Read -> redact -> label -> validate -> checkpoint. No pandas row alignment."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import time
import uuid

from .labeling import ProviderError
from .prompting import prompt_hash
from .redaction import VERSION, redact
from .schemas import ValidationError, parse_labels
from .storage import Store, now

DATA = Path(__file__).parent / "data"
PIPELINE_VERSION = "1"


def load_calls(path=None):
    calls, seen = [], set()
    for number, line in enumerate(Path(path or DATA / "calls.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON at input line {number}") from None
        if not isinstance(row, dict) or set(row) != {"call_id", "transcript"}:
            raise ValueError(f"Expected call_id and transcript at input line {number}")
        call_id = row["call_id"]
        if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", call_id):
            raise ValueError(f"Use an opaque call_id at input line {number}")
        if call_id in seen:
            raise ValueError(f"Duplicate call_id at input line {number}")
        if not isinstance(row["transcript"], str) or not row["transcript"].strip():
            raise ValueError(f"Empty transcript at input line {number}")
        if len(row["transcript"]) > 100_000:
            raise ValueError(f"Transcript exceeds sample size limit at input line {number}")
        seen.add(call_id)
        calls.append(row)
    if not calls:
        raise ValueError("Input contains no calls")
    return calls


@dataclass
class Run:
    run_id: str
    summary: dict
    rows: list


def run_pipeline(calls, provider, db="outputs/calls.db", prompt="explicit", max_attempts=3,
                 sleep=time.sleep):
    if not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5")
    ids = [call["call_id"] for call in calls]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate call IDs are not allowed")
    digest = prompt_hash(prompt)
    config = {"mode": provider.mode, "model": provider.model, "prompt": prompt,
              "prompt_hash": digest, "redactor_version": VERSION,
              "pipeline_version": PIPELINE_VERSION, "provider": provider.identity,
              "provenance": provider.provenance}
    run_id = uuid.uuid4().hex
    summary = {"total": len(calls), "succeeded": 0, "failed": 0, "cached": 0}
    store = Store(db)
    try:
        store.start(run_id, config)
        for call in calls:
            cleaned = redact(call["transcript"])
            input_hash = sha256(call["transcript"].encode()).hexdigest()
            work_key = sha256(json.dumps([call["call_id"], input_hash, config], sort_keys=True).encode()).hexdigest()
            existing = store.cached(work_key)
            if existing:
                store.record(run_id, existing, cached=True)
                summary["cached"] += 1
                summary["succeeded"] += 1
                continue
            labels, usage, error, attempts = None, {}, None, 0
            started = time.perf_counter()
            for attempt in range(1, max_attempts + 1):
                attempts = attempt
                try:
                    response = provider.generate(call["call_id"], cleaned.text, prompt)
                    usage = response.usage
                    labels = parse_labels(response.text)
                    # Never store raw model output. Apply the same local check to notes.
                    labels["labeling_notes"] = redact(labels["labeling_notes"]).text[:300]
                    error = None
                    break
                except ValidationError:
                    error = "invalid_response"
                    break  # A schema error is not a transient network failure.
                except ProviderError as exc:
                    error = exc.code
                    if not exc.retryable or attempt == max_attempts:
                        break
                    sleep(min(2 ** (attempt - 1) + random.uniform(0, 0.25), 8))
            status = "success" if labels is not None else "failed"
            # Keep failed observations immutable. A later retry must not rewrite
            # an earlier run's evidence or its evaluation denominator.
            if status == "failed":
                work_key += "-failed-" + run_id
            summary["succeeded" if status == "success" else "failed"] += 1
            row = {
                "work_key": work_key, "call_id": call["call_id"], "input_hash": input_hash,
                "mode": provider.mode, "model": provider.model, "prompt": prompt,
                "prompt_hash": digest, "redactor_version": VERSION, "status": status,
                "attempts": attempts, "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "redacted_text": cleaned.text, "pii_counts_json": json.dumps(cleaned.counts),
                "labels_json": json.dumps(labels) if labels else None,
                "usage_json": json.dumps(usage), "error_code": error, "updated_at": now(),
            }
            store.record(run_id, row)
        store.finish(run_id, summary)
        return Run(run_id, summary, store.rows(run_id))
    finally:
        store.close()


def export_rows(run, directory):
    import csv
    from .schemas import FIELDS
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for row in run.rows:
        labels = json.loads(row["labels_json"]) if row["labels_json"] else {}
        rows.append({
            "call_id": row["call_id"], "status": row["status"], "mode": row["mode"],
            "model": row["model"], "prompt": row["prompt"], "cached": bool(row["cached"]),
            "pii_counts": json.loads(row["pii_counts_json"]),
            "error_code": row["error_code"], **labels,
        })
    (directory / "labels.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    with (directory / "labels.csv").open("w", newline="", encoding="utf-8") as handle:
        columns = ["call_id", "status", "mode", "model", "prompt", "cached", "pii_counts", "error_code"] + FIELDS
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            # Prevent spreadsheet formulas in any model-generated text.
            safe = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
                    for k, v in row.items()}
            writer.writerow(safe)
    (directory / "run.json").write_text(json.dumps({"run_id": run.run_id, **run.summary}, indent=2) + "\n", encoding="utf-8")
    return rows

