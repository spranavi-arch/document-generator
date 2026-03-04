"""
Parse a CURL command or API config and fetch JSON. Extract values for required field names.
Supports "chat" APIs: use CURL as template, call API once per field with a question to get each value.
Uses CurlParser and FieldFetcher classes (OOP).
"""
import json
import random
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

OnFieldStartCallback = Callable[[str, int, int], None]

try:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    HAS_AZURE_SEARCH = True
except ImportError:
    HAS_AZURE_SEARCH = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

API_REQUEST_TIMEOUT = 300
DEFAULT_DELAY_BETWEEN_CALLS_MIN = 6.0
DEFAULT_DELAY_BETWEEN_CALLS_MAX = 15.0
FETCH_FIELDS_BATCH_SIZE = 10
RETRY_STATUS_CODES = (500, 501, 502, 503, 504)
API_MAX_RETRIES = 5
API_RETRY_BASE_DELAY = 15
API_RETRY_MAX_DELAY = 120

REQUEST_CONTENT_KEYS = ("content", "message", "query", "question", "user_message", "user_content", "text")
RESPONSE_ANSWER_KEYS = ("content", "answer", "reply", "message", "text", "response", "output")
RESPONSE_ANSWER_PATHS = (
    "choices.0.message.content",
    "choices.0.text",
    "data.0.content",
    "result.content",
    "result.message",
    "output.text",
)

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

CASE_SUMMARY_QUESTION = (
    "Provide a brief case summary including the key facts, parties, dates, and context "
    "that would be relevant for drafting legal documents for this case."
)

BROAD_QUESTIONS = [
    ("additional_facts_for_draft", "List every fact that should appear in the summons and complaint or in the allegations. Be comprehensive and include all relevant details."),
    ("additional_dates", "List all dates relevant to this case: incident, filing, intake, settlement, negotiations, medical treatment, and any other important dates."),
    ("additional_amounts_and_figures", "List all amounts, figures, and financial information: medical bills, liens, demands, offers, settlement amount, expenses, and any other monetary values."),
    ("additional_parties_and_addresses", "List all parties (plaintiff, defendant, attorneys, firms) with their full names, addresses, and contact details."),
    ("additional_claims_and_theories", "List the legal claims, causes of action, and theories of liability (e.g. negligence, medical malpractice) and any key legal points that should be in the complaint."),
    ("additional_venue_jurisdiction", "What is the court, venue, jurisdiction, index/docket number, and any filing or procedural details?"),
]


class CurlParser:
    """Parses CURL command strings and builds CURL from token + URL."""

    @staticmethod
    def parse_curl(curl_str: str) -> dict:
        """
        Parse a CURL command string into url, method, headers, body.
        Returns: {"url": str, "method": str, "headers": dict, "data": str | None}
        """
        curl_str = (curl_str or "").strip()
        curl_str = re.sub(r"\\\s*\n\s*", " ", curl_str)
        if not curl_str.startswith("curl "):
            curl_str = "curl " + curl_str

        url = ""
        method = "GET"
        headers = {}
        data = None

        m = re.search(r"-X\s+(\w+)", curl_str, re.I)
        if m:
            method = m.group(1).upper()

        tokens = re.findall(r"'([^']*)'|\"([^\"]*)\"|(-[^\s]+)|(\S+)", curl_str)
        for t in tokens:
            s = t[0] or t[1] or t[3] or ""
            if s.startswith("-"):
                continue
            if s.startswith("http://") or s.startswith("https://"):
                url = s
                break

        if not url:
            m = re.search(r"(https?://[^\s'\"]+)", curl_str)
            if m:
                url = m.group(1)

        for m in re.finditer(r"-H\s+['\"]([^'\"]+)['\"]", curl_str):
            h = m.group(1)
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()

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

    @staticmethod
    def build_curl_from_token(
        token: str,
        api_url: str,
        subscription_key: str | None = None,
        body_template: str | None = None,
    ) -> str:
        """Build a CURL-style string from a Bearer token and API URL."""
        token = (token or "").strip()
        api_url = (api_url or "").strip()
        if not api_url or not token:
            return ""
        if subscription_key is None or body_template is None:
            try:
                from docgen.core.config import CHAT_API_SUBSCRIPTION_KEY, CHAT_API_BODY_TEMPLATE
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

    @staticmethod
    def normalize_chat_api_input(input_str: str, api_url: str | None = None) -> str:
        """If input looks like full CURL, return as-is; else treat as Bearer token and build CURL."""
        s = (input_str or "").strip()
        if not s:
            return ""
        if "curl " in s.lower() or "https://" in s or "http://" in s:
            return s
        try:
            from docgen.core.config import CHAT_API_URL
            url = (api_url or "").strip() or CHAT_API_URL
        except Exception:
            url = (api_url or "").strip()
        if not url:
            return ""
        return CurlParser.build_curl_from_token(s, url)


