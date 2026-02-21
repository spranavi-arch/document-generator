"""
Rule-based structural detection for legal documents.
Converts raw LLM text (flat stream) into typed blocks so the formatter can apply
court caption, party block, section headings, allegations, WHEREFORE, signature, verification styles.
"""

import re

from utils.legal_block_ontology import (
    BODY_PARAGRAPH,
    BULLET_ITEM,
    CAPTION_PARTY,
    CAPTION_ROLE,
    CAPTION_SEPARATOR,
    CAUSE_OF_ACTION_HEADING,
    CAUSE_OF_ACTION_TITLE,
    COUNTY_LINE,
    COURT_HEADER,
    DAMAGES_HEADING,
    DATING_LINE,
    DOC_TITLE,
    EMAIL_LINE,
    EMPTY,
    FIRM_BLOCK_LINE,
    JURAT_BLOCK,
    LEGAL_ALLEGATION,
    LINE,
    LIST_INTRO,
    MATTER_OF_LINE,
    NOTICE_TO_LINE,
    NUMBERED_PARAGRAPH,
    PHONE_FAX_LINE,
    SECTION_HEADING,
    SIGNATURE_BLOCK,
    SIGNATURE_LINE,
    SUMMONS_BODY,
    VERIFICATION_BODY,
    VERIFICATION_HEADING,
    VERSUS_LINE,
    WHEREFORE_CLAUSE,
)


def _is_separator_line(text: str) -> bool:
    """True if line is dashes/underscores (optionally ending in X)."""
    t = (text or "").strip()
    if not t or len(t) < 3:
        return False
    if t.endswith("X") or t.endswith("x"):
        t = t[:-1].strip()
    return all(c in " \t_-.\u00A0" for c in t)


