"""Small, transparent checks against synthetic reference labels."""

import json
from pathlib import Path
import statistics

from .pipeline import DATA
from .schemas import EVALUATED_FIELDS


def evaluate(run, references=None):
    reference = json.loads(Path(references or DATA / "reference_labels.json").read_text(encoding="utf-8"))
    expected = reference["labels"]
    matched = [r for r in run.rows if r["call_id"] in expected]
    scored = [r for r in matched if r["status"] == "success"]
    differences, scores = [], {}
    for field in EVALUATED_FIELDS:
        correct = 0
        for row in scored:
            predicted = json.loads(row["labels_json"])[field]
            truth = expected[row["call_id"]][field]
            correct += predicted == truth
            if predicted != truth:
                differences.append({"call_id": row["call_id"], "field": field,
                                    "expected": truth, "predicted": predicted})
        scores[field] = {
            "correct": correct, "scored": len(scored),
            "accuracy_on_valid": correct / len(scored) if scored else None,
            "accuracy_including_failures": correct / len(matched) if matched else None,
        }
    fresh = [r for r in run.rows if not r["cached"]]
    return {
        "mode": run.rows[0]["mode"] if run.rows else "unknown",
        "notice": "Synthetic reference set; fixture scores test the harness, not Gemini quality.",
        "total": len(run.rows), "reference_matched": len(matched), "scored": len(scored),
        "reference_coverage": len(matched) / len(run.rows) if run.rows else 0,
        "valid_response_rate": run.summary["succeeded"] / len(run.rows) if run.rows else 0,
        "invalid_response_count": sum(r["error_code"] == "invalid_response" for r in run.rows),
        "fresh_calls": len(fresh),
        "median_fresh_latency_ms": statistics.median(r["latency_ms"] for r in fresh) if fresh else None,
        "successful_attempt_input_tokens": sum(json.loads(r["usage_json"]).get("input_tokens") or 0 for r in fresh),
        "successful_attempt_output_tokens": sum(json.loads(r["usage_json"]).get("output_tokens") or 0 for r in fresh),
        "scores": scores, "disagreements": differences,
    }


def write_report(report, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

