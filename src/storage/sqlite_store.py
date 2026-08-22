"""Compact SQLite/FTS5 store for parser output.

Repeated source metadata in the interchange JSON is normalized here.  The raw
corpus is never modified and the database can always trace a record back to a
document, receipt number, source file, and locator JSON.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sqlite_numeric(value: Any) -> Any:
    """Avoid sqlite3 adapter overflow while retaining exact original text.

    SQLite INTEGER is signed 64-bit.  REAL-affinity columns can accept a text
    representation and coerce it without Python's integer binder overflowing;
    audited calculations still read ``original_text`` and use Decimal.
    """
    if isinstance(value, int) and not (-(2**63) <= value <= 2**63 - 1):
        return str(value)
    return value


SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS companies (
  corp_code TEXT PRIMARY KEY, stock_code TEXT, corp_name TEXT, listed_name TEXT,
  corp_eng_name TEXT, market TEXT, industry TEXT, sector TEXT, listing_date TEXT,
  fiscal_month TEXT, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY, corp_code TEXT, corp_name TEXT, listed_name TEXT,
  stock_code TEXT, flr_nm TEXT, report_nm TEXT, rcept_no TEXT UNIQUE, rcept_dt TEXT,
  doc_group TEXT, doc_subtype TEXT, base_year INTEGER, base_month INTEGER,
  is_correction INTEGER NOT NULL, file_path TEXT, file_format TEXT,
  parse_status TEXT, warnings_json TEXT, errors_json TEXT, document_json TEXT,
  original_doc_id TEXT, supersedes_doc_id TEXT, superseded_by_doc_id TEXT,
  is_latest_version INTEGER DEFAULT 1,
  FOREIGN KEY(corp_code) REFERENCES companies(corp_code)
);
CREATE INDEX IF NOT EXISTS idx_documents_lookup ON documents(corp_code, doc_group, doc_subtype, base_year, base_month, rcept_dt);
CREATE INDEX IF NOT EXISTS idx_documents_latest ON documents(is_latest_version, is_correction, rcept_dt);
CREATE TABLE IF NOT EXISTS sections (
  section_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, parent_section_id TEXT,
  level INTEGER, title TEXT, section_path TEXT, source_file TEXT,
  source_file_role TEXT, ordinal INTEGER, locator_json TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sections_doc ON sections(doc_id, ordinal);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, section_id TEXT,
  content_type TEXT, heading TEXT, section_path TEXT, text TEXT NOT NULL,
  source_file TEXT, source_file_role TEXT, locator_json TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED, doc_id UNINDEXED, corp_name, report_nm, section_path, text,
  tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS logical_tables (
  table_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, section_id TEXT,
  table_title TEXT, section_path TEXT, unit_json TEXT, scope TEXT,
  statement_type TEXT, periods_json TEXT, columns_json TEXT, rows_json TEXT,
  footnotes_json TEXT, search_text TEXT, source_file TEXT, source_file_role TEXT,
  locator_json TEXT, table_json TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tables_doc ON logical_tables(doc_id);
CREATE INDEX IF NOT EXISTS idx_tables_type ON logical_tables(statement_type, scope);
CREATE VIRTUAL TABLE IF NOT EXISTS tables_fts USING fts5(
  table_id UNINDEXED, doc_id UNINDEXED, corp_name, report_nm, table_title, section_path, search_text,
  tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS cells (
  cell_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, doc_id TEXT NOT NULL,
  row_index INTEGER, column_index INTEGER, row_label TEXT, row_path TEXT,
  column_label TEXT, column_path TEXT, original_text TEXT, value_type TEXT,
  numeric_value REAL, unit_raw TEXT, unit_currency TEXT, unit_scale REAL,
  period_label TEXT, period_start TEXT, period_end TEXT, period_role TEXT,
  period_aggregation TEXT, scope TEXT, is_missing INTEGER, locator_json TEXT,
  FOREIGN KEY(table_id) REFERENCES logical_tables(table_id) ON DELETE CASCADE,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cells_metric ON cells(row_label, scope, period_end);
CREATE INDEX IF NOT EXISTS idx_cells_doc ON cells(doc_id, table_id);
CREATE INDEX IF NOT EXISTS idx_cells_table ON cells(table_id);
CREATE TABLE IF NOT EXISTS corrections (
  correction_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, original_doc_id TEXT,
  supersedes_doc_id TEXT, superseded_by_doc_id TEXT, is_latest_version INTEGER,
  correction_date TEXT, original_filing_date TEXT, target_document TEXT,
  correction_json TEXT NOT NULL,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_corrections_doc ON corrections(doc_id);
CREATE TABLE IF NOT EXISTS correction_items (
  item_id TEXT PRIMARY KEY, correction_id TEXT NOT NULL, item TEXT, reason TEXT,
  before_text TEXT, after_text TEXT, effective_text TEXT, locator_json TEXT,
  FOREIGN KEY(correction_id) REFERENCES corrections(correction_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_correction_items_parent ON correction_items(correction_id);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, event_type TEXT,
  event_title TEXT, scope TEXT, effective_status TEXT, search_text TEXT,
  event_json TEXT, FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_doc ON events(doc_id);
CREATE TABLE IF NOT EXISTS event_fields (
  event_id TEXT NOT NULL, ordinal INTEGER NOT NULL, field_key TEXT, label TEXT,
  original_text TEXT, numeric_value REAL, value_type TEXT, unit_json TEXT,
  locator_json TEXT, PRIMARY KEY(event_id, ordinal),
  FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_event_fields_key ON event_fields(field_key, numeric_value);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
  event_id UNINDEXED, doc_id UNINDEXED, corp_name, report_nm, event_type, search_text,
  tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS parse_runs (
  run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT,
  requested_groups TEXT, attempted INTEGER DEFAULT 0, success INTEGER DEFAULT 0,
  warning INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, details_json TEXT
);
"""


