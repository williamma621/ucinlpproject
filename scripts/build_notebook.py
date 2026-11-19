"""Build and execute the demo's plain Python cells using the standard library.

This records actual cell output locally, without a Jupyter dependency. It does
not test Colab's upload UI or execute the opt-in live API branch.
"""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def build():
    cells = []

    def add(kind, source):
        cell = {"cell_type": kind, "metadata": {}, "id": f"cell-{len(cells):02d}",
                "source": textwrap.dedent(source).strip().splitlines(keepends=True)}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    md = lambda source: add("markdown", source)
    code = lambda source: add("code", source)

    md("""
        # From call transcript to analytics

        This notebook walks through a small data pipeline: local redaction,
        structured labeling, validation, SQLite storage, and evaluation.

        **No credentials needed.** All 16 calls are fictional. The bundled responses
        are hand-authored fixtures, not Gemini output. Two deliberate mistakes help
        demonstrate error analysis. Saved outputs were produced by executing the
        Python cells locally; the Colab upload UI and optional live API path were
        not exercised during preparation.

        In Colab, run the first cell and upload `call-analytics-sample.zip` when asked.
        Locally, open this notebook from the extracted project folder.
    """)
    code('''
        from pathlib import Path
        import sys
        import os
        import json

        candidates = [Path.cwd(), Path.cwd().parent, Path.cwd() / "call-analytics-sample"]
        ROOT = next((p for p in candidates if (p / "call_pipeline" / "pipeline.py").is_file()), None)
        if ROOT is None:
            try:
                from google.colab import files
            except ImportError:
                raise RuntimeError("Open this notebook from the extracted call-analytics-sample folder.") from None
            import io
            import zipfile
            from pathlib import PurePosixPath
            print("Upload call-analytics-sample.zip (the complete project, not just the notebook).")
            uploads = files.upload()
            archives = [value for name, value in uploads.items() if name.endswith(".zip")]
            if len(archives) != 1:
                raise ValueError("Upload exactly one project ZIP.")
            with zipfile.ZipFile(io.BytesIO(archives[0])) as archive:
                if sum(item.file_size for item in archive.infolist()) > 10_000_000:
                    raise ValueError("Unexpectedly large sample archive.")
                for item in archive.infolist():
                    path = PurePosixPath(item.filename)
                    if (path.is_absolute() or ".." in path.parts or "\\\\" in item.filename
                            or not path.parts or path.parts[0] != "call-analytics-sample"):
                        raise ValueError("Unexpected path in archive.")
                archive.extractall(Path.cwd())
            ROOT = Path.cwd() / "call-analytics-sample"
        os.chdir(ROOT)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        print("Ready. The offline demo uses only the Python standard library.")
    ''')
    md("""
        ## 1. Inspect a fictional call

        Input is JSONL with an opaque `call_id` and a transcript. There are examples
        of scheduling, pricing, callbacks, stop-contact requests, missing audio,
        sensitive information, and a transcript trying to give the model instructions.
        No original customer audio or transcripts are included.
    """)
    code('''
        from call_pipeline.pipeline import load_calls
        calls = load_calls()
        print(f"Loaded {len(calls)} fictional calls.")
        print(calls[12]["call_id"])
        print(calls[12]["transcript"])
    ''')
    md("""
        ## 2. Redact locally before labeling

        The redactor replaces supported identifier formats and records counts by
        type. Repeated identifiers share a placeholder within the call. The original
        replacement map is not written to the database.

        Pattern matching has limits: unintroduced names, city/ZIP fragments, and
        spoken-out identifiers may remain. This is not a complete anonymizer.
        If transcription occurs in the cloud, the transcription provider has already
        received the audio before this stage runs.
    """)
    code('''
        from call_pipeline.redaction import redact
        redacted = redact(calls[12]["transcript"])
        print(redacted.text)
        print("Detected occurrences:", json.dumps(redacted.counts, sort_keys=True))
    ''')
    md("""
        ## 3. Process and checkpoint the calls

        `FixtureProvider` verifies the input and prompt hashes before returning an
        authored response. The live provider implements the same interface.
        Both paths use the same local schema validation and database writes.
        A fresh temporary directory keeps each notebook execution reproducible.
    """)
    code('''
        import tempfile
        from call_pipeline.labeling import FixtureProvider
        from call_pipeline.pipeline import run_pipeline, export_rows

        OUTPUT = Path(tempfile.mkdtemp(prefix="call-analytics-demo-"))
        provider = FixtureProvider()
        run = run_pipeline(calls, provider, db=OUTPUT / "calls.db")
        exported = export_rows(run, OUTPUT)
        print(provider.provenance)
        print(json.dumps(run.summary, indent=2))
        print("First validated result:")
        print(json.dumps(json.loads(run.rows[0]["labels_json"]), indent=2))
    ''')
    md("""
        ## 4. Query the results with SQL

        The database keeps run metadata, per-call results, and run membership separate.
        The query is scoped to this run, so prior prompt experiments cannot inflate
        the counts. The labels are stored as validated JSON in SQLite.
    """)
    code('''
        import sqlite3
        from contextlib import closing
        from html import escape

        def table(headers, rows):
            head = "".join("<th style='padding:8px;text-align:left'>" + escape(str(v)) + "</th>" for v in headers)
            body = "".join("<tr>" + "".join("<td style='padding:8px;border-top:1px solid #ddd'>" + escape(str(v)) + "</td>" for v in row) + "</tr>" for row in rows)
            display({"text/html": "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>"}, raw=True)

        with closing(sqlite3.connect(OUTPUT / "calls.db")) as connection:
            outcome_counts = connection.execute("""
                SELECT json_extract(r.labels_json, '$.call_outcome') AS outcome, COUNT(*)
                FROM run_items i JOIN results r USING(work_key)
                WHERE i.run_id=? AND r.status='success'
                GROUP BY outcome ORDER BY COUNT(*) DESC, outcome
            """, (run.run_id,)).fetchall()
        table(["Call outcome", "Calls"], outcome_counts)
    ''')
    md("""
        ## 5. Check a rerun

        Reverse the input order and rerun. The work key identifies each call and its
        processing configuration, so input order does not affect matching. Successful
        work is reused; a previous failure would be attempted again.
    """)
    code('''
        rerun = run_pipeline(list(reversed(calls)), provider, db=OUTPUT / "calls.db")
        print(json.dumps(rerun.summary, indent=2))
        assert rerun.summary["cached"] == len(calls)
        assert {row["call_id"]: row["labels_json"] for row in run.rows} == {
            row["call_id"]: row["labels_json"] for row in rerun.rows
        }
        print("All calls were matched correctly and reused.")
    ''')
    md("""
        ## 6. Inspect disagreements

        The reference set covers intent, outcome, sentiment, and the triage flag.
        Two fixture mistakes are intentional: treating an offered appointment as
        booked, and ignoring how a customer's sentiment changes during a call.
        These are examples for the evaluation harness, not observed Gemini errors.
        Failed calls count as incorrect in the second accuracy measure.
    """)
    code('''
        from call_pipeline.evaluation import evaluate, write_report
        evaluation = evaluate(run)
        write_report(evaluation, OUTPUT / "evaluation.json")
        table(["Field", "Correct", "Scored", "Accuracy incl. failures"], [
            [field, score["correct"], score["scored"], f"{score['accuracy_including_failures']:.1%}"]
            for field, score in evaluation["scores"].items()
        ])
        table(["Call", "Field", "Reference", "Fixture"], [
            [d["call_id"], d["field"], d["expected"], d["predicted"]]
            for d in evaluation["disagreements"]
        ])
        print(evaluation["notice"])
    ''')
    md("""
        ## 7. Run the tests

        Tests cover interrupted processing, bounded retries, malformed responses,
        failed-call isolation, input reordering, redaction, and recorded-response replay.
        One test blocks socket creation to verify the offline path needs no network.
    """)
    code('''
        import io
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        test_log = io.StringIO()
        result = unittest.TextTestRunner(stream=test_log, verbosity=1).run(suite)
        print(test_log.getvalue())
        assert result.wasSuccessful(), "A test failed; inspect the output above."
    ''')
    md("""
        ## 8. Optional: generate actual responses and compare prompts

        Leave the switch off for an entirely offline demo. Turning it on installs
        Google's SDK, asks privately for your own API key, and sends the **fictional,
        redacted** calls to Gemini. You may incur charges.

        `COMPARE_PROMPTS = True` makes 48 logical requests before retries: 16 calls
        with each of `baseline`, `explicit`, and `few_shot`. The report includes
        accuracy, coverage, invalid responses, fresh-call latency, token metadata,
        and disagreements. This small development set cannot establish real-world
        accuracy. No prompt variant is claimed to be best until a live run is evaluated.
    """)
    code('''
        RUN_LIVE = False
        COMPARE_PROMPTS = False

        if RUN_LIVE:
            import subprocess
            import getpass
            from call_pipeline.cli import main
            subprocess.check_call([sys.executable, "-m", "pip", "install", ".[live]"])
            had_key = "GEMINI_API_KEY" in os.environ
            if not had_key:
                os.environ["GEMINI_API_KEY"] = getpass.getpass("Your Gemini API key (hidden): ")
            model = input("Model ID available to your Gemini account: ").strip()
            try:
                command = "compare" if COMPARE_PROMPTS else "live"
                exit_code = main([command, "--model", model, "--output", str(OUTPUT / "live"), "--record"])
                if exit_code:
                    print("Some work failed. Inspect error_code in the exported labels and rerun after resolving it.")
            finally:
                if not had_key:
                    os.environ.pop("GEMINI_API_KEY", None)
        else:
            print("Live requests are disabled. No API key was read or sent.")
    ''')
    md("""
        ## 9. Download this run's outputs

        The output ZIP contains the database, labels, and evaluation for this session.
        These default results use fictional calls and authored fixtures. To share
        source code, use the original project ZIP instead.
    """)
    code('''
        DOWNLOAD_OUTPUTS = False
        if DOWNLOAD_OUTPUTS:
            import shutil
            archive_path = shutil.make_archive(str(OUTPUT.parent / (OUTPUT.name + "-results")), "zip", OUTPUT)
            try:
                from google.colab import files
            except ImportError:
                print("Output archive:", archive_path)
            else:
                files.download(archive_path)
        else:
            print("Set DOWNLOAD_OUTPUTS = True to save this run's results.")
    ''')
    md("""
        ## Design limits worth discussing

        - SQLite and sequential requests keep setup small. Higher throughput would
          need bounded concurrency, rate-limit coordination, and shared durable storage.
        - Database checkpoints avoid repeated completed work. A crash between a remote
          response and its local commit can still cause a repeated billable request.
        - Pattern-based redaction is inspectable but incomplete. Real data needs stronger
          local detection and an evaluation of missed identifiers.
        - The taxonomy and reference labels need independent domain review. A model's
          self-reported confidence is not a measured probability of correctness.

        The README explains input formats, provenance, and how to record actual
        Gemini responses for a future no-key replay.
    """)

    # Capture actual output from every default code cell. Jupyter supplies display;
    # this small recorder captures the same raw MIME dictionaries during local runs.
    namespace = {"__name__": "__main__"}
    count = 0
    previous = Path.cwd()
    try:
        os.chdir(ROOT)
        for cell in cells:
            if cell["cell_type"] != "code":
                continue
            count += 1
            cell["execution_count"] = count
            output = io.StringIO()
            def capture_display(data, raw=False):
                assert raw and isinstance(data, dict)
                cell["outputs"].append({"output_type": "display_data", "data": data, "metadata": {}})
            namespace["display"] = capture_display
            with redirect_stdout(output), redirect_stderr(output):
                exec(compile("".join(cell["source"]), f"notebook-cell-{count}", "exec"), namespace)
            if output.getvalue():
                cell["outputs"].append({"output_type": "stream", "name": "stdout",
                                        "text": output.getvalue().splitlines(keepends=True)})
    finally:
        os.chdir(previous)

    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.10"},
            "colab": {"name": "Call analytics demo", "provenance": []},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }
    path = ROOT / "notebooks" / "demo.ipynb"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")
    examples = ROOT / "examples"
    examples.mkdir(exist_ok=True)
    for name in ["labels.json", "labels.csv", "evaluation.json"]:
        (examples / name).write_bytes((namespace["OUTPUT"] / name).read_bytes())
    print(f"Executed and recorded {count} default code cells. Wrote notebook and synthetic examples.")


if __name__ == "__main__":
    build()

