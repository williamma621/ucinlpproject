import copy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from call_pipeline.cli import main, record_responses
from call_pipeline.evaluation import evaluate
from call_pipeline.labeling import FixtureProvider, GeminiProvider, ProviderError, Response
from call_pipeline.pipeline import export_rows, load_calls, run_pipeline
from call_pipeline.prompting import prompt_hash
from call_pipeline.redaction import redact
from call_pipeline.schemas import ValidationError, parse_labels


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = load_calls()
        self.provider = FixtureProvider()

    def run_calls(self, calls=None, provider=None, **kwargs):
        return run_pipeline(calls if calls is not None else self.calls,
                            provider or self.provider, db=self.root / "calls.db", **kwargs)

    def test_redaction_and_stable_placeholders(self):
        raw = ("My name is Alex Rowan. Call 202-555-0142 or (202) 555-0142. "
               "Email alex@example.com. SSN 000-12-3456. Account number CT-12345. "
               "Date of birth is 01/01/1990. Address: 123 Example Lane.")
        result = redact(raw)
        self.assertEqual(result.text.count("[PHONE_1]"), 2)
        self.assertEqual(result.counts["PHONE"], 2)
        for identifier in ["Alex Rowan", "202-555-0142", "alex@example.com", "000-12-3456",
                           "CT-12345", "01/01/1990", "123 Example Lane"]:
            self.assertNotIn(identifier, result.text)
        self.assertEqual(redact(result.text).text, result.text)

    def test_redaction_preserves_useful_numbers(self):
        text = "I owe $12,450. Call at 10 AM. I have 3 notices."
        self.assertEqual(redact(text).text, text)

    def test_redaction_known_limits_are_explicit(self):
        # Documents a limitation rather than implying complete anonymization.
        self.assertEqual(redact("Please ask Morgan about my case.").text,
                         "Please ask Morgan about my case.")

    def test_fixture_demo_has_no_network_and_is_repeatable(self):
        with patch("socket.socket", side_effect=AssertionError("Network attempted")):
            first = self.run_calls()
            second = self.run_calls(list(reversed(self.calls)))
        self.assertEqual(first.summary, {"total": 16, "succeeded": 16, "failed": 0, "cached": 0})
        self.assertEqual(second.summary["cached"], 16)
        self.assertEqual({r["call_id"]: r["labels_json"] for r in first.rows},
                         {r["call_id"]: r["labels_json"] for r in second.rows})

    def test_input_change_does_not_reuse_result(self):
        self.run_calls(self.calls[:1])
        changed = copy.deepcopy(self.calls[:1])
        changed[0]["transcript"] += " New sentence."
        run = self.run_calls(changed)
        self.assertEqual(run.summary["cached"], 0)
        self.assertEqual(run.rows[0]["error_code"], "fixture_input_mismatch")

    def test_prompt_change_does_not_reuse_result(self):
        self.run_calls(self.calls[:1])
        run = self.run_calls(self.calls[:1], prompt="baseline")
        self.assertEqual(run.summary["cached"], 0)
        self.assertEqual(run.summary["failed"], 1)

    def test_fixture_rejects_changed_prompt_contents(self):
        self.provider.document["prompt_hash"] = "outdated"
        run = self.run_calls(self.calls[:1])
        self.assertEqual(run.rows[0]["error_code"], "fixture_prompt_changed")

    def test_retry_is_bounded_and_only_temporary_errors_retry(self):
        original = self.provider.generate
        attempts = []
        def transient(call_id, text, prompt):
            attempts.append(call_id)
            if len(attempts) < 3:
                raise ProviderError("api_429", retryable=True)
            return original(call_id, text, prompt)
        self.provider.generate = transient
        sleeps = []
        run = self.run_calls(self.calls[:1], sleep=sleeps.append)
        self.assertEqual(run.rows[0]["attempts"], 3)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(run.summary["succeeded"], 1)

    def test_failed_call_isolated_and_later_retry_preserves_history(self):
        original = self.provider.generate
        def fail_one(call_id, text, prompt):
            if call_id == "call_001":
                raise ProviderError("api_503", retryable=True)
            return original(call_id, text, prompt)
        self.provider.generate = fail_one
        first = self.run_calls(self.calls[:2], sleep=lambda _: None)
        self.assertEqual(first.summary["failed"], 1)
        self.assertEqual(first.rows[0]["attempts"], 3)
        self.provider.generate = original
        second = self.run_calls(self.calls[:2])
        self.assertEqual(second.summary["cached"], 1)
        self.assertEqual(second.summary["succeeded"], 2)
        with closing(sqlite3.connect(self.root / "calls.db")) as connection:
            status = connection.execute("SELECT r.status FROM run_items i JOIN results r USING(work_key) "
                                        "WHERE i.run_id=? AND i.call_id='call_001'", (first.run_id,)).fetchone()[0]
        self.assertEqual(status, "failed")

    def test_resume_after_process_interruption(self):
        original = self.provider.generate
        def interrupt(call_id, text, prompt):
            if call_id == "call_002":
                raise KeyboardInterrupt()
            return original(call_id, text, prompt)
        self.provider.generate = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_calls(self.calls[:2])
        self.provider.generate = original
        resumed = self.run_calls(self.calls[:2])
        self.assertEqual(resumed.summary["cached"], 1)
        self.assertEqual(resumed.summary["succeeded"], 2)

    def test_schema_rejects_bad_values_and_duplicate_keys(self):
        valid = self.provider.records["call_001"]["labels"]
        for update in [{"confidence": 1.1}, {"confidence": True}, {"confidence": float("nan")},
                       {"call_outcome": "made_up"}, {"labeling_notes": "x" * 301},
                       {"unexpected": "field"}, {"secondary_intent": valid["primary_intent"]}]:
            with self.subTest(update=list(update)):
                with self.assertRaises(ValidationError):
                    parse_labels(json.dumps({**valid, **update}))
        with self.assertRaises(ValidationError):
            parse_labels('{"confidence": 0.4, "confidence": 0.6}')

    def test_invalid_response_not_retried_or_saved_raw(self):
        self.provider.generate = lambda *a: Response('secret@example.com is not JSON')
        run = self.run_calls(self.calls[:1])
        self.assertEqual(run.rows[0]["attempts"], 1)
        self.assertIsNone(run.rows[0]["labels_json"])
        self.assertNotIn("secret@example.com", json.dumps(run.rows))

    def test_raw_identifiers_not_stored_and_input_to_provider_redacted(self):
        seen = []
        original = self.provider.generate
        def capture(call_id, text, prompt):
            seen.append(text)
            return original(call_id, text, prompt)
        self.provider.generate = capture
        run = self.run_calls([self.calls[12]])
        stored = json.dumps(run.rows)
        for value in ["Jordan Finch", "000-12-3456", "01/01/1990", "123 Example Lane", "jordan.finch@example.com"]:
            self.assertNotIn(value, stored)
            self.assertNotIn(value, seen[0])
        self.assertEqual(json.loads(run.rows[0]["pii_counts_json"])["SSN"], 1)

    def test_model_notes_are_redacted_and_csv_formulas_escaped(self):
        labels = dict(self.provider.records["call_001"]["labels"])
        labels["labeling_notes"] = "= contact private@example.com"
        self.provider.generate = lambda *a: Response(json.dumps(labels))
        run = self.run_calls(self.calls[:1])
        export_rows(run, self.root / "export")
        csv_text = (self.root / "export" / "labels.csv").read_text()
        self.assertNotIn("private@example.com", csv_text)
        self.assertIn("'= contact [EMAIL_1]", csv_text)

    def test_evaluation_matches_ids_and_counts_failures(self):
        run = self.run_calls(list(reversed(self.calls)))
        report = evaluate(run)
        self.assertEqual(len(report["disagreements"]), 2)
        self.assertEqual(report["scores"]["call_outcome"]["correct"], 15)
        run.rows[0]["status"] = "failed"
        run.summary["succeeded"] -= 1
        report = evaluate(run)
        self.assertEqual(report["scores"]["primary_intent"]["accuracy_including_failures"], 15 / 16)
        self.assertEqual(report["scores"]["primary_intent"]["accuracy_on_valid"], 1)

    def test_input_validation(self):
        for content in ["", "{bad json}", json.dumps({"call_id": "x", "transcript": ""}),
                        "\n".join([json.dumps(self.calls[0])] * 2)]:
            path = self.root / "bad.jsonl"
            path.write_text(content)
            with self.assertRaises(ValueError):
                load_calls(path)

    def test_record_and_replay(self):
        first = self.run_calls(self.calls[:1])
        target = self.root / "recorded.json"
        record_responses(first, target)
        provider = FixtureProvider(target)
        replay = self.run_calls(self.calls[:1], provider)
        self.assertEqual(first.rows[0]["labels_json"], replay.rows[0]["labels_json"])

    def test_gemini_adapter_sends_schema_and_redacted_text(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        labels = self.provider.records["call_001"]["labels"]
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(labels), usage_metadata=None)
        provider = GeminiProvider("test-model", client=client)
        self.run_calls(self.calls[:1], provider)
        arguments = client.models.generate_content.call_args.kwargs
        self.assertIn("response_json_schema", arguments["config"])
        self.assertIn("[PHONE_1]", arguments["contents"])
        self.assertNotIn("202-555-0142", json.dumps(arguments))

    def test_live_errors_do_not_include_provider_secrets(self):
        from unittest.mock import Mock
        client = Mock()
        client.models.generate_content.side_effect = RuntimeError("key and transcript could be here")
        run = self.run_calls(self.calls[:1], GeminiProvider("test-model", client=client))
        self.assertEqual(run.rows[0]["error_code"], "api_request_failed")
        self.assertNotIn("key and transcript", json.dumps(run.rows))


if __name__ == "__main__":
    unittest.main()

