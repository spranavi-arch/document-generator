"""
Parse a CURL command or API config and fetch JSON. Extract values for required field names.
Supports "chat" APIs: use CURL as template, call API once per field with a question to get each value.
"""
import json
import random
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

# Type for optional UI callback: (field_name, index_1based, total) -> None
OnFieldStartCallback = Callable[[str, int, int], None]

# API request timeout (seconds). Responses can take several minutes.
API_REQUEST_TIMEOUT = 300
# Default delay between API calls to appear human-like (seconds). Use random range around this.
DEFAULT_DELAY_BETWEEN_CALLS_MIN = 6.0
DEFAULT_DELAY_BETWEEN_CALLS_MAX = 15.0

# Retry on server errors (501, 502, 503, 504): max retries and backoff (seconds).
RETRY_STATUS_CODES = (500, 501, 502, 503, 504)
API_MAX_RETRIES = 5
API_RETRY_BASE_DELAY = 15
API_RETRY_MAX_DELAY = 120

# Optional: requests if available (simpler for POST with body)
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# Request body keys we replace with the user question (first found wins)
REQUEST_CONTENT_KEYS = ("content", "message", "query", "question", "user_message", "user_content", "text")
# Response keys we try to extract the answer text from (first non-empty string wins)
RESPONSE_ANSWER_KEYS = ("content", "answer", "reply", "message", "text", "response", "output")
# Nested paths to try (e.g. OpenAI-style choices[0].message.content)
RESPONSE_ANSWER_PATHS = (
    "choices.0.message.content",
    "choices.0.text",
    "data.0.content",
    "result.content",
    "result.message",
    "output.text",
)


def _get_nested(data: Any, path: str) -> Any:
    """Get value at dot-separated path, e.g. 'party.plaintiff_name' or 'choices.0.message.content'."""
    keys = path.replace(" ", "").split(".")
    obj = data
    for k in keys:
        if obj is None:
            return None
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        elif isinstance(obj, list) and k.isdigit():
            idx = int(k)
            if 0 <= idx < len(obj):
                obj = obj[idx]
            else:
                return None
        else:
            return None
    return obj


def parse_curl(curl_str: str) -> dict:
    """
    Parse a CURL command string into url, method, headers, body.
    Returns: {"url": str, "method": str, "headers": dict, "data": str | None}
    Handles multi-line paste: joins lines ending with \\ before parsing.
    """
    curl_str = (curl_str or "").strip()
    # Join continuation lines (backslash + newline) so multiline paste works
    curl_str = re.sub(r"\\\s*\n\s*", " ", curl_str)
    if not curl_str.startswith("curl "):
        curl_str = "curl " + curl_str

    url = ""
    method = "GET"
    headers = {}
    data = None

    # -X METHOD
    m = re.search(r"-X\s+(\w+)", curl_str, re.I)
    if m:
        method = m.group(1).upper()

    # URL: first non-flag argument that looks like URL
    tokens = re.findall(r"'([^']*)'|\"([^\"]*)\"|(-[^\s]+)|(\S+)", curl_str)
    for t in tokens:
        s = t[0] or t[1] or t[3] or ""
        if s.startswith("-"):
            continue
        if s.startswith("http://") or s.startswith("https://"):
            url = s
            break

    if not url:
        # Fallback: find first https?://
        m = re.search(r"(https?://[^\s'\"]+)", curl_str)
        if m:
            url = m.group(1)

    # -H "Key: Value"
    for m in re.finditer(r"-H\s+['\"]([^'\"]+)['\"]", curl_str):
        h = m.group(1)
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    # -d / --data / --data-raw: single-quoted (allows double quotes inside) or double-quoted
    for pattern in [
        r"--data-raw\s+'([^']*)'",
        r"--data-raw\s+\"((?:[^\"\\]|\\.)*)\"",
        r"-d\s+'([^']*)'",
        r"--data\s+'([^']*)'",
    ]:
        m = re.search(pattern, curl_str, re.DOTALL)
        if m:
            data = m.group(1).strip()
            if method == "GET":
                method = "POST"
            break

    return {"url": url, "method": method, "headers": headers, "data": data}


