"""
Schema for template-based document generation.

Phase 2: Single source of truth for summons placeholders.
Validate LLM output: required keys, types, no extra keys.
"""

# Exact schema for summons template fill. Types: str or list of str (for paragraphs).
SUMMONS_SCHEMA = {
    "INDEX_NO": "",
    "DATE_FILED": "",
    "PLAINTIFF_NAME": "",
    "DEFENDANT_NAME": "",
    "PLAINTIFF_RESIDENCE": "",
    "CAUSE_OF_ACTION_1_PARAGRAPHS": [],
    "SIGNATURE_DATE": "",
    "ATTORNEY_NAME": "",
    "FIRM_NAME": "",
    "FIRM_ADDRESS": "",
    "PHONE": "",
}

# Schema spec for validation: key -> "string" | "string_list"
SUMMONS_SCHEMA_SPEC = {
    "INDEX_NO": "string",
    "DATE_FILED": "string",
    "PLAINTIFF_NAME": "string",
    "DEFENDANT_NAME": "string",
    "PLAINTIFF_RESIDENCE": "string",
    "CAUSE_OF_ACTION_1_PARAGRAPHS": "string_list",
    "SIGNATURE_DATE": "string",
    "ATTORNEY_NAME": "string",
    "FIRM_NAME": "string",
    "FIRM_ADDRESS": "string",
    "PHONE": "string",
}

REQUIRED_KEYS = frozenset(SUMMONS_SCHEMA_SPEC.keys())


def validate_summons_data(data: dict) -> None:
    """
    Validate data against SUMMONS_SCHEMA_SPEC.
    - Required keys exist.
    - Types match (str or list of str for string_list).
    - No extra keys allowed.
    Raises ValueError on failure.
    """
    if not isinstance(data, dict):
        raise ValueError("Data must be a JSON object")
    errors = []
    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"Missing required key: {key}")
            continue
        val = data[key]
        spec = SUMMONS_SCHEMA_SPEC[key]
        if spec == "string":
            if not isinstance(val, str):
                errors.append(f"{key}: expected string, got {type(val).__name__}")
        elif spec == "string_list":
            if not isinstance(val, list):
                errors.append(f"{key}: expected list of strings, got {type(val).__name__}")
            elif not all(isinstance(x, str) for x in val):
                errors.append(f"{key}: all items must be strings")
    for key in data:
        if key not in REQUIRED_KEYS:
            errors.append(f"Unexpected key: {key}")
    if errors:
        raise ValueError("Validation failed: " + "; ".join(errors))
