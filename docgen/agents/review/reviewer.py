"""
Review and refine the complete draft document for consistency, flow, and redundancy.
Ensures the document reads as a cohesive whole rather than a collection of disjointed sections.
"""
from docgen.core.llm_client import LLMClient
from docgen.core.prompts import PromptsBuilder


class DocumentReviewer:
    """
    Acts as a Senior Legal Editor.
    Takes the full draft (concatenated sections) and refines it.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or LLMClient()

    def review_draft(self, draft_text: str, field_values: dict, category_of_document: str) -> str:
        """
        Review the full draft for consistency and flow.
        Returns the polished document text.
        """
        if not draft_text or not draft_text.strip():
            return ""

        prompt = PromptsBuilder.build_draft_review_prompt(draft_text, field_values, category_of_document)
        
        # Use a large context model if possible, or standard model
        # The prompt is large (full draft), so we need max_tokens to be high enough for the full output.
        # Azure OpenAI's GPT-4o typically handles 128k context, 4k output. 
        # If the document is very long, we might hit output limits. 
        # For now, we assume standard legal docs fit within 4k-16k output tokens (depending on model).
        
        # We'll request a high max_tokens.
        try:
            response = self._llm.generate(
                prompt, 
                max_tokens=16000,  # Try for a large output window
                temperature=0.1
            )
            return response.strip()
        except Exception as e:
            print(f"[DocumentReviewer] Error reviewing draft: {e}")
            # Fallback: return the original draft if review fails
            return draft_text
