"""Shared markdown utilities used by both local and cloud MCP servers."""

import re


def extract_section(content: str, section_title: str) -> str:
    """Return the markdown section matching *section_title* (including child headings).

    Matches case-insensitively against heading text.  Returns an empty string
    when the section is not found.
    """
    lines = content.split('\n')
    result = []
    in_section = False
    section_level = 0
    for line in lines:
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if not in_section:
                if section_title.lower() == title.lower():
                    in_section = True
                    section_level = level
                    result.append(line)
            else:
                if level <= section_level:
                    break
                result.append(line)
        elif in_section:
            result.append(line)
    return '\n'.join(result).strip()