def build_curl_from_token(
    token: str,
    api_url: str,
    subscription_key: str | None = None,
    body_template: str | None = None,
) -> str:
    """
    Build a CURL-style string from a Bearer token and API URL.
    Used when the user pastes only the token (the part after "Bearer ").
    Optionally adds ocp-apim-subscription-key header and uses a custom body template
    (must contain "content" key, replaced with the question per call).
    """
    token = (token or "").strip()
    api_url = (api_url or "").strip()
    if not api_url or not token:
        return ""
    if subscription_key is None or body_template is None:
        try:
            from docgen.config import (
                CHAT_API_SUBSCRIPTION_KEY,
                CHAT_API_BODY_TEMPLATE,
            )
            if subscription_key is None:
                subscription_key = CHAT_API_SUBSCRIPTION_KEY
            if body_template is None:
                body_template = CHAT_API_BODY_TEMPLATE
        except Exception:
            subscription_key = subscription_key or ""
            body_template = body_template or ""

    token_esc = token.replace("'", "'\"'\"'")
    if body_template and '"content"' in body_template:
        body = body_template.strip().replace("\n", " ").replace("\r", " ")
    else:
        body = '{"content": "REPLACE_WITH_QUESTION"}'

    try:
        json.loads(body)
    except json.JSONDecodeError:
        body = '{"content": "REPLACE_WITH_QUESTION"}'
    if "'" in body:
        body = '{"content": "REPLACE_WITH_QUESTION"}'

    parts = [f"curl '{api_url}' -X POST -H 'Authorization: Bearer {token_esc}' -H 'Content-Type: application/json'"]
    if subscription_key:
        sub_esc = subscription_key.replace("'", "'\"'\"'")
        parts.append(f"-H 'ocp-apim-subscription-key: {sub_esc}'")
    parts.append("-H 'accept: application/json, text/plain, */*'")
    parts.append(f"--data-raw '{body}'")
    return " ".join(parts)


def normalize_chat_api_input(input_str: str, api_url: str | None = None) -> str:
    """
    If input looks like a full CURL command, return it as-is.
    Otherwise treat as Bearer token only and build CURL from token + api_url.
    """
    s = (input_str or "").strip()
    if not s:
        return ""
    if "curl " in s.lower() or "https://" in s or "http://" in s:
        return s
    try:
        from docgen.config import CHAT_API_URL
        url = (api_url or "").strip() or CHAT_API_URL
    except Exception:
        url = (api_url or "").strip()
    if not url:
        return ""
    return build_curl_from_token(s, url)


