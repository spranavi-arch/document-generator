


from docgen.core.utils import JsonParser
from docgen.core.llm_client import LLMClient


class CategoryIdentifier:
    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or LLMClient()
    
    def identify_category(self, s1: str, s2: str) -> str:
        prompt = f"""Identify the category of the document based on the following text:
        document sample 1:
        {s1}
        \n\n\n
        document sample 2:
        {s2}
        \n\n\n
        Return the category of document based on text of document. The category can be any document category which can be used in personal injury, criminal, family, civil, business, real estate, etc. 
        provide category name and the overall document structure for that category.
        also include the details like who will send that document to whome, who will be the recipient(plaintiff, defendent, court, lawfirm, hospital, etc.) of that document, etc.
        Return only the JSON object like this: {{"category": "category_name", "structure": "overall_document_structure", "details": "more details of the document like who will send that document to whome, who will be the recipient(plaintiff, defendent, court, lawfirm, hospital, etc.) of that document, etc. any details which can be useful for generating the docuemnt for that category."}}
        Return only the JSON object.
        Do not include any other text in your response.

        """
        response = self.llm.generate(prompt)
        result = JsonParser.extract_json_from_llm(response)
        
        return response