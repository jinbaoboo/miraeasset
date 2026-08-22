"""Parser for KRX disclosure forms stored with an ``.xml`` suffix.

The files are HTML, not DART XML.  This module intentionally uses the Python
standard library so the corpus can be processed in an offline environment.
Raw files are read only; decoding and HTML recovery happen in memory.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .table_parser import parse_numeric
from .text_cleaner import normalize_text


SOURCE_FIELDS = [
    "doc_id", "corp_code", "corp_name", "listed_name", "stock_code", "industry", "sector", "flr_nm",
    "report_nm", "rcept_no",
    "rcept_dt", "doc_group", "doc_subtype", "base_year", "base_month",
    "is_correction", "file_path",
]


class _HTMLTableCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[Dict[str, Any]]]] = []
        self._table_depth = 0
        self._table: Optional[List[List[Dict[str, Any]]]] = None
        self._row: Optional[List[Dict[str, Any]]] = None
        self._cell: Optional[Dict[str, Any]] = None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
        elif tag == "tr" and self._table_depth == 1:
            self._row = []
        elif tag in {"td", "th"} and self._table_depth == 1 and self._row is not None:
            self._cell = {
                "tag": tag.upper(),
                "text_parts": [],
                "rowspan": _positive_int(attr.get("rowspan")),
                "colspan": _positive_int(attr.get("colspan")),
                "attrs": attr,
            }
        elif tag == "br" and self._cell is not None:
            self._cell["text_parts"].append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._cell["text"] = normalize_text("".join(self._cell.pop("text_parts")))
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._table_depth == 1 and self._row is not None:
            if self._row:
                self._table.append(self._row)  # type: ignore[union-attr]
            self._row = None
        elif tag == "table":
            if self._table_depth == 1 and self._table is not None:
                if self._table:
                    self.tables.append(self._table)
                self._table = None
            self._table_depth = max(0, self._table_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text_parts"].append(data)
        elif self._in_title:
            self.title += data


def _positive_int(value: Optional[str]) -> int:
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


def _decode_html(raw: bytes) -> Tuple[str, str, List[str]]:
    warnings: List[str] = []
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(encoding)
            if encoding != "utf-8-sig":
                warnings.append(f"encoding_fallback:{encoding}")
            return text, encoding, warnings
        except UnicodeDecodeError:
            continue
    warnings.append("encoding_replacement:utf-8")
    return raw.decode("utf-8", errors="replace"), "utf-8-replace", warnings


def _grid_rows(rows: Sequence[Sequence[Dict[str, Any]]]) -> List[List[Optional[Dict[str, Any]]]]:
    occupied: Dict[Tuple[int, int], Dict[str, Any]] = {}
    width = 0
    for row_index, row in enumerate(rows):
        col = 0
        for cell in row:
            while (row_index, col) in occupied:
                col += 1
            cell["row_index"] = row_index
            cell["column_index"] = col
            for r in range(row_index, row_index + cell["rowspan"]):
                for c in range(col, col + cell["colspan"]):
                    occupied[(r, c)] = cell
                    width = max(width, c + 1)
            col += cell["colspan"]
    return [[occupied.get((row, col)) for col in range(width)] for row in range(len(rows))]


def _unique_values(row: Sequence[Optional[Dict[str, Any]]]) -> List[str]:
    result: List[str] = []
    seen = set()
    for cell in row:
        if cell is None or id(cell) in seen:
            continue
        seen.add(id(cell))
        text = cell.get("text", "")
        if text:
            result.append(text)
    return result


def _canonical_key(label: str) -> str:
    compact = re.sub(r"[\sㆍ·()\[\].%-]", "", label)
    mappings = [
        ("계약금액원", "contract_amount_krw"), ("최근매출액원", "recent_revenue_krw"),
        ("매출액대비", "revenue_ratio_pct"), ("계약상대", "counterparty"),
        ("체결계약명", "contract_name"), ("판매공급지역", "region"),
        ("계약기간시작일", "contract_start_date"), ("계약기간종료일", "contract_end_date"),
        ("계약수주일자", "contract_date"), ("투자금액원", "investment_amount_krw"),
        ("자기자본원", "equity_krw"), ("자기자본대비", "equity_ratio_pct"),
        ("투자기간시작일", "investment_start_date"), ("투자기간종료일", "investment_end_date"),
        ("투자목적", "investment_purpose"), ("투자대상", "investment_target"),
        ("정정일자", "correction_date"), ("정정사유", "correction_reason"),
    ]
    for token, key in mappings:
        if token in compact:
            return key
    return "field_" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:10]


def _scope_from_text(text: str) -> Tuple[str, str]:
    if "연결" in text:
        return "consolidated", "form_text:연결"
    if "별도" in text or "개별" in text:
        return "separate", "form_text:별도/개별"
    return "unknown", "no_explicit_evidence"


def _corrections(grids: Sequence[Sequence[Sequence[Optional[Dict[str, Any]]]]], source: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    correction_date = None
    original_filing_date = None
    target_document = None
    for grid in grids:
        for row in grid:
            values = _unique_values(row)
            line = " | ".join(values)
            date_match = re.search(r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", line)
            iso = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}" if date_match else None
            if values and "정정일자" in values[0]: correction_date = iso
            if values and "정정관련 공시서류제출일" in values[0]: original_filing_date = iso
            if values and "정정관련 공시서류" in values[0] and "제출일" not in values[0] and len(values) > 1:
                target_document = values[-1]
    for table_index, grid in enumerate(grids, start=1):
        header = None
        before_col = after_col = item_col = None
        for row_index, row in enumerate(grid):
            values = [re.sub(r"\s+", "", cell.get("text", "")) if cell else "" for cell in row]
            if any("정정전" in value for value in values) and any("정정후" in value for value in values):
                header = row_index
                for col, value in enumerate(values):
                    if "정정항목" in value: item_col = col
                    if "정정전" in value: before_col = col
                    if "정정후" in value: after_col = col
                break
        if header is None or before_col is None or after_col is None:
            continue
        for row_index, row in enumerate(grid[header + 1:], start=header + 1):
            def value(col: Optional[int]) -> str:
                return row[col].get("text", "") if col is not None and col < len(row) and row[col] else ""
            before, after, item = value(before_col), value(after_col), value(item_col)
            if not any((item, before, after)):
                continue
            items.append({
                "item_id": f"{source['doc_id']}:correction:item:{len(items)+1:04d}",
                "item": item or None, "reason": None,
                "before": {"original_text": before, "cell_ids": []},
                "after": {"original_text": after, "cell_ids": []},
                "current_effective_value": {"source": "after", "original_text": after, "doc_id": source["doc_id"]},
                "source_locator": {"html_table_ordinal": table_index, "row_index": row_index},
            })
    if not items:
        return []
    return [{
        "correction_id": f"{source['doc_id']}:correction:0001", "source": source,
        "version_role": "correction", "original_doc_id": None,
        "correction_doc_id": source["doc_id"], "supersedes_doc_id": None,
        "superseded_by_doc_id": None, "is_latest_version": True,
        "correction_date": correction_date, "original_filing_date": original_filing_date,
        "target_document": target_document, "correction_items": items, "raw_summary_text": None,
    }]


class ExchangeParser:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)

    def parse_document(self, record: Dict[str, Any]) -> Dict[str, Any]:
        source = {field: record.get(field) for field in SOURCE_FIELDS}
        folder = self.data_root / str(record["file_path"])
        files = sorted(folder.glob("*.xml")) if folder.is_dir() else []
        result: Dict[str, Any] = {
            "document": {}, "sections": [], "text_chunks": [], "logical_tables": [],
            "table_cells": [], "corrections": [], "images": [], "events": [],
            "parse_log": {"status": "success", "files": [], "warnings": [], "errors": []},
        }
        if not files:
            result["parse_log"]["status"] = "failed"
            result["parse_log"]["errors"].append("source_file_not_found")
            result["document"] = self._document(record, source, result, [])
            return result
        path = files[0]
        try:
            raw = path.read_bytes()
            text, encoding, warnings = _decode_html(raw)
            collector = _HTMLTableCollector()
            collector.feed(text)
            grids = [_grid_rows(table) for table in collector.tables]
            body_lines: List[str] = []
            event_fields: List[Dict[str, Any]] = []
            all_text = " ".join(cell for grid in grids for row in grid for cell in _unique_values(row))
            scope, scope_evidence = _scope_from_text(all_text)
            for table_index, grid in enumerate(grids, start=1):
                table_id = f"{source['doc_id']}:main:table:{table_index:05d}"
                rows: List[Dict[str, Any]] = []
                cells: List[Dict[str, Any]] = []
                correction_table = any("정정전" in " ".join(_unique_values(row)) and "정정후" in " ".join(_unique_values(row)) for row in grid)
                for row_index, row in enumerate(grid):
                    values = _unique_values(row)
                    if not values:
                        continue
                    body_lines.append(" | ".join(values))
                    label = " > ".join(values[:-1]) if len(values) > 1 else values[0]
                    row_ids: List[str] = []
                    seen = set()
                    for col, anchor in enumerate(row):
                        if anchor is None or id(anchor) in seen:
                            continue
                        seen.add(id(anchor))
                        cell_id = f"{table_id}:r{row_index}:c{col}"
                        parsed = parse_numeric(anchor.get("text", ""))
                        cell = {
                            "cell_id": cell_id, "table_id": table_id, "source": source,
                            "source_file": path.name, "source_file_role": "main", "section_path": [],
                            "physical_table_ordinal": table_index, "row_index": row_index, "column_index": col,
                            "row_label": label, "row_path": [label], "column_label": None, "column_path": [],
                            "original_text": anchor.get("text", ""), **parsed,
                            "unit": {"raw": "원" if "(원)" in label else "%" if "(%)" in label else None,
                                     "currency": "KRW" if "(원)" in label else None,
                                     "scale": 1 if "(원)" in label else None,
                                     "quantity": "money" if "(원)" in label else "percent" if "(%)" in label else "unknown"},
                            "period": None, "scope": scope, "is_missing": parsed["is_missing"],
                            "original_tag": anchor.get("tag"), "rowspan": anchor.get("rowspan", 1),
                            "colspan": anchor.get("colspan", 1), "parse_warnings": [],
                        }
                        cells.append(cell); row_ids.append(cell_id); result["table_cells"].append(cell)
                    rows.append({"row_key": f"r{row_index}", "row_index": row_index, "label": label,
                                 "row_path": [label], "cell_ids": row_ids})
                    if not correction_table and len(values) >= 2:
                        field_label, field_value = " > ".join(values[:-1]), values[-1]
                        parsed = parse_numeric(field_value)
                        event_fields.append({
                            "field_key": _canonical_key(field_label), "label": field_label,
                            "original_text": field_value, "numeric_value": parsed["numeric_value"],
                            "value_type": parsed["value_type"], "unit": "KRW" if "(원)" in field_label else "percent" if "(%)" in field_label else None,
                            "source_locator": {"table_id": table_id, "row_index": row_index},
                        })
                table_title = "정정사항" if correction_table else (record.get("report_nm") or collector.title)
                result["logical_tables"].append({
                    "table_id": table_id, "source": source, "source_file": path.name,
                    "source_file_role": "main", "section_id": None, "section": None, "subsection": None,
                    "section_path": [], "table_title": table_title, "title_source": "form_type",
                    "unit": {"raw": None, "currency": None, "scale": None, "quantity": "mixed"},
                    "scope": scope, "scope_evidence": scope_evidence, "statement_type": "event_form",
                    "periods": [], "columns": [], "rows": rows,
                    "normalized_cell_ids": [cell["cell_id"] for cell in cells], "footnotes": [],
                    "physical_tables": [{"ordinal": table_index, "role": "correction" if correction_table else "data",
                                         "row_count": len(rows), "cell_count": len(cells)}],
                    "search_text": "\n".join(" | ".join(_unique_values(row)) for row in grid),
                    "source_locator": {"html_table_ordinal": table_index}, "aclass": None,
                })
            body = "\n".join(body_lines)
            chunk_id = f"{source['doc_id']}:main:text:00001"
            result["text_chunks"].append({
                "chunk_id": chunk_id, "source": source, "source_file": path.name,
                "source_file_role": "main", "section_id": None, "section": None, "subsection": None,
                "section_path": [], "content_type": "form_text", "heading": record.get("report_nm"),
                "text": f"{source.get('corp_name')} | {source.get('report_nm')}\n{body}", "body_text": body,
                "paragraph_count": len(body_lines), "related_table_ids": [t["table_id"] for t in result["logical_tables"]],
                "source_locator": {"html_table_ordinal_start": 1, "html_table_ordinal_end": len(grids)},
            })
            result["corrections"] = _corrections(grids, source) if record.get("is_correction") else []
            result["events"].append({
                "event_id": f"{source['doc_id']}:event:0001", "source": source,
                "event_type": record.get("doc_subtype") or "exchange_event", "event_title": record.get("report_nm"),
                "scope": scope, "scope_evidence": scope_evidence, "fields": event_fields,
                "effective_status": "current", "search_text": body,
            })
            result["parse_log"]["warnings"].extend(warnings)
            result["parse_log"]["status"] = "warning" if warnings else "success"
            result["parse_log"]["files"].append({
                "source_file": path.name, "status": result["parse_log"]["status"],
                "encoding": encoding, "raw_sha256": hashlib.sha256(raw).hexdigest(), "warnings": warnings,
            })
            result["document"] = self._document(record, source, result, [{"filename": path.name, "role": "main", "raw_sha256": hashlib.sha256(raw).hexdigest()}])
        except Exception as error:
            result["parse_log"]["status"] = "failed"
            result["parse_log"]["errors"].append(str(error))
            result["document"] = self._document(record, source, result, [])
        return result

    @staticmethod
    def _document(record: Dict[str, Any], source: Dict[str, Any], result: Dict[str, Any], files: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "schema_version": "1.1.0", "source": source, "document_name": record.get("report_nm"),
            "document_acode": None, "formula_version": None, "file_format": "html",
            "source_files": files, "attachments": [],
            "version": {"version_role": "correction" if record.get("is_correction") else "original",
                        "original_doc_id": None, "supersedes_doc_id": None, "superseded_by_doc_id": None,
                        "is_latest_version": True},
            "parse_summary": {"status": result["parse_log"]["status"],
                              "warning_count": len(result["parse_log"]["warnings"]),
                              "error_count": len(result["parse_log"]["errors"])},
            "record_counts": {key: len(result[key]) for key in ("sections", "text_chunks", "logical_tables", "table_cells", "corrections", "images", "events")},
        }
