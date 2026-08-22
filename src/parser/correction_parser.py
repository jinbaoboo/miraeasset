"""Correction notice extraction and version semantics."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from .table_parser import build_grid
from .text_cleaner import element_text, local_name, normalize_text


_DATE_RE = re.compile(r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일")


def _date_to_iso(match: Optional[re.Match[str]]) -> Optional[str]:
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _cell_text(physical: Any, row: int, col: int) -> str:
    if row >= len(physical.grid) or col >= len(physical.grid[row]):
        return ""
    cell = physical.grid[row][col]
    return cell.text if cell else ""


def parse_correction(
    root: ET.Element,
    source: Dict[str, Any],
    version_info: Dict[str, Any],
) -> List[Dict[str, Any]]:
    correction = next((node for node in root.iter() if local_name(node.tag) == "CORRECTION"), None)
    if correction is None:
        return []

    all_text = element_text(correction)
    paragraphs = [
        element_text(node) for node in correction.iter() if local_name(node.tag) == "P" and element_text(node)
    ]
    date_matches = list(_DATE_RE.finditer(all_text))
    correction_date = _date_to_iso(date_matches[0]) if date_matches else None
    original_date = None
    target_document = None
    for paragraph in paragraphs:
        if "정정대상 공시서류의 최초제출일" in paragraph:
            original_date = _date_to_iso(_DATE_RE.search(paragraph))
        elif "정정대상 공시서류" in paragraph and ":" in paragraph:
            target_document = normalize_text(paragraph.split(":", 1)[1])

    items: List[Dict[str, Any]] = []
    tables = [node for node in correction.iter() if local_name(node.tag) == "TABLE"]
    for table_ordinal, table in enumerate(tables, start=1):
        physical = build_grid(table)
        if not physical.grid:
            continue
        header_row = None
        column_map: Dict[str, int] = {}
        for row_index in physical.header_rows or range(min(3, len(physical.grid))):
            values = [_cell_text(physical, row_index, col) for col in range(len(physical.grid[row_index]))]
            compact = [re.sub(r"\s+", "", value) for value in values]
            if any("정정전" in value for value in compact) and any("정정후" in value for value in compact):
                header_row = row_index
                for col, value in enumerate(compact):
                    if "항목" in value:
                        column_map["item"] = col
                    elif "정정사유" in value:
                        column_map["reason"] = col
                    elif "정정전" in value:
                        column_map["before"] = col
                    elif "정정후" in value:
                        column_map["after"] = col
                break
        if header_row is None or "before" not in column_map or "after" not in column_map:
            continue
        for row_index in range(header_row + 1, len(physical.grid)):
            before = _cell_text(physical, row_index, column_map["before"])
            after = _cell_text(physical, row_index, column_map["after"])
            item = _cell_text(physical, row_index, column_map.get("item", 0))
            reason = _cell_text(physical, row_index, column_map.get("reason", 0)) if "reason" in column_map else None
            if not any((item, before, after)):
                continue
            if version_info.get("is_latest_version"):
                current_effective = {"source": "after", "original_text": after, "doc_id": source["doc_id"]}
            else:
                current_effective = {
                    "source": "superseded_by",
                    "original_text": None,
                    "doc_id": version_info.get("superseded_by_doc_id"),
                }
            items.append({
                "item_id": f"{source['doc_id']}:correction:item:{len(items) + 1:04d}",
                "item": item or None,
                "reason": reason or None,
                "before": {"original_text": before, "cell_ids": []},
                "after": {"original_text": after, "cell_ids": []},
                "current_effective_value": current_effective,
                "source_locator": {"correction_table_ordinal": table_ordinal, "row_index": row_index},
            })

    return [{
        "correction_id": f"{source['doc_id']}:correction:0001",
        "source": source,
        "version_role": "correction",
        "original_doc_id": version_info.get("original_doc_id"),
        "correction_doc_id": source["doc_id"],
        "supersedes_doc_id": version_info.get("supersedes_doc_id"),
        "superseded_by_doc_id": version_info.get("superseded_by_doc_id"),
        "is_latest_version": bool(version_info.get("is_latest_version")),
        "correction_date": correction_date,
        "original_filing_date": original_date,
        "target_document": target_document,
        "correction_items": items,
        "raw_summary_text": "\n".join(paragraphs[:20]),
    }]
