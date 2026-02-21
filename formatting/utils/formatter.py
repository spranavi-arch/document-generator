import re

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT, WD_TAB_LEADER, WD_UNDERLINE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

# Section underline (thin bottom border) for headings
try:
    from utils.html_to_docx import _paragraph_border_bottom
except Exception:
    _paragraph_border_bottom = None
from utils.style_extractor import build_style_map_from_doc, _paragraph_has_bottom_border

# Unicode checkbox characters for rendering
CHECKBOX_UNCHECKED = "\u2610"  # ☐
CHECKBOX_CHECKED = "\u2611"    # ☑


def parse_inline_formatting_markers(text: str) -> list[tuple[str, bool, bool, bool]]:
    """Parse **bold**, *italic*, and __underline__ in text; return list of (segment_text, bold, italic, underline).
    Italics are applied only where * is used (e.g. *Claimant*, *-Against-*, *respondent*); body styles have no default italic."""
    if not text or not isinstance(text, str):
        return [("", False, False, False)]
    # Split by **, __, and * (order: ** and __ before * so __ is one token)
    tokens = re.split(r"(\*\*|__|\*)", text)
    segments = []
    bold = False
    italic = False
    underline = False
    for t in tokens:
        if t == "**":
            bold = not bold
        elif t == "__":
            underline = not underline
        elif t == "*":
            italic = not italic
        else:
            if t:
                segments.append((t, bold, italic, underline))
    return segments if segments else [("", False, False, False)]


# Phrases that indicate address/signature block—do not auto-number these even when using list style
NOT_LIST_CONTENT_PHRASES = (
    "attorneys for plaintiff",
    "attorneys for defendant",
    "park avenue",
    "floor",
    "new york, new york",
    "street",
    "avenue",
    "road",
    "drive",
    "court",
    "pllc",
    "esq.",
    "tel.",
    "fax",
)


# Intro sentences before numbered allegations — keep as body paragraph, never number these
INTRO_PHRASES_NO_NUMBER = (
    "at the time of the accident",
    "at the time of the occurrence",
    "at all times relevant herein",
)


def _looks_like_list_item(text: str) -> bool:
    """True if text looks like a list item (numbered, lettered, or common list starters); False for address/signature/intro."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.strip().lower()
    for phrase in INTRO_PHRASES_NO_NUMBER:
        if t.startswith(phrase):
            return False
    # Only exclude when line starts with address/signature phrases (avoid "court" in "all lower courts" etc.)
    for phrase in NOT_LIST_CONTENT_PHRASES:
        if t.startswith(phrase) and not re.match(r"^[\dai]+[\.\)]\s*", t):
            return False
    if re.match(r"^\(\d{3}\)\s*\d{3}-\d{4}", t):
        return False
    # Numbered or lettered: "1. ...", "a. ...", "i. ..."
    if re.match(r"^\d+[\.\)]\s+", t) or re.match(r"^[a-z][\.\)]\s+", t) or re.match(r"^[ivx]+[\.\)]\s+", t):
        return True
    # Common list starters (any document type)
    list_starts = (
        "that ", "first,", "second,", "third,", "plaintiff ", "plaintiff's ", "defendant ", "the court ",
        "movant ", "respondent ", "applicant ", "petitioner ", "1.", "2.", "a.", "b.",
        "by reason of", "pursuant to", "the detailed", "the above-stated",
    )
    for start in list_starts:
        if t.startswith(start):
            return True
    return False


# Starters for allegation-style paragraphs (so we can split one block into many numbered paragraphs)
ALLEGATION_STARTERS = (
    "that ",
    "by reason of",
    "pursuant to",
    "plaintiff's ",
    "the detailed ",
    "the above-stated ",
)
# Affirmation / motion support points that should be numbered (1., 2., 3.) but don't start with "That..."
AFFIRMATION_POINT_STARTERS = (
    "i make ",
    "i make this ",
    "this action ",
    "this action was ",
    "thereafter, ",
    "thereafter ",
    "plaintiff served ",
    "a preliminary ",
    "on or about ",
    "on december ",
    "on january ",
    "on february ",
    "on march ",
    "on april ",
    "on may ",
    "on june ",
    "on july ",
    "on august ",
    "on september ",
    "on october ",
    "on november ",
)

# NOTICE OF ENTRY / NOTICE OF SETTLEMENT — do not number these even if they start with "that "
NOTICE_ENTRY_SETTLEMENT_STARTERS = (
    "that the within",
    "that an order of which the within",
)


def _is_notice_of_entry_or_settlement(text: str) -> bool:
    """True if paragraph is NOTICE OF ENTRY or NOTICE OF SETTLEMENT text (do not apply list numbering)."""
    if not text or len(text.strip()) < 15:
        return False
    t = text.strip().lower()
    return any(t.startswith(s) for s in NOTICE_ENTRY_SETTLEMENT_STARTERS)


def _starts_allegation(line: str) -> bool:
    """True if line looks like the start of a numbered allegation (e.g. 'That on...', 'By reason of...')."""
    if not line or len(line.strip()) < 10:
        return False
    if _is_notice_of_entry_or_settlement(line):
        return False
    t = line.strip().lower()
    return any(t.startswith(s) for s in ALLEGATION_STARTERS)


def _starts_affirmation_point(line: str) -> bool:
    """True if line looks like a numbered affirmation/motion point (e.g. 'I make this affirmation...', 'This action was commenced...')."""
    if not line or len(line.strip()) < 12:
        return False
    t = line.strip().lower()
    return any(t.startswith(s) for s in AFFIRMATION_POINT_STARTERS)


def _is_numbered_point_content(text: str) -> bool:
    """True if this paragraph should get list numbering (allegation or affirmation-style point)."""
    return _starts_allegation(text) or _starts_affirmation_point(text)


def _split_allegation_block(text: str) -> list[str]:
    """If text contains multiple allegation-style paragraphs, split into one string per paragraph for numbering.
    Splits on double newline first; if a chunk contains single newlines and allegation starters, split by line."""
    if not text or not text.strip():
        return []
    text = text.strip()
    # First split by double newline (paragraph boundaries)
    chunks = re.split(r"\n\s*\n", text)
    out = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        # If this chunk has single newlines and multiple lines that start like allegations, split further
        lines = [ln.strip() for ln in chunk.split("\n") if ln.strip()]
        if len(lines) <= 1:
            out.append(chunk)
            continue
        # Check if we have multiple allegation/affirmation starters in this chunk
        allegation_lines = [i for i, ln in enumerate(lines) if _starts_allegation(ln) or _starts_affirmation_point(ln)]
        if len(allegation_lines) <= 1:
            out.append(chunk)
            continue
        # Split: each line that starts an allegation or affirmation point begins a new paragraph; merge continuation lines
        current = []
        for ln in lines:
            if (_starts_allegation(ln) or _starts_affirmation_point(ln)) and current:
                out.append(" ".join(current))
                current = [ln]
            elif _starts_allegation(ln) or _starts_affirmation_point(ln):
                current = [ln]
            else:
                current.append(ln)
        if current:
            out.append(" ".join(current))
    return out if out else [text]


# Style names that are body text — always justify, never center; never inherit template italic
BODY_STYLE_NAMES = ("normal", "body text", "list paragraph", "list number", "list")

def _block_type_for_alignment(block_kind: str, section_type: str, style_name: str = "") -> str:
    """Map block_kind + section_type to alignment block_type for enforce_legal_alignment."""
    if block_kind == "line":
        return "line"
    if block_kind == "signature_line":
        return "signature"
    if block_kind == "section_underline":
        return "paragraph"
    # Body styles: always justify (prevent template center/italic from leaking)
    if (style_name or "").strip().lower() in BODY_STYLE_NAMES:
        return "paragraph"
    # Content slots: use section_type
    if section_type in ("caption",):
        return "section_header"
    if section_type in ("attorney_signature", "notary"):
        return "signature"
    if section_type == "to_section":
        return "to_section"
    return "paragraph"


def enforce_legal_alignment(block_type: str, paragraph):
    """Override alignment only for body text (justify). Non-body blocks keep template alignment (center, right, etc.)."""
    if not paragraph:
        return
    try:
        if block_type in ("paragraph", "numbered", "body"):
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        # heading, section_header, signature, address, to_section, line: leave as set by _apply_paragraph_format (template)
    except Exception:
        pass


def _template_has_alignment(style: str, style_formatting: dict) -> bool:
    """True if the template/source document explicitly set alignment for this style. Prefer source: do not override when True."""
    if not style or not style_formatting:
        return False
    fmt = (style_formatting or {}).get(style) or {}
    pf = fmt.get("paragraph_format") or {}
    return bool((pf.get("alignment") or "").strip())


def _ensure_center_only_when_template_center(paragraph, style: str, style_formatting: dict):
    """Set center only when the sample/template explicitly has alignment CENTER for this style. Do not default to center."""
    if not paragraph or not style:
        return
    try:
        fmt = (style_formatting or {}).get(style) or {}
        pf = fmt.get("paragraph_format") or {}
        template_align = (pf.get("alignment") or "").upper()
        if template_align == "CENTER":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        pass


def clear_body_italic(paragraph):
    """Remove italic from all runs so body text is not italicised by template style."""
    if not paragraph:
        return
    try:
        for run in paragraph.runs:
            run.italic = False
    except Exception:
        pass


def force_legal_run_format(paragraph):
    """Force black color on all runs (legal standard). Italic is preserved so * in input is applied."""
    if not paragraph:
        return
    try:
        for run in paragraph.runs:
            try:
                run.font.color.rgb = RGBColor(0, 0, 0)
            except Exception:
                pass
    except Exception:
        pass


def force_legal_run_format_document(doc):
    """Force black color on every run (legal standard). Italics from * in text are preserved."""
    if not doc:
        return
    try:
        for paragraph in doc.paragraphs:
            force_legal_run_format(paragraph)
    except Exception:
        pass


def _apply_paragraph_format(paragraph, fmt: dict):
    """Apply stored paragraph format dict (exact Word features: alignment, spacing, indent, line_spacing, keep_*, page_break_before)."""
    if not fmt or not paragraph:
        return
    pf = paragraph.paragraph_format
    try:
        if "alignment" in fmt and fmt["alignment"]:
            alignment = getattr(WD_ALIGN_PARAGRAPH, fmt["alignment"], None)
            if alignment is not None:
                pf.alignment = alignment
    except Exception:
        pass
    for attr, key in (
        ("space_before", "space_before"),
        ("space_after", "space_after"),
        ("left_indent", "left_indent"),
        ("right_indent", "right_indent"),
        ("first_line_indent", "first_line_indent"),
    ):
        try:
            val = fmt.get(key)
            if val is not None and isinstance(val, (int, float)):
                setattr(pf, attr, Pt(val))
        except Exception:
            pass
    try:
        if "line_spacing" in fmt and fmt["line_spacing"] is not None:
            val = fmt["line_spacing"]
            rule_name = fmt.get("line_spacing_rule")
            rule = getattr(WD_LINE_SPACING, rule_name, None) if isinstance(rule_name, str) else None
            # EXACTLY or AT_LEAST: use fixed height in points
            if rule in (WD_LINE_SPACING.EXACTLY, WD_LINE_SPACING.AT_LEAST):
                pf.line_spacing = Pt(val) if isinstance(val, (int, float)) else val
                pf.line_spacing_rule = rule
            # MULTIPLE, SINGLE, DOUBLE, ONE_POINT_FIVE: use multiplier (float)
            else:
                num = float(val) if isinstance(val, (int, float)) else None
                if num is not None and 0.25 <= num <= 3.0:
                    pf.line_spacing = num
    except Exception:
        pass
    for attr in ("page_break_before", "keep_with_next", "keep_together"):
        try:
            if attr in fmt and fmt[attr] is not None:
                setattr(pf, attr, bool(fmt[attr]))
        except Exception:
            pass
    try:
        tab_stops = fmt.get("tab_stops")
        if tab_stops and isinstance(tab_stops, list):
            pf.tab_stops.clear_all()
            for ts in tab_stops:
                pos_pt = ts.get("position_pt") if isinstance(ts, dict) else None
                if pos_pt is None:
                    continue
                align_name = (ts.get("alignment") or "LEFT") if isinstance(ts, dict) else "LEFT"
                leader_name = (ts.get("leader") or "SPACES") if isinstance(ts, dict) else "SPACES"
                align = getattr(WD_TAB_ALIGNMENT, align_name, WD_TAB_ALIGNMENT.LEFT)
                leader = getattr(WD_TAB_LEADER, leader_name, WD_TAB_LEADER.SPACES)
                pf.tab_stops.add_tab_stop(Pt(pos_pt), align, leader)
    except Exception:
        pass


def _render_checkboxes(text: str) -> str:
    """Replace [ ], [x], [X] with Unicode checkbox characters so they render in the document."""
    if not text:
        return text
    text = re.sub(r"\[\s*[xX]\s*\]", CHECKBOX_CHECKED + " ", text)
    text = re.sub(r"\[\s*\]", CHECKBOX_UNCHECKED + " ", text)
    return text


def _is_section_start(
    text: str, block_type: str, style_map: dict, valid_style_names: set,
    section_heading_samples: list = None,
) -> bool:
    """True if this heading should get a page break (template-driven: only when template had page break before this text)."""
    if not text or not text.strip():
        return False
    is_heading = (
        block_type in ("heading", "section_header")
        or (style_map.get("heading") and block_type == style_map["heading"])
        or (style_map.get("section_header") and block_type == style_map["section_header"])
    )
    if not is_heading:
        return False
    if not section_heading_samples:
        return False
    t = text.strip().lower()
    for sample in section_heading_samples:
        if sample in t or t in sample or t.startswith(sample) or sample.startswith(t):
            return True
    return False


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping (start, end) ranges."""
    if not ranges:
        return []
    sorted_r = sorted(ranges)
    out = [sorted_r[0]]
    for r0, r1 in sorted_r[1:]:
        if r0 <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], r1))
        else:
            out.append((r0, r1))
    return out