class DisclosureStore:
    def __init__(self, path: Path, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-65536")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        if not read_only:
            # Large filings insert tens of thousands of cells per transaction.
            # A wider WAL interval avoids checkpoint thrashing while preserving
            # document-level atomicity; the build performs an explicit checkpoint.
            self.conn.execute("PRAGMA wal_autocheckpoint=100000")

    def initialize(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(documents)")}
        if "flr_nm" not in columns:
            self.conn.execute("ALTER TABLE documents ADD COLUMN flr_nm TEXT")
        self.conn.commit()

    def load_companies(self, universe_csv: Path) -> int:
        count = 0
        with Path(universe_csv).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                self.conn.execute(
                    "INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (row.get("corp_code"), row.get("stock_code"), row.get("corp_name"), row.get("listed_name"),
                     row.get("corp_eng_name"), row.get("market"), row.get("industry"), row.get("sector"),
                     row.get("listing_date"), row.get("fiscal_month"), _json(row)),
                )
                count += 1
        self.conn.commit()
        return count

    def sync_manifest_metadata(self, records: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """UPDATE documents SET stock_code=?,flr_nm=?,
                   document_json=json_set(coalesce(document_json,'{}'),'$.source.stock_code',?,'$.source.flr_nm',?,
                                          '$.source.industry',?,'$.source.sector',?)
               WHERE doc_id=?""",
            ((record.get("stock_code"), record.get("flr_nm"), record.get("stock_code"), record.get("flr_nm"),
              record.get("industry"), record.get("sector"), record.get("doc_id")) for record in records),
        )
        self.conn.commit()

    def has_document(self, doc_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM documents WHERE doc_id=? AND parse_status IN ('success','warning')", (doc_id,)
        ).fetchone() is not None

    def upsert_result(self, record: Dict[str, Any], result: Dict[str, Any], refresh_fts: bool = True) -> None:
        doc_id = record["doc_id"]
        status = result["parse_log"]["status"]
        document = result.get("document", {})
        version = document.get("version", {})
        with self.conn:
            # Reprocessing one document is idempotent and does not touch any raw file.
            self._delete_document_children(doc_id, refresh_fts=refresh_fts)
            self.conn.execute(
                """INSERT OR REPLACE INTO documents
                (doc_id,corp_code,corp_name,listed_name,stock_code,flr_nm,report_nm,rcept_no,rcept_dt,
                 doc_group,doc_subtype,base_year,base_month,is_correction,file_path,file_format,
                 parse_status,warnings_json,errors_json,document_json,original_doc_id,supersedes_doc_id,
                 superseded_by_doc_id,is_latest_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc_id, record.get("corp_code"), record.get("corp_name"), record.get("listed_name"),
                 record.get("stock_code"), record.get("flr_nm"), record.get("report_nm"), record.get("rcept_no"), record.get("rcept_dt"),
                 record.get("doc_group"), record.get("doc_subtype"), record.get("base_year"), record.get("base_month"),
                 int(bool(record.get("is_correction"))), record.get("file_path"), document.get("file_format", record.get("file_format")),
                 status, _json(result["parse_log"].get("warnings", [])), _json(result["parse_log"].get("errors", [])),
                 _json(document), version.get("original_doc_id"), version.get("supersedes_doc_id"),
                 version.get("superseded_by_doc_id"), int(version.get("is_latest_version", True))),
            )
            section_rows = [
                (section["section_id"], doc_id, section.get("parent_section_id"), section.get("level"),
                 section.get("title"), _json(section.get("section_path", [])), section.get("source_file"),
                 section.get("source_file_role"), section.get("element_ordinal"), _json(section.get("source_locator", {})))
                for section in result.get("sections", [])
            ]
            self.conn.executemany("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?)", section_rows)
            chunk_rows, chunk_fts_rows = [], []
            for chunk in result.get("text_chunks", []):
                section_path = " > ".join(chunk.get("section_path", []))
                chunk_rows.append((chunk["chunk_id"], doc_id, chunk.get("section_id"), chunk.get("content_type"),
                                   chunk.get("heading"), section_path, chunk.get("text", ""), chunk.get("source_file"),
                                   chunk.get("source_file_role"), _json(chunk.get("source_locator", {}))))
                if refresh_fts:
                    chunk_fts_rows.append((chunk["chunk_id"], doc_id, record.get("corp_name"), record.get("report_nm"), section_path, chunk.get("text", "")))
            self.conn.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)", chunk_rows)
            if refresh_fts:
                self.conn.executemany("INSERT INTO chunks_fts VALUES (?,?,?,?,?,?)", chunk_fts_rows)
            table_rows, table_fts_rows = [], []
            for table in result.get("logical_tables", []):
                section_path = " > ".join(table.get("section_path", []))
                table_rows.append((table["table_id"], doc_id, table.get("section_id"), table.get("table_title"), section_path,
                                   _json(table.get("unit", {})), table.get("scope"), table.get("statement_type"),
                                   _json(table.get("periods", [])), _json(table.get("columns", [])), _json(table.get("rows", [])),
                                   _json(table.get("footnotes", [])), table.get("search_text", ""), table.get("source_file"),
                                   table.get("source_file_role"), _json(table.get("source_locator", {})), _json(_compact_table(table))))
                if refresh_fts:
                    table_fts_rows.append((table["table_id"], doc_id, record.get("corp_name"), record.get("report_nm"),
                                           table.get("table_title"), section_path, table.get("search_text", "")))
            self.conn.executemany("INSERT INTO logical_tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", table_rows)
            if refresh_fts:
                self.conn.executemany("INSERT INTO tables_fts VALUES (?,?,?,?,?,?,?)", table_fts_rows)
            cell_rows = []
            for cell in result.get("table_cells", []):
                unit, period = cell.get("unit") or {}, cell.get("period") or {}
                cell_rows.append((cell["cell_id"], cell["table_id"], doc_id, cell.get("row_index"), cell.get("column_index"),
                                  cell.get("row_label"), _json(cell.get("row_path", [])), cell.get("column_label"),
                                  _json(cell.get("column_path", [])), cell.get("original_text"), cell.get("value_type"),
                                  _sqlite_numeric(cell.get("numeric_value")), unit.get("raw"), unit.get("currency"), unit.get("scale"),
                                  period.get("label"), period.get("start_date"), period.get("end_date"),
                                  period.get("comparison_role"), period.get("aggregation"), cell.get("scope"),
                                  int(bool(cell.get("is_missing"))), _json({"physical_table_ordinal": cell.get("physical_table_ordinal"),
                                                                           "rowspan": cell.get("rowspan"), "colspan": cell.get("colspan")})))
            self.conn.executemany("INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cell_rows)
            for correction in result.get("corrections", []):
                self.conn.execute(
                    "INSERT INTO corrections VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (correction["correction_id"], doc_id, correction.get("original_doc_id"),
                     correction.get("supersedes_doc_id"), correction.get("superseded_by_doc_id"),
                     int(bool(correction.get("is_latest_version"))), correction.get("correction_date"),
                     correction.get("original_filing_date"), correction.get("target_document"), _json(correction)),
                )
                for item in correction.get("correction_items", []):
                    self.conn.execute(
                        "INSERT INTO correction_items VALUES (?,?,?,?,?,?,?,?)",
                        (item["item_id"], correction["correction_id"], item.get("item"), item.get("reason"),
                         (item.get("before") or {}).get("original_text"), (item.get("after") or {}).get("original_text"),
                         (item.get("current_effective_value") or {}).get("original_text"), _json(item.get("source_locator", {}))),
                    )
            event_field_rows = []
            for event in result.get("events", []):
                self.conn.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                    (event["event_id"], doc_id, event.get("event_type"), event.get("event_title"),
                     event.get("scope"), event.get("effective_status"), event.get("search_text", ""), _json(_compact_event(event))),
                )
                if refresh_fts:
                    self.conn.execute("INSERT INTO events_fts VALUES (?,?,?,?,?,?)",
                                      (event["event_id"], doc_id, record.get("corp_name"), record.get("report_nm"),
                                       event.get("event_type"), event.get("search_text", "")))
                for ordinal, field in enumerate(event.get("fields", []), start=1):
                    event_field_rows.append((event["event_id"], ordinal, field.get("field_key"), field.get("label"),
                                             field.get("original_text"), _sqlite_numeric(field.get("numeric_value")), field.get("value_type"),
                                             _json(field.get("unit")), _json(field.get("source_locator", {}))))
            self.conn.executemany("INSERT INTO event_fields VALUES (?,?,?,?,?,?,?,?,?)", event_field_rows)

    def _delete_document_children(self, doc_id: str, refresh_fts: bool = True) -> None:
        if refresh_fts:
            for fts in ("chunks_fts", "tables_fts", "events_fts"):
                self.conn.execute(f"DELETE FROM {fts} WHERE doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))

    def finalize_version_links(self) -> int:
        """Apply conservative links; ambiguous non-periodic chains remain unlinked."""
        updated = 0
        periodic = self.conn.execute(
            "SELECT * FROM documents WHERE doc_group='periodic' ORDER BY corp_code,doc_subtype,base_year,base_month,rcept_dt,rcept_no"
        ).fetchall()
        groups: Dict[tuple, list] = {}
        for row in periodic:
            groups.setdefault((row["corp_code"], row["doc_subtype"], row["base_year"], row["base_month"]), []).append(row)
        with self.conn:
            for versions in groups.values():
                original = next((row["doc_id"] for row in versions if not row["is_correction"]), None)
                for index, row in enumerate(versions):
                    is_latest = int(index == len(versions)-1)
                    self.conn.execute(
                        "UPDATE documents SET original_doc_id=?,supersedes_doc_id=?,superseded_by_doc_id=?,is_latest_version=? WHERE doc_id=?",
                        (original, versions[index-1]["doc_id"] if index else None,
                         versions[index+1]["doc_id"] if index+1 < len(versions) else None,
                         is_latest, row["doc_id"]),
                    ); updated += 1
                    self.conn.execute(
                        "UPDATE corrections SET original_doc_id=?,supersedes_doc_id=?,superseded_by_doc_id=?,is_latest_version=? WHERE doc_id=?",
                        (original, versions[index-1]["doc_id"] if index else None,
                         versions[index+1]["doc_id"] if index+1 < len(versions) else None,
                         is_latest, row["doc_id"]),
                    )
                    self.conn.execute(
                        """UPDATE correction_items SET effective_text=CASE WHEN ? THEN after_text ELSE NULL END
                           WHERE correction_id IN (SELECT correction_id FROM corrections WHERE doc_id=?)""",
                        (is_latest, row["doc_id"]),
                    )
            correction_rows = self.conn.execute(
                """SELECT c.correction_id,c.doc_id,c.original_filing_date,d.corp_code,d.doc_group,d.report_nm,d.rcept_dt
                   FROM corrections c JOIN documents d ON d.doc_id=c.doc_id
                   WHERE c.original_filing_date IS NOT NULL AND d.doc_group!='periodic'"""
            ).fetchall()
            chains: Dict[str, list] = {}
            for correction in correction_rows:
                target_date = correction["original_filing_date"].replace("-", "")
                candidates = self.conn.execute(
                    """SELECT doc_id,report_nm,rcept_dt FROM documents
                       WHERE corp_code=? AND doc_group=? AND is_correction=0 AND rcept_dt=?""",
                    (correction["corp_code"], correction["doc_group"], target_date),
                ).fetchall()
                wanted = _normalize_report_name(correction["report_nm"])
                matches = [row for row in candidates if _normalize_report_name(row["report_nm"]) == wanted]
                if len(matches) != 1:
                    continue
                original_id = matches[0]["doc_id"]
                chains.setdefault(original_id, []).append(correction)
            for original_id, corrections in chains.items():
                corrections.sort(key=lambda row: (row["rcept_dt"], row["doc_id"]))
                version_ids = [original_id] + [row["doc_id"] for row in corrections]
                for index, doc_id in enumerate(version_ids):
                    previous_id = version_ids[index - 1] if index else None
                    next_id = version_ids[index + 1] if index + 1 < len(version_ids) else None
                    self.conn.execute(
                        "UPDATE documents SET original_doc_id=?,supersedes_doc_id=?,superseded_by_doc_id=?,is_latest_version=? WHERE doc_id=?",
                        (original_id, previous_id, next_id, int(index == len(version_ids) - 1), doc_id),
                    ); updated += 1
                for row in corrections:
                    index = version_ids.index(row["doc_id"])
                    is_latest = int(index == len(version_ids) - 1)
                    self.conn.execute(
                        "UPDATE corrections SET original_doc_id=?,supersedes_doc_id=?,superseded_by_doc_id=?,is_latest_version=? WHERE correction_id=?",
                        (original_id, version_ids[index - 1], version_ids[index + 1] if index + 1 < len(version_ids) else None,
                         is_latest, row["correction_id"]),
                    )
                    self.conn.execute(
                        "UPDATE correction_items SET effective_text=CASE WHEN ? THEN after_text ELSE NULL END WHERE correction_id=?",
                        (is_latest, row["correction_id"]),
                    )
            # Older parser runs could classify a title containing both scopes as
            # consolidated merely because that token appeared first.  Conflicting
            # evidence must remain unknown; keep the already-built DB consistent
            # with the current parser without touching source files.
            conflict = "((table_title LIKE '%연결%' AND (table_title LIKE '%별도%' OR table_title LIKE '%개별%')) OR " \
                       "((table_title IS NULL OR (table_title NOT LIKE '%연결%' AND table_title NOT LIKE '%별도%' AND table_title NOT LIKE '%개별%')) " \
                       "AND section_path LIKE '%연결%' AND (section_path LIKE '%별도%' OR section_path LIKE '%개별%')))"
            self.conn.execute(
                f"""UPDATE logical_tables SET scope='unknown',
                       table_json=json_set(table_json,'$.scope','unknown','$.scope_evidence','storage_migration:conflicting_scope')
                    WHERE {conflict}"""
            )
            self.conn.execute(
                f"UPDATE cells SET scope='unknown' WHERE table_id IN (SELECT table_id FROM logical_tables WHERE {conflict})"
            )
            def unit_cases(raw: str, current_currency: str, current_scale: str) -> tuple[str, str, str]:
                foreign = (f"(upper({raw}) LIKE '%USD%' OR {raw} LIKE '%미화%' OR {raw} LIKE '%달러%' OR "
                           f"upper({raw}) LIKE '%EUR%' OR {raw} LIKE '%유로%' OR upper({raw}) LIKE '%JPY%' OR "
                           f"upper({raw}) LIKE '%CNY%' OR {raw} LIKE '%위안%')")
                currency = (f"CASE WHEN {raw} LIKE '%조원%' OR {raw} LIKE '%십억원%' THEN 'KRW' "
                            f"WHEN upper({raw}) LIKE '%USD%' OR {raw} LIKE '%미화%' OR {raw} LIKE '%달러%' THEN 'USD' "
                            f"WHEN upper({raw}) LIKE '%EUR%' OR {raw} LIKE '%유로%' THEN 'EUR' "
                            f"WHEN upper({raw}) LIKE '%JPY%' THEN 'JPY' "
                            f"WHEN upper({raw}) LIKE '%CNY%' OR {raw} LIKE '%위안%' THEN 'CNY' ELSE {current_currency} END")
                scale = (f"CASE WHEN {raw} LIKE '%조원%' THEN 1000000000000 WHEN {raw} LIKE '%십억원%' THEN 1000000000 "
                         f"WHEN {foreign} AND {raw} LIKE '%백만%' THEN 1000000 WHEN {foreign} AND {raw} LIKE '%천%' THEN 1000 "
                         f"WHEN {foreign} THEN 1 ELSE {current_scale} END")
                where = f"({raw} LIKE '%조원%' OR {raw} LIKE '%십억원%' OR {foreign})"
                return currency, scale, where

            table_raw = "coalesce(json_extract(unit_json,'$.raw'),'')"
            table_currency, table_scale, table_where = unit_cases(
                table_raw, "json_extract(unit_json,'$.currency')", "json_extract(unit_json,'$.scale')"
            )
            self.conn.execute(
                f"""UPDATE logical_tables
                    SET unit_json=json_set(unit_json,'$.currency',{table_currency},'$.scale',{table_scale}),
                        table_json=json_set(table_json,'$.unit.currency',{table_currency},'$.unit.scale',{table_scale})
                    WHERE {table_where} AND (json_extract(unit_json,'$.currency') IS NOT ({table_currency})
                                             OR json_extract(unit_json,'$.scale') IS NOT ({table_scale}))"""
            )
            cell_raw = "coalesce(unit_raw,'')"
            cell_currency, cell_scale, cell_where = unit_cases(cell_raw, "unit_currency", "unit_scale")
            self.conn.execute(
                f"""UPDATE cells SET unit_currency={cell_currency},unit_scale={cell_scale}
                    WHERE {cell_where} AND (unit_currency IS NOT ({cell_currency}) OR unit_scale IS NOT ({cell_scale}))"""
            )
        return updated

    def counts(self) -> Dict[str, int]:
        names = ["companies", "documents", "sections", "chunks", "logical_tables", "cells", "corrections", "correction_items", "events", "event_fields"]
        return {name: self.conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in names}

    def close(self) -> None:
        if not self.read_only and not self.conn.in_transaction:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.OperationalError:
                pass
        self.conn.close()


def _compact_table(table: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in table.items() if key not in {"source", "search_text", "rows", "columns", "periods", "footnotes"}}


def _compact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in event.items() if key not in {"source", "search_text", "fields"}}


def _normalize_report_name(value: Optional[str]) -> str:
    text = re.sub(r"^\[(?:기재정정|첨부추가)\]", "", value or "")
    return re.sub(r"[\sㆍ·]", "", text)
