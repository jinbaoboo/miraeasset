"""Logical table and normalized cell extraction for DART periodic filings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from .text_cleaner import element_text, is_note_text, join_nonempty, local_name, normalize_text


CELL_TAGS = {"TH", "TD", "TE", "TU"}
_UNIT_RE = re.compile(r"\(\s*단위\s*[:：]\s*([^)]{1,80})\)")
_DATE_RE = re.compile(r"(20\d{2})[.년/-]\s*(\d{1,2})[.월/-]\s*(\d{1,2})")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_HEADING_WORD_RE = re.compile(r"(재무상태표|손익계산서|포괄손익계산서|현금흐름표|자본변동표|연구개발|매출|생산능력|투자현황|수주)")


@dataclass
class GridCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    tag: str
    text: str
    attrs: Dict[str, str]


@dataclass
class PhysicalTable:
    element: ET.Element
    rows: List[ET.Element]
    anchors: List[GridCell]
    grid: List[List[Optional[GridCell]]]
    header_rows: List[int]
    role: str


def _safe_int(value: Optional[str], default: int = 1) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _row_elements(table: ET.Element) -> List[ET.Element]:
    return [node for node in table.iter() if local_name(node.tag) == "TR"]


def build_grid(table: ET.Element) -> PhysicalTable:
    rows = _row_elements(table)
    anchors: List[GridCell] = []
    occupied: Dict[Tuple[int, int], GridCell] = {}
    header_ids = {
        id(row)
        for container in table.iter()
        if local_name(container.tag) == "THEAD"
        for row in container.iter()
        if local_name(row.tag) == "TR"
    }
    header_rows: List[int] = []
    max_col = 0
    for row_index, row in enumerate(rows):
        cells = [child for child in list(row) if local_name(child.tag) in CELL_TAGS]
        if id(row) in header_ids or any(local_name(cell.tag) == "TH" for cell in cells):
            header_rows.append(row_index)
        col_index = 0
        for cell in cells:
            while (row_index, col_index) in occupied:
                col_index += 1
            rowspan = _safe_int(cell.attrib.get("ROWSPAN"))
            colspan = _safe_int(cell.attrib.get("COLSPAN"))
            anchor = GridCell(
                row=row_index,
                col=col_index,
                rowspan=rowspan,
                colspan=colspan,
                tag=local_name(cell.tag),
                text=element_text(cell),
                attrs=dict(cell.attrib),
            )
            anchors.append(anchor)
            for r in range(row_index, row_index + rowspan):
                for c in range(col_index, col_index + colspan):
                    occupied[(r, c)] = anchor
                    max_col = max(max_col, c + 1)
            col_index += colspan

    grid: List[List[Optional[GridCell]]] = []
    for row_index in range(len(rows)):
        grid.append([occupied.get((row_index, col)) for col in range(max_col)])

    row_texts = []
    for row in rows:
        text = element_text(row)
        if text:
            row_texts.append(text)
    note_like = bool(row_texts) and all(is_note_text(text) for text in row_texts)
    all_single = bool(rows) and all(
        len([child for child in list(row) if local_name(child.tag) in CELL_TAGS]) <= 1 for row in rows
    )
    joined = " ".join(row_texts)
    metadata_like = len(rows) <= 7 and all_single and bool(_UNIT_RE.search(joined) or _HEADING_WORD_RE.search(joined))
    role = "footnote" if note_like else "metadata" if metadata_like else "data"
    return PhysicalTable(table, rows, anchors, grid, sorted(set(header_rows)), role)


def parse_unit(texts: Iterable[str]) -> Dict[str, Any]:
    raw = None
    content = ""
    for text in texts:
        match = _UNIT_RE.search(normalize_text(text))
        if match:
            raw = match.group(0)
            content = match.group(1).replace(" ", "")
            break
    currency = None
    scale = None
    quantity = "unknown"
    upper = content.upper()
    currency_tokens = (("USD", ("USD", "미화")), ("EUR", ("EUR", "유로")),
                       ("JPY", ("JPY", "일본엔", "엔")), ("CNY", ("CNY", "위안")))
    currency = next((code for code, tokens in currency_tokens if any(token in upper for token in tokens)), None)
    if currency is None and any(token in upper for token in ("원", "KRW")):
        currency = "KRW"
    if currency is not None:
        quantity = "money"
        if "조원" in content:
            scale = 1_000_000_000_000
        elif "십억원" in content:
            scale = 1_000_000_000
        elif "억원" in content:
            scale = 100_000_000
        elif "백만" in content or "MILLION" in upper:
            scale = 1_000_000
        elif "천" in content or "THOUSAND" in upper:
            scale = 1_000
        else:
            scale = 1
    if "%" in content:
        quantity = "mixed" if quantity != "unknown" else "percent"
    if "주" in content:
        quantity = "mixed" if quantity != "unknown" else "shares"
    if any(token in content for token in ("개", "명", "대", "톤")) and quantity == "unknown":
        quantity = "count"
    return {"raw": raw, "currency": currency, "scale": scale, "quantity": quantity}


def parse_numeric(text: str) -> Dict[str, Any]:
    value = normalize_text(text)
    if not value:
        return {"value": "", "value_type": "empty", "numeric_value": None, "is_missing": True}
    if value in {"-", "–", "—", "N/A", "해당사항없음", "해당사항 없음"}:
        return {"value": value, "value_type": "dash", "numeric_value": None, "is_missing": True}
    negative = False
    candidate = value
    if candidate.startswith(("△", "▲")):
        negative = True
        candidate = candidate[1:].strip()
    if candidate.startswith("(") and candidate.endswith(")"):
        negative = True
        candidate = candidate[1:-1].strip()
    is_percent = candidate.endswith("%")
    if is_percent:
        candidate = candidate[:-1].strip()
    if _NUMBER_RE.match(candidate):
        number_text = candidate.replace(",", "")
        number: Any = float(number_text) if "." in number_text else int(number_text)
        if negative:
            number = -number
        return {
            "value": value,
            "value_type": "percent" if is_percent else "number",
            "numeric_value": number,
            "is_missing": False,
        }
    return {"value": value, "value_type": "text", "numeric_value": None, "is_missing": False}


def infer_scope(title: Optional[str], section_path: Sequence[str], aclass: Optional[str]) -> Tuple[str, str]:
    candidates = [("table_title", title or ""), ("section", " > ".join(section_path))]
    for source, value in candidates:
        if "연결" in value and ("별도" in value or "개별" in value):
            return "unknown", f"{source}:conflicting_scope:{value}"
        if "연결" in value:
            return "consolidated", f"{source}:{value}"
        if "별도" in value or "개별" in value:
            return "separate", f"{source}:{value}"
    code = (aclass or "").upper()
    if re.search(r"(?:_C(?:_|$)|NT_C_)", code):
        return "consolidated", f"aclass:{aclass}"
    if re.search(r"(?:_S(?:_|$)|NT_S_)", code):
        return "separate", f"aclass:{aclass}"
    return "unknown", "no_explicit_evidence"


def infer_statement_type(title: Optional[str]) -> str:
    value = title or ""
    mapping = [
        ("재무상태표", "balance_sheet"),
        ("포괄손익계산서", "comprehensive_income_statement"),
        ("손익계산서", "income_statement"),
        ("현금흐름표", "cash_flow_statement"),
        ("자본변동표", "changes_in_equity"),
    ]
    for token, result in mapping:
        if token in value:
            return result
    return "other"


def _iso_date(match: re.Match[str]) -> str:
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def parse_periods(texts: Iterable[str]) -> List[Dict[str, Any]]:
    periods: List[Dict[str, Any]] = []
    seen = set()
    for text in texts:
        value = normalize_text(text)
        dates = list(_DATE_RE.finditer(value))
        if not dates:
            continue
        label_match = re.search(r"제\s*\d+\s*기(?:\s*(?:반기|\d+분기|분기|말|반기말))?", value)
        label = normalize_text(label_match.group(0)) if label_match else value[:80]
        if ("부터" in value or "~" in value) and len(dates) >= 2:
            start_date, end_date = _iso_date(dates[0]), _iso_date(dates[-1])
            period_type = "duration"
        else:
            start_date, end_date = None, _iso_date(dates[-1])
            period_type = "instant"
        key = (label, start_date, end_date, period_type)
        if key in seen:
            continue
        seen.add(key)
        periods.append({
            "period_id": f"period_{len(periods) + 1}",
            "label": label,
            "start_date": start_date,
            "end_date": end_date,
            "period_type": period_type,
            "comparison_role": "current" if not periods else "prior",
            "aggregation": "annual" if start_date and start_date.endswith("01-01") and end_date and end_date.endswith("12-31") else "unknown",
            "raw": value,
        })
    return periods


def _period_for_column(header_path: Sequence[str], periods: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    header = " ".join(header_path)
    chosen = None
    for period in periods:
        label_tokens = re.findall(r"제\s*\d+\s*기|반기|\d+분기", period["label"])
        if label_tokens and all(token.replace(" ", "") in header.replace(" ", "") for token in label_tokens[:1]):
            chosen = dict(period)
            break
    if chosen is None and len(periods) == 1:
        chosen = dict(periods[0])
    if chosen is None:
        return None
    leaf = header_path[-1] if header_path else ""
    if "누적" in leaf:
        chosen["aggregation"] = "ytd"
    elif "3개월" in leaf:
        chosen["aggregation"] = "three_month"
    return chosen


def _header_paths(physical: PhysicalTable) -> List[List[str]]:
    width = max((len(row) for row in physical.grid), default=0)
    paths: List[List[str]] = []
    for col in range(width):
        values: List[str] = []
        for row_index in physical.header_rows:
            cell = physical.grid[row_index][col] if col < len(physical.grid[row_index]) else None
            value = cell.text if cell else ""
            if value and (not values or values[-1] != value):
                values.append(value)
        paths.append(values)
    return paths


class TableParser:
    def __init__(self, source: Dict[str, Any], source_file: str, source_file_role: str):
        self.source = source
        self.source_file = source_file
        self.source_file_role = source_file_role

    def parse_logical_table(
        self,
        table_id: str,
        table_elements: Sequence[ET.Element],
        section_id: Optional[str],
        section_path: Sequence[str],
        group_element: Optional[ET.Element] = None,
        preceding_text: str = "",
        following_text: str = "",
        element_ordinal: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        physicals = [build_grid(table) for table in table_elements]
        group_title = ""
        aclass = None
        if group_element is not None:
            aclass = group_element.attrib.get("ACLASS")
            for child in list(group_element):
                if local_name(child.tag) == "TITLE":
                    group_title = element_text(child)
                    break
        all_row_texts = [element_text(row) for physical in physicals for row in physical.rows]
        metadata_texts = [element_text(p.element) for p in physicals if p.role == "metadata"]
        title, title_source = self._infer_title(group_title, metadata_texts, preceding_text, section_path)
        unit = parse_unit([group_title, preceding_text, *all_row_texts, following_text])
        scope, scope_evidence = infer_scope(title, section_path, aclass)
        periods = parse_periods(all_row_texts)
        footnotes = []
        for physical in physicals:
            if physical.role == "footnote":
                footnotes.extend(element_text(row) for row in physical.rows if element_text(row))
        if is_note_text(following_text):
            footnotes.append(normalize_text(following_text))

        columns: List[Dict[str, Any]] = []
        rows: List[Dict[str, Any]] = []
        cells: List[Dict[str, Any]] = []
        for physical_index, physical in enumerate(physicals, start=1):
            if physical.role != "data":
                continue
            header_paths = _header_paths(physical)
            column_keys: Dict[int, str] = {}
            column_periods: Dict[int, Optional[Dict[str, Any]]] = {}
            for col, path in enumerate(header_paths):
                if col == 0:
                    continue
                key = f"p{physical_index}_c{col}"
                column_keys[col] = key
                period = _period_for_column(path, periods)
                column_periods[col] = period
                columns.append({
                    "column_key": key,
                    "physical_table_ordinal": physical_index,
                    "column_index": col,
                    "label": path[-1] if path else None,
                    "header_path": path,
                    "period_id": period["period_id"] if period else None,
                })
            header_set = set(physical.header_rows)
            for row_index, grid_row in enumerate(physical.grid):
                if row_index in header_set:
                    continue
                row_anchors = sorted({id(cell): cell for cell in grid_row if cell}.values(), key=lambda cell: cell.col)
                if not row_anchors:
                    continue
                row_label_cell = row_anchors[0]
                row_label = row_label_cell.text or None
                row_cell_ids: List[str] = []
                for anchor in row_anchors[1:]:
                    if anchor.col not in column_keys:
                        continue
                    cell_id = f"{table_id}:p{physical_index}:r{row_index}:c{anchor.col}"
                    column_path = header_paths[anchor.col] if anchor.col < len(header_paths) else []
                    period = column_periods.get(anchor.col)
                    parsed = parse_numeric(anchor.text)
                    cell = {
                        "cell_id": cell_id,
                        "table_id": table_id,
                        "source": self.source,
                        "source_file": self.source_file,
                        "source_file_role": self.source_file_role,
                        "section_path": list(section_path),
                        "physical_table_ordinal": physical_index,
                        "row_index": row_index,
                        "column_index": anchor.col,
                        "row_label": row_label,
                        "row_path": [row_label] if row_label else [],
                        "column_label": column_path[-1] if column_path else None,
                        "column_path": column_path,
                        "original_text": anchor.text,
                        **parsed,
                        "unit": unit,
                        "period": period,
                        "scope": scope,
                        "is_missing": parsed["is_missing"],
                        "original_tag": anchor.tag,
                        "rowspan": anchor.rowspan,
                        "colspan": anchor.colspan,
                        "parse_warnings": [],
                    }
                    cells.append(cell)
                    row_cell_ids.append(cell_id)
                rows.append({
                    "row_key": f"p{physical_index}_r{row_index}",
                    "physical_table_ordinal": physical_index,
                    "row_index": row_index,
                    "label": row_label,
                    "row_path": [row_label] if row_label else [],
                    "cell_ids": row_cell_ids,
                })

        search_parts = [
            self.source.get("corp_name", ""), self.source.get("report_nm", ""),
            " > ".join(section_path), title or "", unit.get("raw") or "",
        ]
        for cell in cells:
            search_parts.append(join_nonempty([
                cell.get("row_label") or "", " > ".join(cell.get("column_path") or []), cell.get("value") or ""
            ], " | "))
        table = {
            "table_id": table_id,
            "source": self.source,
            "source_file": self.source_file,
            "source_file_role": self.source_file_role,
            "section_id": section_id,
            "section": section_path[0] if section_path else None,
            "subsection": section_path[1] if len(section_path) > 1 else None,
            "section_path": list(section_path),
            "table_title": title,
            "title_source": title_source,
            "unit": unit,
            "scope": scope,
            "scope_evidence": scope_evidence,
            "statement_type": infer_statement_type(title),
            "periods": periods,
            "columns": columns,
            "rows": rows,
            "normalized_cell_ids": [cell["cell_id"] for cell in cells],
            "footnotes": list(dict.fromkeys(footnotes)),
            "physical_tables": [
                {"ordinal": index, "role": physical.role, "row_count": len(physical.rows), "cell_count": len(physical.anchors)}
                for index, physical in enumerate(physicals, start=1)
            ],
            "search_text": join_nonempty(search_parts, "\n"),
            "source_locator": {"element_ordinal": element_ordinal, "xml_path": None},
            "aclass": aclass,
        }
        return table, cells

    @staticmethod
    def _infer_title(
        group_title: str,
        metadata_texts: Sequence[str],
        preceding_text: str,
        section_path: Sequence[str],
    ) -> Tuple[Optional[str], str]:
        if group_title:
            return group_title, "table_group_title"
        for text in metadata_texts:
            cleaned = _UNIT_RE.sub("", text).strip(" []")
            statement = re.search(
                r"(?:연결\s*)?(?:재무상태표|포괄손익계산서|손익계산서|현금흐름표|자본변동표)",
                cleaned,
            )
            if statement:
                return normalize_text(statement.group(0)), "metadata_table"
            bracketed = re.match(r"\[([^\]]{1,120})\]", text)
            if bracketed:
                return normalize_text(bracketed.group(1)), "metadata_table"
            if cleaned and len(cleaned) <= 160:
                return cleaned, "metadata_table"
        if preceding_text and len(preceding_text) <= 160:
            return normalize_text(preceding_text), "preceding_text"
        if section_path:
            return section_path[-1], "section_title"
        return None, "unknown"