def _apply_sample_bold_to_segments(segments: list[tuple], extra_bold_phrases: list[str] | None = None) -> list[tuple]:
    """Apply bold only to the exact phrase substrings that match sample/template phrases, not whole segments.
    extra_bold_phrases: from extract_bold_phrases_from_document(template) so bold matches the uploaded sample."""
    if not segments:
        return segments
    built = "".join(seg[0] for seg in segments)
    if not built.strip():
        return segments
    lower = built.lower()
    phrases = list(BOLD_IN_SAMPLE_PHRASES)
    if extra_bold_phrases:
        for p in extra_bold_phrases:
            if p and len(p.strip()) >= 2 and p.strip() not in {q.strip() for q in phrases}:
                phrases.append(p.strip())
    bold_ranges = []
    for phrase in phrases:
        if not phrase or len(phrase) < 2:
            continue
        start = 0
        while True:
            i = lower.find(phrase.lower(), start)
            if i < 0:
                break
            bold_ranges.append((i, i + len(phrase)))
            start = i + 1
    if not bold_ranges:
        return segments
    bold_ranges = _merge_ranges(bold_ranges)
    out = []
    pos = 0
    for seg in segments:
        seg_text = seg[0]
        base_b, base_i, base_u = seg[1], seg[2], seg[3] if len(seg) >= 4 else (seg[1], seg[2], False)
        seg_start = pos
        seg_end = pos + len(seg_text)
        pos = seg_end
        # Ranges that overlap this segment (in segment-relative coords)
        in_seg = []
        for r0, r1 in bold_ranges:
            if seg_end <= r0 or seg_start >= r1:
                continue
            in_seg.append((max(0, r0 - seg_start), min(len(seg_text), r1 - seg_start)))
        in_seg = _merge_ranges(in_seg)
        if not in_seg:
            out.append((seg_text, base_b, base_i, base_u))
            continue
        # Split segment: only phrase parts get bold=True
        idx = 0
        for r0, r1 in in_seg:
            if idx < r0:
                out.append((seg_text[idx:r0], base_b, base_i, base_u))
            out.append((seg_text[r0:r1], True, base_i, base_u))
            idx = r1
        if idx < len(seg_text):
            out.append((seg_text[idx:], base_b, base_i, base_u))
    return out


