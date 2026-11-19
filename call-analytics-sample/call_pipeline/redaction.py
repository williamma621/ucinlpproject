"""Local pattern-based redaction. Deliberately bounded, not a PII guarantee."""

from collections import Counter
from dataclasses import dataclass
import re

VERSION = "patterns-v1"

# Context is required for names and account numbers to limit false positives.
# Names outside these phrases, international addresses and spoken-out numbers
# need a more capable local recognizer before this is used with real calls.
PATTERNS = [
    ("EMAIL", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("SSN", re.compile(r"(?i:\b(?:ssn|social security(?: number)?)\s*(?:is|:)?\s*)(?P<value>\d{9})(?!\d)")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\w)")),
    ("ACCOUNT", re.compile(r"(?i:\b(?:account|case)(?:\s+(?:number|id))?\s*(?:is|:|#)?\s*)(?P<value>[A-Z]{0,5}[- ]?\d{5,14})\b")),
    ("DOB", re.compile(r"(?i:\b(?:date of birth|dob)\s*(?:is|:)?\s*)(?P<value>\d{1,4}[/-]\d{1,2}[/-]\d{1,4})\b")),
    ("ADDRESS", re.compile(r"\b\d{1,6}\s+(?:[A-Za-z]+\s+){1,5}(?i:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd)\b(?:\s+(?i:Apt|Unit)\s*\w+)?")),
    ("PERSON", re.compile(r"(?i:\b(?:my name is|this is|I'm|I am|name:)\s+)(?P<value>[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?(?:\s+[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?){1,2})\b")),
]


@dataclass(frozen=True)
class Redacted:
    text: str
    counts: dict

    @property
    def contains_pii(self):
        return bool(self.counts)


def redact(text):
    candidates = []
    for priority, (kind, pattern) in enumerate(PATTERNS):
        for match in pattern.finditer(text):
            start, end = match.span("value") if "value" in match.re.groupindex else match.span()
            candidates.append((start, end, priority, kind))
    # Prefer the earlier pattern if two recognizers cover the same characters.
    selected = []
    for span in sorted(candidates, key=lambda x: (x[2], x[0], -(x[1] - x[0]))):
        if not any(span[0] < end and span[1] > start for start, end, _, _ in selected):
            selected.append(span)
    replacements, counts, fragments = {}, Counter(), []
    cursor = 0
    for start, end, _, kind in sorted(selected):
        value = text[start:end]
        normalized = re.sub(r"\D", "", value) if kind in {"PHONE", "SSN"} else value.casefold()
        if kind == "PHONE" and len(normalized) == 11 and normalized.startswith("1"):
            normalized = normalized[1:]
        key = (kind, normalized)
        if key not in replacements:
            number = 1 + sum(k[0] == kind for k in replacements)
            replacements[key] = f"[{kind}_{number}]"
        fragments.extend([text[cursor:start], replacements[key]])
        counts[kind] += 1
        cursor = end
    fragments.append(text[cursor:])
    return Redacted("".join(fragments), dict(sorted(counts.items())))

