import re

def parse_legal_blocks(text: str):
    blocks = []
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    for line in lines:
        if "CAUSE OF ACTION" in line.upper():
            blocks.append(("section_header", line))
        elif line.upper().startswith("WHEREFORE"):
            blocks.append(("wherefore", line))
        elif re.match(r"^\d+\.", line):
            blocks.append(("numbered", line))
        elif line.isupper() and len(line) < 120:
            blocks.append(("heading", line))
        else:
            blocks.append(("paragraph", line))

    return blocks