def _add_paragraph_with_inline_formatting(doc, segments: list[tuple], style, run_fmt_base: dict):
    """Add a paragraph with multiple runs for bold/italic/underline segments. Each segment is (text, bold, italic, underline)."""
    p = doc.add_paragraph(style=style)
    for seg in segments:
        if len(seg) == 4:
            seg_text, bold, italic, underline = seg
        else:
            seg_text, bold, italic = seg[0], seg[1], seg[2]
            underline = False
        if not seg_text:
            continue
        run = p.add_run(seg_text)
        fmt = dict(run_fmt_base)
        if bold:
            fmt["bold"] = True
        if italic:
            fmt["italic"] = True
        if underline:
            fmt["underline"] = True
        _apply_run_format(run, fmt)
    return p


def _apply_num_pr(paragraph, num_id: int, ilvl: int = 0):
    """Set Word list numbering on a paragraph (numPr) so it displays as 1., 2., 3."""
    if not paragraph or num_id is None:
        return
    try:
        p_el = paragraph._element
        pPr = p_el.get_or_add_pPr()
        numPr = OxmlElement("w:numPr")
        numId_el = OxmlElement("w:numId")
        numId_el.set(qn("w:val"), str(num_id))
        numPr.append(numId_el)
        ilvl_el = OxmlElement("w:ilvl")
        ilvl_el.set(qn("w:val"), str(ilvl))
        numPr.append(ilvl_el)
        pPr.append(numPr)
    except Exception:
        pass


def _apply_run_format(run, fmt: dict):
    """Apply stored run/font format (bold, italic, underline, font name/size). Color is not applied so output stays black (legal standard)."""
    if not fmt or not run:
        return
    font = run.font
    try:
        if "bold" in fmt:
            font.bold = fmt["bold"]
    except Exception:
        pass
    try:
        if "italic" in fmt:
            font.italic = fmt["italic"]
    except Exception:
        pass
    try:
        if "underline" in fmt:
            u = fmt["underline"]
            if u is True or u == "True":
                font.underline = True
            elif u is False or u == "False":
                font.underline = False
            elif isinstance(u, str) and hasattr(WD_UNDERLINE, u):
                font.underline = getattr(WD_UNDERLINE, u)
            else:
                font.underline = u
    except Exception:
        pass
    try:
        if "name" in fmt and fmt["name"]:
            font.name = fmt["name"]
    except Exception:
        pass
    try:
        if "size_pt" in fmt and fmt["size_pt"] is not None:
            font.size = Pt(fmt["size_pt"])
    except Exception:
        pass
    # Force black text (legal standard); do not copy template color (e.g. blue). Italic is preserved from fmt for non-body; body gets no-italic in force_legal_run_format_document.
    try:
        font.color.rgb = RGBColor(0, 0, 0)
    except Exception:
        pass


# Fallback when template has no line samples
DEFAULT_SIGNATURE_LINE = "_________________________"
# Default separator line (dashes ending in X) so it always renders
DEFAULT_LINE = "----------------------------------------------------------------------X"


def _add_bottom_border_to_paragraph(paragraph, pt=0.5, dashed=False):
    """Add a thin bottom border to a paragraph (separator line spans full width). Use dashed=True for ----------- style."""
    try:
        p = paragraph._p
        pPr = p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "dashed" if dashed else "single")
        bottom.set(qn("w:sz"), str(int(pt * 8)))
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "000000")
        pBdr.append(bottom)
        pPr.append(pBdr)
    except Exception:
        pass


def _add_full_width_separator(doc, style=None, space_after_pt=None, dashed=False):
    """Add a full-width horizontal separator line as a paragraph with bottom border. Spans the text column.
    Use dashed=True for ----------- style; default solid for caption (line below court/county)."""
    if not doc:
        return
    try:
        p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
        # Ensure paragraph has minimal content so it has height and the border is visible
        p.add_run("\u00A0")
        _add_bottom_border_to_paragraph(p, pt=0.5, dashed=dashed)
        if space_after_pt is not None:
            p.paragraph_format.space_after = Pt(space_after_pt)
    except Exception:
        pass


def _is_separator_noise(text: str) -> bool:
    """True if text is only underscores, dashes, equals, spaces, dots, or ends with X (stray separator noise)."""
    if not text or not text.strip():
        return True
    t = text.strip()
    if not t:
        return True
    # Allow trailing X (legal separator style e.g. "------------------------------------------------------------------X")
    if t and t[-1] in ("X", "x"):
        t = t[:-1].strip()
    # Only these chars: space, underscore, hyphen, dot, equals
    allowed = set(" _-.=\u00A0\t")
    if all(c in allowed for c in t):
        return True
    if re.match(r"^[\s\-\._=]+$", t):
        return True
    return False

# Phrases that start the main body (after caption); caption = everything before this
BODY_START_PHRASES = (
    "please take notice",
    "take further notice",
    "dated:",
    "affirms the following",
    "under the penalties of perjury",
    "being duly sworn",
    "duly sworn, says",
)
# Right-column caption: index number and motion/document title (placed right-aligned, index on same line as plaintiff when merged)
RIGHT_CAPTION_PHRASES = (
    "index no",
    "index number",
    "notice of motion",
    "to restore",
    "affirmation in support",
    "affidavit of service",
    "memorandum of law",
)
# Tab position for right-aligned caption (index no., doc title): ~6" from left for standard margins
RIGHT_CAPTION_TAB_POSITION_PT = 432.0
# Block text that starts a new document (repeated caption) when not at start of paste
NEW_DOCUMENT_START_PHRASES = (
    "supreme court of the state of new york",
    "supreme court of new york",
)

# Court caption patterns: use one consistent style for all so layout doesn't vary
COURT_CAPTION_PHRASES = (
    "superior court",
    "superior court of",
    "supreme court of",
    "index no.:",
    "index no.",
    "date filed:",
    "plaintiff,",
    "defendant.",
    "-against-",
)

# Phrases that start a major section: add space_before for clear separation
SECTION_STARTER_PHRASES = (
    "to the above named defendant",
    "wherefore",
    "dated",
    "yours, etc",
    "please take notice",
    "notice of entry",
    "notice of settlement",
)
# Space before a section starter (pt) and after court caption (pt) for consistent structure
SPACE_BEFORE_SECTION_PT = 14.0
SPACE_AFTER_CAPTION_PT = 10.0
SPACE_BEFORE_SIGNATURE_PT = 18.0

# Numbered list (allegations): spacing and hanging indent for clean legal layout
SPACE_BEFORE_NUMBERED_PT = 0.0
SPACE_AFTER_NUMBERED_PT = 8.0   # space between each numbered point for readability
NUMBERED_LEFT_INDENT_PT = 18.0   # body text indented 0.25"
NUMBERED_FIRST_LINE_INDENT_PT = -18.0  # hanging: number left-aligned, description indented

# Default space after every paragraph when template does not set it (readability)
DEFAULT_SPACE_AFTER_PARAGRAPH_PT = 10.0
# Minimum space after body paragraphs so output is not cramped (override template if smaller)
MIN_SPACE_AFTER_PARAGRAPH_PT = 6.0
# Default first-line indent for body paragraphs when template does not set it (court-style)
DEFAULT_FIRST_LINE_INDENT_PT = 12.0
MIN_FIRST_LINE_INDENT_PT = 6.0  # avoid zero indent when template set tiny value
# Default line spacing within a paragraph when template does not set it (1.5 = court-style readability)
DEFAULT_LINE_SPACING_MULTIPLE = 1.5
# Space after section/heading lines (headings get this when template does not set space_after)
SPACE_AFTER_HEADING_PT = 12.0

# Cause-of-action headings (e.g. "AS AND FOR A FIRST CAUSE OF ACTION:") — treat as section header
CAUSE_OF_ACTION_PHRASE = "cause of action"

# NOTICE OF CLAIM numbered points (get list numbering 1., 2., 3. from template)
NUMBERED_CLAIM_HEADING_STARTERS = (
    "the name and post-office address of the claimant",
    "the nature of the claim",
    "the time when, the place where and the manner in which the claim arose",
    "the damages, and injuries sustained",
    "the damages, and injuries sustained:",
)

