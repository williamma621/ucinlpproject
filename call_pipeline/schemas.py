"""The public sample's taxonomy; the original definition files were unavailable."""

import json
import math


LABELS = {
    "primary_intent": {
        "tax_debt_help": "Requests help with an existing tax debt.",
        "pricing": "Asks about service fees.",
        "legitimacy": "Asks whether the company or offer is legitimate.",
        "case_status": "Asks for an update on an existing case.",
        "do_not_contact": "Explicitly asks to stop future contact.",
        "wrong_number": "Says the caller reached the wrong person.",
        "other": "A clear request outside the listed intents.",
        "unknown": "Not enough evidence to identify an intent.",
    },
    "call_outcome": {
        "consultation_scheduled": "A consultation time is explicitly agreed.",
        "follow_up_needed": "Further contact is needed; no consultation is booked.",
        "information_provided": "An informational question was answered.",
        "declined": "Customer declines the service without a stop-contact request.",
        "do_not_contact": "Customer asks for no further contact.",
        "wrong_number": "Call ends after identifying the wrong recipient.",
        "unresolved": "No supported resolution or next step.",
    },
    "qualification_status": {
        "needs_review": "Potential service request; eligibility needs human review.",
        "existing_customer": "Customer explicitly has an existing case.",
        "not_applicable": "No eligibility assessment is relevant to the call.",
        "unknown": "Insufficient evidence.",
    },
    "objection_type": dict.fromkeys([
        "none", "price_concern", "legitimacy_concern", "already_resolved",
        "already_has_help", "too_busy", "not_interested", "privacy_concern",
        "confused", "angry_or_hostile", "other"], "Use only when explicitly supported."),
    "customer_sentiment": dict.fromkeys(
        ["positive", "neutral", "negative", "angry", "confused", "anxious"],
        "Infer from customer wording, not the agent; audio tone is unavailable."),
    "urgency": {
        "high": "An explicit near-term deadline or immediate request.",
        "medium": "Wants help soon but no immediate deadline.",
        "low": "Explicitly says there is no rush.",
        "unknown": "Timing is not stated.",
    },
    "kb_usefulness": {
        "useful": "Contains a reusable general question and substantive answer.",
        "limited": "Some context, but little reusable explanation.",
        "not_useful": "No reusable information.",
    },
    "compliance_flag": {
        "none": "No listed issue is evident; this is not a compliance certification.",
        "possible_tax_advice": "Agent gives individualized tax instructions.",
        "promise_or_guarantee": "Agent guarantees a tax outcome.",
        "aggressive_sales_language": "Agent uses coercive sales language.",
        "do_not_contact_request": "Customer asks to stop contact.",
        "bad_audio": "Transcript explicitly indicates missing or unintelligible speech.",
        "other": "Other issue that needs human review.",
    },
}
LABELS["secondary_intent"] = {**LABELS["primary_intent"], "none": "No second clear intent."}
FIELDS = list(LABELS) + ["confidence", "labeling_notes"]
EVALUATED_FIELDS = ["primary_intent", "call_outcome", "customer_sentiment", "compliance_flag"]
SCHEMA = {
    "type": "object",
    "properties": {
        **{name: {"type": "string", "enum": list(values)} for name, values in LABELS.items()},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "labeling_notes": {"type": "string", "maxLength": 300},
    },
    "required": FIELDS,
    "additionalProperties": False,
}


class ValidationError(ValueError):
    """Messages intentionally omit response contents."""


def validate_labels(value):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValidationError("Response has missing or unexpected fields")
    for name, choices in LABELS.items():
        if not isinstance(value[name], str) or value[name] not in choices:
            raise ValidationError(f"Invalid label for {name}")
    confidence = value["confidence"]
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValidationError("Confidence must be a finite number between 0 and 1")
    if not isinstance(value["labeling_notes"], str) or len(value["labeling_notes"]) > 300:
        raise ValidationError("Notes must be text of at most 300 characters")
    if value["secondary_intent"] == value["primary_intent"]:
        raise ValidationError("Secondary intent must differ from primary intent")
    return dict(value)


def parse_labels(text):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError("Duplicate JSON field")
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, TypeError):
        raise ValidationError("Response is not valid JSON") from None
    return validate_labels(value)

