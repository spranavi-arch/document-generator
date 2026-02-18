# backend/blueprint/generator.py

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from llm.client import LLMClient
from utils.text_utils import clean_text, extract_json_from_llm


logger = logging.getLogger(__name__)

llm = LLMClient()


# --------------------------------------------------
# Configuration
# --------------------------------------------------

MAX_RETRIES = 3

# Per-doc character limit for discovery so the model sees both start and end (avoids missing last pages).
# For long docs we send start + end so closing sections (signature, verification, etc.) are included.
_DISCOVERY_CHARS_HEAD = 6000
_DISCOVERY_CHARS_TAIL = 6000
_DISCOVERY_DOC_MAX = _DISCOVERY_CHARS_HEAD + _DISCOVERY_CHARS_TAIL


def _doc_for_discovery(text: str) -> str:
    return text


SECTION_KEYS = (
    "sections",
    "Sections",
    "items",
    "results",
    "blocks",
    "parts",
    "structure",
    "outline",
    "section_list",
    "chapters",
)


# --------------------------------------------------
# Fallback Templates
# --------------------------------------------------

_DEFAULT_COMPLAINT_SECTIONS = [
    ("Case Caption", "Court name, county, case index number, and parties."),
    ("Summons Notice", "Notice to defendant of obligation to respond and deadline."),
    ("Venue and Jurisdiction", "Statement of venue and jurisdictional basis."),
    ("Attorney and Party Details", "Plaintiff's attorney and party contact information."),
    ("Defendant Service Information", "Where and how defendants are to be served."),
    ("Complaint Introduction", "Introductory paragraph(s) and nature of the action."),
    ("Jurisdictional Facts", "Facts supporting jurisdiction and venue."),
    ("Cause of Action", "Statement of causes of action and legal claims."),
    ("Factual Allegations", "Numbered factual allegations describing events."),
    ("Damages and Relief Claim", "Prayer for damages and requested relief."),
    ("Signature Block", "Plaintiff or attorney signature block."),
    ("Attorney Verification or Affirmation", "Verification or affirmation by attorney."),
    ("Filing and Certification Page", "Filing instructions, certification, and proof of service."),
]


_DEFAULT_MOTION_SECTIONS = [
    ("Case Caption", "Court, case number, and parties."),
    ("Notice of Motion", "Notice of motion hearing and relief sought."),
    ("Affidavit in Support", "Sworn factual affidavit supporting motion."),
    ("Memorandum of Law", "Legal argument and citations."),
    ("Conclusion and Prayer", "Requested ruling and relief."),
    ("Signature Block", "Attorney signature and contact info."),
]


# --------------------------------------------------
# Helpers
# --------------------------------------------------


def _find_sections_list(data) -> Optional[List]:
    """Find list of sections inside arbitrary JSON."""

    if isinstance(data, list):
        return data if data else None

    if not isinstance(data, dict):
        return None

    if "name" in data:
        return [data]

    for k in SECTION_KEYS:
        v = data.get(k)
        if isinstance(v, list) and v:
            return v

    for v in data.values():
        if isinstance(v, list) and v:
            return v
        if isinstance(v, dict):
            found = _find_sections_list(v)
            if found:
                return found

    return None



def _section_item_to_pair(item) -> Optional[tuple]:
    """Convert section item to (name, purpose)."""

    if isinstance(item, dict):

        name = (
            item.get("name")
            or item.get("Name")
            or item.get("title")
            or item.get("section")
            or item.get("heading")
        )

        if name:
            purpose = (
                item.get("purpose")
                or item.get("Purpose")
                or item.get("description")
                or ""
            )

            return (str(name).strip(), str(purpose).strip())

        return None

    if isinstance(item, str) and item.strip():
        return (item.strip(), "")

    return None



def _guess_doc_type(text: str) -> str:
    """Guess document type using heuristics."""

    t = text.lower()

    if "summons" in t and "complaint" in t:
        return "complaint"

    if "notice of motion" in t or "motion" in t:
        return "motion"

    if "petition" in t:
        return "petition"

    if "affidavit" in t:
        return "affidavit"

    return "unknown"


def _parse_discovery_list(raw_list: str) -> List[Tuple[str, str]]:
    """
    Parse discovery phase output (numbered lines) into (name, purpose) pairs.
    Handles: "1. Section Name — purpose" or "1) Name - purpose" (dash/colon separators).
    """
    out = []
    for line in raw_list.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading number. or number)
        rest = re.sub(r"^\d+[.)]\s*", "", line).strip()
        if not rest:
            continue
        # Split on first " — " or " - " or ": " for purpose
        for sep in (" — ", " – ", " - ", ": "):
            if sep in rest:
                parts = rest.split(sep, 1)
                name, purpose = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
                if name:
                    out.append((name, purpose))
                break
        else:
            out.append((rest, ""))
    return out


# --------------------------------------------------
# Blueprint Generator
# --------------------------------------------------