# Phrases that are bold in the sample (NOTICE OF CLAIM, captions, verification, etc.). Applied to segments when no ** in input.
BOLD_IN_SAMPLE_PHRASES = (
    "NOTICE OF CLAIM",
    "PLEASE TAKE NOTICE",
    "CITY OF NEW YORK",
    "ANTHONY SCHEMBRI",
    "Personal Injury Action",
    "TOTAL DAMAGES ALLEGED",
    "ATTORNEY'S VERIFICATION",
    "STATE OF NEW YORK",
    "COUNTY OF NASSAU",
    "The damages, and injuries sustained",
    "The damages, and Injuries sustained",
    "4. The damages",
    "SEELIG DRESSLER OCHANI",
)


def _looks_like_court_caption(text: str) -> bool:
    """True if block text is a court caption line (so we can apply one consistent style)."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.strip().lower()
    return any(p in t or t.startswith(p) for p in COURT_CAPTION_PHRASES)


def _looks_like_index_no(text: str) -> bool:
    """True if block is the case index number (Index no. EF005844-2023) for right-column caption."""
    if not text or len(text.strip()) < 5:
        return False
    t = text.strip().lower()
    return t.startswith("index no") or t.startswith("index number") or ("index no" in t and ("ef" in t or "-20" in t or re.search(r"\d{2,}", t)))


def _last_paragraph_looks_like_caption_line(doc) -> bool:
    """True if the last paragraph is a caption line we can put Index no. on the same line with (e.g. plaintiff name ending with comma, or Plaintiff,)."""
    if not doc or not doc.paragraphs:
        return False
    last = doc.paragraphs[-1]
    text = (last.text or "").strip()
    if not text or len(text) > 80:
        return False
    # Name line (e.g. ROSEANN COZZUPOLI,) or single role (Plaintiff,) — not "Defendants." which is a full line
    if text.endswith(",") and len(text) <= 60:
        return True
    if re.match(r"^(Plaintiff|Defendant|Claimant|Respondent)\,?\.?$", text, re.I):
        return True
    return False


def _should_align_right_caption(text: str) -> bool:
    """True if block should be right-aligned in the caption (Index no., NOTICE OF MOTION TO RESTORE, AFFIRMATION IN SUPPORT, etc.)."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.strip().lower()
    if _looks_like_index_no(text):
        return True
    if any(p in t for p in ("notice of motion", "to restore", "affirmation in support", "affidavit of service", "memorandum of law")):
        return len(t) <= 80
    return False


def _append_index_no_to_paragraph(paragraph, index_text: str, run_fmt: dict):
    """Append a tab and the index-no text to the paragraph; add right tab stop so index aligns right."""
    if not paragraph or not index_text:
        return
    try:
        paragraph.add_run("\t")
        run = paragraph.add_run(index_text)
        _apply_run_format(run, run_fmt or {})
        pf = paragraph.paragraph_format
        pf.tab_stops.add_tab_stop(Pt(RIGHT_CAPTION_TAB_POSITION_PT), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.SPACES)
    except Exception:
        pass


def _should_align_left_caption_block(text: str) -> bool:
    """True if this block is part of the court caption (court, county, parties, -against-) and should be left-aligned."""
    if not text or len(text.strip()) < 2:
        return False
    t = text.strip()
    lower = t.lower()
    if _looks_like_court_caption(t):
        return True
    if t.isupper() and ("COURT" in t or "COUNTY" in t) and len(t) < 60:
        return True
    if re.match(r"^\-against\-\.?$", t, re.I) or (len(t) < 15 and "against" in lower and t.count("-") >= 2):
        return True
    if re.match(r"^(Claimant|Respondent|Plaintiff|Defendant|Petitioner)\,?\.?$", t, re.I):
        return True
    if len(t) < 55 and (t.endswith(",") or t.endswith(".")) and (t.isupper() or ("," in t and len(t.split()) <= 4)):
        if any(x in lower for x in ("plaintiff", "defendant", "claimant", "respondent", "city of", "county of")):
            return True
        if t.isupper() and not lower.startswith("to:") and not lower.startswith("attached"):
            return True
    if re.match(r"^In\s+the\s+Matter\s+of\s+", t, re.I) and len(t) < 70:
        return True
    return False


def _should_align_center_caption(text: str) -> bool:
    """True if this block is a document title and should be centered (NOTICE OF MOTION, AFFIRMATION IN SUPPORT, etc.). Caption block is left-aligned."""
    if not text or len(text.strip()) < 2:
        return False
    t = text.strip()
    lower = t.lower()
    # Document titles only (centered): NOTICE OF MOTION, AFFIRMATION IN SUPPORT, AFFIDAVIT OF SERVICE, NOTICE OF CLAIM, SUMMONS, etc.
    if len(t) <= 80 and (
        lower in ("notice of claim", "summons", "verified complaint", "complaint")
        or (t.isupper() and any(kw in lower for kw in ("notice of claim", "summons", "complaint", "motion", "affirmation", "affidavit", "restore", "support", "service")) and len(t.split()) <= 12)
        or (lower.startswith("notice of claim") or lower == "notice of claim")
        or (lower.startswith("notice of motion") or lower.startswith("affirmation in support") or lower.startswith("affidavit of"))
    ):
        return True
    return False


def _looks_like_caption_separator(line_text: str) -> bool:
    """True if line is a caption separator (mostly dashes/underscores, optionally ending with X)."""
    if not line_text or len(line_text.strip()) < 5:
        return False
    t = line_text.strip()
    # Allow trailing X or x (court caption separator)
    core = t.rstrip("Xx").rstrip()
    if not core:
        return True
    # Underscore-only lines are name/signature lines (e.g. under plaintiff name), not full-width separators
    if all(c in " _\t" for c in core) and "-" not in t and "X" not in t.upper():
        return False
    # Mostly dashes, underscores, or spaces
    allowed = set("-_\t ")
    return len(core) >= 3 and all(c in allowed for c in core)


def _is_underscore_name_line(line_text: str) -> bool:
    """True if line is only underscores/spaces (plaintiff/defendant name line to render as-is)."""
    if not line_text or len(line_text.strip()) < 3:
        return False
    t = line_text.strip()
    return all(c in " _\u00A0\t" for c in t)


def _split_underscore_line_and_name(text: str) -> tuple[str | None, str | None]:
    """If text is '_____..._____\\nNAME,' return (underscore_line, name_line); else (None, None)."""
    if not text or "\n" not in text:
        return None, None
    first_line = text.split("\n", 1)[0].strip()
    rest = text.split("\n", 1)[1].strip()
    if not rest or not _is_underscore_name_line(first_line) or len(first_line) < 8:
        return None, None
    # Rest should look like a party name (ends with comma) or role (Plaintiff, / Defendant.)
    if rest.endswith(",") or re.match(r"^(Plaintiff|Defendant|Claimant|Respondent)\,?\.?$", rest, re.I):
        return first_line, rest
    if len(rest) <= 70 and rest[0].isupper():  # e.g. ROSEANN COZZUPOLI,
        return first_line, rest
    return None, None


def _looks_like_jurat_line(text: str) -> bool:
    """True if paragraph is part of jurat block (STATE OF NEW YORK, COUNTY OF X, ) ss.:) — use keep_with_next to avoid page break inside block."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.strip()
    lower = t.lower()
    if re.match(r"^(STATE|COUNTY)\s+OF\s+", t, re.I) and len(t) < 55:
        return True
    if "ss." in lower and ")" in t and len(t) < 30:
        return True
    if re.match(r"^\)\s*ss\.\s*:", t, re.I):
        return True
    return False


def _should_align_left_only(text: str) -> bool:
    """True if this block should be left-aligned (not justified): TO:/FROM:, addresses, TOTAL DAMAGES ALLEGED, Attached hereto, bullet items."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.strip()
    lower = t.lower()
    if lower.startswith("to:") or lower.startswith("to the ") or lower.startswith("from:"):
        return True
    if re.match(r"^P:\s*\d|^F:\s*\d|^Fax\s*:", t, re.I):
        return True
    if "@" in t and (".com" in t or ".org" in t) and len(t) < 80:
        return True
    if re.match(r"^\d+\s+[A-Za-z0-9\s,]+(Turnpike|Street|Avenue|Boulevard|Road|Drive|Lane),?\s*$", t, re.I):
        return True
    if re.match(r"^[A-Za-z\s]+,?\s*(New York|NY|Connecticut|CT)\s+\d{5}", t, re.I):
        return True
    if lower.startswith("total damages alleged") or (lower.startswith("total damages") and ":" in t):
        return True
    if re.match(r"^attached\s+(hereto|herein|herewith)\s+is\s*:?\s*$", lower) or (lower.startswith("attached ") and ":" in t and len(t) < 120):
        return True
    if re.match(r"^[\-\•]\s+", t) or (t.startswith("-") and len(t) > 2):
        return True
    return False


