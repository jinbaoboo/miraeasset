"""Text normalization helpers for DART periodic filings."""

from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple
from xml.etree import ElementTree as ET


_SPACE_RE = re.compile(r"[\t\f\v\u00a0\u2000-\u200b\u3000 ]+")
_NEWLINE_RE = re.compile(r"\s*\n\s*")
_HEADING_RE = re.compile(
    r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.]|[IVX]+[.]|\d+(?:[-.]\d+)*[.]?|[가-힣][.]|\([0-9가-힣]+\))\s*"
)


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].upper()


def normalize_text(text: Optional[str]) -> str:
    """Normalize display whitespace without changing punctuation or numbers."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _NEWLINE_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text)
    text = re.sub(r"(?<=[.!?])(?=[가-힣A-Z])", " ", text)
    return text.strip()


def element_text(element: Optional[ET.Element]) -> str:
    if element is None:
        return ""
    return normalize_text("".join(element.itertext()))


def is_bold_span(element: ET.Element) -> bool:
    if local_name(element.tag) != "SPAN":
        return False
    mark = (element.attrib.get("USERMARK") or "").upper()
    return bool(re.search(r"(?:^|\s)B(?:\s|$)", mark))


def split_leading_bold_heading(paragraph: ET.Element) -> Tuple[Optional[str], str]:
    """Return a style-inferred heading and remaining paragraph text.

    DART often writes ``<P><SPAN USERMARK='... B'>가. 제목</SPAN>본문``.
    We only infer short, heading-like leading spans and preserve the full text when
    the evidence is weak.
    """
    children = list(paragraph)
    if normalize_text(paragraph.text):
        return None, element_text(paragraph)
    if not children or not is_bold_span(children[0]):
        return None, element_text(paragraph)

    heading = element_text(children[0])
    if not heading or len(heading) > 160:
        return None, element_text(paragraph)
    if not (_HEADING_RE.match(heading) or heading.startswith("[") or heading.startswith("<")):
        return None, element_text(paragraph)

    remainder_parts = [children[0].tail or ""]
    for child in children[1:]:
        remainder_parts.extend(child.itertext())
        if child.tail:
            remainder_parts.append(child.tail)
    return heading, normalize_text(" ".join(remainder_parts))


def is_note_text(text: str) -> bool:
    value = normalize_text(text)
    return value.startswith(("※", "주)", "주:", "주 :", "*", "[△", "[▲"))


def join_nonempty(values: Iterable[str], separator: str = " ") -> str:
    return separator.join(value for value in (normalize_text(v) for v in values) if value)
