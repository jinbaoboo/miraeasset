"""One entry point for all four disclosure groups."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Sequence

from .event_normalizer import mask_sensitive_text, normalize_xml_event
from .exchange_parser import ExchangeParser
from .periodic_parser import PeriodicParser


class DisclosureParser:
    def __init__(self, data_root: Path, manifest_records: Sequence[Dict[str, Any]], chunk_soft_limit: int = 1800):
        self.xml_parser = PeriodicParser(data_root, manifest_records, chunk_soft_limit=chunk_soft_limit)
        self.exchange_parser = ExchangeParser(data_root)

    def parse_document(self, record: Dict[str, Any], include_attachments: bool = True) -> Dict[str, Any]:
        if record.get("doc_group") == "exchange":
            return self.exchange_parser.parse_document(record)
        result = self.xml_parser.parse_document(record, include_attachments=include_attachments)
        result["events"] = normalize_xml_event(result, record)
        result["document"].setdefault("record_counts", {})["events"] = len(result["events"])
        if record.get("doc_group") == "holding":
            # Public originals remain untouched.  Only derived full-text search content is minimized.
            for chunk in result.get("text_chunks", []):
                chunk["text"] = mask_sensitive_text(chunk.get("text", ""))
                chunk["body_text"] = mask_sensitive_text(chunk.get("body_text", ""))
            for table in result.get("logical_tables", []):
                table["search_text"] = mask_sensitive_text(table.get("search_text", ""))
        return result
