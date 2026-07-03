import re

from services.file_service import get_output_root_path


_INTERNAL_RULE_LINE_PATTERN = re.compile(
    r"(" 
    r"only\s+use\s+tools\s+in\s+the\s+slot"
    r"|start\s+planning\s+now"
    r"|rules\s+parsed"
    r"|last\s+updated\s+on"
    r"|if\s+there\s+is\s+an\s+empty\s+variable\s+such\s+as"
    r"|consider\s+using\s+the\s+tools\s+above"
    r"|please\s+return\s*\{\s*'error'"
    r"|<next_step_description>|</next_step_description>"
    r"|<required_tools>|</required_tools>"
    r"|<expected_output>|</expected_output>"
    r"|<success_criteria>|</success_criteria>"
    r"|如果涉及\s*file_write"
    r"|执行工具出错时"
    r"|禁止输出\s*<invoke>"
    r"|tool_call_id"
    r")",
    flags=re.IGNORECASE,
)


def sanitize_user_visible_text(text: str) -> str:
    """Clean tool traces and local path leakage from model-visible text."""
    if not isinstance(text, str) or not text:
        return ""

    cleaned = text
    cleaned = re.sub(r"<invoke>[\s\S]*?</invoke>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?(invoke|write_file|path|content)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfile_write\s*\([^)]*\)", "", cleaned, flags=re.IGNORECASE)

    filtered_lines = []
    output_root_str = str(get_output_root_path()).replace("\\", "/").lower()
    for line in cleaned.splitlines():
        low = line.lower().replace("\\", "/")
        stripped_low = low.strip()

        if _INTERNAL_RULE_LINE_PATTERN.search(stripped_low):
            continue
        if re.match(r"^\s*\d+[\.)]\s+", stripped_low) and _INTERNAL_RULE_LINE_PATTERN.search(stripped_low):
            continue

        if output_root_str and output_root_str in low:
            continue
        if stripped_low.startswith("/home/"):
            continue
        if re.match(r"^[a-z]:/", stripped_low):
            continue
        filtered_lines.append(line)

    cleaned = "\n".join(filtered_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned
