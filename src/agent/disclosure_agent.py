"""Grounded disclosure QA orchestration."""

from __future__ import annotations

import sqlite3
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.retrieval.hybrid_search import HybridRetriever
from src.retrieval.query_analyzer import QueryAnalyzer
from src.tools.calculator import calculate

from .hyperclova_client import HyperClovaClient


OUT_OF_SCOPE = ("주가 전망", "목표주가", "매수", "매도", "추천", "뉴스", "실시간 주가",
                "코퍼스에 없는", "비상장회사")
SECURITY_TERMS = ("API 키", "API키", "비밀키", "시스템 프롬프트", "프롬프트를 공개", "이전 지시를 무시",
                  "지시를 무시하고", "환경변수를 출력")


class DisclosureAgent:
    def __init__(self, db_path: Path, hcx_client: Optional[HyperClovaClient] = None):
        self.db_path = Path(db_path)
        self.retriever = HybridRetriever(self.db_path)
        analyzer_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        analyzer_conn.row_factory = sqlite3.Row
        self._analyzer_conn = analyzer_conn
        self.analyzer = QueryAnalyzer(analyzer_conn)
        self.hcx = hcx_client or HyperClovaClient()

    def answer(self, question_id: str, question: str, use_llm: bool = True) -> Dict[str, Any]:
        if not question.strip():
            return self._response(question_id, question, [], ["empty_question"], "질문이 비어 있어 답변할 수 없습니다.")
        if any(term.lower() in question.lower() for term in SECURITY_TERMS):
            return self._response(question_id, question, [], ["prompt_injection_or_secret_request_blocked"],
                                  "보안상 시스템 지시·자격증명·환경변수는 공개하거나 변경할 수 없습니다. 공시 내용을 질문해 주세요.")
        if any(term in question for term in OUT_OF_SCOPE):
            return self._response(question_id, question, [], ["out_of_corpus_scope"],
                                  "제공된 공시 코퍼스만으로는 주가·뉴스·투자 추천을 확인할 수 없습니다.")
        plan = self.analyzer.analyze(question)
        trace: List[str] = ["query_analyzed", "metadata_filters_applied"]
        if plan.get("metric") and not plan.get("companies") and not plan.get("cross_corpus"):
            trace.append("company_not_identified")
            return self._response(
                question_id, question, [], trace,
                "제공 코퍼스의 기업을 식별할 수 없습니다. 회사명 또는 종목코드를 정확히 입력해 주세요.", plan,
            )
        contexts: List[Dict[str, Any]] = []
        financial_metrics = {"revenue", "operating_profit", "net_income", "assets", "liabilities", "equity", "rnd", "capex"}
        cell_limit = 200 if plan.get("cross_corpus") else 60 if len(plan.get("companies") or []) > 1 else 12
        cells = self.retriever.find_metric_cells(plan, limit=cell_limit) if plan.get("metric") in financial_metrics else []
        cells = self._prioritize_cells(cells, plan)
        if cells:
            trace.append("structured_cells_retrieved")
            contexts.extend(self._cell_context(cell) for cell in cells[:6])
        event_limit = 1000 if plan.get("intent") == "calculation" else 8
        event_fields = self.retriever.find_event_fields(plan, limit=event_limit) if plan.get("metric") else []
        if event_fields:
            trace.append("structured_event_fields_retrieved")
            context_fields = event_fields if plan.get("intent") == "calculation" else event_fields[:8]
            contexts.extend(self._event_field_context(field) for field in context_fields)
        if "정정" in question and plan.get("companies"):
            history = self.retriever.correction_history(plan["companies"][0]["corp_code"], limit=50,
                                                        doc_groups=plan.get("doc_groups"))
            if history:
                trace.append("correction_history_retrieved")
                contexts.extend(self._correction_context(item) for item in history)
        retrieved = self.retriever.search(question, plan, limit=max(4, 8-len(contexts)))
        contexts.extend({"kind": item["kind"], "record_id": item["record_id"],
                         "content": item.get("content", "")[:6000], "citation": item.get("citation", {})} for item in retrieved)
        if retrieved: trace.append("fts_evidence_retrieved")
        context_limit = 1000 if plan.get("intent") == "calculation" and event_fields else 50 if "정정" in question else 10
        contexts = self._deduplicate(contexts)[:context_limit]
        if not contexts:
            trace.append("insufficient_evidence")
            answer = "제공된 공시 코퍼스에서 질문을 뒷받침할 근거를 찾지 못했습니다. 회사명·기간·공시 유형을 더 구체적으로 입력해 주세요."
            return self._response(question_id, question, contexts, trace, answer, plan)
        answer = None
        deterministic_numeric = plan.get("intent") in {"comparison", "calculation"} and bool(cells or event_fields)
        if deterministic_numeric:
            trace.append("deterministic_numeric_tool_preferred")
        if use_llm and self.hcx.configured and not deterministic_numeric:
            try:
                generation_contexts = contexts[:20]
                generated = self.hcx.generate(question, generation_contexts)
                if generated and self._valid_generated_citations(generated, len(generation_contexts)):
                    answer = generated
                    trace.append("hyperclova_x_grounded_generation")
                else:
                    trace.append("hyperclova_x_citation_validation_failed")
            except RuntimeError:
                trace.append("hyperclova_x_request_failed_fallback")
        if not answer:
            answer = self._template_answer(question, plan, cells, event_fields, contexts)
            trace.append("deterministic_grounded_template")
        trace.append("citations_attached")
        return self._response(question_id, question, contexts, trace, answer, plan)

    @staticmethod
    def _cell_context(cell: Dict[str, Any]) -> Dict[str, Any]:
        unit = cell.get("unit_raw") or "단위 미상"
        column = " > ".join(__import__("json").loads(cell.get("column_path") or "[]"))
        content = (f"{cell['corp_name']} | {cell['report_nm']} | {cell.get('table_title') or ''}\n"
                   f"{cell.get('row_label')} | {column} | {cell.get('original_text')} | {unit}")
        return {"kind": "cell", "record_id": cell["cell_id"], "content": content, "citation": cell["citation"]}

    @staticmethod
    def _event_field_context(field: Dict[str, Any]) -> Dict[str, Any]:
        content = f"{field['corp_name']} | {field['report_nm']} | {field.get('label')} | {field.get('original_text')}"
        return {"kind": "event_field", "record_id": f"{field['event_id']}:{field['ordinal']}",
                "content": content, "citation": field["citation"]}

    @staticmethod
    def _correction_context(item: Dict[str, Any]) -> Dict[str, Any]:
        effective = item.get("effective_text") or ("후속 정정으로 대체" if item.get("correction_is_latest") is False or item.get("correction_is_latest") == 0 else "")
        content = (f"{item.get('corp_name')} | {item.get('report_nm')} | {item.get('item') or ''} | "
                   f"정정 전: {item.get('before_text') or ''} | 정정 후: {item.get('after_text') or ''} | "
                   f"현재 유효 값: {effective}")
        citation = {key: item.get(key) for key in ("doc_id", "corp_name", "report_nm", "rcept_no", "rcept_dt", "correction_id")}
        if item.get("locator_json"):
            try:
                citation["source_locator"] = json.loads(item["locator_json"])
            except json.JSONDecodeError:
                citation["source_locator"] = {"raw": item["locator_json"]}
        return {"kind": "correction", "record_id": item.get("correction_id"), "content": content, "citation": citation}

    @staticmethod
    def _deduplicate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set(); result = []
        for item in items:
            key = (item.get("kind"), item.get("record_id"))
            if key not in seen:
                seen.add(key); result.append(item)
        return result

    @staticmethod
    def _prioritize_cells(cells: List[Dict[str, Any]], plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Put one best record per requested company first, then retain recall."""
        companies = plan.get("companies") or []
        if plan.get("cross_corpus"):
            first = []
            seen_companies = set()
            for cell in cells:
                corp_code = cell.get("corp_code")
                if corp_code not in seen_companies:
                    seen_companies.add(corp_code); first.append(cell)
            first.sort(
                key=lambda item: Decimal(DisclosureAgent._exact_numeric(item)) * Decimal(str(item.get("unit_scale") or 0)),
                reverse=True,
            )
            chosen_ids = {cell.get("cell_id") for cell in first}
            return first + [cell for cell in cells if cell.get("cell_id") not in chosen_ids]
        if len(companies) < 2:
            return cells
        first: List[Dict[str, Any]] = []
        chosen_ids = set()
        for company in companies:
            candidate = next((cell for cell in cells if cell.get("corp_code") == company.get("corp_code")), None)
            if candidate:
                first.append(candidate); chosen_ids.add(candidate.get("cell_id"))
        return first + [cell for cell in cells if cell.get("cell_id") not in chosen_ids]

    @staticmethod
    def _template_answer(question: str, plan: Dict[str, Any], cells: List[Dict[str, Any]],
                         event_fields: List[Dict[str, Any]], contexts: List[Dict[str, Any]]) -> str:
        if plan.get("cross_corpus") and cells:
            best_by_company: Dict[str, Dict[str, Any]] = {}
            for cell in cells:
                if cell.get("unit_currency") and cell.get("unit_scale") is not None:
                    best_by_company.setdefault(cell.get("corp_code") or cell.get("corp_name"), cell)
            comparable = list(best_by_company.values())
            currencies = {cell.get("unit_currency") for cell in comparable}
            if comparable and len(currencies) == 1:
                ranked = sorted(
                    comparable,
                    key=lambda item: Decimal(DisclosureAgent._exact_numeric(item)) * Decimal(str(item["unit_scale"])),
                    reverse=True,
                )
                top = ranked[:min(3, len(ranked))]
                details = ", ".join(
                    f"{item['corp_name']} {item.get('original_text')} ({item.get('unit_raw') or '단위 미상'})" for item in top
                )
                receipts = ", ".join(item.get("rcept_no") or "" for item in top)
                return f"검색 조건에서 규모 상위는 {details} 순입니다. 근거 접수번호: {receipts}."
            return "검색된 기업 간 통화·단위 scale이 호환되지 않아 전체 순위를 안전하게 계산할 수 없습니다."
        if plan.get("intent") == "comparison" and len(plan.get("companies") or []) >= 2 and cells:
            selected = []
            for company in plan["companies"]:
                candidate = next((cell for cell in cells if cell.get("corp_code") == company.get("corp_code")), None)
                if candidate:
                    selected.append(candidate)
            if len(selected) >= 2:
                currencies = {item.get("unit_currency") for item in selected}
                scales_known = all(item.get("unit_scale") is not None for item in selected)
                if len(currencies) == 1 and None not in currencies and scales_known:
                    normalized = [Decimal(DisclosureAgent._exact_numeric(item)) * Decimal(str(item.get("unit_scale") or 1))
                                  for item in selected]
                    winner_index = max(range(len(selected)), key=lambda index: normalized[index])
                    details = ", ".join(
                        f"{item['corp_name']} {item.get('original_text')} ({item.get('unit_raw') or '단위 미상'})"
                        for item in selected
                    )
                    receipts = ", ".join(item.get("rcept_no") or "" for item in selected)
                    return (f"{details}이며, 동일 통화·scale로 환산하면 "
                            f"{selected[winner_index]['corp_name']}의 규모가 더 큽니다. "
                            f"근거 접수번호: {receipts}.")
                details = ", ".join(
                    f"{item['corp_name']} {item.get('original_text')} ({item.get('unit_raw') or '단위 미상'})"
                    for item in selected
                )
                return (f"검색된 값은 {details}입니다. 통화 또는 단위 scale을 일치시키 거나 확인할 수 없어 "
                        "어느 기업이 더 큰지 자동 판단하지 않습니다.")
        if plan.get("intent") == "calculation" and event_fields:
            selected_events = DisclosureAgent._distinct_event_fields(event_fields)
            numeric_events = [field for field in selected_events if field.get("numeric_value") is not None]
            if numeric_events and all(field.get("field_key", "").endswith("_krw") for field in numeric_events):
                total = calculate("sum", [DisclosureAgent._exact_numeric(field) for field in numeric_events])
                receipts = ", ".join(field["rcept_no"] for field in numeric_events)
                return (f"조건에 맞는 최신 유효 공시 {len(numeric_events)}건의 금액 합계는 "
                        f"{DecimalFormatter.comma(total['result'])}원입니다. 계산식: {total['formula']}. "
                        f"근거 접수번호: {receipts}.")
        if plan.get("intent") in {"comparison", "calculation"} and len(cells) >= 2:
            selected = DisclosureAgent._distinct_cells(cells)
            if len(selected) >= 2:
                current, prior = selected[0], selected[1]
                try:
                    if current.get("corp_code") != prior.get("corp_code"):
                        raise ValueError("cross-company comparison requires explicit compatible units")
                    if (current.get("unit_currency"), current.get("unit_scale"), current.get("scope")) != \
                       (prior.get("unit_currency"), prior.get("unit_scale"), prior.get("scope")):
                        raise ValueError("incompatible unit or scope")
                    if current.get("unit_scale") is None:
                        raise ValueError("unknown unit scale")
                    difference = calculate("difference", [DisclosureAgent._exact_numeric(current), DisclosureAgent._exact_numeric(prior)])
                    growth = calculate("growth_rate", [DisclosureAgent._exact_numeric(current), DisclosureAgent._exact_numeric(prior)])
                    unit = current.get("unit_raw") or "단위 미상"
                    return (f"{current['corp_name']}의 {current.get('row_label')}은(는) "
                            f"{prior.get('base_year')}년 {prior.get('base_month')}월 {prior.get('original_text')}에서 "
                            f"{current.get('base_year')}년 {current.get('base_month')}월 {current.get('original_text')}로 "
                            f"변했습니다. 차이는 {difference['result']}이고 증감률은 {growth['result_float']:.2f}%입니다 "
                            f"({unit}). 계산식: {growth['formula']}. 근거 접수번호: {prior.get('rcept_no')}, {current.get('rcept_no')}.")
                except ValueError:
                    pass
        if cells:
            cell = cells[0]
            scope = {"consolidated": "연결", "separate": "별도", "unknown": "기준 미상"}.get(cell.get("scope"), cell.get("scope"))
            unit = cell.get("unit_raw") or "단위가 명시되지 않음"
            return (f"{cell['corp_name']}의 {cell.get('base_year')}년 {cell.get('base_month')}월 기준 "
                    f"{scope} {cell.get('row_label')}은(는) {cell.get('original_text')} ({unit})입니다. "
                    f"근거: {cell.get('report_nm')}, 접수번호 {cell.get('rcept_no')}, "
                    f"{cell.get('table_title') or '표 제목 미상'}의 해당 행·열.")
        if event_fields:
            field = event_fields[0]
            unit = "원" if field.get("field_key", "").endswith("_krw") else ""
            return (f"{field['corp_name']}의 {field.get('label')}은(는) {field.get('original_text')} {unit}입니다. "
                    f"근거: {field.get('report_nm')}, 접수번호 {field.get('rcept_no')}의 정형 공시 항목.")
        first = contexts[0]
        citation = first.get("citation", {})
        excerpt = " ".join(first.get("content", "").split())[:500]
        return (f"가장 관련성이 높은 공시 근거는 다음과 같습니다: {excerpt} "
                f"(출처: {citation.get('corp_name','')}, {citation.get('report_nm','')}, "
                f"접수번호 {citation.get('rcept_no','')}). 추가 수치 판단은 이 근거 범위 안에서만 가능합니다.")

    @staticmethod
    def _distinct_cells(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        best: Dict[tuple, Dict[str, Any]] = {}
        for cell in cells:
            key = (cell.get("doc_id"), cell.get("row_label"), cell.get("scope"))
            if key not in best or cell.get("selection_score", 0) > best[key].get("selection_score", 0):
                best[key] = cell
        return sorted(best.values(), key=lambda item: (item.get("base_year") or 0, item.get("base_month") or 0,
                                                        item.get("rcept_dt") or ""), reverse=True)

    @staticmethod
    def _distinct_event_fields(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = {}
        for field in fields:
            result.setdefault((field.get("event_id"), field.get("field_key")), field)
        return list(result.values())

    @staticmethod
    def _exact_numeric(item: Dict[str, Any]) -> str:
        value = str(item.get("original_text") or "").strip().replace(",", "")
        negative = value.startswith(("△", "▲")) or (value.startswith("(") and value.endswith(")"))
        value = value.lstrip("△▲").strip().strip("()").rstrip("%").strip()
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
            fallback = item.get("numeric_value")
            if fallback is None:
                raise ValueError("missing exact numeric value")
            value = str(fallback)
        return "-" + value.lstrip("+") if negative and not value.startswith("-") else value

    @staticmethod
    def _valid_generated_citations(answer: str, context_count: int) -> bool:
        cited = [int(value) for value in re.findall(r"\[근거\s*(\d+)\]", answer)]
        return bool(cited) and all(1 <= index <= context_count for index in cited)

    @staticmethod
    def _response(question_id: str, question: str, contexts: List[Dict[str, Any]], trace: List[str],
                  answer: str, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"question_id": question_id, "question": question, "retrieved_context": contexts,
                "think_trace": {"steps": trace, "query_plan": plan or {},
                                "note": "내부 추론 원문이 아닌 실행 단계 요약입니다."}, "answer": answer}

    def close(self) -> None:
        self.retriever.close(); self._analyzer_conn.close()


class DecimalFormatter:
    @staticmethod
    def comma(value: str) -> str:
        integer, dot, fraction = value.partition(".")
        rendered = f"{int(integer):,}"
        return rendered + (dot + fraction.rstrip("0") if dot and fraction.rstrip("0") else "")