def _looks_like_list_intro(text: str) -> bool:
    """True if paragraph is list intro (Attached hereto is:) — use keep_with_next so list stays with bullets on same page."""
    if not text or len(text.strip()) < 5:
        return False
    t = text.strip().lower()
    return bool(re.match(r"^attached\s+(hereto|herein|herewith)\s+is\s*:?\s*$", t)) or ("attached" in t and t.endswith(":") and len(t) < 55)


def _looks_like_bullet_item(text: str) -> bool:
    """True if paragraph is a bullet list item (- ... or • ...) — use keep_with_next so list block stays on same page."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.strip()
    return bool(re.match(r"^[\-\•]\s+", t)) or (t.startswith("-") and len(t) > 2)


def _is_section_starter(text: str) -> bool:
    """True if paragraph starts a major section (TO THE ABOVE NAMED DEFENDANT, WHEREFORE, Dated, etc.)."""
    if not text or len(text.strip()) < 4:
        return False
    t = text.strip().lower()
    return any(t.startswith(p) or t == p for p in SECTION_STARTER_PHRASES)


def _looks_like_cause_of_action_heading(text: str) -> bool:
    """True if paragraph is a cause-of-action heading (e.g. 'AS AND FOR A FIRST CAUSE OF ACTION:')."""
    if not text or len(text.strip()) < 10:
        return False
    t = text.strip().lower()
    return CAUSE_OF_ACTION_PHRASE in t and "as and for" in t


def _looks_like_numbered_claim_heading(text: str) -> bool:
    """True if paragraph is a NOTICE OF CLAIM numbered point (1. The name and post-office address..., 2. The nature of the claim:, etc.)."""
    if not text or len(text.strip()) < 10:
        return False
    t = text.strip().lower()
    # Strip leading "1.", "2.", "3." etc. for matching
    t = re.sub(r"^\d+[\.\)]\s+", "", t).strip()
    return any(t.startswith(s) for s in NUMBERED_CLAIM_HEADING_STARTERS)


def _looks_like_attorney_verification_heading(text: str) -> bool:
    """True if paragraph is the ATTORNEY'S VERIFICATION heading — add page break before it."""
    if not text or len(text.strip()) < 5:
        return False
    t = text.strip().lower()
    return "attorney" in t and "verification" in t and len(t) < 80


def _apply_numbered_paragraph_layout(paragraph):
    """Apply consistent spacing and hanging indent for numbered points: number left-aligned, description indented."""
    if not paragraph:
        return
    try:
        pf = paragraph.paragraph_format
        pf.space_before = Pt(SPACE_BEFORE_NUMBERED_PT)
        pf.space_after = Pt(SPACE_AFTER_NUMBERED_PT)
        pf.left_indent = Pt(NUMBERED_LEFT_INDENT_PT)
        pf.first_line_indent = Pt(NUMBERED_FIRST_LINE_INDENT_PT)
    except Exception:
        pass


def _space_pt(pf_attr) -> float | None:
    """Return paragraph format space value in pt, or None if unset/zero."""
    if pf_attr is None:
        return None
    if hasattr(pf_attr, "pt"):
        return getattr(pf_attr, "pt", None)
    return float(pf_attr) if isinstance(pf_attr, (int, float)) else None


