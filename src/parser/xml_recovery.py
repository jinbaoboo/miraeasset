"""In-memory recovery for DART XML-like filings.

The source file is never rewritten. Recovery is deliberately conservative:
known DART tags are preserved, angle-bracket text outside the allowlist is
escaped, bare ampersands are escaped, and invalid XML control characters are
removed before a second strict ElementTree parse.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET


ALLOWED_TAGS = {
    "DOCUMENT", "DOCUMENT-NAME", "FORMULA-VERSION", "COMPANY-NAME",
    "SUMMARY", "EXTRACTION", "BODY", "COVER", "COVER-TITLE", "CORRECTION",
    "SECTION", "SECTION-1", "SECTION-2", "SECTION-3", "SECTION-4", "SECTION-5",
    "SECTION-6", "TITLE", "LIBRARY", "P", "SPAN", "PGBRK", "BR", "HR",
    "TABLE-GROUP", "TABLE", "COLGROUP", "COL", "THEAD", "TBODY", "TFOOT",
    "TR", "TH", "TD", "TE", "TU", "IMAGE", "IMG", "IMG-CAPTION", "A",
    "UL", "OL", "LI", "SUB", "SUP", "B", "I", "U", "FONT"
}

_TAG_NAME_RE = re.compile(r"^\s*/?\s*([A-Za-z][A-Za-z0-9_-]*)\b")
_ENTITY_RE = re.compile(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|amp;|lt;|gt;|quot;|apos;)")
_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ATTRIBUTE_OPEN_QUOTES_RE = re.compile(r"(=\s*)\"{2,}(?=[^\s/>])")
_ATTRIBUTE_OPEN_QUOTES_SPACED_RE = re.compile(
    r"(=\s*)\"{2,}([\s\u3000]+)(?![A-Za-z_:][A-Za-z0-9_.:-]*\s*=)(?=[^\r\n<>]*\")"
)
_ATTRIBUTE_CLOSE_QUOTES_RE = re.compile(r"(?<!=)\"{2,}(?=\s*(?:[A-Za-z_:][A-Za-z0-9_.:-]*\s*=|/?>))")
_ATTRIBUTE_LEADING_INNER_QUOTE_RE = re.compile(
    r"(\b[A-Za-z_:][A-Za-z0-9_.:-]*\s*=\s*\"[\s\u3000]*)\"(?=[A-Za-z])"
)
_ATTRIBUTE_PAREN_QUOTED_TEXT_RE = re.compile(r'\("([^"<>\r\n]+)"\)')
_ATTRIBUTE_PAREN_MISMATCHED_QUOTE_RE = re.compile(r'\("([^"<>\r\n]+)\'\)')


@dataclass
class RecoveryResult:
    root: ET.Element
    raw_sha256: str
    strict_xml_valid: bool
    repair_applied: bool
    repair_counts: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    strict_error: Optional[str] = None


def _is_real_tag(inner: str) -> bool:
    stripped = inner.strip()
    if stripped.startswith(("?xml", "!--", "![CDATA[", "!DOCTYPE")):
        return True
    match = _TAG_NAME_RE.match(inner)
    return bool(match and match.group(1).upper() in ALLOWED_TAGS)


def _find_tag_end(text: str, start: int) -> int:
    quote = None
    for index in range(start + 1, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == ">":
            return index
    return -1


def _escape_non_tag_angles(text: str, counts: Dict[str, int]) -> str:
    output: List[str] = []
    index = 0
    while index < len(text):
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            if end >= 0:
                output.append(text[index:end + 3])
                index = end + 3
                continue
        if text.startswith("<![CDATA[", index):
            end = text.find("]]>", index + 9)
            if end >= 0:
                output.append(text[index:end + 3])
                index = end + 3
                continue
        char = text[index]
        if char == "<":
            end = _find_tag_end(text, index)
            if end >= 0 and _is_real_tag(text[index + 1:end]):
                output.append(text[index:end + 1])
                index = end + 1
                continue
            output.append("&lt;")
            counts["angle_text"] += 1
            index += 1
            continue
        if char == ">":
            output.append("&gt;")
            counts["angle_text"] += 1
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def recover_xml_text(text: str) -> tuple[str, Dict[str, int], List[str]]:
    counts = {"invalid_control_chars": 0, "bare_ampersands": 0, "angle_text": 0,
              "malformed_attribute_quotes": 0}
    warnings: List[str] = []

    def control_repl(match: re.Match[str]) -> str:
        counts["invalid_control_chars"] += 1
        return ""

    text = _CONTROL_RE.sub(control_repl, text)

    def amp_repl(match: re.Match[str]) -> str:
        counts["bare_ampersands"] += 1
        return "&amp;"

    text = _ENTITY_RE.sub(amp_repl, text)

    def quote_repl(match: re.Match[str]) -> str:
        counts["malformed_attribute_quotes"] += 1
        return (match.group(1) if match.lastindex else "") + '"'

    text = _ATTRIBUTE_OPEN_QUOTES_RE.sub(quote_repl, text)

    def spaced_open_quote_repl(match: re.Match[str]) -> str:
        counts["malformed_attribute_quotes"] += 1
        return match.group(1) + '"' + match.group(2)

    text = _ATTRIBUTE_OPEN_QUOTES_SPACED_RE.sub(spaced_open_quote_repl, text)
    text = _ATTRIBUTE_CLOSE_QUOTES_RE.sub(quote_repl, text)

    def inner_quote_repl(match: re.Match[str]) -> str:
        counts["malformed_attribute_quotes"] += 1
        return match.group(1)

    text = _ATTRIBUTE_LEADING_INNER_QUOTE_RE.sub(inner_quote_repl, text)

    def paren_quote_repl(match: re.Match[str]) -> str:
        counts["malformed_attribute_quotes"] += 2
        return "(&quot;" + match.group(1) + "&quot;)"

    text = _ATTRIBUTE_PAREN_QUOTED_TEXT_RE.sub(paren_quote_repl, text)
    text = _ATTRIBUTE_PAREN_MISMATCHED_QUOTE_RE.sub(paren_quote_repl, text)

    text = _escape_non_tag_angles(text, counts)
    for key, count in counts.items():
        if count:
            warnings.append(f"{key}:{count}")
    return text, counts, warnings


def parse_xml_file(path: Path) -> RecoveryResult:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        root = ET.fromstring(text)
        return RecoveryResult(
            root=root,
            raw_sha256=digest,
            strict_xml_valid=True,
            repair_applied=False,
        )
    except ET.ParseError as strict_error:
        repaired, counts, warnings = recover_xml_text(text)
        try:
            root = ET.fromstring(repaired)
        except ET.ParseError as recovery_error:
            raise ValueError(
                f"XML recovery failed for {path.name}: strict={strict_error}; recovered={recovery_error}"
            ) from recovery_error
        return RecoveryResult(
            root=root,
            raw_sha256=digest,
            strict_xml_valid=False,
            repair_applied=True,
            repair_counts=counts,
            warnings=warnings,
            strict_error=str(strict_error),
        )
