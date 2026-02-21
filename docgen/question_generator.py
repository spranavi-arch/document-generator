"""
Generate a natural-language question for each required field, so we can call the chat API once per field.
Uses QuestionGenerator class (OOP).
"""
from docgen.llm_client import LLMClient
from docgen.utils import JsonParser


class QuestionGenerator:
    """
    Converts snake_case field names into explicit, unambiguous questions
    for a legal case Q&A API (one question per field).
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or LLMClient()

    @staticmethod
    def _ensure_unambiguous(field_name: str, question: str) -> str:
        """If the question is still generic/vague, replace with fallback. Otherwise return as-is."""
        q_lower = question.lower().strip()
        generic = (
            q_lower == "what is the date?"
            or q_lower == "what is the name?"
            or q_lower == "what is the number?"
            or q_lower == "what is the address?"
            or q_lower == "what is the amount?"
            or q_lower == "where?"
            or q_lower.startswith("what is the date?")
            or (len(question) < 25 and "date" in q_lower and "accident" not in q_lower and "filing" not in q_lower and "incident" not in q_lower)
            or (len(question) < 25 and "name" in q_lower and "plaintiff" not in q_lower and "defendant" not in q_lower and "attorney" not in q_lower)
        )
        if generic:
            return QuestionGenerator._fallback_question(field_name)
        return question

    @staticmethod
    def _fallback_question(field_name: str) -> str:
        """Produce an explicit, unambiguous question from a snake_case field name."""
        if not field_name or not field_name.strip():
            return "What is the value for this field in the case?"
        f = field_name.strip()
        lower = f.lower().replace("_", " ")
        if "plaintiff" in lower and "name" in lower:
            return "What is the plaintiff's full name?"
        if "defendant" in lower and "name" in lower:
            return "What is the defendant's full name?"
        if "attorney" in lower and "name" in lower:
            return "What is the attorney's full name?"
        if "plaintiff" in lower and ("address" in lower or "addr" in lower):
            return "What is the plaintiff's full address?"
        if "defendant" in lower and ("address" in lower or "addr" in lower):
            return "What is the defendant's full address?"
        if "date" in lower and ("accident" in lower or "incident" in lower):
            return "What was the date of the accident or incident?"
        if "date" in lower and "filing" in lower:
            return "What is the date of filing of this case?"
        if "date" in lower and "birth" in lower:
            return "What is the date of birth?"
        if "date" in lower:
            return "What is the date referred to in this case? (Please specify: e.g. date of accident, filing date, or incident date.)"
        if "case" in lower and ("index" in lower or "number" in lower or "docket" in lower):
            return "What is the case index number or docket number?"
        if "index" in lower or "docket" in lower:
            return "What is the case index or docket number for this case?"
        if "number" in lower and "case" in lower:
            return "What is the case number for this case?"
        if "court" in lower and "name" in lower:
            return "What is the name of the court where the case is filed?"
        if "venue" in lower or "jurisdiction" in lower:
            return "What is the venue or jurisdiction for this case?"
        if "amount" in lower or "damages" in lower or "sum" in lower:
            return "What is the amount or sum of damages claimed (or other amount referred to) in this case?"
        if "location" in lower or "place" in lower or "where" in lower:
            return "Where did the incident or event occur? (Full address or location.)"
        if "address" in lower:
            return f"What is the full address for {lower}?"
        if "name" in lower:
            return f"What is the full name for {lower}?"
        if "number" in lower:
            return f"What is the {lower} for this case?"
        words = f.replace("_", " ")
        return f"What is the {words} in this case? (Be specific: provide the exact value from the case.)"

    def generate_questions_for_fields(self, field_names: list[str]) -> dict[str, str]:
        """
        Given snake_case field names, return a dict mapping each field to one
        explicit, unambiguous question to ask a case Q&A API.
        """
        if not field_names:
            return {}
        fields_str = ", ".join(f'"{f}"' for f in field_names)
        prompt = f"""You are generating questions for a legal case Q&A API. Each question must be EXPLICIT and UNAMBIGUOUS: the reader must know exactly which single piece of information to provide.

Field names to convert to questions: {fields_str}

REQUIRED RULES (follow strictly):
1. Every question must name BOTH the subject and the thing asked for. BAD: "What is the date?" GOOD: "What was the date of the accident?" or "What is the date of filing?"
2. For names: always state whose name. BAD: "What is the name?" GOOD: "What is the plaintiff's full name?" or "What is the defendant's name?"
3. For dates: always state which date. BAD: "What is the date?" GOOD: "What was the date of the accident?" or "What is the date of the incident?" or "What is the date of filing?"
4. For numbers: always state which number. BAD: "What is the number?" GOOD: "What is the case index number?" or "What is the docket number?" or "What is the medical bill amount?"
5. For addresses/places: state whose address or which place. BAD: "What is the address?" GOOD: "What is the plaintiff's full address?" or "Where did the incident occur?"
6. For amounts: state what amount. BAD: "What is the amount?" GOOD: "What are the total damages claimed?" or "What is the settlement amount?"
7. Derive the subject from the field name: plaintiff_name → plaintiff; date_of_accident → date of the accident; defendant_address → defendant's address; case_index_number → case index/docket number; attorney_name → attorney's name; incident_location → location where the incident occurred.

FORMAT: Return ONLY valid JSON. Key = exact field name, value = the unambiguous question. Use double quotes. Escape internal quotes with backslash.
Example:
{{"plaintiff_name": "What is the plaintiff's full name?", "date_of_accident": "What was the date of the accident or incident?", "defendant_address": "What is the defendant's full address?", "case_index_number": "What is the case index number or docket number?", "attorney_name": "What is the name of the attorney representing the plaintiff?", "incident_location": "Where did the incident occur (full address or location)?", "amount_of_damages": "What is the total amount of damages claimed?", "date_of_filing": "What is the date of filing of this case?"}}

Generate one such question for each field. No generic or vague questions."""
        response = self._llm.generate(
            prompt,
            json_mode=True,
            max_tokens=2000,
            temperature=0.0,
        )
        data = JsonParser.extract_json_from_llm(response)
        if not isinstance(data, dict):
            return {}
        result = {}
        for f in field_names:
            q = data.get(f)
            if isinstance(q, str) and q.strip():
                result[f] = self._ensure_unambiguous(f, q.strip())
            else:
                result[f] = self._fallback_question(f)
        return result


def generate_questions_for_fields(field_names: list[str]) -> dict[str, str]:
    """Backward-compatible: delegates to QuestionGenerator().generate_questions_for_fields."""
    return QuestionGenerator().generate_questions_for_fields(field_names)


def _fallback_question(field_name: str) -> str:
    """Module-level alias for field_fetcher and other callers that need the fallback by name."""
    return QuestionGenerator._fallback_question(field_name)
