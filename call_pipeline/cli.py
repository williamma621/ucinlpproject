import argparse
import json
from pathlib import Path

from .evaluation import evaluate, write_report
from .labeling import FixtureProvider, GeminiProvider
from .pipeline import DATA, export_rows, load_calls, run_pipeline
from .prompting import PROMPTS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic call analytics; offline by default")
    parser.add_argument("command", choices=["demo", "live", "compare"])
    parser.add_argument("--input", type=Path, default=DATA / "calls.jsonl")
    parser.add_argument("--references", type=Path, default=DATA / "reference_labels.json")
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--model", help="Live model ID available to your Gemini account")
    parser.add_argument("--prompt", choices=PROMPTS, default="explicit")
    parser.add_argument("--record", action="store_true", help="Save redacted, validated live responses for replay")
    parser.add_argument("--fixtures", type=Path, help="Replay a recorded response file in demo mode")
    args = parser.parse_args(argv)
    if args.command == "demo" and ((args.prompt != "explicit" and not args.fixtures) or args.record):
        parser.error("The bundled demo uses explicit fixtures; --record is for live mode")
    provider = None
    try:
        calls = load_calls(args.input)
        provider = FixtureProvider(args.fixtures) if args.command == "demo" else GeminiProvider(args.model)
        prompts = PROMPTS if args.command == "compare" else [args.prompt]
        failed, reports = False, {}
        for prompt in prompts:
            destination = args.output / prompt if args.command == "compare" else args.output
            run = run_pipeline(calls, provider, db=args.output / "calls.db", prompt=prompt)
            export_rows(run, destination)
            report = evaluate(run, args.references)
            write_report(report, destination / "evaluation.json")
            reports[prompt] = report
            if args.record:
                record_responses(run, destination / "recorded_responses.json")
            failed |= bool(run.summary["failed"])
            print(json.dumps({"prompt": prompt, "mode": provider.mode, **run.summary}, indent=2))
        if args.command == "compare":
            write_report(reports, args.output / "comparison.json")
        print(f"Results: {args.output.resolve()}")
        if provider.mode == "fixture":
            print(provider.provenance)
        return 1 if failed else 0
    except (ValueError, OSError) as exc:
        # Never print exception messages that might contain source text or credentials.
        print(f"Could not run ({type(exc).__name__}). Check input files, model, credentials, and optional dependencies.")
        return 2
    finally:
        if provider is not None:
            provider.close()


def record_responses(run, path):
    from hashlib import sha256
    document = {
        "provenance": ("Recorded Gemini responses; validated and notes redacted after generation"
                       if run.rows[0]["mode"] == "live" else "Replayed hand-authored fixtures; not Gemini output"),
        "prompt": run.rows[0]["prompt"], "model": run.rows[0]["model"],
        "prompt_hash": run.rows[0]["prompt_hash"], "responses": {},
    }
    for row in run.rows:
        if row["status"] == "success":
            document["responses"][row["call_id"]] = {
                "redacted_sha256": sha256(row["redacted_text"].encode()).hexdigest(),
                "labels": json.loads(row["labels_json"]), "recorded_at": row["updated_at"],
            }
    write_report(document, path)

