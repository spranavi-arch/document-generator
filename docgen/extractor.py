"""
Extract section text from documents. Supports:
- split_document_into_sections: split one doc into ordered sections (no overlap, no gaps).
- extract_sections_from_docs: split both docs by the same section list and return combined samples per section.
- extract_one_section / extract_section_from_docs: legacy per-section extraction by name.

Uses Extractor class (OOP).
"""
from docgen.llm_client import LLMClient
from docgen.prompts import PromptsBuilder, EXTRACTION_CHUNK_SIZE
from docgen.utils import JsonParser

MAX_EXTRACTION_TOKENS = 16384


class Extractor:
    """
    Extracts section text from one or two documents using chunked LLM calls
    so full document content is returned without truncation.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or LLMClient()

    @staticmethod
    def _clean_extracted(t: str) -> str:
        if t is None or not isinstance(t, str):
            return ""
        return t.strip()

    def split_document_into_sections(self, doc: str, sections: list[dict]) -> list[str]:
        """
        Split the document into exactly the given sections in order. Uses chunked extraction.
        sections: list of {"name": str, "purpose": str} in reading order.
        Returns list of verbatim text strings, one per section.
        """
        if not (doc or "").strip() or not sections:
            return [""] * len(sections)
        n = len(sections)
        result = [""] * n
        chunk_size = EXTRACTION_CHUNK_SIZE
        start = 0
        while start < n:
            end = min(start + chunk_size, n)
            prompt = PromptsBuilder.build_split_document_into_sections_chunk_prompt(doc, sections, start, end)
            for attempt in range(2):
                try:
                    response = self._llm.generate(prompt, max_tokens=MAX_EXTRACTION_TOKENS, json_mode=True)
                    data = JsonParser.extract_json_from_llm(response)
                    if isinstance(data, dict):
                        raw = data.get("sections") or data.get("Sections")
                        if isinstance(raw, list):
                            for i, t in enumerate(raw):
                                if start + i < n:
                                    result[start + i] = self._clean_extracted(t)
                    break
                except ValueError:
                    if attempt == 1:
                        pass
                    continue
            start = end
        return result

    def extract_sections_from_docs(self, doc1: str, doc2: str, sections: list[dict]) -> list[str]:
        """
        Split both documents into the same ordered sections, then combine per section.
        Returns list of combined sample texts (one per section, same order as sections).
        """
        n = len(sections)
        parts1 = self.split_document_into_sections(doc1 or "", sections)
        parts2 = self.split_document_into_sections(doc2 or "", sections)
        if len(parts1) < n:
            parts1.extend([""] * (n - len(parts1)))
        if len(parts2) < n:
            parts2.extend([""] * (n - len(parts2)))
        combined = []
        for i in range(n):
            p1 = (parts1[i] or "").strip()
            p2 = (parts2[i] or "").strip()
            if not p1 and not p2:
                combined.append("")
            elif not p2:
                combined.append(p1)
            elif not p1:
                combined.append(p2)
            else:
                combined.append(f"From sample 1:\n{p1}\n\nFrom sample 2:\n{p2}")
        return combined

    def extract_section_from_docs(self, doc1: str, doc2: str, section_name: str) -> str:
        """Extract the section text from both sample documents and combine (legacy per-section)."""
        part1 = self.extract_one_section(doc1 or "", section_name)
        part2 = self.extract_one_section(doc2 or "", section_name)
        if not part1 and not part2:
            return ""
        if not part2:
            return part1
        if not part1:
            return part2
        return f"From sample 1:\n{part1}\n\nFrom sample 2:\n{part2}"

    def extract_one_section(self, doc: str, section_name: str) -> str:
        """Extract from the document only the full text of the section with the given name."""
        if not (doc or "").strip():
            return ""
        prompt = PromptsBuilder.build_extract_section_prompt(doc, section_name)
        response = self._llm.generate(prompt, max_tokens=4096, json_mode=True)
        data = JsonParser.extract_json_from_llm(response)
        if not isinstance(data, dict):
            return ""
        content = data.get("content") or data.get(section_name)
        if content is None:
            for k, v in data.items():
                if isinstance(v, str):
                    content = v
                    break
        return (content if isinstance(content, str) else str(content)).strip()


def split_document_into_sections(doc: str, sections: list[dict]) -> list[str]:
    return Extractor().split_document_into_sections(doc, sections)


def extract_sections_from_docs(doc1: str, doc2: str, sections: list[dict]) -> list[str]:
    return Extractor().extract_sections_from_docs(doc1, doc2, sections)


def extract_section_from_docs(doc1: str, doc2: str, section_name: str) -> str:
    return Extractor().extract_section_from_docs(doc1, doc2, section_name)


def extract_one_section(doc: str, section_name: str) -> str:
    return Extractor().extract_one_section(doc, section_name)
