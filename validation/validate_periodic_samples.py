"""Validate a fixed, diverse sample without batch-processing the corpus."""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from src.parser.periodic_parser import PeriodicParser, load_manifest
from src.parser.text_cleaner import element_text, local_name, split_leading_bold_heading
from src.parser.xml_recovery import parse_xml_file


SAMPLES = [
    {"doc_id": "periodic_20230515002335", "tags": ["quarter", "strict_recovery", "multilevel_header"]},
    {"doc_id": "periodic_20230814002534", "tags": ["half", "three_month_ytd"]},
    {"doc_id": "periodic_20240312000736", "tags": ["annual", "complex_table_group", "attachments"], "attachments": True},
    {"doc_id": "periodic_20240329002895", "tags": ["correction_annual", "before_after"]},
    {"doc_id": "periodic_20240913000803", "tags": ["correction_half", "before_after"]},
    {"doc_id": "periodic_20230518000337", "tags": ["correction_quarter", "materials"]},
    {"doc_id": "periodic_20240318000916", "tags": ["annual", "steel", "rowspan_colspan"]},
    {"doc_id": "periodic_20240314001585", "tags": ["annual", "finance"]},
    {"doc_id": "periodic_20240322000254", "tags": ["annual", "entertainment"]},
    {"doc_id": "periodic_20240307000835", "tags": ["annual", "biopharma"]},
    {"doc_id": "periodic_20240313001451", "tags": ["annual", "automotive"]},
    {"doc_id": "periodic_20240318000952", "tags": ["annual", "aerospace_defense"]},
]


DOCUMENT_NAMES = {"annual": "사업보고서", "half": "반기보고서", "quarter": "분기보고서"}


