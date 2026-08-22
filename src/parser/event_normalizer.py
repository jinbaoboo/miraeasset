"""Normalize XML filing tables into searchable event records."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List


_SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{6}[- ]?[1-4]\d{6}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{5}\b"),
    re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def mask_sensitive_text(text: str) -> str:
    value = text
    for pattern in _SENSITIVE_PATTERNS:
        value = pattern.sub("[MASKED]", value)
    return value


def _field_key(label: str) -> str:
    compact = re.sub(r"[\sㆍ·()\[\].%-]", "", label)
    mappings = [
        ("취득예정주식보통주식", "planned_common_shares"),
        ("취득예정금액보통주식", "planned_common_amount_krw"),
        ("처분예정주식보통주식", "planned_disposal_common_shares"),
        ("처분예정금액보통주식", "planned_disposal_common_amount_krw"),
        ("계약금액", "contract_amount_krw"), ("권면총액", "face_value_krw"),
        ("조달자금의사용목적", "funding_purpose"), ("보유목적", "holding_purpose"),
        ("보고서작성기준일", "report_base_date"), ("주식등의수", "shares_held"),
        ("비율", "holding_ratio_pct"), ("직전보고서", "previous_holding"),
        ("이번보고서", "current_holding"), ("증감", "change_amount"),
        ("변동사유", "change_reason"), ("변경사유", "amendment_reason"),
        ("취득예상기간시작일", "acquisition_start_date"),
        ("취득예상기간종료일", "acquisition_end_date"),
    ]
    for token, key in mappings:
        if token in compact:
            return key
    return "field_" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:10]


def normalize_xml_event(result: Dict[str, Any], record: Dict[str, Any]) -> List[Dict[str, Any]]:
    if record.get("doc_group") not in {"major", "holding"}:
        return []
    fields: List[Dict[str, Any]] = []
    # Exact cells remain complete in ``table_cells``.  Event fields are a
    # denormalized search aid, so cap them to avoid duplicating enormous holder
    # lists (some filings exceed 90k cells).
    max_event_fields = 5000
    for cell in result.get("table_cells", []):
        label = cell.get("row_label") or ""
        column_label = " > ".join(cell.get("column_path") or [])
        semantic_label = " > ".join(part for part in (label, column_label) if part)
        value = cell.get("original_text") or ""
        if not label or not value or value == label:
            continue
        if record.get("doc_group") == "holding":
            value = mask_sensitive_text(value)
        fields.append({
            "field_key": _field_key(semantic_label), "label": semantic_label, "original_text": value,
            "numeric_value": cell.get("numeric_value"), "value_type": cell.get("value_type"),
            "unit": cell.get("unit"), "period": cell.get("period"), "scope": cell.get("scope"),
            "source_locator": {"table_id": cell.get("table_id"), "cell_id": cell.get("cell_id")},
        })
        if len(fields) >= max_event_fields:
            break
    title = re.sub(r"^\[(?:기재정정|첨부추가)\]", "", record.get("report_nm") or "")
    search_parts = [record.get("corp_name") or "", title]
    for field in fields[:2000]:
        search_parts.append(f"{field['label']} | {field['original_text']}")
    search_text = "\n".join(search_parts)
    if record.get("doc_group") == "holding":
        search_text = mask_sensitive_text(search_text)
    return [{
        "event_id": f"{record['doc_id']}:event:0001", "source": result["document"]["source"],
        "event_type": title, "event_title": title, "scope": "unknown",
        "scope_evidence": "not_applicable_or_no_explicit_evidence", "fields": fields,
        "effective_status": "current", "search_text": search_text,
    }]