def classify_paragraph(text: str) -> str:
    """
    Classify a single paragraph into an ontology block type.
    Uses legal formatting heuristics + regex; no LLM. Fast and deterministic.
    """
    t = (text or "").strip()
    if not t:
        return EMPTY

    # Separator line (----X or similar)
    if _is_separator_line(t):
        return LINE

    # Court caption: ALL CAPS and contains COURT
    if re.match(r"^[A-Z0-9\s\-\.\,]{4,}$", t) and "COURT" in t.upper():
        return COURT_HEADER

    # County / venue line: ALL CAPS, contains COUNTY or similar
    if re.match(r"^[A-Z0-9\s\-\.\,]{4,}$", t) and ("COUNTY" in t.upper() or "DISTRICT" in t.upper() or "JURISDICTION" in t.upper()):
        return COUNTY_LINE

    # -against- / versus
    if re.match(r"^\-against\-$", t, re.I) or t.strip() == "-against-" or (len(t) < 20 and "against" in t.lower() and t.count("-") >= 2):
        return VERSUS_LINE

    # WHEREFORE clause (before party check so "WHEREFORE, Plaintiff..." is not treated as caption)
    if t.strip().upper().startswith("WHEREFORE"):
        return WHEREFORE_CLAUSE

    # Allegation-style line that starts with "Respondent, its agents" / "Defendant, its agents" (before generic party check)
    if re.match(r"^(Respondent|Defendant|Plaintiff),?\s+its\s+(agents|employees)", t, re.I):
        return LEGAL_ALLEGATION

    # Party caption: short lines with Plaintiff, Defendant, Petitioner, Respondent, Claimant (name or role only)
    if any(x in t for x in ("Plaintiff", "Defendant", "Petitioner", "Respondent", "Claimant")):
        if t.endswith(",") or t.endswith(".") or len(t) < 60:
            return CAPTION_ROLE if re.match(r"^(Plaintiff|Defendant|Petitioner|Respondent|Claimant)\,?\.?$", t, re.I) else CAPTION_PARTY
        if len(t) < 80 and not re.search(r"\b(agents|servants|employees|negligent|careless|maintenance|inspection)\b", t, re.I):
            return CAPTION_PARTY

    # Document title: short, ALL CAPS (SUMMONS, NOTICE OF CLAIM, etc.)
    if t.isupper() and len(t.split()) <= 12 and len(t) < 80:
        if any(kw in t for kw in ("SUMMONS", "COMPLAINT", "NOTICE OF CLAIM", "NOTICE OF", "VERIFIED", "MOTION", "DEMAND")):
            return DOC_TITLE
        if not t.endswith(".") and not t.endswith(":"):
            return DOC_TITLE

    # TO: line (recipient)
    if t.upper().startswith("TO:") or t.upper().startswith("TO THE "):
        return NOTICE_TO_LINE

    # "In the Matter of the Claim of:" (NOTICE OF CLAIM / caption preamble)
    if re.match(r"^In\s+the\s+Matter\s+of\s+the\s+Claim\s+of\s*:?\s*$", t, re.I) or re.match(r"^In\s+the\s+Matter\s+of\s+", t, re.I) and len(t) < 60:
        return MATTER_OF_LINE

    # Jurat block: "STATE OF NEW YORK )", "COUNTY OF NASSAU ) ss.:"
    if "ss." in t and ")" in t and ("STATE OF" in t.upper() or "COUNTY OF" in t.upper()):
        return JURAT_BLOCK
    if re.match(r"^(STATE|COUNTY)\s+OF\s+[A-Z\s]+\s*\)\s*$", t, re.I) or (re.match(r"^[A-Z\s]+\s+\)\s*$", t) and ("STATE" in t.upper() or "COUNTY" in t.upper())):
        return JURAT_BLOCK

    # Damages heading: "TOTAL DAMAGES ALLEGED:", "4. The damages, and injuries sustained:"
    if "TOTAL DAMAGES ALLEGED" in t.upper() or ("DAMAGES" in t.upper() and "INJURIES SUSTAINED" in t.upper() and t.strip().endswith(":")):
        return DAMAGES_HEADING
    if re.match(r"^\d+\.\s+The\s+damages", t, re.I):
        return DAMAGES_HEADING

    # NOTICE OF CLAIM numbered points (1. The name and post-office address..., 2. The nature of the claim:, 3. The time when...)
    t_no_num = re.sub(r"^\d+[\.\)]\s+", "", t).strip().lower()
    if t_no_num.startswith("the name and post-office address of the claimant") or t_no_num.startswith("the nature of the claim") or t_no_num.startswith("the time when, the place where and the manner in which the claim arose") or t_no_num.startswith("the damages, and injuries sustained"):
        return NUMBERED_PARAGRAPH

    # List intro: "Attached hereto is:" / "Attached herein is:"
    if re.match(r"^Attached\s+(hereto|herein|herewith)\s+is\s*:?\s*$", t, re.I) or (t.strip().endswith(":") and "attached" in t.lower() and len(t) < 50):
        return LIST_INTRO

    # Bullet item: starts with "- " or "• " (often after list intro)
    if re.match(r"^[\-\•]\s+", t) or (t.strip().startswith("-") and len(t.strip()) > 2):
        return BULLET_ITEM

    # Dating line: "Dated: Mineola, New York" / "January _____, 2026"
    if re.match(r"^Dated\s*:\s*", t, re.I) or (re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+_{2,}", t, re.I)):
        return DATING_LINE

    # Firm / address / contact lines (NOTICE OF CLAIM footer, verification block)
    if re.match(r"^P:\s*\d|^F:\s*\d|^Fax\s*:", t, re.I) or (len(t) < 50 and re.match(r"^[\d\-\(\)\s\.]+$", t) and ("212-" in t or "516-" in t or "Tel" in t)):
        return PHONE_FAX_LINE
    if "@" in t and ("email" in t.lower() or ".com" in t.lower() or ".org" in t.lower()):
        return EMAIL_LINE
    if re.match(r"^\d+\s+[A-Za-z\s]+(Turnpike|Street|Avenue|Boulevard|Road|Drive|Lane),?\s*$", t) or re.match(r"^[A-Za-z\s]+,\s*(New York|NY)\s+\d{5}", t, re.I):
        return BODY_PARAGRAPH  # address-like; keep as body or could add ADDRESS_BLOCK_LINE
    if t.isupper() and len(t) < 60 and ("," in t or "LLC" in t or "P.C." in t or "PLLC" in t) and not t.endswith(":"):
        return FIRM_BLOCK_LINE

    # Section headings: ALL CAPS, short, often ends with colon
    if t.isupper() and len(t.split()) <= 15:
        if "CAUSE OF ACTION" in t or "AS AND FOR" in t:
            return CAUSE_OF_ACTION_HEADING
        if "VERIFICATION" in t or "AFFIDAVIT" in t or "JURAT" in t:
            return VERIFICATION_HEADING
        if not t.endswith(".") and (t.endswith(":") or len(t) < 50):
            return SECTION_HEADING

    # Cause of action title (e.g. NEGLIGENCE, BREACH OF CONTRACT)
    if t.isupper() and len(t.split()) <= 5 and len(t) < 40 and not t.endswith("."):
        return CAUSE_OF_ACTION_TITLE

    # Legal allegation: starts with "That on...", "By reason of...", "It is alleged that...", "Respondent, its agents...", etc.
    allegation_starts = (
        r"^That\s+on\s+",
        r"^That\s+at\s+",
        r"^That\s+the\s+",
        r"^That\s+defendant",
        r"^By\s+reason\s+of\s+",
        r"^As\s+a\s+result\s+",
        r"^At\s+all\s+times\s+",
        r"^Plaintiff\s+repeats",
        r"^Upon\s+information",
        r"^It\s+is\s+alleged\s+that\s+",
        r"^Respondent,?\s+its\s+agents",
        r"^All\s+respondent\s+had\s+",
        r"^management,?\s+maintenance",
        r"^The\s+injuries\s+sustained",
        r"^Due\s+to\s+the\s+(dangerous|negligent)",
    )
    for pat in allegation_starts:
        if re.match(pat, t, re.I):
            return LEGAL_ALLEGATION

    # Numbered paragraph: "1. ...", "2. ..."
    if re.match(r"^\d+[\.\)]\s+", t):
        return NUMBERED_PARAGRAPH

    # Signature block: underscore line or ESQ / Attorneys for
    if re.match(r"^[\s_\-]+$", t) and len(t) > 5:
        return SIGNATURE_LINE
    if "ESQ" in t.upper() or "ESQ." in t.upper():
        return SIGNATURE_BLOCK
    if "ATTORNEYS FOR" in t.upper() or "ATTORNEY FOR" in t.upper():
        return SIGNATURE_BLOCK
    if re.match(r"^_{10,}$", t) or re.match(r"^[\s_]{15,}$", t):
        return SIGNATURE_LINE

    # Verification body (after verification heading)
    if "under penalty" in t.lower() or "penalties of perjury" in t.lower() or "duly sworn" in t.lower():
        return VERIFICATION_BODY
    if re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+,?\s+an?\s+attorney", t, re.I):
        return VERIFICATION_BODY

    # Summons body (short directive)
    if "you are hereby summoned" in t.lower() or "you are hereby directed" in t.lower():
        return SUMMONS_BODY

    # Default body
    return BODY_PARAGRAPH


def split_into_paragraphs(raw: str) -> list[str]:
    """Split raw text into paragraphs (blank line or double newline = break). Keep separator lines as single paragraphs."""
    if not raw or not raw.strip():
        return []
    # Normalize line endings and split on double newline or single newline when line looks complete
    lines = []
    current = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                lines.append(" ".join(current))
                current = []
            continue
        if _is_separator_line(stripped):
            if current:
                lines.append(" ".join(current))
                current = []
            lines.append(stripped)
            continue
        current.append(stripped)
    if current:
        lines.append(" ".join(current))
    return lines


def detect_blocks(raw_text: str) -> list[tuple[str, str]]:
    """
    Convert raw LLM text into a list of (block_type, text) using rule-based classification.
    block_type is from the legal_block_ontology. Use style_matcher to resolve to template style names
    before calling inject_blocks.
    """
    paragraphs = split_into_paragraphs(raw_text)
    out = []
    for para in paragraphs:
        block_type = classify_paragraph(para)
        if block_type == EMPTY:
            continue
        # Emit (ontology_type, text). Formatter expects (style_name, text); style_matcher will convert.
        out.append((block_type, para))
    return out
