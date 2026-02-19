"""
Universal legal style ontology: block types used by the section detector and style matcher.
Maps semantic roles (court_header, caption_party, legal_allegation, etc.) to style_map keys
so the formatter can apply template styles consistently across summons, complaints, notices, motions.
"""

# Ontology block types (output of section_detector)
COURT_HEADER = "court_header"
COUNTY_LINE = "county_line"
CAPTION_SEPARATOR = "caption_separator"
CAPTION_PARTY = "caption_party"
CAPTION_ROLE = "caption_role"
VERSUS_LINE = "versus_line"
DOC_TITLE = "doc_title"
NOTICE_TO_LINE = "notice_to_line"
SECTION_HEADING = "section_heading"
SECTION_HEADING_MAJOR = "section_heading_major"
CAUSE_OF_ACTION_HEADING = "cause_of_action_heading"
CAUSE_OF_ACTION_TITLE = "cause_of_action_title"
BODY_PARAGRAPH = "body_paragraph"
LEGAL_ALLEGATION = "legal_allegation"
NUMBERED_PARAGRAPH = "numbered_paragraph"
WHEREFORE_CLAUSE = "wherefore_clause"
WHEREFORE_HEADING = "wherefore_heading"
SIGNATURE_LINE = "signature_line"
SIGNATURE_BLOCK = "signature_block"
FIRM_BLOCK_LINE = "firm_block_line"
VERIFICATION_HEADING = "verification_heading"
VERIFICATION_BODY = "verification_body"
SUMMONS_BODY = "summons_body"
LINE = "line"
EMPTY = "empty"

# Map ontology type -> style_map key (used by style_matcher when template has no exact match)
# style_map keys: heading, section_header, paragraph, numbered, wherefore
ONTOLOGY_TO_STYLE_MAP_KEY = {
    COURT_HEADER: "heading",
    COUNTY_LINE: "heading",
    CAPTION_SEPARATOR: "paragraph",
    CAPTION_PARTY: "heading",
    CAPTION_ROLE: "heading",
    VERSUS_LINE: "heading",
    DOC_TITLE: "heading",
    NOTICE_TO_LINE: "section_header",
    SECTION_HEADING: "section_header",
    SECTION_HEADING_MAJOR: "section_header",
    CAUSE_OF_ACTION_HEADING: "section_header",
    CAUSE_OF_ACTION_TITLE: "section_header",
    BODY_PARAGRAPH: "paragraph",
    LEGAL_ALLEGATION: "numbered",
    NUMBERED_PARAGRAPH: "numbered",
    WHEREFORE_CLAUSE: "wherefore",
    WHEREFORE_HEADING: "wherefore",
    SIGNATURE_LINE: "paragraph",
    SIGNATURE_BLOCK: "paragraph",
    FIRM_BLOCK_LINE: "paragraph",
    VERIFICATION_HEADING: "section_header",
    VERIFICATION_BODY: "paragraph",
    SUMMONS_BODY: "paragraph",
    LINE: "paragraph",
    EMPTY: "paragraph",
}