class FieldFetcher:
    """
    Fetches field values via chat API (one call per field with a question).
    Parses CURL, sends requests with retry, extracts answer text, and filters non-answers.
    """

    def __init__(self, llm_client=None):
        if llm_client:
            self._llm = llm_client
        else:
            from docgen.core.llm_client import LLMClient
            self._llm = LLMClient()

    @staticmethod
    def _get_nested(data: Any, path: str) -> Any:
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

    @staticmethod
    def flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict) and v:
                items.extend(FieldFetcher.flatten_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                items.append((new_key, json.dumps(v)))
            else:
                items.append((new_key, v))
        return dict(items)

    def get_field_values(self, api_response: dict, required_fields: list[str]) -> dict:
        flat = self.flatten_dict(api_response)
        result = {}
        for field in required_fields:
            value = None
            if field in flat:
                value = flat[field]
            elif field in api_response:
                value = api_response[field]
            else:
                alt = field.replace("_", ".")
                value = self._get_nested(api_response, alt) or flat.get(alt)
                if value is None:
                    for k, v in flat.items():
                        if k.lower().replace(".", "_").replace("-", "_") == field.lower().replace("-", "_"):
                            value = v
                            break
            if value is not None:
                result[field] = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        return result

    @staticmethod
    def _is_substantive_answer_regex(text: str) -> bool:
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

    def _is_substantive_answer_llm(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        t = text.strip()
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
            reply = self._llm.generate(prompt, max_tokens=10, temperature=0.0).strip().upper()
            if "NON_ANSWER" in reply:
                return False
            if "ANSWER" in reply:
                return True
            return self._is_substantive_answer_regex(t)
        except Exception:
            return self._is_substantive_answer_regex(t)

    def _is_substantive_answer(self, text: str) -> bool:
        return self._is_substantive_answer_llm(text)

    @staticmethod
    def _human_delay(min_sec: float = DEFAULT_DELAY_BETWEEN_CALLS_MIN, max_sec: float = DEFAULT_DELAY_BETWEEN_CALLS_MAX) -> None:
        time.sleep(random.uniform(min_sec, max_sec))

    def _extract_answer_from_response(self, resp: dict) -> str:
        if not isinstance(resp, dict):
            return str(resp) if resp is not None else ""
        for path in RESPONSE_ANSWER_PATHS:
            val = self._get_nested(resp, path.replace("[", ".").replace("]", ""))
            if isinstance(val, str) and val.strip():
                return val.strip()
        for key in RESPONSE_ANSWER_KEYS:
            val = resp.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                for k in RESPONSE_ANSWER_KEYS:
                    if k in val and isinstance(val[k], str) and val[k].strip():
                        return val[k].strip()
        for prefix in ("data", "result", "response", "body"):
            node = resp.get(prefix)
            if isinstance(node, dict):
                for key in RESPONSE_ANSWER_KEYS:
                    if key in node and isinstance(node[key], str) and node[key].strip():
                        return node[key].strip()
        flat = self.flatten_dict(resp)
        for k, v in flat.items():
            if isinstance(v, str) and v.strip() and not k.startswith(("headers", "status", "id", "conversation")):
                return v.strip()
        return ""

    @staticmethod
    def _body_with_question(body_str: str, question: str) -> str:
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
        body["content"] = question
        return json.dumps(body)

    def _do_request(self, method: str, url: str, headers: dict, body: str | None) -> tuple[dict, int | None, str | None]:
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

    @staticmethod
    def _should_retry(status: int | None, err: str | None) -> bool:
        if status is not None and status in RETRY_STATUS_CODES:
            return True
        if err:
            err_lower = err.lower()
            if any(x in err_lower for x in ("timeout", "504", "503", "502", "connection", "gateway")):
                return True
        return False

    def _do_request_with_retry(
        self, method: str, url: str, headers: dict, body: str | None, max_retries: int = API_MAX_RETRIES
    ) -> tuple[dict, int | None, str | None]:
        last_resp, last_status, last_err = self._do_request(method, url, headers, body)
        attempt = 0
        while attempt < max_retries and self._should_retry(last_status, last_err):
            delay = min(API_RETRY_MAX_DELAY, API_RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, 2))
            time.sleep(delay)
            last_resp, last_status, last_err = self._do_request(method, url, headers, body)
            attempt += 1
        return (last_resp, last_status, last_err)

    def call_chat_api_with_question(self, curl_str: str, question: str) -> str:
        parsed = CurlParser.parse_curl(curl_str)
        url = parsed.get("url")
        if not url:
            return ""
        method = (parsed.get("method") or "POST").upper()
        headers = dict(parsed.get("headers") or {})
        data_raw = parsed.get("data")
        body = self._body_with_question(data_raw, question) if data_raw else None
        if body and not any(k.lower() == "content-type" for k in headers):
            try:
                json.loads(body)
                headers["Content-Type"] = "application/json"
            except json.JSONDecodeError:
                pass
        resp, _status, _err = self._do_request_with_retry(method, url, headers, body)
        return self._extract_answer_from_response(resp) if resp else ""

    def call_chat_api_with_question_debug(self, curl_str: str, question: str) -> dict:
        parsed = CurlParser.parse_curl(curl_str)
        url = parsed.get("url")
        if not url:
            return {"answer": "", "error": "No URL in CURL", "status_code": None, "response_keys": [], "extracted_preview": ""}
        method = (parsed.get("method") or "POST").upper()
        headers = dict(parsed.get("headers") or {})
        data_raw = parsed.get("data")
        body = self._body_with_question(data_raw, question) if data_raw else None
        if body and not any(k.lower() == "content-type" for k in headers):
            try:
                json.loads(body)
                headers["Content-Type"] = "application/json"
            except json.JSONDecodeError:
                pass
        resp, status_code, err = self._do_request_with_retry(method, url, headers, body)
        if err:
            return {"answer": "", "error": err, "status_code": status_code, "response_keys": [], "extracted_preview": ""}
        answer = self._extract_answer_from_response(resp)
        keys = list(resp.keys()) if isinstance(resp, dict) else []
        return {
            "answer": answer,
            "error": None,
            "status_code": status_code,
            "response_keys": keys,
            "extracted_preview": (answer[:300] + "..." if len(answer) > 300 else answer),
        }

    def fetch_case_summary(self, curl_str: str) -> str:
        if not (curl_str or "").strip():
            return ""
        answer = self.call_chat_api_with_question(curl_str, CASE_SUMMARY_QUESTION)
        answer = (answer or "").strip()
        return answer if self._is_substantive_answer(answer) else ""

    def fetch_all_fields_via_chat(
        self,
        curl_str: str,
        required_fields: list[str],
        field_to_question: dict[str, str],
        delay_seconds: float | None = None,
        on_field_start: OnFieldStartCallback | None = None,
        batch_size: int = FETCH_FIELDS_BATCH_SIZE,
    ) -> dict[str, str]:
        min_delay = DEFAULT_DELAY_BETWEEN_CALLS_MIN if delay_seconds is None else delay_seconds
        max_delay = DEFAULT_DELAY_BETWEEN_CALLS_MAX if delay_seconds is None else (delay_seconds * 1.5)
        total = len(required_fields)
        result = {}

        def fetch_one(field: str) -> tuple[str, str]:
            question = field_to_question.get(field) or _default_question_for_field(field)
            answer = self.call_chat_api_with_question(curl_str, question)
            answer = (answer or "").strip()
            if answer and self._is_substantive_answer(answer):
                return (field, answer)
            return (field, "")

        for batch_start in range(0, total, batch_size):
            batch = required_fields[batch_start : batch_start + batch_size]
            for i, field in enumerate(batch):
                if on_field_start:
                    on_field_start(field, batch_start + i + 1, total)
            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = [executor.submit(fetch_one, field) for field in batch]
                for future in as_completed(futures):
                    field, answer = future.result()
                    result[field] = answer
            if max_delay > 0 and batch_start + len(batch) < total:
                self._human_delay(min_delay, max_delay)
        return result

    def fetch_broad_answers(
        self,
        curl_str: str,
        delay_seconds: float | None = None,
        on_question_start: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, str]:
        min_delay = DEFAULT_DELAY_BETWEEN_CALLS_MIN if delay_seconds is None else delay_seconds
        max_delay = DEFAULT_DELAY_BETWEEN_CALLS_MAX if delay_seconds is None else (delay_seconds * 1.5)
        total = len(BROAD_QUESTIONS)
        result = {}
        for i, (key, question) in enumerate(BROAD_QUESTIONS):
            if on_question_start:
                on_question_start(key, i + 1, total)
            answer = self.call_chat_api_with_question(curl_str, question)
            answer = (answer or "").strip()
            if answer and self._is_substantive_answer(answer):
                result[key] = answer
            else:
                result[key] = ""
            if max_delay > 0 and i < len(BROAD_QUESTIONS) - 1:
                self._human_delay(min_delay, max_delay)
        return result

    def _extract_fields_from_chunk(self, chunk: str, missing_fields: set[str]) -> tuple[dict[str, dict], list[str]]:
        """
        Helper method to call LLM for a single chunk.
        Returns tuple:
          1. dict of found fields: { "field_name": { "value": "...", "confidence": "HIGH"/"LOW" } }
          2. list of extracted fact strings
        """
        # Sort fields to ensure deterministic prompt
        current_fields = sorted(missing_fields)
        
        # Batching logic (if many fields missing)
        if len(current_fields) > 50:
             field_batches = [current_fields[j:j+50] for j in range(0, len(current_fields), 50)]
        else:
             field_batches = [current_fields]

        results = {}
        all_key_facts = []

        for batch in field_batches:
            fields_list_str = ', '.join(batch)
            
            prompt = f"""You are extracting data for a legal case from a document chunk.
Target Fields: {fields_list_str}

Chunk Content:
\"\"\"
{chunk}
\"\"\"

INSTRUCTIONS:
1. Extract values for the target fields ONLY if present in the text.
2. For each extracted value, assign a CONFIDENCE score ("HIGH" or "LOW").
3. **ALSO EXTRACT "key_facts"**: Identify key substantive facts, dates, injuries, or significant events relevant to the legal case. **CRITICAL:** Do NOT extract meta-information about the document itself (e.g. avoid facts like "This document is a blank template", "This is a letter to a hospital", "Signatures are missing"). Only extract actual case-related facts (e.g. "Incident occurred on Nov 16", "Plaintiff sustained foot injuries").
4. Return a JSON object with keys: "items" (list of fields) and "key_facts" (list of strings).

Example:
{{
  "items": [
    {{ "field": "plaintiff_name", "value": "John Doe", "confidence": "HIGH" }}
  ],
  "key_facts": [
    "Incident occurred on Jan 1, 2023 at Main St.",
    "Plaintiff was treated at Mercy Hospital."
  ]
}}
"""
            # Define Schema for structured output
            schema = {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "value": {"type": "string"},
                                "confidence": {"type": "string", "enum": ["HIGH", "LOW"]}
                            },
                            "required": ["field", "value", "confidence"],
                            "additionalProperties": False
                        }
                    },
                    "key_facts": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["items", "key_facts"],
                "additionalProperties": False
            }

            try:
                # Call LLM with schema
                raw_resp = self._llm.generate(prompt, json_mode=True, max_tokens=4096, response_schema=schema)
                
                from docgen.core.utils import JsonParser
                data = JsonParser.extract_json_from_llm(raw_resp)
                
                if not data:
                    continue

                items_list = []
                key_facts_list = []

                if isinstance(data, dict):
                    items_list = data.get("items", [])
                    key_facts_list = data.get("key_facts", [])
                    # Fallback for old format if 'items' missing but dict has keys
                    if not items_list and not key_facts_list:
                         for k, v in data.items():
                             if k == "items": continue
                             if k == "key_facts": continue
                             if isinstance(v, dict):
                                items_list.append({"field": k, "value": v.get("value"), "confidence": v.get("confidence")})
                             else:
                                items_list.append({"field": k, "value": v, "confidence": "LOW"})

                # Collect facts
                if isinstance(key_facts_list, list):
                    for f in key_facts_list:
                        if isinstance(f, str) and len(f) > 5:
                            all_key_facts.append(f)

                # Process field results
                for item in items_list:
                    if not isinstance(item, dict):
                        continue
                        
                    field = item.get("field")
                    val = str(item.get("value", "")).strip()
                    conf = str(item.get("confidence", "LOW")).upper()
                    
                    if not field or not val or val.lower() in ("null", "none", "n/a"):
                        continue
                        
                    results[field] = {"value": val, "confidence": conf}

            except ValueError as e:
                print(f"[FieldFetcher]     - Error: {e}. Full Response:\n{raw_resp}")
            except Exception as e:
                print(f"[FieldFetcher]     - Error processing chunk: {e}")
        
        return results, all_key_facts

    def _prune_irrelevant_fields(
        self, 
        missing_fields: set[str], 
        facts: list[str], 
        category: str,
        on_field_found: Callable[[str, str, str], None] | None = None
    ) -> set[str]:
        """
        Uses LLM to evaluate if any of the missing fields are completely irrelevant 
        to the current case based on the known facts and document category.
        Returns the set of fields that are STILL relevant (i.e. missing_fields - irrelevant_fields).
        """
        if not facts or not missing_fields:
            return missing_fields
            
        facts_text = "\n".join(f"- {f}" for f in facts[:80]) # limit to avoid huge prompt
        fields_text = ", ".join(missing_fields)
        
        prompt = f"""You are a legal assistant. We are drafting a "{category}".
We are trying to find values for these specific missing fields in the case documents: 
[{fields_text}]

Here are the known facts of the case we have collected so far:
---
{facts_text}
---

Based STRICTLY on the known facts and the category of the document, determine if any of these missing fields are clearly IRRELEVANT or INAPPLICABLE to this specific case. 
For example, if the facts show this is a slip and fall involving only a foot injury, a field like "cervical_spine_mri" or "property_transfer_date" is completely irrelevant.

Return a JSON array of objects for the fields from the list above that are DEFINITELY IRRELEVANT, along with the reason why they are irrelevant.
If a field might still be relevant, or if it's standard boilerplate that could be missing, or if you are unsure, DO NOT include it in the irrelevant list.

Example return format:
{{
    "irrelevant_fields": [
        {{"field": "cervical_spine_mri", "reason": "The facts only mention a foot injury, no neck or spinal injuries."}},
        {{"field": "loss_of_consortium_claim", "reason": "The facts state the plaintiff is single."}}
    ]
}}
"""
        schema = {
            "type": "object",
            "properties": {
                "irrelevant_fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "reason": {"type": "string"}
                        },
                        "required": ["field", "reason"],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["irrelevant_fields"],
            "additionalProperties": False
        }
        
        try:
            resp = self._llm.generate(prompt, json_mode=True, max_tokens=2048, response_schema=schema)
            from docgen.core.utils import JsonParser
            data = JsonParser.extract_json_from_llm(resp)
            if isinstance(data, dict) and "irrelevant_fields" in data:
                irrelevant_list = data["irrelevant_fields"]
                irrelevant_names = set(item.get("field") for item in irrelevant_list if isinstance(item, dict) and item.get("field"))
                
                # Intersect to ensure the LLM didn't hallucinate new field names
                valid_irrelevant = irrelevant_names.intersection(missing_fields)
                
                if valid_irrelevant:
                    for item in irrelevant_list:
                        f_name = item.get("field")
                        if f_name in valid_irrelevant:
                            reason = item.get('reason')
                            print(f"[FieldFetcher] Pruned irrelevant field '{f_name}': {reason}")
                            if on_field_found:
                                on_field_found(f_name, f"Irrelevant: {reason}", "PRUNED")
                    
                    return missing_fields - valid_irrelevant
        except Exception as e:
            print(f"[FieldFetcher] Error during field pruning: {e}")
            
        return missing_fields

    def fetch_fields_from_case_search(
        self,
        case_id: str | int,
        required_fields: list[str],
        firm_id: str | int = 1,
        on_doc_start: Callable[[str, int, int], None] | None = None,
        on_field_found: Callable[[str, str, str], None] | None = None, # field, value, confidence
        on_fact_found: Callable[[str], None] | None = None, # fact string
        category_of_document: str = "",
    ) -> dict[str, Any]:
        """
        Fetch field values by searching documents in Azure Search for the given case_id.
        Iteratively processes documents to find missing fields.
        ALSO accumulates key facts to generate a 'case_summary'.
        """
        from docgen.agents.data_retrieval.document_fetcher import DocumentFetcher
        from docgen.agents.data_retrieval.case_data_manager import CaseDataManager

        print(f"\n[FieldFetcher] Starting search for case_id={case_id} (firm_id={firm_id})")
        print(f"[FieldFetcher] Required fields: {required_fields}")

        # Initialize Data Manager and load cache
        data_manager = CaseDataManager()
        cached_data = data_manager.load_case_data(firm_id, case_id)
        
        found_values = {}
        confused_values = {} 
        collected_facts = [] # List of strings

        # 1. Load cached facts
        if cached_data.get("facts"):
            print(f"[FieldFetcher] Loaded {len(cached_data['facts'])} cached facts.")
            collected_facts.extend(cached_data["facts"])
            if on_fact_found:
                for fact in cached_data["facts"]:
                    on_fact_found(f"[Cache] {fact}")

        # 2. Load cached fields
        cached_fields = cached_data.get("fields", {})
        for field in required_fields:
            if field in cached_fields:
                found_values[field] = cached_fields[field]
                print(f"[FieldFetcher] Loaded cached value for '{field}'")
                if on_field_found:
                    on_field_found(field, cached_fields[field], "HIGH (Cache)")

        # 3. Determine what is still missing
        missing_fields = set(required_fields) - set(found_values.keys())

        # 3.5. Try to extract missing fields directly from cached facts first
        if missing_fields and collected_facts:
            print(f"[FieldFetcher] Attempting to extract {len(missing_fields)} missing fields from {len(collected_facts)} cached facts...")
            facts_text = "\n".join(f"- {f}" for f in collected_facts)
            
            # Split facts text if it's too long
            fact_chunks = self._split_text(facts_text)
            
            for chunk in fact_chunks:
                if not missing_fields:
                    break
                
                chunk_results, _ = self._extract_fields_from_chunk(chunk, missing_fields)
                
                for field, res in chunk_results.items():
                    if field not in missing_fields:
                        continue
                    val = res["value"]
                    conf = res["confidence"]
                    
                    if on_field_found:
                         on_field_found(field, val, f"{conf} (Extracted from Cache)")
                         
                    if conf == "HIGH":
                        found_values[field] = val
                        missing_fields.remove(field)
                        print(f"[FieldFetcher]       >>> CONFIDENTLY FOUND '{field}' FROM CACHED FACTS")
                        if field in confused_values:
                            del confused_values[field]
                    else:
                        if field not in confused_values:
                            confused_values[field] = val

        # 4. Prune completely irrelevant missing fields based on known facts and case type
        if missing_fields and collected_facts:
            print(f"[FieldFetcher] Pruning {len(missing_fields)} missing fields based on {len(collected_facts)} known facts...")
            missing_fields = self._prune_irrelevant_fields(
                missing_fields, 
                collected_facts, 
                category_of_document,
                on_field_found=on_field_found
            )
            print(f"[FieldFetcher] After pruning, {len(missing_fields)} fields remain to be searched.")

        if not missing_fields and len(collected_facts) > 5:
            # We have everything we need from cache
            print("[FieldFetcher] All fields satisfied by cache and/or pruning. Skipping document search.")
            summary = self._synthesize_case_summary(collected_facts, category_of_document)
            found_values["case_summary"] = summary
            return found_values

        # Using DocumentFetcher as a context manager ensures automatic cleanup of temp files
        with DocumentFetcher() as fetcher:
            # Fetch and store documents internally
            count = fetcher.fetch_documents(case_id, firm_id)
            
            if count == 0:
                print("[FieldFetcher] No documents returned from search.")
                return {}

            # Iterate through documents one by one
            for doc in fetcher.iter_documents():
                # We continue even if all fields are found, just to collect more facts?
                # Decision: Stop if fields found AND we have enough facts (e.g. > 20).
                # For now, let's stick to the field stopping condition but maybe peek at a few more docs if facts are sparse?
                # Actually, user wants facts. Let's process at least top 3 documents fully for facts even if fields are found.
                if not missing_fields and len(collected_facts) > 30:
                    print("[FieldFetcher] All fields found and facts collected. Stopping.")
                    break
                    
                doc_name = doc["name"]
                doc_content = doc["content"]
                idx = doc["index"]
                total = doc["total"]
                
                if on_doc_start:
                    on_doc_start(doc_name, idx, total)
                
                print(f"[FieldFetcher] Processing document {idx}/{total}: {doc_name} ({len(doc_content)} chars)")
                
                # Split content
                chunks = self._split_text(doc_content)
                print(f"[FieldFetcher]   - Document split into {len(chunks)} chunks.")
                
                del doc_content
                
                for c_idx, chunk in enumerate(chunks):
                    # If we found all fields, we might still want to scan for facts in early important docs.
                    # Heuristic: Scan for facts if we are in the first 3 docs or still missing fields.
                    if not missing_fields and idx > 3:
                        break

                    # Call helper to process chunk
                    chunk_results, chunk_facts = self._extract_fields_from_chunk(chunk, missing_fields)
                    
                    # Accumulate facts
                    collected_facts.extend(chunk_facts)
                    if on_fact_found:
                        for fact in chunk_facts:
                            on_fact_found(fact)

                    found_in_chunk = 0
                    for field, res in chunk_results.items():
                        if field not in missing_fields:
                            continue

                        val = res["value"]
                        conf = res["confidence"]
                        
                        found_in_chunk += 1
                        print(f"[FieldFetcher]       + Found '{field}': '{val}' (Confidence: {conf})")
                        
                        if on_field_found:
                             on_field_found(field, val, conf)

                        if conf == "HIGH":
                            found_values[field] = val
                            missing_fields.remove(field)
                            print(f"[FieldFetcher]       >>> CONFIDENTLY FOUND '{field}': '{val}' (Removed from search)")
                            if field in confused_values:
                                del confused_values[field]
                        else:
                            if field not in confused_values:
                                confused_values[field] = val
                    
                    if found_in_chunk == 0:
                        pass # print(f"[FieldFetcher]     - No target fields found in chunk {c_idx+1}.")
        
        # Final merge: use confused values for anything not found with HIGH confidence
        for k, v in confused_values.items():
            if k not in found_values:
                found_values[k] = v
                print(f"[FieldFetcher] Using LOW confidence value for '{k}': '{v}'")
        
        # Save results to cache (only facts we found from this session, plus new fields)
        if found_values or collected_facts:
            data_manager.save_case_data(firm_id, case_id, found_values, collected_facts)

        # --- Synthesize Case Summary from Collected Facts ---
        if collected_facts:
            print(f"[FieldFetcher] Synthesizing case summary from {len(collected_facts)} collected facts...")
            summary = self._synthesize_case_summary(collected_facts, category_of_document)
            found_values["case_summary"] = summary
            print("[FieldFetcher] Case Summary generated.")
        else:
            found_values["case_summary"] = "No facts collected to generate summary."

        print(f"[FieldFetcher] Search complete. Found {len(found_values)}/{len(required_fields)} fields.")
        return found_values

    def _synthesize_case_summary(self, facts: list[str], category: str) -> str:
        """Use LLM to condense a list of facts into a coherent summary."""
        # Deduplicate and limit
        unique_facts = sorted(list(set(facts)))
        if len(unique_facts) > 100:
            # If too many, take a sampling or first/last to fit context
            unique_facts = unique_facts[:100]
        
        facts_text = "\n".join(f"- {f}" for f in unique_facts)
        
        prompt = f"""You are a legal assistant.
Task: Create a concise "Case Summary" from the following raw collected facts.
This summary will be used to draft a "{category}".

Raw Facts:
---
{facts_text}
---

Instructions:
1. Synthesize the facts into a coherent narrative (3-5 paragraphs).
2. Focus on: Parties involved, Key Dates, The Incident/Dispute, Damages/Injuries, and Legal Context.
3. Ignore irrelevant or repetitive details.
4. Output ONLY the summary text.
"""
        try:
            return self._llm.generate(prompt, max_tokens=1000, temperature=0.2).strip()
        except Exception as e:
            print(f"[FieldFetcher] Summary generation failed: {e}")
            return "Summary generation failed."

    @staticmethod
    def _split_text(text: str, chunk_size=35000, chunk_overlap=400) -> list[str]:
        if not text:
            return []
        try:
            chunks = []
            start = 0
            print(f"[FieldFetcher] Splitting text into chunks of size {chunk_size} with overlap {chunk_overlap}")
            text_len = len(text)
            
            # Safety check
            if chunk_overlap >= chunk_size:
                print(f"[FieldFetcher] Adjusting overlap from {chunk_overlap} to {int(chunk_size * 0.1)} because it >= chunk_size")
                chunk_overlap = max(0, int(chunk_size * 0.1))

            while start < text_len:
                end = min(start + chunk_size, text_len)
                # print(f"[FieldFetcher] DEBUG: start={start}, end={end}, text_len={text_len}")
                
                if end < text_len:
                    # Try to split at newline
                    last_newline = text.rfind("\n", start, end)
                    if last_newline != -1 and last_newline > start + chunk_size - chunk_overlap:
                        end = last_newline + 1
                    else:
                        last_space = text.rfind(" ", start, end)
                        if last_space != -1 and last_space > start + chunk_size - chunk_overlap:
                            end = last_space + 1
                
                # Infinite loop protection
                if end <= start:
                    print(f"[FieldFetcher] ERROR: Infinite loop detected. start={start}, end={end}, chunk_size={chunk_size}")
                    end = min(start + chunk_size, text_len)
                    if end <= start:
                        print(f"[FieldFetcher] CRITICAL: Cannot advance. Breaking.")
                        break

                chunks.append(text[start:end])
                
                # If we reached the end of the text, stop.
                # Otherwise, the overlap backtracking will cause us to emit 
                # hundreds of redundant tiny chunks at the tail end.
                if end == text_len:
                    break

                new_start = end - chunk_overlap
                
                # Ensure we move forward
                if new_start <= start:
                     # print(f"[FieldFetcher] DEBUG: Overlap prevents forward movement. Forcing advance.")
                     new_start = start + 1
                
                start = new_start
            
            print(f"[FieldFetcher] Split text into {len(chunks)} chunks")
            return chunks
            
        except Exception as e:
            print(f"[FieldFetcher] Error splitting text: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to returning the whole text as one chunk to avoid losing data
            return [text]


def _default_question_for_field(field_name: str) -> str:
    from docgen.agents.data_retrieval.question_generator import _fallback_question
    return _fallback_question(field_name)


def fetch_from_curl(curl_str: str) -> dict:
    parsed = CurlParser.parse_curl(curl_str)
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


# Backward-compatible module-level functions
def parse_curl(curl_str: str) -> dict:
    return CurlParser.parse_curl(curl_str)


def build_curl_from_token(token: str, api_url: str, subscription_key: str | None = None, body_template: str | None = None) -> str:
    return CurlParser.build_curl_from_token(token, api_url, subscription_key, body_template)


def normalize_chat_api_input(input_str: str, api_url: str | None = None) -> str:
    return CurlParser.normalize_chat_api_input(input_str, api_url)


def flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
    return FieldFetcher.flatten_dict(d, parent_key, sep)


def get_field_values(api_response: dict, required_fields: list[str]) -> dict:
    return FieldFetcher().get_field_values(api_response, required_fields)


def call_chat_api_with_question(curl_str: str, question: str) -> str:
    return FieldFetcher().call_chat_api_with_question(curl_str, question)


def call_chat_api_with_question_debug(curl_str: str, question: str) -> dict:
    return FieldFetcher().call_chat_api_with_question_debug(curl_str, question)


def fetch_case_summary(curl_str: str) -> str:
    return FieldFetcher().fetch_case_summary(curl_str)


def fetch_all_fields_via_chat(
    curl_str: str,
    required_fields: list[str],
    field_to_question: dict[str, str],
    delay_seconds: float | None = None,
    on_field_start: OnFieldStartCallback | None = None,
) -> dict[str, str]:
    return FieldFetcher().fetch_all_fields_via_chat(
        curl_str, required_fields, field_to_question, delay_seconds, on_field_start
    )


def fetch_broad_answers(
    curl_str: str,
    delay_seconds: float | None = None,
    on_question_start: Callable[[str, int, int], None] | None = None,
) -> dict[str, str]:
    return FieldFetcher().fetch_broad_answers(curl_str, delay_seconds, on_question_start)


def fetch_fields_from_case_search(
    case_id: str | int,
    required_fields: list[str],
    firm_id: str | int = 1,
    on_doc_start: Callable[[str, int, int], None] | None = None,
    on_field_found: Callable[[str, str, str], None] | None = None,
    category_of_document: str = "",
) -> dict[str, Any]:
    return FieldFetcher().fetch_fields_from_case_search(
        case_id=case_id, 
        required_fields=required_fields, 
        firm_id=firm_id, 
        on_doc_start=on_doc_start,
        on_field_found=on_field_found,
        category_of_document=category_of_document
    )
