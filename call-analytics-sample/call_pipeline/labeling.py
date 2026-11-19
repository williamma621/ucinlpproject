"""Providers share one interface; fixtures never fall back to a paid API."""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path

from .prompting import instruction, prompt_hash, user_content
from .schemas import SCHEMA


class ProviderError(Exception):
    """Safe error code, without provider messages or transcript text."""

    def __init__(self, code, retryable=False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass
class Response:
    text: str
    usage: dict = field(default_factory=dict)


class FixtureProvider:
    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).parent / "data" / "fixtures.json")
        raw = self.path.read_bytes()
        self.document = json.loads(raw)
        self.records = self.document["responses"]
        self.mode = "fixture"
        self.model = self.document.get("model", "hand-authored-fixtures")
        self.identity = "fixture:" + sha256(raw).hexdigest()
        self.provenance = self.document["provenance"]

    def generate(self, call_id, text, prompt):
        if prompt != self.document["prompt"]:
            raise ProviderError("fixture_prompt_mismatch")
        if self.document["prompt_hash"] != prompt_hash(prompt):
            raise ProviderError("fixture_prompt_changed")
        record = self.records.get(call_id)
        if not record or record["redacted_sha256"] != sha256(text.encode()).hexdigest():
            raise ProviderError("fixture_input_mismatch")
        return Response(json.dumps(record["labels"]))

    def close(self):
        pass


class GeminiProvider:
    def __init__(self, model=None, client=None):
        self.model = model or os.environ.get("GEMINI_MODEL", "")
        if not self.model:
            raise ValueError("Set GEMINI_MODEL or pass --model with a model available to your account")
        self.mode = "live"
        self.provenance = "Gemini API response"
        self.identity = "gemini-generate-content-v1:" + self.model
        if client is not None:
            self.client = client
            return
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("Set GEMINI_API_KEY privately; never put it in source files")
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ValueError('Live mode needs: python -m pip install ".[live]"') from None
        self.client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=60000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def generate(self, call_id, text, prompt):
        # No call ID, raw transcript, or local file path is sent to Gemini.
        try:
            result = self.client.models.generate_content(
                model=self.model,
                contents=user_content(text),
                config={
                    "system_instruction": instruction(prompt),
                    "temperature": 0,
                    "max_output_tokens": 2048,
                    "response_mime_type": "application/json",
                    "response_json_schema": SCHEMA,
                },
            )
        except Exception as exc:
            status = getattr(exc, "code", None)
            if status in {408, 429, 500, 502, 503, 504}:
                raise ProviderError(f"api_{status}", retryable=True) from None
            if isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {
                "ReadTimeout", "ConnectTimeout", "ConnectError", "RemoteProtocolError"
            }:
                raise ProviderError("network_error", retryable=True) from None
            raise ProviderError("api_request_failed") from None
        if not result.text:
            raise ProviderError("empty_or_blocked_response")
        usage = getattr(result, "usage_metadata", None)
        return Response(result.text, {
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
        })

    def close(self):
        self.client.close()

