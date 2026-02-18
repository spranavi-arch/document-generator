class Assembler:

    @staticmethod
    def _strip_leading_section_title(text: str, section_name: str) -> str:
        """Remove a leading line if it is the section name/title, so sections read as one flow."""
        if not text or not section_name:
            return text
        lines = text.strip().splitlines()
        if not lines:
            return text
        first = lines[0].strip()
        # Normalize: strip markdown bold, leading numbers/dots, case-insensitive compare
        normalized_first = first.lstrip(".#0123456789) ").strip().strip("*_")
        normalized_name = section_name.strip().strip("*_")
        if normalized_first.lower() == normalized_name.lower():
            rest = "\n".join(lines[1:]).strip()
            return rest if rest else text
        return text

    def assemble(self, blueprint, sections):
        """Join generated sections in blueprint order into one document (no section titles in body)."""
        parts = []
        for s in blueprint["sections"]:
            name = s["name"]
            text = sections.get(name, "").strip()
            text = self._strip_leading_section_title(text, name)
            if text:
                parts.append(text)
        return "\n\n".join(parts)
