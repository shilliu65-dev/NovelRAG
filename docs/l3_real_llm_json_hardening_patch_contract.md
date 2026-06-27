# L3.13b Real LLM JSON Hardening Patch contract

## Purpose

L3.13b hardens the L3.11/L3.13 real LLM consumer sandbox against provider responses that contain valid JSON wrapped in Markdown or explanatory text.

It does not weaken validation. If no valid JSON object can be extracted, the response remains invalid and `json_parse_error_count` increases.

## Prompt Rules

The sandbox prompt must tell the model:

- Output exactly one JSON object.
- Do not output Markdown.
- Do not output ```json code blocks.
- Do not output explanations, prefixes, or suffixes.
- Start with `{` and end with `}`.
- Use double quotes for all JSON fields and strings.
- Do not use trailing commas.
- Do not invent facts outside the supplied evidence.
- If evidence is insufficient, set `response_status` to `insufficient`.

## HTTP Rules

The OpenAI-compatible request keeps the current endpoint behavior and JSON-only messages.

The request may include:

```json
{
  "response_format": {
    "type": "json_object"
  }
}
```

If the provider returns HTTP 400 and the body indicates `response_format` is unsupported, the sandbox retries the same request without `response_format`.

API keys must never be printed, stored in test fixtures, or written to artifacts.

## JSON Extraction

The pure function:

```python
extract_json_object(raw_text: str) -> tuple[str, list[str]]
```

must:

- Return plain valid JSON objects unchanged.
- Extract valid JSON from ```json code blocks.
- Extract the first balanced valid JSON object from wrapped text.
- Return the original text and warnings when extraction fails.
- Use `json.loads` for validation.
- Never use `eval`.

## Response Fields

Each response row records:

```json
{
  "raw_response": "...",
  "extracted_json_text": "...",
  "json_extraction_warnings": [],
  "raw_response_was_wrapped": false
}
```

`raw_response` preserves the original provider output.

## Manifest Fields

The sandbox manifest records:

```json
{
  "json_parse_error_count": 0,
  "json_extraction_warning_count": 0,
  "raw_response_wrapped_count": 0
}
```

`json_parse_error_count` only increases when extracted text cannot be parsed as a JSON object.
