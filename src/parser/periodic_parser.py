"""End-to-end parser for DART periodic filing folders.

Usage:
    python -m src.parser.periodic_parser \
      --data-root /path/to/corpus \
      --doc-id periodic_20230515002335 \
      --output-root outputs/sample

The parser only reads ``raw`` files. Structured records are written under the
explicit output root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from .correction_parser import parse_correction
from .table_parser import TableParser, build_grid
from .text_cleaner import (
    element_text,
    is_bold_span,
    is_note_text,
    local_name,
    normalize_text,
    split_leading_bold_heading,
)
from .path_utils import resolve_manifest_path
from .xml_recovery import RecoveryResult, parse_xml_file


SCHEMA_VERSION = "1.0.0"
SOURCE_FIELDS = [
    "doc_id", "corp_code", "corp_name", "listed_name", "stock_code", "industry", "sector", "flr_nm",
    "report_nm", "rcept_no",
    "rcept_dt", "doc_group", "doc_subtype", "base_year", "base_month",
    "is_correction", "file_path",
]
SECTION_TAGS = {f"SECTION-{level}" for level in range(1, 7)} | {"SECTION"}


def load_manifest(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid manifest JSON at line {line_number}: {error}") from error
    return records


def _source_from_manifest(record: Dict[str, Any]) -> Dict[str, Any]:
    return {field: record.get(field) for field in SOURCE_FIELDS}


def build_version_index(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for record in records:
        if record.get("doc_group") != "periodic":
            continue
        key = (
            record.get("corp_code"), record.get("doc_subtype"),
            record.get("base_year"), record.get("base_month"),
        )
        groups.setdefault(key, []).append(record)
    result: Dict[str, Dict[str, Any]] = {}
    for versions in groups.values():
        versions.sort(key=lambda item: (str(item.get("rcept_dt", "")), str(item.get("rcept_no", ""))))
        originals = [item for item in versions if not item.get("is_correction")]
        original_doc_id = originals[0].get("doc_id") if originals else None
        for index, record in enumerate(versions):
            result[record["doc_id"]] = {
                "version_role": "correction" if record.get("is_correction") else "original",
                "original_doc_id": original_doc_id,
                "supersedes_doc_id": versions[index - 1].get("doc_id") if index else None,
                "superseded_by_doc_id": versions[index + 1].get("doc_id") if index + 1 < len(versions) else None,
                "is_latest_version": index == len(versions) - 1,
                "version_index": index + 1,
                "version_count": len(versions),
            }
    return result


def _direct_title(element: ET.Element) -> str:
    for child in list(element):
        if local_name(child.tag) == "TITLE":
            return element_text(child)
    return ""


def _file_role(path: Path) -> str:
    name = path.stem
    if name.endswith("_00760"):
        return "audit_report"
    if name.endswith("_00761"):
        return "consolidated_audit_report"
    return "main" if "_" not in name else "other_attachment"


def _json_dump(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _jsonl_dump(path: Path, values: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


class PeriodicParser:
    def __init__(
        self,
        data_root: Path,
        manifest_records: Optional[Sequence[Dict[str, Any]]] = None,
        chunk_soft_limit: int = 1800,
    ):
        self.data_root = Path(data_root)
        self.manifest_records = list(manifest_records or [])
        self.version_index = build_version_index(self.manifest_records)
        self.chunk_soft_limit = chunk_soft_limit

    def parse_document(
        self,
        record: Dict[str, Any],
        output_root: Optional[Path] = None,
        include_attachments: bool = True,
        overwrite_output: bool = False,
    ) -> Dict[str, Any]:
        source = _source_from_manifest(record)
        version = self.version_index.get(record["doc_id"], {
            "version_role": "correction" if record.get("is_correction") else "original",
            "original_doc_id": None,
            "supersedes_doc_id": None,
            "superseded_by_doc_id": None,
            "is_latest_version": True,
            "version_index": 1,
            "version_count": 1,
        })
        folder = resolve_manifest_path(self.data_root, str(record["file_path"]))
        result: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "document": {}, "sections": [], "text_chunks": [], "logical_tables": [],
            "table_cells": [], "corrections": [], "images": [],
            "parse_log": {"status": "success", "files": [], "warnings": [], "errors": []},
        }
        if not folder.is_dir():
            result["parse_log"]["status"] = "failed"
            result["parse_log"]["errors"].append(f"source_folder_not_found:{folder}")
            result["document"] = self._document_record(source, version, [], result, record)
            return result
        if record.get("file_format") == "pdf+html":
            return self._parse_pdf_fallback(record, source, version, folder, result, output_root, overwrite_output)
        if record.get("file_format") != "xml":
            result["parse_log"]["status"] = "failed"
            result["parse_log"]["errors"].append(f"unsupported_file_format:{record.get('file_format')}")
            result["document"] = self._document_record(source, version, [], result, record)
            return result

        xml_files = sorted(folder.glob("*.xml"), key=lambda path: (path.stem != record["rcept_no"], path.name))
        if not include_attachments:
            xml_files = [path for path in xml_files if path.stem == record["rcept_no"]]
        source_files: List[Dict[str, Any]] = []
        main_failed = False
        for path in xml_files:
            role = _file_role(path)
            try:
                parsed_file, recovery = self._parse_file(path, role, source, version)
                for key in ("sections", "text_chunks", "logical_tables", "table_cells", "corrections", "images"):
                    result[key].extend(parsed_file[key])
                file_log = {
                    "source_file": path.name,
                    "source_file_role": role,
                    "status": "warning" if recovery.warnings else "success",
                    "strict_xml_valid": recovery.strict_xml_valid,
                    "repair_applied": recovery.repair_applied,
                    "repair_counts": recovery.repair_counts,
                    "warnings": recovery.warnings,
                    "error": None,
                }
                missing_images = sum(1 for image in parsed_file["images"] if not image["asset_exists"])
                if missing_images:
                    file_log["status"] = "warning"
                    file_log["warnings"].append(f"missing_image_assets:{missing_images}")
                result["parse_log"]["files"].append(file_log)
                source_files.append({
                    "filename": path.name,
                    "role": role,
                    "document_name": parsed_file["file_meta"].get("document_name"),
                    "document_acode": parsed_file["file_meta"].get("document_acode"),
                    "formula_version": parsed_file["file_meta"].get("formula_version"),
                    "raw_sha256": recovery.raw_sha256,
                    "strict_xml_valid": recovery.strict_xml_valid,
                })
            except Exception as error:  # file-level isolation is intentional
                result["parse_log"]["files"].append({
                    "source_file": path.name, "source_file_role": role, "status": "failed",
                    "strict_xml_valid": False, "repair_applied": False, "repair_counts": {},
                    "warnings": [], "error": str(error),
                })
                result["parse_log"]["errors"].append(f"{path.name}:{error}")
                if role == "main":
                    main_failed = True

        for file_log in result["parse_log"]["files"]:
            result["parse_log"]["warnings"].extend(
                f"{file_log['source_file']}:{warning}" for warning in file_log.get("warnings", [])
            )
        if main_failed or not source_files:
            result["parse_log"]["status"] = "failed"
        elif result["parse_log"]["warnings"] or result["parse_log"]["errors"]:
            result["parse_log"]["status"] = "warning"

        result["document"] = self._document_record(source, version, source_files, result, record)
        if output_root is not None:
            self.write_result(result, Path(output_root), overwrite=overwrite_output)
        return result

    def _parse_pdf_fallback(
        self, record: Dict[str, Any], source: Dict[str, Any], version: Dict[str, Any], folder: Path,
        result: Dict[str, Any], output_root: Optional[Path], overwrite_output: bool,
    ) -> Dict[str, Any]:
        """Preserve the three official non-XML periodic filings as searchable text.

        PDF layout is not promoted to cells or logical tables: text extraction cannot
        safely recover row/column relationships.  Page locators and a warning make
        this limitation explicit while preventing otherwise valid filings from being
        omitted from retrieval.
        """
        try:
            from pypdf import PdfReader
        except ImportError as error:  # pragma: no cover - dependency failure is environment-specific
            result["parse_log"]["status"] = "failed"
            result["parse_log"]["errors"].append("pdf_fallback_dependency_missing:pypdf")
            result["document"] = self._document_record(source, version, [], result, record)
            return result

        pdf_files = sorted(folder.glob("*.pdf"))
        if not pdf_files:
            result["parse_log"]["status"] = "failed"
            result["parse_log"]["errors"].append("pdf_fallback_source_not_found")
            result["document"] = self._document_record(source, version, [], result, record)
            return result

        path = pdf_files[0]
        try:
            reader = PdfReader(path)
            section_id = f"{source['doc_id']}:main:section:00001"
            section_title = "PDF 대체 원문"
            result["sections"].append({
                "section_id": section_id, "source": source, "source_file": path.name,
                "source_file_role": "main", "parent_section_id": None, "level": 1,
                "title": section_title, "title_source": "pdf_fallback",
                "section_path": [section_title], "order": 1, "source_tag": "PDF",
                "xml_path": None, "element_ordinal": None,
            })
            extracted_pages = 0
            chunk_index = 0
            for page_number, page in enumerate(reader.pages, start=1):
                raw_text = page.extract_text() or ""
                pieces = self._split_pdf_text(raw_text)
                if not pieces:
                    continue
                extracted_pages += 1
                for piece_index, body in enumerate(pieces, start=1):
                    chunk_index += 1
                    context = " | ".join(filter(None, [source.get("corp_name"), source.get("report_nm"), section_title]))
                    result["text_chunks"].append({
                        "chunk_id": f"{source['doc_id']}:main:text:{chunk_index:05d}",
                        "source": source, "source_file": path.name, "source_file_role": "main",
                        "section_id": section_id, "section": section_title, "subsection": f"{page_number}페이지",
                        "section_path": [section_title, f"{page_number}페이지"], "content_type": "pdf_text",
                        "heading": None, "text": f"{context} | {page_number}페이지\n{body}",
                        "body_text": body, "paragraph_count": 1, "related_table_ids": [],
                        "source_locator": {"page_number": page_number, "page_chunk": piece_index, "xml_path": None},
                    })
            if not result["text_chunks"]:
                raise ValueError("no_extractable_pdf_text")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            source_files = [{
                "filename": path.name, "role": "main", "document_name": record.get("report_nm"),
                "document_acode": None, "formula_version": None, "raw_sha256": digest,
                "strict_xml_valid": None, "page_count": len(reader.pages), "text_pages": extracted_pages,
            }]
            for viewer in sorted(folder.glob("*.html")):
                source_files.append({
                    "filename": viewer.name, "role": "viewer_html", "document_name": None,
                    "document_acode": None, "formula_version": None,
                    "raw_sha256": hashlib.sha256(viewer.read_bytes()).hexdigest(), "strict_xml_valid": None,
                })
            warnings = ["pdf_fallback_text_only:tables_and_images_not_structured"]
            if record.get("is_correction"):
                warnings.append("pdf_correction_before_after_not_structured")
            result["parse_log"].update({
                "status": "warning", "warnings": warnings,
                "files": [{"source_file": path.name, "source_file_role": "main", "status": "warning",
                           "strict_xml_valid": None, "repair_applied": False, "repair_counts": {},
                           "warnings": warnings, "error": None}],
            })
            result["document"] = self._document_record(source, version, source_files, result, record)
        except Exception as error:
            result["parse_log"]["status"] = "failed"
            result["parse_log"]["errors"].append(f"pdf_fallback_failed:{error}")
            result["document"] = self._document_record(source, version, [], result, record)
        if output_root is not None:
            self.write_result(result, Path(output_root), overwrite=overwrite_output)
        return result

    def _split_pdf_text(self, raw_text: str) -> List[str]:
        lines = [normalize_text(line) for line in raw_text.splitlines()]
        lines = [line for line in lines if line]
        pieces: List[str] = []
        current: List[str] = []
        current_length = 0
        for line in lines:
            # Long extracted lines are split at sentence boundaries where possible.
            fragments = re.split(r"(?<=[.!?])\s+", line) if len(line) > self.chunk_soft_limit else [line]
            for fragment in fragments:
                if current and current_length + len(fragment) + 1 > self.chunk_soft_limit:
                    pieces.append("\n".join(current)); current = []; current_length = 0
                if len(fragment) > self.chunk_soft_limit:
                    for start in range(0, len(fragment), self.chunk_soft_limit):
                        if current:
                            pieces.append("\n".join(current)); current = []; current_length = 0
                        pieces.append(fragment[start:start + self.chunk_soft_limit])
                    continue
                current.append(fragment); current_length += len(fragment) + 1
        if current:
            pieces.append("\n".join(current))
        return pieces

    def _parse_file(
        self,
        path: Path,
        role: str,
        source: Dict[str, Any],
        version: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], RecoveryResult]:
        recovery = parse_xml_file(path)
        root = recovery.root
        ordinals = {id(element): index for index, element in enumerate(root.iter(), start=1)}
        parent_map = {id(child): parent for parent in root.iter() for child in list(parent)}
        document_name_element = next((node for node in root.iter() if local_name(node.tag) == "DOCUMENT-NAME"), None)
        formula_element = next((node for node in root.iter() if local_name(node.tag) == "FORMULA-VERSION"), None)
        file_meta = {
            "document_name": element_text(document_name_element),
            "document_acode": document_name_element.attrib.get("ACODE") if document_name_element is not None else None,
            "formula_version": element_text(formula_element),
            "formula_date": formula_element.attrib.get("ADATE") if formula_element is not None else None,
        }
        body = next((node for node in root.iter() if local_name(node.tag) == "BODY"), root)
        state: Dict[str, Any] = {
            "sections": [], "text_blocks": [], "logical_tables": [], "table_cells": [],
            "images": [], "section_counter": 0, "table_counter": 0,
            "node_section": {}, "processed_images": set(),
        }
        self._walk_container(
            body, None, [], state, source, path, role, ordinals, parent_map
        )
        self._collect_remaining_images(root, state, source, path, role, ordinals, parent_map)
        chunks = self._build_chunks(state["text_blocks"], source, path.name, role)
        corrections = parse_correction(root, source, version) if role == "main" else []
        return ({
            "file_meta": file_meta,
            "sections": state["sections"],
            "text_chunks": chunks,
            "logical_tables": state["logical_tables"],
            "table_cells": state["table_cells"],
            "corrections": corrections,
            "images": state["images"],
        }, recovery)

    def _walk_container(
        self,
        container: ET.Element,
        base_section: Optional[Dict[str, Any]],
        base_path: Sequence[str],
        state: Dict[str, Any],
        source: Dict[str, Any],
        path: Path,
        role: str,
        ordinals: Dict[int, int],
        parent_map: Dict[int, ET.Element],
    ) -> None:
        children = list(container)
        active_section = base_section
        active_path = list(base_path)
        last_text = ""
        index = 0
        while index < len(children):
            child = children[index]
            tag = local_name(child.tag)
            if active_section:
                state["node_section"][id(child)] = active_section
            if tag in SECTION_TAGS:
                title = _direct_title(child) or f"Untitled {tag}"
                level = int(tag.split("-")[-1]) if "-" in tag and tag.split("-")[-1].isdigit() else ((base_section or {}).get("level", 0) + 1)
                official = self._new_section(
                    state, source, path.name, role, base_section, level, title, "TITLE",
                    list(base_path) + [title], ordinals.get(id(child)), tag,
                )
                state["node_section"][id(child)] = official
                self._walk_container(child, official, official["section_path"], state, source, path, role, ordinals, parent_map)
                active_section = base_section
                active_path = list(base_path)
                index += 1
                continue
            if tag == "LIBRARY":
                self._walk_container(child, base_section, base_path, state, source, path, role, ordinals, parent_map)
                index += 1
                continue
            if tag in {"TITLE", "PGBRK", "COVER", "CORRECTION"}:
                index += 1
                continue
            if tag == "P":
                heading, remainder = split_leading_bold_heading(child)
                if heading:
                    parent = base_section
                    parent_path = list(base_path)
                    inferred = self._new_section(
                        state, source, path.name, role, parent,
                        ((parent or {}).get("level", 0) + 1), heading, "bold_span",
                        parent_path + [heading], ordinals.get(id(child)), "SPAN",
                    )
                    active_section, active_path = inferred, inferred["section_path"]
                    if remainder:
                        self._add_text_block(state, remainder, active_section, active_path, ordinals.get(id(child)))
                        last_text = remainder
                else:
                    text = element_text(child)
                    if text:
                        self._add_text_block(state, text, active_section, active_path, ordinals.get(id(child)))
                        last_text = text
                index += 1
                continue
            if tag == "SPAN":
                text = element_text(child)
                if text:
                    if is_bold_span(child) and len(text) <= 160:
                        inferred = self._new_section(
                            state, source, path.name, role, base_section,
                            ((base_section or {}).get("level", 0) + 1), text, "bold_span",
                            list(base_path) + [text], ordinals.get(id(child)), "SPAN",
                        )
                        active_section, active_path = inferred, inferred["section_path"]
                    else:
                        self._add_text_block(state, text, active_section, active_path, ordinals.get(id(child)))
                        last_text = text
                index += 1
                continue
            if tag == "IMAGE":
                self._add_image(child, active_section, active_path, state, source, path, role, ordinals, parent_map)
                index += 1
                continue
            if tag == "TABLE-GROUP":
                tables = [node for node in child.iter() if local_name(node.tag) == "TABLE"]
                if tables:
                    following = self._following_text(children, index + 1)
                    self._add_table(
                        tables, child, active_section, active_path, state, source, path.name,
                        role, last_text, following, ordinals.get(id(child)),
                    )
                index += 1
                continue
            if tag == "TABLE":
                grouped = [child]
                physical = build_grid(child)
                scan = index + 1
                if physical.role in {"metadata", "data"}:
                    while scan < len(children):
                        between = children[scan]
                        between_tag = local_name(between.tag)
                        if between_tag == "PGBRK" or (between_tag == "P" and not element_text(between)):
                            scan += 1
                            continue
                        if between_tag != "TABLE":
                            break
                        candidate = build_grid(between)
                        if physical.role == "metadata" and len(grouped) == 1 and candidate.role == "data":
                            grouped.append(between)
                            physical = candidate
                            scan += 1
                            continue
                        if candidate.role == "footnote":
                            grouped.append(between)
                            scan += 1
                        break
                self._add_table(
                    grouped, None, active_section, active_path, state, source, path.name,
                    role, last_text, self._following_text(children, scan), ordinals.get(id(child)),
                )
                index = max(index + 1, scan if len(grouped) > 1 else index + 1)
                continue
            if tag not in {"TABLE", "TR", "TH", "TD", "TE", "TU", "COLGROUP", "COL", "THEAD", "TBODY"}:
                self._walk_container(child, active_section, active_path, state, source, path, role, ordinals, parent_map)
            index += 1

    @staticmethod
    def _following_text(children: Sequence[ET.Element], start: int) -> str:
        for candidate in children[start:start + 4]:
            tag = local_name(candidate.tag)
            if tag == "PGBRK" or (tag == "P" and not element_text(candidate)):
                continue
            return element_text(candidate) if tag in {"P", "SPAN"} else ""
        return ""

    @staticmethod
    def _new_section(
        state: Dict[str, Any], source: Dict[str, Any], source_file: str, role: str,
        parent: Optional[Dict[str, Any]], level: int, title: str, title_source: str,
        section_path: Sequence[str], ordinal: Optional[int], source_tag: str,
    ) -> Dict[str, Any]:
        state["section_counter"] += 1
        section_id = f"{source['doc_id']}:{role}:section:{state['section_counter']:05d}"
        section = {
            "section_id": section_id,
            "source": source,
            "source_file": source_file,
            "source_file_role": role,
            "parent_section_id": parent.get("section_id") if parent else None,
            "level": level,
            "title": title,
            "title_source": title_source,
            "section_path": list(section_path),
            "order": state["section_counter"],
            "source_tag": source_tag,
            "xml_path": None,
            "element_ordinal": ordinal,
        }
        state["sections"].append(section)
        return section

    @staticmethod
    def _add_text_block(
        state: Dict[str, Any], text: str, section: Optional[Dict[str, Any]],
        section_path: Sequence[str], ordinal: Optional[int],
    ) -> None:
        cleaned = normalize_text(text)
        if not cleaned:
            return
        state["text_blocks"].append({
            "text": cleaned,
            "section_id": section.get("section_id") if section else None,
            "section_path": list(section_path),
            "heading": section.get("title") if section and section.get("title_source") == "bold_span" else None,
            "element_ordinal": ordinal,
        })

    def _add_table(
        self, tables: Sequence[ET.Element], group: Optional[ET.Element],
        section: Optional[Dict[str, Any]], section_path: Sequence[str], state: Dict[str, Any],
        source: Dict[str, Any], source_file: str, role: str, preceding_text: str,
        following_text: str, ordinal: Optional[int],
    ) -> None:
        state["table_counter"] += 1
        table_id = f"{source['doc_id']}:{role}:table:{state['table_counter']:05d}"
        parser = TableParser(source, source_file, role)
        table, cells = parser.parse_logical_table(
            table_id, tables, section.get("section_id") if section else None,
            section_path, group, preceding_text, following_text, ordinal,
        )
        state["logical_tables"].append(table)
        state["table_cells"].extend(cells)

    def _add_image(
        self, image: ET.Element, section: Optional[Dict[str, Any]], section_path: Sequence[str],
        state: Dict[str, Any], source: Dict[str, Any], path: Path, role: str,
        ordinals: Dict[int, int], parent_map: Dict[int, ET.Element],
    ) -> None:
        if id(image) in state["processed_images"]:
            return
        state["processed_images"].add(id(image))
        img = next((node for node in image.iter() if local_name(node.tag) == "IMG"), None)
        caption_node = next((node for node in image.iter() if local_name(node.tag) == "IMG-CAPTION"), None)
        filename = element_text(img)
        caption = element_text(caption_node) or None
        parent = parent_map.get(id(image))
        siblings = list(parent) if parent is not None else []
        position = siblings.index(image) if image in siblings else -1
        before = element_text(siblings[position - 1]) if position > 0 else ""
        after = element_text(siblings[position + 1]) if 0 <= position + 1 < len(siblings) else ""
        state["images"].append({
            "image_id": f"{source['doc_id']}:{role}:image:{len(state['images']) + 1:04d}",
            "source": source,
            "source_file": path.name,
            "source_file_role": role,
            "section_id": section.get("section_id") if section else None,
            "section": section_path[0] if section_path else None,
            "section_path": list(section_path),
            "filename": filename,
            "caption": caption,
            "width": img.attrib.get("WIDTH") if img is not None else None,
            "height": img.attrib.get("HEIGHT") if img is not None else None,
            "asset_exists": bool(filename and (path.parent / filename).is_file()),
            "context_before": before[-500:],
            "context_after": after[:500],
            "ocr_attempted": False,
            "source_locator": {"element_ordinal": ordinals.get(id(image)), "xml_path": None},
        })

    def _collect_remaining_images(
        self, root: ET.Element, state: Dict[str, Any], source: Dict[str, Any], path: Path,
        role: str, ordinals: Dict[int, int], parent_map: Dict[int, ET.Element],
    ) -> None:
        for image in (node for node in root.iter() if local_name(node.tag) == "IMAGE"):
            section = None
            cursor: Optional[ET.Element] = image
            while cursor is not None:
                section = state["node_section"].get(id(cursor))
                if section:
                    break
                cursor = parent_map.get(id(cursor))
            self._add_image(
                image, section, section.get("section_path", []) if section else [], state,
                source, path, role, ordinals, parent_map,
            )

    def _build_chunks(
        self, blocks: Sequence[Dict[str, Any]], source: Dict[str, Any],
        source_file: str, role: str,
    ) -> List[Dict[str, Any]]:
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_length = 0
        current_section = object()
        for block in blocks:
            section_id = block.get("section_id")
            text_length = len(block["text"])
            if current and (section_id != current_section or current_length + text_length + 1 > self.chunk_soft_limit):
                groups.append(current)
                current, current_length = [], 0
            current.append(block)
            current_length += text_length + 1
            current_section = section_id
        if current:
            groups.append(current)

        chunks: List[Dict[str, Any]] = []
        for index, group in enumerate(groups, start=1):
            first = group[0]
            section_path = first.get("section_path") or []
            context = " | ".join(filter(None, [
                source.get("corp_name"), source.get("report_nm"), " > ".join(section_path),
            ]))
            body = "\n".join(block["text"] for block in group)
            chunks.append({
                "chunk_id": f"{source['doc_id']}:{role}:text:{index:05d}",
                "source": source,
                "source_file": source_file,
                "source_file_role": role,
                "section_id": first.get("section_id"),
                "section": section_path[0] if section_path else None,
                "subsection": section_path[1] if len(section_path) > 1 else None,
                "section_path": section_path,
                "content_type": "text",
                "heading": first.get("heading"),
                "text": f"{context}\n{body}" if context else body,
                "body_text": body,
                "paragraph_count": len(group),
                "related_table_ids": [],
                "source_locator": {
                    "element_ordinal_start": first.get("element_ordinal"),
                    "element_ordinal_end": group[-1].get("element_ordinal"),
                    "xml_path": None,
                },
            })
        return chunks

    @staticmethod
    def _document_record(
        source: Dict[str, Any], version: Dict[str, Any], source_files: Sequence[Dict[str, Any]],
        result: Dict[str, Any], manifest_record: Dict[str, Any],
    ) -> Dict[str, Any]:
        main = next((item for item in source_files if item.get("role") == "main"), {})
        return {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "document_name": main.get("document_name"),
            "document_acode": main.get("document_acode"),
            "formula_version": main.get("formula_version"),
            "file_format": manifest_record.get("file_format"),
            "source_files": list(source_files),
            "attachments": [item for item in source_files if item.get("role") != "main"],
            "version": version,
            "parse_summary": {
                "status": result["parse_log"]["status"],
                "warning_count": len(result["parse_log"]["warnings"]),
                "error_count": len(result["parse_log"]["errors"]),
            },
            "record_counts": {
                "sections": len(result["sections"]),
                "text_chunks": len(result["text_chunks"]),
                "logical_tables": len(result["logical_tables"]),
                "table_cells": len(result["table_cells"]),
                "corrections": len(result["corrections"]),
                "images": len(result["images"]),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def write_result(result: Dict[str, Any], output_root: Path, overwrite: bool = False) -> Path:
        source = result["document"]["source"]
        final_dir = output_root / source["corp_code"] / source["rcept_no"]
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{source['rcept_no']}.", dir=str(final_dir.parent)))
        try:
            _json_dump(temp_dir / "document.json", result["document"])
            _jsonl_dump(temp_dir / "sections.jsonl", result["sections"])
            _jsonl_dump(temp_dir / "text_chunks.jsonl", result["text_chunks"])
            _jsonl_dump(temp_dir / "tables.jsonl", result["logical_tables"])
            _jsonl_dump(temp_dir / "cells.jsonl", result["table_cells"])
            _jsonl_dump(temp_dir / "corrections.jsonl", result["corrections"])
            _jsonl_dump(temp_dir / "images.jsonl", result["images"])
            _json_dump(temp_dir / "parse_log.json", result["parse_log"])
            if final_dir.exists():
                if not overwrite:
                    raise FileExistsError(f"Output already exists (use --overwrite): {final_dir}")
                shutil.rmtree(final_dir)
            os.replace(str(temp_dir), str(final_dir))
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return final_dir


def _find_record(records: Sequence[Dict[str, Any]], doc_id: Optional[str], rcept_no: Optional[str]) -> Dict[str, Any]:
    for record in records:
        if doc_id and record.get("doc_id") == doc_id:
            return record
        if rcept_no and record.get("rcept_no") == rcept_no:
            return record
    raise KeyError(f"Document not found: doc_id={doc_id!r}, rcept_no={rcept_no!r}")


def main() -> int:
    argument_parser = argparse.ArgumentParser(description="Parse one DART periodic filing folder")
    argument_parser.add_argument("--data-root", type=Path, required=True, help="Corpus root containing manifest.jsonl and raw/")
    selector = argument_parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--doc-id")
    selector.add_argument("--rcept-no")
    argument_parser.add_argument("--output-root", type=Path, required=True)
    argument_parser.add_argument("--no-attachments", action="store_true")
    argument_parser.add_argument("--overwrite", action="store_true", help="Replace an existing structured output directory")
    arguments = argument_parser.parse_args()

    records = load_manifest(arguments.data_root / "manifest.jsonl")
    record = _find_record(records, arguments.doc_id, arguments.rcept_no)
    parser = PeriodicParser(arguments.data_root, records)
    result = parser.parse_document(
        record, arguments.output_root,
        include_attachments=not arguments.no_attachments,
        overwrite_output=arguments.overwrite,
    )
    print(json.dumps({
        "doc_id": record["doc_id"],
        "status": result["parse_log"]["status"],
        "record_counts": result["document"]["record_counts"],
        "output": str(arguments.output_root / record["corp_code"] / record["rcept_no"]),
    }, ensure_ascii=False, indent=2))
    return 1 if result["parse_log"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