class BlueprintGenerator:

    # ----------------------------------------------

    def _build_discovery_prompt(self, doc1: str, doc2: str) -> str:
        """Phase 1: Discover sections (free text). One section per line so we can parse without JSON."""

        return f"""You are a legal document analyst. Read both documents and identify EVERY logical section or part in reading order. The document type may be a complaint, motion, petition, affidavit, order, or other—identify the sections that THIS type of document actually contains.

Task:
- List each distinct part as a separate section. Do not merge different functions (e.g. caption vs. notice vs. allegations vs. prayer vs. signature) into one.
- Be detailed: aim for around 10–12 sections when the document has that many parts. For a short document there may be fewer; for a long or formal one there may be more (caption, notices, venue, parties, body, causes of action, facts, relief requested, signature, verification, certification, etc.).
- Base section names and purposes on what you see in the documents, not on a fixed template. Different document types have different structures.
- You MUST identify sections from the beginning through the very end of each document. Do not skip the closing pages: include any signature blocks, verification/affirmation, certification, filing instructions, or proof-of-service sections that appear at the end.

Output format (use this format only; do NOT output JSON):
- One section per line.
- Each line: a number, then the section name, then " — " (dash), then a short purpose in a few words.
- Example of the format (your actual section names will depend on the documents):
  1. [Section name from document] — [what that part does]
  2. [Next section] — [purpose]
  ...
- You MUST list at least 6 lines. If the documents have more distinct parts, list all of them (aim for detailed breakdown, around 10–12 sections when appropriate).

Doc1:
{_doc_for_discovery(doc1)}

Doc2:
{_doc_for_discovery(doc2)}
"""

    # ----------------------------------------------

    def _build_struct_prompt(self, raw_list: str) -> str:
        """Phase 2: Convert list to JSON."""

        return f"""
Convert the following section list into valid JSON.

Rules:
- Preserve order.
- Do NOT remove sections.
- Each item must have name and purpose.

Return ONLY JSON.

Format:

{{
  "sections": [
    {{"name": "...", "purpose": "..."}}
  ]
}}

Section List:
{raw_list}
"""

    # ----------------------------------------------

    def _fallback_sections(self, doc_type: str) -> List[Dict]:
        """Return fallback sections."""

        if doc_type == "motion":
            base = _DEFAULT_MOTION_SECTIONS
        else:
            base = _DEFAULT_COMPLAINT_SECTIONS

        return [
            {"id": i + 1, "name": n, "purpose": p}
            for i, (n, p) in enumerate(base)
        ]

    # ----------------------------------------------

    def generate(self, doc1: str, doc2: str) -> Dict:
        """Generate blueprint using two-phase extraction."""

        doc1 = clean_text(doc1)
        doc2 = clean_text(doc2)

        combined = doc1 + "\n" + doc2
        doc_type = _guess_doc_type(combined)

        last_error = None

        # ==================================================
        # PHASE 1 — DISCOVERY
        # ==================================================

        discovery_prompt = self._build_discovery_prompt(doc1, doc2)

        try:
            raw_list = llm.generate(
                discovery_prompt,
                json_mode=False,
                max_tokens=3000,
                temperature=0.1,
            )

            logger.info("Raw section list generated")
            logger.debug("Raw list:\n%s", raw_list)

        except Exception as e:
            logger.error("Discovery phase failed: %s", e)
            return {
                "sections": self._fallback_sections(doc_type),
                "fallback_used": True,
                "error": str(e),
            }

        # Parse discovery list first; skip structuring if we already have 5+ sections
        parsed = _parse_discovery_list(raw_list)
        if len(parsed) >= 5:
            logger.info("Using %d sections from discovery list (skipping structuring)", len(parsed))
            return {
                "sections": [
                    {"id": i + 1, "name": n, "purpose": p or ""}
                    for i, (n, p) in enumerate(parsed)
                ],
            }

        # ==================================================
        # PHASE 2 — STRUCTURING (only if discovery had < 5 parseable lines)
        # ==================================================

        struct_prompt = self._build_struct_prompt(raw_list)

        for attempt in range(1, MAX_RETRIES + 1):

            try:
                logger.info("Structuring attempt %d", attempt)

                response = llm.generate(
                    struct_prompt,
                    json_mode=True,
                    max_tokens=2000,
                    temperature=0.0,
                )

                data = extract_json_from_llm(response)

                sections = _find_sections_list(data)

                if not sections:
                    raise ValueError("No sections found in structured output")

                output = []

                for item in sections:

                    pair = _section_item_to_pair(item)

                    if pair:
                        name, purpose = pair

                        output.append({
                            "id": len(output) + 1,
                            "name": name,
                            "purpose": purpose,
                        })

                # Validation
                if len(output) < 5:
                    raise ValueError(f"Too few sections: {len(output)}")

                for s in output:
                    if not s["name"]:
                        raise ValueError("Empty section name detected")

                logger.info("Blueprint generated successfully")

                return {"sections": output}

            except Exception as e:

                last_error = e
                logger.warning("Structuring attempt %d failed: %s", attempt, e)

        # ==================================================
        # FALLBACK: parse discovery output directly
        # ==================================================

        parsed = _parse_discovery_list(raw_list)
        if len(parsed) >= 5:
            logger.info("Using %d sections parsed from discovery list (structuring returned too few)", len(parsed))
            return {
                "sections": [
                    {"id": i + 1, "name": n, "purpose": p or ""}
                    for i, (n, p) in enumerate(parsed)
                ],
            }

        logger.error("All blueprint attempts failed, using default sections")

        return {
            "sections": self._fallback_sections(doc_type),
            "fallback_used": True,
            "error": str(last_error),
        }