def _looks_like_short_section_heading(text: str) -> bool:
    """True if paragraph is a short all-caps heading (e.g. 'NEGLIGENCE') that should have space after."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.strip()
    if len(t) > 50 or not t.isupper():
        return False
    # Avoid "AS AND FOR A FIRST CAUSE OF ACTION:" (handled by cause-of-action) and long lines
    if "cause of action" in t.lower() or ":" in t:
        return False
    return True


def _looks_like_document_title_heading(text: str) -> bool:
    """True if paragraph is a main document title (NOTICE OF MOTION TO RESTORE, AFFIRMATION IN SUPPORT, AFFIDAVIT OF SERVICE)."""
    if not text or len(text.strip()) < 5:
        return False
    lower = text.strip().lower()
    return (
        lower.startswith("notice of motion")
        or lower.startswith("affirmation in support")
        or lower.startswith("affidavit of service")
        or lower.startswith("affidavit of")
        or (lower.startswith("notice of") and len(text.strip()) <= 60)
    )


def _apply_section_spacing(paragraph, text: str, is_court_caption: bool):
    """Add space_before/space_after when template did not set them, or enforce minimums (court-style spacing)."""
    if not paragraph:
        return
    try:
        pf = paragraph.paragraph_format
        before_pt = _space_pt(getattr(pf, "space_before", None))
        after_pt = _space_pt(getattr(pf, "space_after", None))
        if _is_section_starter(text) and (before_pt is None or before_pt == 0):
            pf.space_before = Pt(SPACE_BEFORE_SECTION_PT)
        if _looks_like_cause_of_action_heading(text):
            if before_pt is None or before_pt == 0:
                pf.space_before = Pt(SPACE_BEFORE_SECTION_PT)
            if after_pt is None or after_pt == 0:
                pf.space_after = Pt(SPACE_AFTER_HEADING_PT)
        if _looks_like_short_section_heading(text) and (after_pt is None or after_pt == 0):
            pf.space_after = Pt(SPACE_AFTER_HEADING_PT)
        if _looks_like_document_title_heading(text):
            if before_pt is None or before_pt == 0 or before_pt < SPACE_BEFORE_SECTION_PT:
                pf.space_before = Pt(SPACE_BEFORE_SECTION_PT)
            if after_pt is None or after_pt == 0 or after_pt < SPACE_AFTER_HEADING_PT:
                pf.space_after = Pt(SPACE_AFTER_HEADING_PT)
        if _looks_like_attorney_verification_heading(text):
            if before_pt is None or before_pt == 0:
                pf.space_before = Pt(SPACE_BEFORE_SECTION_PT)
            if after_pt is None or after_pt == 0:
                pf.space_after = Pt(SPACE_AFTER_HEADING_PT)
        if is_court_caption and (after_pt is None or after_pt == 0 or after_pt < MIN_SPACE_AFTER_PARAGRAPH_PT):
            pf.space_after = Pt(SPACE_AFTER_CAPTION_PT)
    except Exception:
        pass


def _apply_default_paragraph_spacing(paragraph, style: str = None, style_formatting: dict = None):
    """Set space_after when template did not set it, or when template set a very small value (avoid cramped output)."""
    if not paragraph:
        return
    try:
        after_pt = _space_pt(getattr(paragraph.paragraph_format, "space_after", None))
        if after_pt is None or after_pt == 0:
            paragraph.paragraph_format.space_after = Pt(DEFAULT_SPACE_AFTER_PARAGRAPH_PT)
        elif after_pt < MIN_SPACE_AFTER_PARAGRAPH_PT:
            paragraph.paragraph_format.space_after = Pt(DEFAULT_SPACE_AFTER_PARAGRAPH_PT)
    except Exception:
        pass


def _apply_default_body_indent(paragraph, style: str = None, style_formatting: dict = None):
    """Set first-line indent when template did not set it, or when template set a very small value (court-style)."""
    if not paragraph:
        return
    try:
        pf = paragraph.paragraph_format
        current = _space_pt(getattr(pf, "first_line_indent", None))
        if current is None or current == 0:
            pf.first_line_indent = Pt(DEFAULT_FIRST_LINE_INDENT_PT)
        elif 0 < current < MIN_FIRST_LINE_INDENT_PT:
            pf.first_line_indent = Pt(DEFAULT_FIRST_LINE_INDENT_PT)
    except Exception:
        pass


def _apply_default_line_spacing(paragraph, style: str = None, style_formatting: dict = None):
    """Set line spacing only when the template/source did not set it (prefer source document)."""
    if not paragraph:
        return
    try:
        if style is not None and style_formatting is not None:
            fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
            if fmt.get("line_spacing") is not None or fmt.get("line_spacing_rule") is not None:
                return
        pf = paragraph.paragraph_format
        current = getattr(pf, "line_spacing", None)
        rule = getattr(pf, "line_spacing_rule", None)
        if current is None and rule is None:
            pf.line_spacing = DEFAULT_LINE_SPACING_MULTIPLE
            pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    except Exception:
        pass


def _split_into_document_segments(blocks: list) -> list[list]:
    """Split blocks into one list per document. New document starts at repeated court heading (not at index 0)."""
    if not blocks:
        return []
    segment_starts = [0]
    for i in range(1, len(blocks)):
        bt, text = blocks[i]
        t = (text or "").strip().lower()
        if not t:
            continue
        for phrase in NEW_DOCUMENT_START_PHRASES:
            if t.startswith(phrase) or t == phrase.strip() or phrase in t[:80]:
                segment_starts.append(i)
                break
    out = []
    for j in range(len(segment_starts)):
        start = segment_starts[j]
        end = segment_starts[j + 1] if j + 1 < len(segment_starts) else len(blocks)
        out.append(blocks[start:end])
    return out


def _split_caption_body(blocks: list) -> tuple[list, list, list]:
    """Split blocks into caption_left, caption_right, body. Returns (caption_left, caption_right, body_blocks)."""
    if not blocks:
        return [], [], []
    body_start_idx = None
    for i, (bt, text) in enumerate(blocks):
        t = (text or "").strip().lower()
        if not t:
            continue
        for phrase in BODY_START_PHRASES:
            if phrase in t or t.startswith(phrase):
                body_start_idx = i
                break
        if body_start_idx is not None:
            break
    if body_start_idx is None:
        return [], [], blocks
    caption_blocks = blocks[:body_start_idx]
    body_blocks = blocks[body_start_idx:]
    left, right = [], []
    for b in caption_blocks:
        bt, text = b
        t = (text or "").strip().lower()
        is_right = any(p in t for p in RIGHT_CAPTION_PHRASES) or (t == "to restore")
        if is_right:
            right.append(b)
        else:
            left.append(b)
    return left, right, body_blocks


def _resolve_style(block_type: str, style_map: dict, style_formatting: dict):
    """Resolve block_type to a style name: use template style name if present, else logical style_map."""
    if block_type in style_formatting:
        return block_type
    return style_map.get(block_type, style_map.get("paragraph"))


def _add_paragraph_to_cell_with_inline_formatting(cell, segments: list, style, run_fmt_base: dict):
    """Add one paragraph to a table cell with multiple runs for bold/italic/underline segments."""
    p = cell.add_paragraph(style=style)
    for seg in segments:
        if len(seg) == 4:
            seg_text, bold, italic, underline = seg
        else:
            seg_text, bold, italic = seg[0], seg[1], seg[2]
            underline = False
        if not seg_text:
            continue
        run = p.add_run(seg_text)
        fmt = dict(run_fmt_base)
        if bold:
            fmt["bold"] = True
        if italic:
            fmt["italic"] = True
        if underline:
            fmt["underline"] = True
        _apply_run_format(run, fmt)
    return p


def _render_caption_blocks_into_cell(cell, blocks: list, style_map: dict, style_formatting: dict, valid_style_names: set, bold_phrases_from_template=None, right_align=False):
    """Render a list of (block_type, text) into a table cell (one paragraph per block)."""
    for block_type, text in blocks:
        text = (text or "").strip()
        if not text:
            continue
        style = _resolve_style(block_type, style_map, style_formatting)
        if style not in valid_style_names:
            style = style_map.get("paragraph") or (list(valid_style_names)[0] if valid_style_names else "Normal")
        # Render underscore name line then party name as two paragraphs when present
        underscore_line, name_part = _split_underscore_line_and_name(text)
        if underscore_line is not None and name_part is not None:
            p_line = cell.add_paragraph(underscore_line, style=style)
            fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
            _apply_paragraph_format(p_line, fmt)
            if right_align:
                p_line.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            text = name_part
        text = _render_checkboxes(text)
        segments = parse_inline_formatting_markers(text)
        segments = _apply_sample_bold_to_segments(segments, extra_bold_phrases=bold_phrases_from_template)
        run_fmt = (style_formatting.get(style) or {}).get("run_format") or {}
        p = _add_paragraph_to_cell_with_inline_formatting(cell, segments, style, run_fmt)
        fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
        _apply_paragraph_format(p, fmt)
        if right_align:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT


def inject_blocks(doc, blocks, style_map=None, style_formatting=None, line_samples=None, section_heading_samples=None, template_structure=None, numbered_num_id=None, numbered_ilvl=0, bold_phrases_from_template=None, caption_table_layout=None):
    """Inject text into template structure. When template_structure is provided (slot-fill):
    assign paragraph.style = template style only — no manual formatting. Word handles layout,
    numbering, spacing from style definitions. Renderer never invents formatting.
    numbered_num_id/numbered_ilvl: when set, paragraphs with the numbered style get list numbering from the template (1., 2., 3. or a., b., etc.).
    bold_phrases_from_template: phrases extracted from template bold runs; bold is applied to matching substrings in generated text.
    caption_table_layout: when use_table is True, caption (left/right) is rendered in a 1x2 table instead of paragraphs."""
    if style_map is None:
        style_map = build_style_map_from_doc(doc)[0]
    if not style_map:
        style_map = {"heading": None, "section_header": None, "paragraph": None, "numbered": None, "wherefore": None}
    style_formatting = style_formatting or {}
    caption_table_layout = caption_table_layout or {}
    line_samples = line_samples or []
    section_heading_samples = section_heading_samples or []
    valid_style_names = set(style_formatting.keys())

    # Structure-driven slot-fill: parser only — assign existing text to slots; never invent or fallback.
    if template_structure and len(blocks) == len(template_structure):
        seen = set()  # Caption deduplication: do not render the same block text twice (stops repeated court headers)
        for i in range(len(template_structure)):
            spec = template_structure[i]
            style = (blocks[i][0] if isinstance(blocks[i], (list, tuple)) else spec.get("style", "Normal"))
            slot_text = (blocks[i][1] if isinstance(blocks[i], (list, tuple)) and len(blocks[i]) > 1 else (blocks[i] if isinstance(blocks[i], str) else ""))
            slot_text = (slot_text or "").strip()
            if style not in valid_style_names:
                style = style_map.get("paragraph") or (list(valid_style_names)[0] if valid_style_names else "Normal")
            block_kind = spec.get("block_kind", "paragraph")
            section_type = spec.get("section_type", "body")
            template_text = (spec.get("template_text") or "").strip()
            if spec.get("page_break_before"):
                doc.add_page_break()
            elif _looks_like_attorney_verification_heading(slot_text):
                doc.add_page_break()
            if block_kind == "line":
                # Always render a line: use template_text when present, else add separator so line is not missing
                if (slot_text or template_text).strip():
                    if template_text:
                        if _looks_like_caption_separator(template_text):
                            _add_full_width_separator(doc, style=style, space_after_pt=SPACE_AFTER_CAPTION_PT, dashed=False)
                        else:
                            p = doc.add_paragraph(template_text, style=style)
                            fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                            _apply_paragraph_format(p, fmt)
                            enforce_legal_alignment(_block_type_for_alignment(block_kind, section_type, style), p)
                else:
                    # Empty line slot (e.g. template had a graphic/border line): still render visible separator
                    _add_full_width_separator(doc, style=style, space_after_pt=SPACE_AFTER_CAPTION_PT, dashed=False)
                continue
            if block_kind == "section_underline":
                p = doc.add_paragraph(style=style)
                if _paragraph_border_bottom:
                    _paragraph_border_bottom(p, pt=0.5)
                fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                _apply_paragraph_format(p, fmt)
                enforce_legal_alignment(_block_type_for_alignment(block_kind, section_type, style), p)
                continue
            if block_kind == "signature_line":
                if template_text:
                    p = doc.add_paragraph(template_text, style=style)
                    fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                    _apply_paragraph_format(p, fmt)
                    enforce_legal_alignment(_block_type_for_alignment(block_kind, section_type, style), p)
                continue
            # Content slots: empty → skip; dedupe then render
            if not slot_text:
                continue
            if slot_text in seen:
                continue
            seen.add(slot_text)
            segments = parse_inline_formatting_markers(_render_checkboxes(slot_text))
            segments = _apply_sample_bold_to_segments(segments, extra_bold_phrases=bold_phrases_from_template)
            run_fmt = (style_formatting.get(style) or {}).get("run_format") or {}
            p = doc.add_paragraph(style=style)
            for seg in segments:
                if len(seg) == 4:
                    seg_text, bold, italic, underline = seg
                else:
                    seg_text, bold, italic = seg[0], seg[1], seg[2]
                    underline = False
                if not seg_text:
                    continue
                run = p.add_run(seg_text)
                fmt = dict(run_fmt)
                if bold:
                    fmt["bold"] = True
                if italic:
                    fmt["italic"] = True
                if underline:
                    fmt["underline"] = True
                _apply_run_format(run, fmt)
            fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
            _apply_paragraph_format(p, fmt)
            align_type = _block_type_for_alignment(block_kind, section_type, style)
            enforce_legal_alignment(align_type, p)
            if _should_align_right_caption(slot_text.strip()):
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            elif not _template_has_alignment(style, style_formatting) and _should_align_left_only(slot_text.strip()):
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            if _looks_like_jurat_line(slot_text):
                try:
                    p.paragraph_format.keep_with_next = True
                except Exception:
                    pass
            if _looks_like_list_intro(slot_text.strip()) or _looks_like_bullet_item(slot_text.strip()):
                try:
                    p.paragraph_format.keep_with_next = True
                except Exception:
                    pass
        trim_trailing_separators(doc)
        return

    # Fallback path when no template_structure: still use style only; no fake numbering (Word handles via style).
    # Deduplicate long repeated blocks (e.g. same summons/caption pasted multiple times) so output isn't bloated.
    MIN_DEDUP_LEN = 80  # Only skip when this many chars and we've seen this exact text before
    seen_long_text = set()

    segments = _split_into_document_segments(blocks)
    for seg_idx, segment in enumerate(segments):
        if seg_idx > 0:
            doc.add_page_break()
        caption_left, caption_right, body_blocks = _split_caption_body(segment)
        if caption_table_layout.get("use_table") and (caption_left or caption_right):
            table = doc.add_table(rows=1, cols=2)
            left_cell, right_cell = table.rows[0].cells[0], table.rows[0].cells[1]
            if caption_left:
                _render_caption_blocks_into_cell(left_cell, caption_left, style_map, style_formatting, valid_style_names, bold_phrases_from_template, right_align=False)
            if caption_right:
                _render_caption_blocks_into_cell(right_cell, caption_right, style_map, style_formatting, valid_style_names, bold_phrases_from_template, right_align=True)
            blocks_to_render = body_blocks
        else:
            blocks_to_render = caption_left + caption_right + body_blocks if (caption_left or caption_right) else segment
        section_break_added_in_segment = False

        for block_type, text in blocks_to_render:
            text = (text or "").strip()

            # Skip long duplicate paragraphs (repeated summons, captions, allegations from concatenated input)
            if len(text) >= MIN_DEDUP_LEN:
                normalized = re.sub(r"\s+", " ", text).strip()
                if normalized in seen_long_text:
                    continue
                seen_long_text.add(normalized)

            if block_type == "page_break":
                doc.add_page_break()
                continue

            if block_type == "signature_line":
                label = (text.strip() if text and text.strip() and text.strip() not in ("---", "—", "-") else None)
                line_text = None
                if line_samples:
                    for s in line_samples:
                        t = s.get("text", "")
                        if "_" in t and t.strip().replace("_", "").replace(" ", "") == "":
                            line_text = t
                            break
                    if line_text is None:
                        line_text = line_samples[0].get("text", DEFAULT_SIGNATURE_LINE)
                if not line_text:
                    line_text = DEFAULT_SIGNATURE_LINE
                if label:
                    line_text = f"{line_text}  {label}"
                style = _resolve_style("paragraph", style_map, style_formatting)
                p = doc.add_paragraph(line_text, style=style)
                fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                _apply_paragraph_format(p, fmt)
                if _space_pt(getattr(p.paragraph_format, "space_before", None)) in (None, 0):
                    p.paragraph_format.space_before = Pt(SPACE_BEFORE_SIGNATURE_PT)
                enforce_legal_alignment("signature", p)
                continue

            if block_type == "section_underline":
                style = _resolve_style("paragraph", style_map, style_formatting)
                p = doc.add_paragraph(style=style)
                if _paragraph_border_bottom:
                    _paragraph_border_bottom(p, pt=0.5)
                fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                _apply_paragraph_format(p, fmt)
                enforce_legal_alignment("paragraph", p)
                continue

            if block_type == "line":
                line_text = (text or "").strip()
                if line_text and ("block_type" in line_text or "text field" in line_text):
                    line_text = ""
                if not line_text and line_samples:
                    for s in line_samples:
                        t = s.get("text", "")
                        if t.rstrip().endswith("X") or t.rstrip().endswith("x"):
                            line_text = t
                            break
                    if not line_text:
                        line_text = line_samples[0].get("text", DEFAULT_LINE)
                if not line_text:
                    line_text = DEFAULT_LINE
                style = _resolve_style("paragraph", style_map, style_formatting)
                if _looks_like_caption_separator(line_text):
                    _add_full_width_separator(doc, style=style, space_after_pt=SPACE_AFTER_CAPTION_PT, dashed=False)
                else:
                    p = doc.add_paragraph(line_text, style=style)
                    fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                    _apply_paragraph_format(p, fmt)
                    enforce_legal_alignment("line", p)
                continue

            if not text:
                continue

            # Caption: render underscore name line (____________________________________________) then party name (ROSEANN COZZUPOLI,) as two paragraphs
            underscore_line, name_part = _split_underscore_line_and_name(text)
            if underscore_line is not None and name_part is not None:
                style_line = _resolve_style("paragraph", style_map, style_formatting)
                if style_line not in valid_style_names:
                    style_line = style_map.get("paragraph") or (list(valid_style_names)[0] if valid_style_names else "Normal")
                p_line = doc.add_paragraph(underscore_line, style=style_line)
                fmt_line = (style_formatting.get(style_line) or {}).get("paragraph_format") or {}
                _apply_paragraph_format(p_line, fmt_line)
                enforce_legal_alignment("paragraph", p_line)
                text = name_part

            # Split one block into multiple numbered paragraphs when it contains many allegations (e.g. paste of "That on...", "By reason of...")
            numbered_style = style_map.get("numbered") and (not valid_style_names or style_map["numbered"] in valid_style_names)
            first_line = text.split("\n")[0].strip() if "\n" in text else text
            lines_in_block = [ln.strip() for ln in text.split("\n") if ln.strip()]
            has_any_allegation = any(_starts_allegation(ln) or _starts_affirmation_point(ln) for ln in lines_in_block)
            allegation_paras = _split_allegation_block(text) if numbered_style and (_looks_like_list_item(first_line) or (len(lines_in_block) > 1 and has_any_allegation)) else []

            if len(allegation_paras) > 1:
                # Render each allegation as its own numbered paragraph. Do NOT hardcode "1.", "2." as text:
                # strip any leading number from content and apply Word numPr so the template controls numbering.
                style = style_map["numbered"]
                for one in allegation_paras:
                    one = one.strip()
                    if not one:
                        continue
                    one = re.sub(r"^\d+[\.\)]\s*", "", one).strip()
                    one = re.sub(r"^[a-z][\.\)]\s*", "", one, count=1).strip()
                    one = re.sub(r"^[ivx]+[\.\)]\s*", "", one, count=1, flags=re.IGNORECASE).strip()
                    one = _render_checkboxes(one)
                    segments = parse_inline_formatting_markers(one)
                    run_fmt = (style_formatting.get(style) or {}).get("run_format") or {}
                    _add_paragraph_with_inline_formatting(doc, segments, style, run_fmt)
                    p = doc.paragraphs[-1] if doc.paragraphs else None
                    if p:
                        fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                        _apply_paragraph_format(p, fmt)
                        # Number allegation and affirmation points (That on..., I make this..., This action was..., etc.)
                        if numbered_num_id is not None and _is_numbered_point_content(one):
                            _apply_num_pr(p, numbered_num_id, numbered_ilvl)
                        if _is_numbered_point_content(one):
                            _apply_numbered_paragraph_layout(p)
                        enforce_legal_alignment("numbered", p)
                continue

            is_court_caption = _looks_like_court_caption(text)
            is_cause_of_action_heading = _looks_like_cause_of_action_heading(text)
            # Use one consistent style for court caption lines; cause-of-action headings get section_header for clear distinction from numbered points
            if is_court_caption:
                style = style_map.get("section_header") or style_map.get("heading") or style_map.get("paragraph")
                if not style or (valid_style_names and style not in valid_style_names):
                    style = list(valid_style_names)[0] if valid_style_names else "Normal"
            elif is_cause_of_action_heading:
                style = style_map.get("section_header") or style_map.get("heading") or style_map.get("paragraph")
                if not style or (valid_style_names and style not in valid_style_names):
                    style = list(valid_style_names)[0] if valid_style_names else "Normal"
            elif _looks_like_numbered_claim_heading(text) and numbered_style:
                style = style_map["numbered"]
                if not style or (valid_style_names and style not in valid_style_names):
                    style = list(valid_style_names)[0] if valid_style_names else "Normal"
            elif _looks_like_list_item(text) and numbered_style:
                # Use numbered/list style so Word numbers allegations (1., 2., 3.)
                style = style_map["numbered"]
            else:
                style = block_type if block_type in valid_style_names else style_map.get(block_type, style_map.get("paragraph"))
            # If LLM gave paragraph/body but content is an allegation or affirmation point, use numbered style so it gets numbered
            if style != style_map.get("numbered") and _is_numbered_point_content((text or "").strip()) and numbered_style:
                style = style_map["numbered"]
            if not style:
                style = list(valid_style_names)[0] if valid_style_names else "Normal"
            # Page break before attorney verification (always, so it starts on a new page)
            if doc.paragraphs and _looks_like_attorney_verification_heading(text):
                doc.add_page_break()
            # One page break per segment for other template section starts
            if doc.paragraphs and not section_break_added_in_segment and _is_section_start(text, block_type, style_map, valid_style_names, section_heading_samples):
                doc.add_page_break()
                section_break_added_in_segment = True
            # Strip leading "1.", "2." when Word will supply it via numPr (allegations, affirmation points, numbered claim-form headings)
            if style == style_map.get("numbered") and (_is_numbered_point_content((text or "").strip()) or _looks_like_numbered_claim_heading(text)):
                text = re.sub(r"^\d+[\.\)]\s*", "", text).strip()
                text = re.sub(r"^[a-z][\.\)]\s*", "", text, count=1).strip()
                text = re.sub(r"^[ivx]+[\.\)]\s*", "", text, count=1, flags=re.IGNORECASE).strip()
            text = _render_checkboxes(text)
            segments = parse_inline_formatting_markers(text)
            segments = _apply_sample_bold_to_segments(segments, extra_bold_phrases=bold_phrases_from_template)
            run_fmt = (style_formatting.get(style) or {}).get("run_format") or {}
            txt_stripped = (text or "").strip()
            # Place Index no. on same line as previous caption line (e.g. plaintiff name) with right tab
            if _looks_like_index_no(txt_stripped) and doc.paragraphs and _last_paragraph_looks_like_caption_line(doc):
                _append_index_no_to_paragraph(doc.paragraphs[-1], txt_stripped, run_fmt)
                continue
            _add_paragraph_with_inline_formatting(doc, segments, style, run_fmt)
            p = doc.paragraphs[-1] if doc.paragraphs else None
            if p:
                fmt = (style_formatting.get(style) or {}).get("paragraph_format") or {}
                _apply_paragraph_format(p, fmt)
                _ensure_center_only_when_template_center(p, style, style_formatting)
                # Apply list numbering for allegations, affirmation points, and numbered claim-form points (1., 2., 3., etc.)
                is_negligence_allegation = style == style_map.get("numbered") and _starts_allegation(txt_stripped)
                is_affirmation_point = style == style_map.get("numbered") and _starts_affirmation_point(txt_stripped)
                is_numbered_claim_heading = style == style_map.get("numbered") and _looks_like_numbered_claim_heading(text or txt_stripped)
                is_any_numbered_point = is_negligence_allegation or is_affirmation_point or is_numbered_claim_heading
                if numbered_num_id is not None and is_any_numbered_point:
                    _apply_num_pr(p, numbered_num_id, numbered_ilvl)
                _apply_section_spacing(p, txt_stripped, is_court_caption=is_court_caption)
                _apply_default_paragraph_spacing(p, style, style_formatting)
                if is_any_numbered_point:
                    _apply_numbered_paragraph_layout(p)
                align_type = "section_header" if style in (style_map.get("heading"), style_map.get("section_header")) else ("numbered" if is_any_numbered_point else "paragraph")
                if align_type == "paragraph":
                    _apply_default_body_indent(p, style, style_formatting)
                if align_type in ("paragraph", "numbered"):
                    _apply_default_line_spacing(p, style, style_formatting)
                enforce_legal_alignment(align_type, p)
                # Caption: left (court/parties), right (Index no., NOTICE OF MOTION), or center; TO:/address left
                if _should_align_left_caption_block(txt_stripped):
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                elif _should_align_right_caption(txt_stripped):
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif _should_align_center_caption(txt_stripped):
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif not _template_has_alignment(style, style_formatting) and _should_align_left_only(txt_stripped):
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                if _looks_like_jurat_line(txt_stripped):
                    try:
                        p.paragraph_format.keep_with_next = True
                    except Exception:
                        pass
                if _looks_like_list_intro(txt_stripped) or _looks_like_bullet_item(txt_stripped):
                    try:
                        p.paragraph_format.keep_with_next = True
                    except Exception:
                        pass
    trim_trailing_separators(doc)


def _is_empty_or_noise_paragraph(para) -> bool:
    """True if paragraph has no content or only separator noise. Keep paragraphs with bottom border (separator lines)."""
    if not para:
        return True
    if _paragraph_has_bottom_border(para):
        return False
    text = (para.text or "").strip()
    if not text:
        return True
    return _is_separator_noise(text)


def trim_trailing_separators(doc):
    """Remove trailing paragraphs that look like separators (----, ====, ______). Call after rendering, before save."""
    def is_separator(text):
        t = (text or "").strip()
        return t.startswith("-") or t.startswith("=") or t.startswith("_")

    while doc.paragraphs:
        last = doc.paragraphs[-1]
        if is_separator(last.text):
            try:
                p = last._element
                p.getparent().remove(p)
            except Exception:
                break
        else:
            break


def remove_trailing_empty_and_noise(doc):
    """Remove trailing paragraphs that are empty or only separator noise (underscores, '- - -')."""
    paras = list(doc.paragraphs)
    if not paras:
        return
    removed = 0
    for para in reversed(paras):
        if _is_empty_or_noise_paragraph(para):
            try:
                p_el = para._element
                p_el.getparent().remove(p_el)
                removed += 1
            except Exception:
                break
        else:
            break


def force_single_column(doc):
    """Force all sections to single-column layout so the document renders as one column per page, not multi-column.
    Handles sectPr as direct children of body and sectPr inside paragraph properties (section breaks)."""
    try:
        body = doc.element.body
        for sect_pr in body.iter(qn("w:sectPr")):
            cols = None
            for c in sect_pr:
                if c.tag == qn("w:cols"):
                    cols = c
                    break
            if cols is not None:
                cols.set(qn("w:num"), "1")
            else:
                cols_el = OxmlElement("w:cols")
                cols_el.set(qn("w:num"), "1")
                sect_pr.insert(0, cols_el)
    except Exception:
        pass


def clear_document_body(doc):
    """Remove all paragraphs and tables from the document body, keeping section properties."""
    for para in list(doc.paragraphs):
        p_el = para._element
        p_el.getparent().remove(p_el)
    for table in list(doc.tables):
        tbl_el = table._element
        tbl_el.getparent().remove(tbl_el)