def add_result(
    rows: List[Dict[str, Any]], record: Dict[str, Any], item: str, ok: bool,
    problem: str = "", cause: str = "", needs_fix: bool = False,
) -> None:
    rows.append({
        "문서": f"{record['corp_name']} {record['report_nm']} ({record['rcept_no']})",
        "테스트 항목": item,
        "정상 여부": "정상" if ok else "오류",
        "문제": problem if not ok else "-",
        "원인": cause if not ok else "-",
        "수정 필요 여부": "예" if needs_fix else "아니오",
    })


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--data-root", type=Path, required=True)
    argument_parser.add_argument("--output-dir", type=Path, default=Path("validation/results"))
    arguments = argument_parser.parse_args()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)

    records = load_manifest(arguments.data_root / "manifest.jsonl")
    by_id = {record["doc_id"]: record for record in records}
    parser = PeriodicParser(arguments.data_root, records)
    rows: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []

    for sample in SAMPLES:
        record = by_id[sample["doc_id"]]
        include_attachments = bool(sample.get("attachments"))
        result = parser.parse_document(record, include_attachments=include_attachments)
        document = result["document"]
        source = document["source"]

        document_ok = all([
            source["corp_code"] == record["corp_code"],
            source["corp_name"] == record["corp_name"],
            source["report_nm"] == record["report_nm"],
            source["base_year"] == record["base_year"],
            source["rcept_dt"] == record["rcept_dt"],
            source["is_correction"] == record["is_correction"],
            document["document_name"] == DOCUMENT_NAMES[record["doc_subtype"]],
        ])
        add_result(
            rows, record, "Document 메타데이터", document_ok,
            "manifest와 structured document 불일치", "메타데이터 매핑 또는 XML 문서명 추출", not document_ok,
        )

        official_titles = {section["title"] for section in result["sections"] if section["title_source"] == "TITLE"}
        section_ok = "II. 사업의 내용" in official_titles and "III. 재무에 관한 사항" in official_titles
        add_result(
            rows, record, "Section hierarchy", section_ok,
            "핵심 공식 섹션 누락", "LIBRARY/SECTION 재귀 순회 또는 TITLE 인식", not section_ok,
        )

        main_file = arguments.data_root / record["file_path"] / f"{record['rcept_no']}.xml"
        recovered = parse_xml_file(main_file)
        sample_paragraphs = []
        parent_map = {id(child): parent for parent in recovered.root.iter() for child in list(parent)}
        for node in recovered.root.iter():
            if local_name(node.tag) != "P":
                continue
            cursor = parent_map.get(id(node))
            excluded = False
            within_section = False
            while cursor is not None:
                tag = local_name(cursor.tag)
                if tag in {"TABLE", "COVER", "CORRECTION"}:
                    excluded = True
                    break
                if tag.startswith("SECTION"):
                    within_section = True
                cursor = parent_map.get(id(cursor))
            text = element_text(node)
            if within_section and not excluded and len(text) >= 80:
                heading, remainder = split_leading_bold_heading(node)
                sample_paragraphs.append(remainder if heading and remainder else text)
            if len(sample_paragraphs) == 8:
                break
        parsed_body = "\n".join(chunk.get("body_text", "") for chunk in result["text_chunks"] if chunk["source_file_role"] == "main")
        matched = sum(1 for paragraph in sample_paragraphs if paragraph in parsed_body)
        text_ok = bool(result["text_chunks"]) and (not sample_paragraphs or matched >= max(1, len(sample_paragraphs) - 1))
        add_result(
            rows, record, "본문 원문 대조", text_ok,
            f"표본 문단 {len(sample_paragraphs)}개 중 {matched}개 완전 일치", "mixed content 결합 또는 chunk 분할", not text_ok,
        )

        main_tables = [table for table in result["logical_tables"] if table["source_file_role"] == "main"]
        statement_tables = [
            table for table in main_tables
            if table["statement_type"] in {"balance_sheet", "income_statement", "cash_flow_statement", "changes_in_equity", "comprehensive_income_statement"}
        ]
        useful_statement = next((
            table for table in statement_tables
            if table["unit"].get("raw") and table["scope"] != "unknown" and table["periods"] and table["normalized_cell_ids"]
        ), None)
        table_ok = useful_statement is not None
        add_result(
            rows, record, "Table 제목·단위·scope·period", table_ok,
            "완전한 재무제표 logical table을 찾지 못함", "제목/단위 표 병합 또는 scope/기간 인식", not table_ok,
        )

        main_cells = [cell for cell in result["table_cells"] if cell["source_file_role"] == "main"]
        numeric_cell = next((
            cell for cell in main_cells
            if cell["numeric_value"] is not None and cell["row_label"] and cell["column_path"] and cell["unit"].get("raw")
        ), None)
        raw_text = main_file.read_text(encoding="utf-8", errors="replace")
        cell_ok = bool(numeric_cell and numeric_cell["original_text"] in raw_text)
        add_result(
            rows, record, "Cell 행·열·수치 원문 대조", cell_ok,
            "행/열/단위가 있는 numeric cell을 원문에서 확인하지 못함", "span grid 또는 numeric normalization", not cell_ok,
        )

        span_cell = next((cell for cell in main_cells if cell.get("rowspan", 1) > 1 or cell.get("colspan", 1) > 1), None)
        span_ok = span_cell is not None
        add_result(
            rows, record, "ROWSPAN/COLSPAN", span_ok,
            "병합 셀 provenance가 없음", "grid anchor span 보존", not span_ok,
        )

        if record["is_correction"]:
            correction = result["corrections"][0] if result["corrections"] else None
            items = correction.get("correction_items", []) if correction else []
            correction_ok = bool(
                correction and correction.get("original_doc_id") and
                any(item.get("before", {}).get("original_text") and item.get("after", {}).get("original_text") for item in items)
            )
            add_result(
                rows, record, "Correction original/before/after/current", correction_ok,
                "정정 관계 또는 정정 전·후 항목 누락", "CORRECTION 표 형식 예외 또는 version chain", not correction_ok,
            )

        if include_attachments:
            roles = {attachment.get("role") for attachment in document.get("attachments", [])}
            attachment_ok = {"audit_report", "consolidated_audit_report"}.issubset(roles)
            add_result(
                rows, record, "감사보고서 첨부 XML", attachment_ok,
                "감사/연결감사 첨부 역할 누락", "파일 suffix/ACODE 역할 매핑", not attachment_ok,
            )

        recovery_file = next((item for item in result["parse_log"]["files"] if item["source_file_role"] == "main"), None)
        recovery_ok = bool(recovery_file and recovery_file["status"] != "failed")
        add_result(
            rows, record, "XML strict/recovery 상태", recovery_ok,
            "main XML 파싱 실패", "allowlist 정제 후에도 well-formed가 아님", not recovery_ok,
        )

        summaries.append({
            "doc_id": record["doc_id"], "corp_name": record["corp_name"],
            "industry": record.get("industry"), "sector": record.get("sector"),
            "doc_subtype": record["doc_subtype"], "is_correction": record["is_correction"],
            "tags": sample["tags"], "status": result["parse_log"]["status"],
            "strict_xml_valid": recovery_file.get("strict_xml_valid") if recovery_file else None,
            "repair_counts": recovery_file.get("repair_counts") if recovery_file else None,
            "record_counts": document["record_counts"],
            "max_rowspan": max((cell.get("rowspan", 1) for cell in main_cells), default=1),
            "max_colspan": max((cell.get("colspan", 1) for cell in main_cells), default=1),
            "manual_numeric_example": {
                key: numeric_cell.get(key) for key in ("table_id", "row_label", "column_path", "original_text", "numeric_value", "scope")
            } if numeric_cell else None,
            "source_match": cell_ok,
        })
        del result
        gc.collect()

    payload = {"samples": summaries, "validation_rows": rows}
    (arguments.output_dir / "sample_validation_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    failures = [row for row in rows if row["정상 여부"] == "오류"]
    lines = [
        "# STEP 3. 정기공시 Parser 샘플 검증 결과", "",
        f"- 샘플: {len(summaries)}건", f"- 검증 항목: {len(rows)}건", f"- 오류: {len(failures)}건", "",
        "전체 정기공시를 실행하지 않고 아래 고정 샘플만 파싱했다. Cell 검증은 structured cell의 원문 값이 실제 main XML에 존재하는지 다시 대조했다.", "",
        "| 문서 | 테스트 항목 | 정상 여부 | 문제 | 원인 | 수정 필요 여부 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row[key]).replace("|", "\\|") for key in (
            "문서", "테스트 항목", "정상 여부", "문제", "원인", "수정 필요 여부"
        )) + " |")
    lines.extend(["", "## 샘플 구성", ""])
    for summary in summaries:
        lines.append(
            f"- `{summary['doc_id']}` {summary['corp_name']} / {summary['industry']} / "
            f"{summary['doc_subtype']} / correction={summary['is_correction']} / "
            f"strict={summary['strict_xml_valid']} / tags={', '.join(summary['tags'])}"
        )
    (arguments.output_dir / "sample_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"samples": len(summaries), "checks": len(rows), "failures": len(failures)}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