def fetch_from_curl(curl_str: str) -> dict:
    """
    Execute the request implied by the CURL string and return response as JSON.
    On failure returns {}.
    """
    parsed = parse_curl(curl_str)
    url = parsed.get("url")
    if not url:
        return {}

    method = (parsed.get("method") or "GET").upper()
    headers = parsed.get("headers") or {}
    data = parsed.get("data")

    if HAS_REQUESTS:
        try:
            if method == "GET":
                r = requests.get(url, headers=headers, timeout=30)
            elif method == "POST":
                r = requests.post(url, headers=headers, data=data, timeout=30)
            elif method == "PUT":
                r = requests.put(url, headers=headers, data=data, timeout=30)
            else:
                r = requests.request(method, url, headers=headers, data=data, timeout=30)
            r.raise_for_status()
            return r.json() if r.content else {}
        except Exception:
            return {}
    else:
        req = urllib.request.Request(url, data=data.encode() if data else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode()
                return json.loads(body) if body else {}
        except Exception:
            return {}


def flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
    """Flatten nested dict so we can look up 'party.plaintiff' etc."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict) and v:
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            # Keep list as JSON string for display
            items.append((new_key, json.dumps(v)))
        else:
            items.append((new_key, v))
    return dict(items)


def get_field_values(api_response: dict, required_fields: list[str]) -> dict:
    """
    From a (possibly nested) API response, extract values for required_fields.
    required_fields can be snake_case names that match keys; we also try lowercase and flattened paths.
    Returns dict: field_name -> value (string when possible).
    """
    flat = flatten_dict(api_response)
    result = {}
    for field in required_fields:
        value = None
        # Exact key
        if field in flat:
            value = flat[field]
        elif field in api_response:
            value = api_response[field]
        else:
            # Try lowercase, replace _ with .
            alt = field.replace("_", ".")
            value = _get_nested(api_response, alt) or flat.get(alt)
            if value is None:
                for k, v in flat.items():
                    if k.lower().replace(".", "_").replace("-", "_") == field.lower().replace("-", "_"):
                        value = v
                        break
        if value is not None:
            result[field] = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return result


# ---------- Chat API: one call per field with a question ----------


# Regex fallback when LLM classifier is unavailable or fails.
_NON_ANSWER_PATTERNS = (
    r"\b(?:i\s+)?don'?t\s+know\b",
    r"\b(?:information|data|details?)\s+(?:is\s+)?(?:not\s+)?(?:available|mentioned)\b",
    r"\bdata\s+is\s+not\s+mentioned\b",
    r"\b(?:not\s+)?(?:available|found)\s+(?:in\s+)?(?:the\s+)?(?:case|record)\b",
    r"\bno\s+information\b",
    r"\b(?:i\s+)?(?:cannot|can'?t|unable\s+to)\s+(?:find|provide|answer)\b",
    r"\bdon'?t\s+have\s+(?:that|this|any)\s+information\b",
    r"\bno\s+data\b",
    r"\bnot\s+mentioned\b",
    r"\b(?:could\s+you|can\s+you|please)\s+(?:clarify|specify)\b",
    r"^\s*(\?|none|n/?a|unknown)\s*$",
)
_NON_ANSWER_REGEX = re.compile("|".join(f"({p})" for p in _NON_ANSWER_PATTERNS), re.I | re.DOTALL)


def _is_substantive_answer_regex(text: str) -> bool:
    """Regex-based check: True if text looks like a real answer. Used as fallback when LLM fails."""
    if not text or not text.strip():
        return False
    t = text.strip()
    if t.endswith("?") and len(t) < 200:
        return False
    if _NON_ANSWER_REGEX.search(t):
        return False
    if len(t) < 30 and re.search(r"\b(?:none|n/?a|unknown|not\s+found)\b", t, re.I):
        return False
    return True


def _is_substantive_answer_llm(text: str) -> bool:
    """
    Use LLM to decide if the response is a real answer (specific fact) or a non-answer
    (e.g. "data is not mentioned", "not available", question back, refusal). When False, we leave the field empty.
    Falls back to regex if LLM is unavailable or errors.
    """
    if not text or not text.strip():
        return False
    t = text.strip()
    # Truncate very long responses to save tokens
    text_for_prompt = t[:2000] + "..." if len(t) > 2000 else t
    prompt = f"""You are classifying a response from a legal case Q&A system.

Is the following response a REAL ANSWER (a specific fact we can use: a name, date, number, address, amount, place, or other concrete information)?

Or is it a NON-ANSWER such as: "data is not mentioned", "not available", "not in the record", "I don't know", a question back to the user, a refusal, asking for clarification, or saying the information is missing?

Response to classify:
\"\"\"
{text_for_prompt}
\"\"\"

Reply with exactly one word: ANSWER or NON_ANSWER. No explanation."""
    try:
        from docgen.llm_client import LLMClient
        client = LLMClient()
        reply = client.generate(prompt, max_tokens=10, temperature=0.0).strip().upper()
        if "NON_ANSWER" in reply:
            return False
        if "ANSWER" in reply:
            return True
        # Ambiguous LLM output: fall back to regex
        return _is_substantive_answer_regex(t)
    except Exception:
        return _is_substantive_answer_regex(t)


def _is_substantive_answer(text: str) -> bool:
    """True if response is a real answer; False otherwise (leave field empty). Uses LLM with regex fallback."""
    return _is_substantive_answer_llm(text)


def _human_delay(min_sec: float = DEFAULT_DELAY_BETWEEN_CALLS_MIN, max_sec: float = DEFAULT_DELAY_BETWEEN_CALLS_MAX) -> None:
    """Wait a random number of seconds between min and max to mimic human pacing."""
    time.sleep(random.uniform(min_sec, max_sec))


def _extract_answer_from_response(resp: dict) -> str:
    """Get the answer text from a chat API response. Tries common keys and nested paths."""
    if not isinstance(resp, dict):
        return str(resp) if resp is not None else ""

    # Explicit nested paths (e.g. OpenAI: choices[0].message.content)
    for path in RESPONSE_ANSWER_PATHS:
        val = _get_nested(resp, path.replace("[", ".").replace("]", ""))
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Top-level keys
    for key in RESPONSE_ANSWER_KEYS:
        val = resp.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for k in RESPONSE_ANSWER_KEYS:
                if k in val and isinstance(val[k], str) and val[k].strip():
                    return val[k].strip()
    # Nested: data.content, result.message, etc.
    for prefix in ("data", "result", "response", "body"):
        node = resp.get(prefix)
        if isinstance(node, dict):
            for key in RESPONSE_ANSWER_KEYS:
                if key in node and isinstance(node[key], str) and node[key].strip():
                    return node[key].strip()
    # Flatten and take first substantial string (skip metadata)
    flat = flatten_dict(resp)
    for k, v in flat.items():
        if isinstance(v, str) and v.strip() and not k.startswith(("headers", "status", "id", "conversation")):
            return v.strip()
    return ""


def _body_with_question(body_str: str, question: str) -> str:
    """Replace the user-message field in the JSON body with the given question. Returns new body string."""
    try:
        body = json.loads(body_str)
    except json.JSONDecodeError:
        return body_str
    if not isinstance(body, dict):
        return body_str
    for key in REQUEST_CONTENT_KEYS:
        if key in body:
            body = {**body, key: question}
            return json.dumps(body)
    # Fallback: set "content" if missing
    body["content"] = question
    return json.dumps(body)


def _do_request(method: str, url: str, headers: dict, body: str | None) -> tuple[dict, int | None, str | None]:
    """Send request; return (response_dict, status_code, error_message). Timeout allows for slow API (e.g. several minutes)."""
    if HAS_REQUESTS:
        try:
            r = requests.request(method, url, headers=headers, data=body, timeout=API_REQUEST_TIMEOUT)
            resp = r.json() if r.content else {}
            return (resp, r.status_code, None)
        except requests.exceptions.RequestException as e:
            return ({}, getattr(e.response, "status_code", None), str(e))
        except Exception as e:
            return ({}, None, str(e))
    else:
        req = urllib.request.Request(
            url, data=body.encode("utf-8") if body else None, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=API_REQUEST_TIMEOUT) as resp:
                out = resp.read().decode()
                data = json.loads(out) if out else {}
                return (data, resp.status, None)
        except urllib.error.HTTPError as e:
            try:
                body_err = e.read().decode()[:500]
            except Exception:
                body_err = ""
            return ({}, e.code, f"HTTP {e.code}: {body_err or e.reason}")
        except Exception as e:
            return ({}, None, str(e))


def _should_retry(status: int | None, err: str | None) -> bool:
    """True if we should retry: 5xx or timeout/connection error."""
    if status is not None and status in RETRY_STATUS_CODES:
        return True
    if err:
        err_lower = err.lower()
        if any(x in err_lower for x in ("timeout", "504", "503", "502", "connection", "gateway")):
            return True
    return False


def _do_request_with_retry(
    method: str,
    url: str,
    headers: dict,
    body: str | None,
    max_retries: int = API_MAX_RETRIES,
) -> tuple[dict, int | None, str | None]:
    """
    Call _do_request; on 5xx (501, 502, 503, 504, 500) or timeout/connection errors,
    retry with exponential backoff. Returns same as _do_request.
    """
    last_resp, last_status, last_err = _do_request(method, url, headers, body)
    attempt = 0
    while attempt < max_retries and _should_retry(last_status, last_err):
        delay = min(
            API_RETRY_MAX_DELAY,
            API_RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, 2),
        )
        time.sleep(delay)
        last_resp, last_status, last_err = _do_request(method, url, headers, body)
        attempt += 1
    return (last_resp, last_status, last_err)


# Extra question asked once to get case summary; passed to every section for context.
CASE_SUMMARY_QUESTION = (
    "Provide a brief case summary including the key facts, parties, dates, and context "
    "that would be relevant for drafting legal documents for this case."
)


def call_chat_api_with_question(curl_str: str, question: str) -> str:
    """
    Use the CURL as a template: replace the user message (e.g. "content") with `question`,
    send the request, and return the extracted answer text from the response.
    """
    parsed = parse_curl(curl_str)
    url = parsed.get("url")
    if not url:
        return ""
    method = (parsed.get("method") or "POST").upper()
    headers = dict(parsed.get("headers") or {})
    data_raw = parsed.get("data")
    body = _body_with_question(data_raw, question) if data_raw else None

    # Many APIs need Content-Type: application/json when body is JSON
    if body and not any(k.lower() == "content-type" for k in headers):
        try:
            json.loads(body)
            headers["Content-Type"] = "application/json"
        except json.JSONDecodeError:
            pass

    resp, _status, _err = _do_request_with_retry(method, url, headers, body)
    return _extract_answer_from_response(resp) if resp else ""


def call_chat_api_with_question_debug(curl_str: str, question: str) -> dict:
    """
    Same as call_chat_api_with_question but returns dict with answer + debug for UI.
    Keys: answer, status_code, error, response_keys, extracted_preview.
    """
    parsed = parse_curl(curl_str)
    url = parsed.get("url")
    if not url:
        return {"answer": "", "error": "No URL in CURL", "status_code": None, "response_keys": [], "extracted_preview": ""}
    method = (parsed.get("method") or "POST").upper()
    headers = dict(parsed.get("headers") or {})
    data_raw = parsed.get("data")
    body = _body_with_question(data_raw, question) if data_raw else None
    if body and not any(k.lower() == "content-type" for k in headers):
        try:
            json.loads(body)
            headers["Content-Type"] = "application/json"
        except json.JSONDecodeError:
            pass

    resp, status_code, err = _do_request_with_retry(method, url, headers, body)
    if err:
        return {"answer": "", "error": err, "status_code": status_code, "response_keys": [], "extracted_preview": ""}
    answer = _extract_answer_from_response(resp)
    keys = list(resp.keys()) if isinstance(resp, dict) else []
    return {
        "answer": answer,
        "error": None,
        "status_code": status_code,
        "response_keys": keys,
        "extracted_preview": (answer[:300] + "..." if len(answer) > 300 else answer),
    }


def fetch_case_summary(curl_str: str) -> str:
    """
    Ask the chat API for a case summary (one extra question). Returns the answer text
    or empty string if the API returns a non-answer. Pass the result to section
    prompts along with required fields; sections can use it when they need context.
    """
    if not (curl_str or "").strip():
        return ""
    answer = call_chat_api_with_question(curl_str, CASE_SUMMARY_QUESTION)
    answer = (answer or "").strip()
    return answer if _is_substantive_answer(answer) else ""


def fetch_all_fields_via_chat(
    curl_str: str,
    required_fields: list[str],
    field_to_question: dict[str, str],
    delay_seconds: float | None = None,
    on_field_start: OnFieldStartCallback | None = None,
) -> dict[str, str]:
    """
    Call the chat API once per required field. Wait between calls (human-like delay).
    Only store the response if it looks like a real answer; if the API returns a question,
    "I don't know", "not available", etc., leave the field empty.
    on_field_start: optional callback(field_name, index_1based, total) so UI can show current field.
    """
    min_delay = DEFAULT_DELAY_BETWEEN_CALLS_MIN if delay_seconds is None else delay_seconds
    max_delay = DEFAULT_DELAY_BETWEEN_CALLS_MAX if delay_seconds is None else (delay_seconds * 1.5)
    total = len(required_fields)
    result = {}
    for i, field in enumerate(required_fields):
        if on_field_start:
            on_field_start(field, i + 1, total)
        question = field_to_question.get(field) or _default_question_for_field(field)
        answer = call_chat_api_with_question(curl_str, question)
        answer = (answer or "").strip()
        if answer and _is_substantive_answer(answer):
            result[field] = answer
        else:
            result[field] = ""  # leave field empty when response is not a real answer
        if max_delay > 0 and i < len(required_fields) - 1:
            _human_delay(min_delay, max_delay)
    return result


def _default_question_for_field(field_name: str) -> str:
    """Fallback when no generated question: use same explicit-question logic as question_generator."""
    from docgen.question_generator import _fallback_question
    return _fallback_question(field_name)


# Broad questions to get maximum information from the API (one call per question).
BROAD_QUESTIONS = [
    ("additional_facts_for_draft", "List every fact that should appear in the summons and complaint or in the allegations. Be comprehensive and include all relevant details."),
    ("additional_dates", "List all dates relevant to this case: incident, filing, intake, settlement, negotiations, medical treatment, and any other important dates."),
    ("additional_amounts_and_figures", "List all amounts, figures, and financial information: medical bills, liens, demands, offers, settlement amount, expenses, and any other monetary values."),
    ("additional_parties_and_addresses", "List all parties (plaintiff, defendant, attorneys, firms) with their full names, addresses, and contact details."),
    ("additional_claims_and_theories", "List the legal claims, causes of action, and theories of liability (e.g. negligence, medical malpractice) and any key legal points that should be in the complaint."),
    ("additional_venue_jurisdiction", "What is the court, venue, jurisdiction, index/docket number, and any filing or procedural details?"),
]


def fetch_broad_answers(
    curl_str: str,
    delay_seconds: float | None = None,
    on_question_start: Callable[[str, int, int], None] | None = None,
) -> dict[str, str]:
    """Call the API once per broad question. Wait between calls. Store only substantive answers.
    on_question_start: optional callback(question_key, index_1based, total) for UI."""
    min_delay = DEFAULT_DELAY_BETWEEN_CALLS_MIN if delay_seconds is None else delay_seconds
    max_delay = DEFAULT_DELAY_BETWEEN_CALLS_MAX if delay_seconds is None else (delay_seconds * 1.5)
    total = len(BROAD_QUESTIONS)
    result = {}
    for i, (key, question) in enumerate(BROAD_QUESTIONS):
        if on_question_start:
            on_question_start(key, i + 1, total)
        answer = call_chat_api_with_question(curl_str, question)
        answer = (answer or "").strip()
        if answer and _is_substantive_answer(answer):
            result[key] = answer
        else:
            result[key] = ""
        if max_delay > 0 and i < len(BROAD_QUESTIONS) - 1:
            _human_delay(min_delay, max_delay)
    return result
