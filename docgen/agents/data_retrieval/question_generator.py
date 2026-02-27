"""
Generate a natural-language question for each required field, so we can call the chat API once per field.
Uses QuestionGenerator class (OOP).
"""
import json
from docgen.core.llm_client import LLMClient
from docgen.core.utils import JsonParser


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
        
        # Summaries
        if "summary" in lower or "description" in lower:
            return f"Provide a detailed summary of the {lower}."

        # Roles
        if "plaintiff" in lower and "name" in lower:
            return "What is the plaintiff's full name?"
        if "defendant" in lower and "name" in lower:
            return "What is the defendant's full name?"
        if "attorney" in lower and "name" in lower:
            return "What is the attorney's full name?"
        
        # Addresses
        if "plaintiff" in lower and ("address" in lower or "addr" in lower):
            return "What is the plaintiff's address?"
        if "defendant" in lower and ("address" in lower or "addr" in lower):
            return "What is the defendant's address?"
            
        # Dates
        if "date" in lower and ("accident" in lower or "incident" in lower):
            return "What is the date of the accident or incident?"
        if "date" in lower and "filing" in lower:
            return "What is the date of filing?"
        if "date" in lower and "birth" in lower:
            return "What is the date of birth?"
            
        # Case Info
        if "case" in lower and ("index" in lower or "number" in lower or "docket" in lower):
            return "What is the case index or docket number?"
        if "court" in lower and "name" in lower:
            return "What is the name of the court?"
        if "venue" in lower or "jurisdiction" in lower:
            return "What is the venue or jurisdiction?"
            
        # Financials
        if "amount" in lower or "damages" in lower:
            return "What is the total amount of damages claimed?"
            
        # Locations
        if "location" in lower or "place" in lower or "where" in lower:
            return "Where did the incident occur?"
            
        # General fallbacks
        if "address" in lower:
            return f"What is the address for {lower}?"
        if "name" in lower:
            return f"What is the name for {lower}?"
        
        return f"What is the {lower}?"

    def generate_questions_for_fields(self, field_names: list[str], category_of_document: str = "") -> dict[str, str]:
        """
        Given snake_case field names, return a dict mapping each field to one
        explicit, unambiguous question to ask a case Q&A API.
        
        Processes fields in batches to avoid LLM limits/timeouts.
        """
        if not field_names:
            return {}
        
        # Safely parse category if it is a JSON string
        cat_name = "General Legal Document"
        cat_details = ""
        try:
            if category_of_document and category_of_document.strip().startswith("{"):
                cat_json = json.loads(category_of_document)
                if isinstance(cat_json, dict):
                    cat_name = cat_json.get("category", cat_name)
                    cat_details = cat_json.get("details", "")
            else:
                cat_name = category_of_document or cat_name
        except Exception:
            pass

        # Batch size
        BATCH_SIZE = 10
        total_fields = len(field_names)
        combined_result = {}

        for i in range(0, total_fields, BATCH_SIZE):
            batch = field_names[i : i + BATCH_SIZE]
            fields_str = ", ".join(f'"{f}"' for f in batch)
            
            prompt = f"""You are generating questions for a legal case Q&A API (RAG system).
Your goal is to convert field names into natural language questions to retrieve facts from the case file.

Field names: {fields_str}
Document Context: {cat_name}

REQUIRED RULES:
1. **Target the Case Facts, NOT the Document**: Do NOT ask "What is the [field] in the demand letter?" or "What is the [field] in this document?". The RAG system has the case files (police reports, medical records, etc.), not the document being generated.
   - BAD: "What is the full name of the potential individual defendant mentioned in the demand letter?"
   - GOOD: "What is the defendant's full name?"
   - BAD: "What is the summary of legal claims in the demand letter?"
   - GOOD: "Summarize the legal claims and causes of action."

2. **Be Direct and Concise**: Avoid wordy phrasing.
   - BAD: "What is the specific date on which the accident occurred?"
   - GOOD: "What is the date of the accident?"

3. **Infer Roles from Context**: Use "{cat_name}" to map generic terms like "client" to specific roles (e.g., in a Demand Letter, "client" = Plaintiff).
   - If the field is "client_name", ask "What is the plaintiff's name?" (if representing plaintiff).

4. **Handle Summaries**: If a field looks like `case_summary`, `incident_description`, or `medical_summary`, ask for a comprehensive summary.
   - Example: "case_summary" -> "Provide a detailed summary of the case facts, incident, and injuries."

5. **Specific Data Points**: For names, dates, addresses, and amounts, specify exactly what is needed.
   - "date_of_loss" -> "What is the date of the loss or incident?"
   - "damages_amount" -> "What is the total amount of damages claimed?"

FORMAT: Return ONLY valid JSON. Key = field name, value = the question.
Example:
{{
  "plaintiff_name": "What is the plaintiff's full name?",
  "date_of_accident": "What is the date of the accident?",
  "defendant_address": "What is the defendant's address?",
  "case_summary": "Summarize the key facts of the case, including the incident and injuries.",
  "response_deadline": "What is the deadline for the response?"
}}"""

            try:
                # We construct a list of messages: System prompt + optional details
                prompts_list = [prompt]
                if cat_details:
                    prompts_list.append(f"Overall details of document which can help in generating questions more accurately: {cat_details}")

                response = self._llm.generate(
                    prompts_list,
                    json_mode=True,
                    max_tokens=2000,
                    temperature=0.2,
                )
                data = JsonParser.extract_json_from_llm(response)
                
                # Process this batch
                if isinstance(data, dict):
                    for f in batch:
                        q = data.get(f)
                        if isinstance(q, str) and q.strip():
                            combined_result[f] = self._ensure_unambiguous(f, q.strip())
                        else:
                            combined_result[f] = self._fallback_question(f)
                else:
                    # Fallback for entire batch if JSON fails
                    for f in batch:
                        combined_result[f] = self._fallback_question(f)

            except Exception as e:
                print(f"Error generating questions for batch {batch}: {e}")
                # Fallback for failed batch
                for f in batch:
                    combined_result[f] = self._fallback_question(f)

        return combined_result


def generate_questions_for_fields(field_names: list[str], category_of_document: str = "") -> dict[str, str]:
    """Backward-compatible: delegates to QuestionGenerator().generate_questions_for_fields."""
    return QuestionGenerator().generate_questions_for_fields(field_names, category_of_document)


def _fallback_question(field_name: str) -> str:
    """Module-level alias for field_fetcher and other callers that need the fallback by name."""
    return QuestionGenerator._fallback_question(field_name)
