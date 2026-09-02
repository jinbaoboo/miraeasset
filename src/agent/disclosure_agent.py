"""Grounded disclosure QA orchestration."""

from __future__ import annotations

import sqlite3
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.domain.metric_ontology import FINANCIAL_CELL_METRICS, metric_definition
from src.retrieval.hybrid_search import HybridRetriever
from src.retrieval.query_analyzer import QueryAnalyzer
from src.retrieval.query_planner import QueryPlanner
from src.tools.calculator import calculate
from src.validation import AnswerGuardrail

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
        self.planner = QueryPlanner(self.analyzer)
        self.hcx = hcx_client or HyperClovaClient()
        self.guardrail = AnswerGuardrail()

    def answer(self, question_id: str, question: str, use_llm: bool = True) -> Dict[str, Any]:
        if question.strip() and not any(term.lower() in question.lower() for term in SECURITY_TERMS):
            composite = self.planner.plan(question)
            if composite["is_composite"]:
                return self._answer_composite(question_id, question, composite, use_llm)
        return self._answer_single(question_id, question, use_llm)

    def _answer_single(self, question_id: str, question: str, use_llm: bool = True) -> Dict[str, Any]:
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
        clarification = self._clarification_answer(plan)
        if clarification:
            reason_code, answer = clarification
            plan["resolution"] = {"action": "clarify", "reason_code": reason_code}
            trace.append(f"clarification_required:{reason_code}")
            return self._response(question_id, question, [], trace, answer, plan)
        if plan.get("metric") and not plan.get("companies") and not plan.get("cross_corpus"):
            trace.append("company_not_identified")
            return self._response(
                question_id, question, [], trace,
                "제공 코퍼스에서 대상 기업을 확인할 수 없습니다. 회사명 또는 종목코드를 정확히 입력해 주세요.", plan,
            )
        candidate_docs = self.retriever.candidate_documents(plan)
        search_plan = dict(plan)
        if candidate_docs:
            candidate_doc_ids = [doc["doc_id"] for doc in candidate_docs]
            search_plan["_candidate_doc_ids"] = candidate_doc_ids
            plan["candidate_filter"] = {
                "count": len(candidate_docs),
                "sample_doc_ids": candidate_doc_ids[:10],
            }
            trace.append("candidate_documents_filtered")
        elif self._requires_candidate_documents(plan):
            trace.append("candidate_documents_not_found")
            answer = "질문 조건에 맞는 공시 자료를 제공 코퍼스에서 확인할 수 없습니다. 회사명·기간·공시 유형을 확인해 주세요."
            return self._response(question_id, question, [], trace, answer, plan)
        contexts: List[Dict[str, Any]] = []
        specialized_data: Any = None
        query_type = plan.get("query_type")
        if query_type == "investment_plan":
            specialized_data = self.retriever.find_investment_plan(search_plan)
            if specialized_data:
                trace.append("investment_plan_table_reconstructed")
                contexts.extend(self._investment_context(item) for item in specialized_data)
        elif query_type == "financing_history":
            specialized_data = self.retriever.find_financing_lifecycle(search_plan)
            trace.append("financing_lifecycle_linked")
            for chain in specialized_data.get("chains", []):
                contexts.extend(self._financing_context(item) for item in chain.get("history", []))
        elif query_type == "contract_termination":
            specialized_data = self.retriever.find_contract_lifecycle(search_plan)
            trace.append("contract_lifecycle_linked")
            contexts.append(self._contract_summary_context(specialized_data))
            contract_items = [item for chain in specialized_data.get("chains", [])
                              for item in chain.get("history", []) + chain.get("terminations", [])]
            matched_ids = {item.get("event_id") for item in contract_items}
            contract_items += [item for item in specialized_data.get("terminations", [])
                               if item.get("event_id") not in matched_ids]
            contexts.extend(self._contract_context(item) for item in contract_items[:20])
        elif query_type == "business_change":
            specialized_data = (self.retriever.find_business_document_evidence(search_plan)
                                if plan.get("comparison_axis") == "doc_subtype"
                                else self.retriever.find_business_change_evidence(search_plan))
            if specialized_data.get("evidence"):
                trace.append("business_sections_compared_by_report_type" if plan.get("comparison_axis") == "doc_subtype"
                             else "business_sections_compared_by_year")
                for key, profile in sorted(specialized_data.get("profiles", {}).items(), key=lambda item: str(item[0])):
                    contexts.append(self._business_profile_context(key, profile, specialized_data["evidence"],
                                                                  plan.get("comparison_axis") or "period"))
                contexts.extend(self._business_context(item) for item in specialized_data["evidence"])
                contexts.extend(self._business_mix_context(item)
                                for item in specialized_data.get("revenue_mix_changes", []))
        elif query_type == "business_overview":
            specialized_data = self.retriever.find_business_overview_evidence(search_plan)
            if specialized_data:
                trace.append("business_overview_sections_prioritized")
                contexts.extend(self._business_overview_context(item) for item in specialized_data)
        elif query_type == "correction_history":
            specialized_data = self.retriever.find_correction_chains(search_plan)
            trace.append("correction_chains_reconstructed")
            for chain in specialized_data.get("chains", []):
                original = chain.get("original") or {}
                if original:
                    contexts.append({
                        "kind": "correction", "record_id": original.get("doc_id"),
                        "content": (f"[is_correction] false | {original.get('corp_name')} | "
                                    f"{original.get('report_nm')} | 원 공시 접수번호 {original.get('rcept_no')}"),
                        "citation": {key: original.get(key) for key in
                                     ("doc_id", "corp_name", "report_nm", "rcept_no", "rcept_dt")},
                    })
                effective = {self._normalized_item(item.get("item")): item.get("current")
                             for item in chain.get("effective_items", [])}
                for version in chain.get("versions", []):
                    for item in version.get("items", []):
                        contexts.append(self._correction_item_context(chain, version, item,
                            effective.get(self._normalized_item(item.get("item")))))
        financial_metrics = FINANCIAL_CELL_METRICS
        cell_limit = 200 if plan.get("cross_corpus") else 60 if len(plan.get("companies") or []) > 1 else 12
        structured_values = self.retriever.extract_structured_values(search_plan, limit_per_metric=cell_limit)
        if structured_values.get("missing_metrics"):
            plan["missing_structured_metrics"] = structured_values["missing_metrics"]
        cells = structured_values["cells"]
        dimension_limit_answer: Optional[str] = None
        if plan.get("dimensions"):
            cells = self.retriever.find_dimension_metric_cells(search_plan, limit=12)
            if cells:
                trace.append("dimension_metric_cells_retrieved")
            else:
                dimension = ", ".join(plan["dimensions"])
                label = metric_definition(plan.get("metric")).get("label", plan.get("metric") or "요청 지표")
                plan["missing_dimension_evidence"] = plan["dimensions"]
                dimension_limit_answer = (
                    f"{dimension} 단위의 {label}은 제공 코퍼스의 해당 공시에서 확인할 수 없습니다. "
                    "회사 전체 또는 연결 기준 수치를 해당 세부 차원의 값으로 대신 사용하지 않았습니다."
                )
        cells = self._prioritize_cells(cells, search_plan)
        if cells:
            trace.append("structured_cells_retrieved")
            contexts.extend(self._cell_context(cell) for cell in cells[:6])
        event_limit = 1000 if plan.get("intent") == "calculation" else 8
        event_fields = structured_values["event_fields"]
        if event_fields and event_limit < len(event_fields):
            event_fields = event_fields[:event_limit]
        if event_fields:
            trace.append("structured_event_fields_retrieved")
            context_fields = event_fields if plan.get("intent") == "calculation" else event_fields[:8]
            contexts.extend(self._event_field_context(field) for field in context_fields)
        if query_type != "correction_history" and "정정" in question and plan.get("companies"):
            history = self.retriever.correction_history(plan["companies"][0]["corp_code"], limit=50,
                                                        doc_groups=plan.get("doc_groups"))
            if history:
                trace.append("correction_history_retrieved")
                contexts.extend(self._correction_context(item) for item in history)
        specialized_answer = self._specialized_answer(plan, specialized_data)
        retrieved = ([] if specialized_answer else
                     self.retriever.search(question, search_plan, limit=max(4, 8-len(contexts))))
        contexts.extend({"kind": item["kind"], "record_id": item["record_id"],
                         "content": item.get("content", "")[:6000], "citation": item.get("citation", {}),
                         "retrieval_score": item.get("score"), "score_breakdown": item.get("score_breakdown", {})}
                        for item in retrieved)
        if retrieved: trace.append("fts_evidence_retrieved")
        context_limit = (1000 if plan.get("intent") == "calculation" and event_fields else 50 if "정정" in question
                         else 20 if query_type == "business_change" else 10)
        contexts = self._deduplicate(contexts)[:context_limit]
        if not contexts and not specialized_answer:
            trace.append("insufficient_evidence")
            answer = "제공된 공시 코퍼스에서 질문을 뒷받침할 근거를 찾지 못했습니다. 회사명·기간·공시 유형을 더 구체적으로 입력해 주세요."
            return self._response(question_id, question, contexts, trace, answer, plan)
        answer = specialized_answer or dimension_limit_answer
        if answer:
            trace.append("dimension_evidence_limit" if dimension_limit_answer and not specialized_answer
                         else "deterministic_specialized_answer")
        calculation_answer = self._calculation_answer(plan, cells, event_fields)
        if not answer and calculation_answer:
            answer = calculation_answer
            trace.append("deterministic_calculation_executed")
        can_template_cells = self._can_template_with_cells(plan, financial_metrics)
        deterministic_numeric = plan.get("intent") in {"comparison", "calculation"} and bool((can_template_cells and cells) or event_fields)
        if deterministic_numeric:
            trace.append("deterministic_numeric_tool_preferred")
        # Qualitative multi-year business comparison benefits from grounded
        # generation; all numeric/sum/lifecycle routes remain deterministic.
        if query_type == "business_change" and use_llm and self.hcx.configured:
            answer = None
        if not answer and use_llm and self.hcx.configured and not deterministic_numeric:
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
            answer = self._template_answer(question, plan, cells if can_template_cells else [], event_fields, contexts)
            trace.append("deterministic_grounded_template")
        trace.append("citations_attached")
        return self._response(question_id, question, contexts, trace, answer, plan)

    def _answer_composite(self, question_id: str, question: str, composite: Dict[str, Any],
                          use_llm: bool) -> Dict[str, Any]:
        subresults = [self._answer_single(f"{question_id}:{task['task_id']}", task["question"], use_llm)
                      for task in composite["subtasks"]]
        contexts = self._deduplicate([context for result in subresults for context in result.get("retrieved_context", [])])
        answer_parts = []
        claims = []
        calculations = []
        citations = []
        limitations = []
        seen_citations = set()
        confidence_rank = {"low": 0, "medium": 1, "high": 2}
        confidence = "high"
        for task, result in zip(composite["subtasks"], subresults):
            answer_parts.append(f"[{task['task_id']}] {task['question']}\n{result['answer']}")
            for claim in result.get("claims", []):
                claims.append(dict(claim, claim_id=f"{task['task_id']}:{claim['claim_id']}", task_id=task["task_id"]))
            for calculation in result.get("calculations", []):
                calculations.append(dict(calculation,
                                         calculation_id=f"{task['task_id']}:{calculation['calculation_id']}",
                                         task_id=task["task_id"]))
            for citation in result.get("citations", []):
                key = (citation.get("doc_id"), citation.get("record_id"))
                if key not in seen_citations:
                    seen_citations.add(key)
                    citations.append(dict(citation, citation_id=f"citation_{len(citations)+1:03d}"))
            limitations.extend(dict(item, task_id=task["task_id"]) for item in result.get("limitations", []))
            if confidence_rank.get(result.get("confidence"), 0) < confidence_rank[confidence]:
                confidence = result.get("confidence", "low")
        actions = [result.get("validation", {}).get("action") for result in subresults]
        passed = all(result.get("validation", {}).get("passed") for result in subresults)
        action = "blocked" if "blocked" in actions else "clarify" if "clarify" in actions else "limit" if all(
            value == "limit" for value in actions) else "allow" if passed else "review"
        checks = [{"name": "subtask_coverage", "passed": len(subresults) == len(composite["subtasks"]),
                   "details": [task["task_id"] for task in composite["subtasks"]]},
                  {"name": "all_subtasks_validated", "passed": passed,
                   "details": [{"task_id": task["task_id"], "action": result["validation"]["action"]}
                               for task, result in zip(composite["subtasks"], subresults)]}]
        return {
            "question_id": question_id, "question": question, "retrieved_context": contexts,
            "think_trace": {"steps": ["multi_intent_query_planned", "subtasks_executed", "subtask_answers_merged"],
                            "query_plan": composite, "note": "내부 추론 원문이 아닌 실행 단계 요약입니다."},
            "answer": "\n\n".join(answer_parts), "claims": claims, "calculations": calculations,
            "citations": citations, "limitations": limitations, "confidence": confidence,
            "validation": {"passed": passed, "checks": checks,
                           "requirements": {"query_type": "multi_intent", "passed": passed,
                                            "subtask_count": len(subresults)},
                           "grounding": {"passed": all(result["validation"]["grounding"]["passed"]
                                                       for result in subresults)},
                           "action": action},
        }

    @staticmethod
    def _investment_context(item: Dict[str, Any]) -> Dict[str, Any]:
        rendered_rows = []
        for row in item.get("rows", []):
            values = ", ".join(f"{value.get('column_label')}: {value.get('original_text')}"
                               for value in row.get("values", []))
            rendered_rows.append(f"{row.get('row_label')}: {values}")
        unit = (item.get("unit") or {}).get("raw") or "단위 미상"
        content = (f"{item.get('corp_name')} | {item.get('report_nm')} | {item.get('section_path')} | "
                   f"{item.get('table_title')} | {unit}\n" + "\n".join(rendered_rows))
        return {"kind": "investment_plan", "record_id": item["table_id"], "content": content,
                "citation": item["citation"]}

    @staticmethod
    def _financing_context(item: Dict[str, Any]) -> Dict[str, Any]:
        purposes = ", ".join(f"{purpose['purpose']} {purpose['amount_krw']}원" for purpose in item.get("purposes", []))
        content = (f"{item.get('corp_name')} | {item.get('instrument')} | {item.get('report_nm')} | "
                   f"단계 {item.get('stage') or 'decision'} | 조달 결정금액 {item.get('amount_krw') or '확인 불가'}원 | "
                   f"납입 예정일 {item.get('scheduled_payment_date') or '미상'} | 목적 {purposes or '미상'} | "
                   "실제 납입·발행 완료 금액은 이 공시만으로 확인 불가")
        return {"kind": "financing", "record_id": item["event_id"], "content": content,
                "citation": item["citation"]}

    @staticmethod
    def _contract_context(item: Dict[str, Any]) -> Dict[str, Any]:
        content = (f"{item.get('corp_name')} | {item.get('report_nm')} | {item.get('contract_name') or '계약명 미상'} | "
                   f"금액 {item.get('amount_krw') or '미상'} | 계약일 {item.get('contract_date') or '미상'} | "
                   f"해지일 {item.get('termination_date') or '해당 없음'} | 해지사유 {item.get('termination_reason') or '해당 없음'} | "
                   f"단계 {item.get('stage') or item.get('kind')} | 관련공시 {item.get('related_disclosure') or '미상'}")
        return {"kind": item.get("kind"), "record_id": item["event_id"], "content": content,
                "citation": item["citation"]}

    @staticmethod
    def _contract_summary_context(lifecycle: Dict[str, Any]) -> Dict[str, Any]:
        contracts = lifecycle.get("contracts", [])
        matches = lifecycle.get("matches", [])
        terminations = lifecycle.get("terminations", [])
        first = (contracts or terminations or [{}])[0]
        content = (f"계약 lifecycle 집계 | 원계약 체인 {len(contracts)}건 | 연결된 해지 {len(matches)}건 | "
                   f"검색된 후속 해지 공시 {len(terminations)}건 | "
                   "체인은 정정본을 중복 제거하고 관련공시일·계약명·상대방·계약기간으로 연결")
        return {"kind": "contract_lifecycle", "record_id": "contract_lifecycle:summary", "content": content,
                "citation": first.get("citation") or {},
                "contract_chain_ids": [item.get("contract_chain_id") for item in contracts],
                "matched_chain_ids": [item.get("contract_chain_id") for item in matches],
                "termination_event_ids": [item.get("event_id") for item in terminations]}

    @staticmethod
    def _business_context(item: Dict[str, Any]) -> Dict[str, Any]:
        content = (f"{item.get('corp_name')} | {item.get('base_year')}년 | {item.get('report_nm')} | "
                   f"{item.get('section_path')} | 근거분류 {', '.join(item.get('evidence_categories') or [])} | "
                   f"사업신호 {', '.join(item.get('signals') or [])}\n{item.get('text', '')[:5000]}")
        return {"kind": "business_evidence", "record_id": item["chunk_id"], "content": content,
                "citation": item["citation"]}

    @staticmethod
    def _business_overview_context(item: Dict[str, Any]) -> Dict[str, Any]:
        content = (f"{item.get('corp_name')} | {item.get('report_nm')} | {item.get('section_path')} | "
                   f"사업주제 {', '.join(item.get('topics') or [])}\n{item.get('text', '')[:6000]}")
        return {"kind": "business_overview", "record_id": item["chunk_id"], "content": content,
                "citation": item["citation"]}

    @staticmethod
    def _business_profile_context(key: Any, profile: Dict[str, Any], evidence: List[Dict[str, Any]], axis: str = "period") -> Dict[str, Any]:
        if axis == "doc_subtype":
            representative = next((item for item in evidence if item.get("doc_subtype") == key), {})
        else:
            representative = next((item for item in evidence if item.get("base_year") == int(key)), {})
        label = ({"annual": "사업보고서", "quarter": "분기보고서"}.get(str(key), str(key))
                 if axis == "doc_subtype" else f"{key}년")
        content = (f"{representative.get('corp_name')} | {label} 사업 변화 분류 프로필 | "
                   f"근거분류 {', '.join(profile.get('categories') or [])} | "
                   f"핵심사업 {', '.join(profile.get('topics') or [])} | "
                   f"사업신호 {', '.join(profile.get('signals') or [])} | "
                   f"분석한 사업 섹션 청크 {profile.get('section_count') or 0}개")
        return {"kind": "business_profile", "record_id": f"business_profile:{representative.get('doc_id')}:{key}",
                "content": content, "citation": representative.get("citation") or {},
                "signal_sources": profile.get("signal_sources") or {}}

    @staticmethod
    def _business_mix_context(item: Dict[str, Any]) -> Dict[str, Any]:
        direction = "증가" if Decimal(item["change_pp"]) > 0 else "감소" if Decimal(item["change_pp"]) < 0 else "유지"
        content = (f"{item['segment']} 매출 비중 | {item['old_year']}년 {item['old_share']}% | "
                   f"{item['new_year']}년 {item['new_share']}% | {abs(Decimal(item['change_pp']))}%%p {direction} | "
                   "동일 연결 표·동일 단위 비교")
        return {"kind": "business_revenue_mix", "record_id": f"{item['table_id']}:{item['segment']}",
                "content": content.replace("%%p", "%p"), "citation": item["citation"]}

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
                key=lambda item: DisclosureAgent._comparison_numeric(item, plan),
                reverse=True,
            )
            chosen_ids = {cell.get("cell_id") for cell in first}
            return first + [cell for cell in cells if cell.get("cell_id") not in chosen_ids]
        first: List[Dict[str, Any]] = []
        chosen_ids = set()
        for metric in plan.get("required_metrics") or []:
            candidate = next((cell for cell in cells if cell.get("metric") == metric), None)
            if candidate and candidate.get("cell_id") not in chosen_ids:
                first.append(candidate); chosen_ids.add(candidate.get("cell_id"))
        if len(companies) < 2:
            return first + [cell for cell in cells if cell.get("cell_id") not in chosen_ids]
        for company in companies:
            candidate = next((cell for cell in cells if cell.get("corp_code") == company.get("corp_code")), None)
            if candidate and candidate.get("cell_id") not in chosen_ids:
                first.append(candidate); chosen_ids.add(candidate.get("cell_id"))
        return first + [cell for cell in cells if cell.get("cell_id") not in chosen_ids]

    @staticmethod
    def _specialized_answer(plan: Dict[str, Any], data: Any) -> Optional[str]:
        query_type = plan.get("query_type")
        if query_type == "investment_plan":
            return DisclosureAgent._investment_plan_answer(plan, data or [])
        if query_type == "financing_history":
            return DisclosureAgent._financing_answer(plan, data or {"chains": [], "coverage": {}})
        if query_type == "contract_termination":
            return DisclosureAgent._contract_termination_answer(plan, data or {})
        if query_type == "business_change":
            if plan.get("comparison_axis") == "doc_subtype":
                return DisclosureAgent._business_document_change_answer(plan, data or {"evidence": [], "profiles": {}})
            return DisclosureAgent._business_change_answer(plan, data or {"evidence": [], "profiles": {}})
        if query_type == "business_overview":
            return DisclosureAgent._business_overview_answer(plan, data or [])
        if query_type == "correction_history":
            return DisclosureAgent._correction_history_answer(plan, data or {"chains": [], "unlinked_count": 0})
        return None

    @staticmethod
    def _correction_item_context(chain: Dict[str, Any], version: Dict[str, Any], item: Dict[str, Any],
                                 current: Optional[str]) -> Dict[str, Any]:
        content = (f"[is_correction] true | {version.get('corp_name')} | {version.get('report_nm')} | "
                   f"정정 항목 {item.get('item') or '미상'} | "
                   f"정정 전 {item.get('before_text') or '미상'} | 정정 후 {item.get('after_text') or '미상'} | "
                   f"현재 유효 값 {current or item.get('after_text') or '미상'} | 사유 {item.get('reason') or '미상'}")
        return {"kind": "correction", "record_id": item.get("item_id") or version.get("correction_id"),
                "content": content, "citation": item.get("citation") or {}, "correction_chain_id": chain.get("chain_id")}

    @staticmethod
    def _correction_history_answer(plan: Dict[str, Any], data: Dict[str, Any]) -> str:
        chains = data.get("chains", [])
        company = (plan.get("companies") or [{}])[0].get("corp_name", "해당 기업")
        if not chains:
            return f"{company}의 질문 조건에 해당하는 정정공시를 제공 코퍼스에서 찾지 못했습니다."
        view = plan.get("correction_view") or "history"
        if "공개" in (plan.get("question") or "") and "계약상대" in (plan.get("question") or ""):
            lines = [f"예. {company}의 후속 정정공시에서 계약상대가 공개되었습니다."]
        else:
            lines = [f"{company}의 정정공시를 원본·정정본 체인 기준으로 확인했습니다."]
        receipts: List[str] = []
        for chain in chains[:5]:
            current = chain.get("current_version") or {}
            original = chain.get("original") or {}
            label = current.get("report_nm") or original.get("report_nm") or "정정공시"
            lines.append(f"- {label} (연결 신뢰도 {chain.get('link_confidence') or 'unknown'})")
            if original:
                lines.append(f"  원 공시: {original.get('report_nm')}, 접수번호 {original.get('rcept_no')}")
                receipts.append(original.get("rcept_no") or "")
            meaningful_items = [item for item in chain.get("effective_items", [])
                                if DisclosureAgent._meaningful_correction_item(item)]
            unique_items = []
            seen_items = set()
            for item in meaningful_items:
                item_name = item.get("item") or "정정 항목 미상"
                display_name = "정정 사항" if not DisclosureAgent._normalized_item(item_name) else item_name
                key = (DisclosureAgent._normalized_item(display_name), item.get("original"), item.get("current"))
                if key in seen_items:
                    continue
                seen_items.add(key)
                unique_items.append((display_name, item))
            for item_name, item in unique_items[:10]:
                if view == "original":
                    lines.append(f"  · {item_name}: 최초/정정 전 {item.get('original') or '확인 불가'}")
                elif view == "current":
                    lines.append(f"  · {item_name}: 현재 유효 값 {item.get('current') or '확인 불가'}")
                else:
                    lines.append(f"  · {item_name}: 정정 전 {item.get('original') or '확인 불가'} → "
                                 f"정정 후·현재 유효 {item.get('current') or '확인 불가'}")
            receipts.extend(version.get("rcept_no") or "" for version in chain.get("versions", []))
        if data.get("unlinked_count"):
            lines.append("일부 정정본은 원 공시 식별자가 없어 독립 체인으로 보존했으며, 임의로 원본과 연결하지 않았습니다.")
        if receipts:
            lines.append(f"근거 접수번호: {', '.join(dict.fromkeys(filter(None, receipts)))}.")
        return "\n".join(lines)

    @staticmethod
    def _meaningful_correction_item(item: Dict[str, Any]) -> bool:
        generic_names = {"시작일", "종료일", "해당여부", "우리사주조합", "구주주"}
        name = (item.get("item") or "").strip()
        original = (item.get("original") or "").strip()
        current = (item.get("current") or "").strip()
        if name in generic_names:
            return False
        return bool(name and name not in {"-", "--"} and (len(original) > 2 or len(current) > 2)) or bool(
            name in {"-", "--"} and len(current) > 2)

    @staticmethod
    def _normalized_item(value: Optional[str]) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "").lower()

    @staticmethod
    def _investment_plan_answer(plan: Dict[str, Any], tables: List[Dict[str, Any]]) -> Optional[str]:
        if not tables:
            return None
        table = tables[0]
        unit = (table.get("unit") or {}).get("raw") or "단위 미상"

        def value_for(row: Dict[str, Any], token: str) -> Optional[Dict[str, Any]]:
            return next((value for value in row.get("values", []) if token.replace(" ", "") in
                         (value.get("column_label") or "").replace(" ", "")), None)

        lines: List[str] = []
        total_line: Optional[str] = None
        for row in table.get("rows", []):
            planned = value_for(row, "투자계획")
            actual = value_for(row, "1분기실적")
            previous = value_for(row, "2025년실적")
            if not planned:
                continue
            detail = f"{row.get('row_label')}: 연간 계획 {planned.get('original_text')}"
            if actual:
                detail += f", 1분기 실적 {actual.get('original_text')}"
                planned_number = DisclosureAgent._decimal_from_item(planned)
                actual_number = DisclosureAgent._decimal_from_item(actual)
                if planned_number:
                    detail += f"(집행률 {(actual_number / planned_number * 100):.1f}%)"
            if previous:
                detail += f", 2025년 실적 {previous.get('original_text')}"
            if "합" in (row.get("row_label") or ""):
                total_line = detail
            else:
                lines.append(detail)
        if not lines and not total_line:
            return None
        period = DisclosureAgent._period_label(plan).strip()
        summary = (f"{table.get('corp_name')}의 {period or table.get('report_nm')} "
                   f"{table.get('report_nm')} 기준 주요 투자 계획은 다음과 같습니다. {unit}")
        if total_line:
            summary += f"\n\n- 전체: {total_line.split(': ', 1)[-1]}"
        summary += "".join(f"\n- {line}" for line in lines)
        summary += (f"\n\n이 숫자는 투자 '계획'과 분기 '실적'이며, 집행 완료 금액으로 해석하지 않았습니다. "
                    f"근거: {table.get('report_nm')}, 접수번호 {table.get('rcept_no')}, {table.get('section_path')}.")
        return summary

    @staticmethod
    def _financing_answer(plan: Dict[str, Any], lifecycle: Any) -> str:
        if isinstance(lifecycle, list):
            events = lifecycle
            coverage = {"payment_completion": False, "issuance_result": False,
                        "reason_code": "financing_followup_filings_not_in_corpus"}
        else:
            events = [chain.get("current", {}) for chain in lifecycle.get("chains", [])]
            coverage = lifecycle.get("coverage", {})
        instruments = plan.get("funding_instruments") or ["equity", "CB", "BW", "EB"]
        labels = {"equity": "유상증자", "CB": "CB(전환사채)", "BW": "BW(신주인수권부사채)", "EB": "EB(교환사채)"}
        company = (plan.get("companies") or [{}])[0].get("corp_name", "해당 기업")
        year = (plan.get("years") or [None])[0]
        lines = []
        receipts: List[str] = []
        for instrument in instruments:
            selected = [event for event in events if event.get("instrument") == instrument]
            amounts = [DisclosureAgent._decimal_text(event.get("amount_krw")) for event in selected if event.get("amount_krw")]
            total = sum(amounts) if amounts else None
            if not selected:
                lines.append(f"- {labels[instrument]}: 해당 기간 결정 공시 없음")
                continue
            amount_text = f", 결정금액 합계 {DecimalFormatter.comma(format(total, 'f'))}원" if total is not None else ", 금액 확인 불가"
            purpose_names = sorted({purpose["purpose"] for event in selected for purpose in event.get("purposes", [])})
            purpose_text = f", 용도 {', '.join(purpose_names)}" if purpose_names else ""
            lines.append(f"- {labels[instrument]}: {len(selected)}건{amount_text}{purpose_text}")
            receipts.extend(event.get("rcept_no") or "" for event in selected)
        qualifier = "접수일 기준" if year else "제공 코퍼스 기준"
        answer = f"{company}의 {year or ''}년 자금조달 결정 공시를 {qualifier}으로 집계했습니다.\n" + "\n".join(lines)
        answer += "\n\n주의: 위 금액은 실제 납입·발행 완료액이 아니라 유상증자/사채 발행 '결정 공시'의 계획 금액입니다."
        if not coverage.get("payment_completion") or not coverage.get("issuance_result"):
            answer += " 제공 코퍼스에 납입 완료·발행 결과 공시 유형이 없어 실제 조달 완료 여부와 완료 금액은 확인할 수 없습니다."
        if any(event.get("stage") == "correction" for event in events):
            answer += " 정정 이력이 있는 건은 가장 최근 정정본의 결정 금액을 사용했습니다."
        if plan.get("funding_status_requested") == "completed":
            answer += " 따라서 질문의 '실시한'을 실제 조달 완료로 단정하지 않고, 확인 가능한 결정 내역만 제시합니다."
        if receipts:
            answer += f" 근거 접수번호: {', '.join(dict.fromkeys(receipts))}."
        return answer

    @staticmethod
    def _contract_termination_answer(plan: Dict[str, Any], lifecycle: Dict[str, Any]) -> str:
        company = (plan.get("companies") or [{}])[0].get("corp_name", "해당 기업")
        year = (plan.get("years") or [None])[0]
        contracts = lifecycle.get("contracts", [])
        matches = lifecycle.get("matches", [])
        if matches:
            lines = []
            receipts = []
            for match in matches:
                contract, termination = match["contract"], match["termination"]
                kind = {"partial": "일부 해지", "total": "전체 해지", "unknown": "해지 범위 판단 불가"}.get(
                    match.get("termination_kind"), "해지 범위 판단 불가")
                basis = ", ".join(match.get("match_reasons") or []) or "복합 식별자"
                lines.append(f"- {termination.get('contract_name') or contract.get('contract_name') or '계약명 미상'}: "
                             f"{termination.get('termination_date') or termination.get('rcept_dt')}에 해지 공시 "
                             f"({kind}, 연결 신뢰도 {match.get('match_confidence') or 'unknown'}, 근거 {basis})")
                receipts.extend([contract.get("rcept_no") or "", termination.get("rcept_no") or ""])
            return (f"예. {company}이 {year}년에 공시한 주요 계약 중 이후 해지 공시와 연결된 계약이 "
                    f"{len(matches)}건 있습니다.\n" + "\n".join(lines) +
                    f"\n근거 접수번호: {', '.join(dict.fromkeys(receipts))}.")
        if not contracts:
            return f"{company}의 {year}년 단일판매·공급계약 체결 공시를 제공 코퍼스에서 찾지 못했습니다."
        receipts = ", ".join(contract.get("rcept_no") or "" for contract in contracts)
        return (f"{company}이 {year}년에 공시한 단일판매·공급계약 {len(contracts)}건을 후속 해지 공시와 대조했으나, "
                f"관련공시일·계약명·상대방·계약기간을 기준으로 명확히 연결되는 해지 건은 확인되지 않았습니다. "
                f"이는 해지가 없다는 법적 단정이 아니라 제공 코퍼스 내 검색 결과입니다. 계약 근거 접수번호: {receipts}.")

    @staticmethod
    def _business_change_answer(plan: Dict[str, Any], analysis: Any) -> Optional[str]:
        years = sorted(plan.get("years") or [])
        if isinstance(analysis, list):
            evidence = analysis
            profiles = {}
        else:
            evidence = analysis.get("evidence", [])
            profiles = {int(year): value for year, value in (analysis.get("profiles") or {}).items()}
        if len(years) < 2 or not evidence:
            return None
        by_year = {year: [item for item in evidence if item.get("base_year") == year] for year in years}
        if any(not by_year[year] for year in years):
            return f"{years[0]}년과 {years[-1]}년 모두의 사업보고서 '사업의 내용' 근거가 갖춰지지 않아 변화를 판단할 수 없습니다."
        old, new = years[0], years[-1]
        if not profiles:
            profiles = {year: {"signals": sorted({signal for item in by_year[year] for signal in item.get("signals", [])}),
                               "categories": sorted({category for item in by_year[year]
                                                     for category in item.get("evidence_categories", [])})}
                        for year in years}
        old_profile, new_profile = profiles.get(old, {}), profiles.get(new, {})
        old_signals = set(old_profile.get("signals", [])); new_signals = set(new_profile.get("signals", []))
        old_topics = set(old_profile.get("topics", [])); new_topics = set(new_profile.get("topics", []))
        retained_signals = sorted(new_signals & old_signals)
        newly_emphasized = sorted(new_signals - old_signals)
        less_emphasized = sorted(old_signals - new_signals)
        retained_topics = sorted(old_topics & new_topics)
        old_topic_counts = old_profile.get("topic_counts") or {}
        new_topic_counts = new_profile.get("topic_counts") or {}
        newly_observed_topics = sorted(topic for topic in new_topics - old_topics
                                       if int(new_topic_counts.get(topic, 0)) >= 2)
        less_observed_topics = sorted(topic for topic in old_topics - new_topics
                                      if int(old_topic_counts.get(topic, 0)) >= 2)
        company = (plan.get("companies") or [{}])[0].get("corp_name", "해당 기업")
        parts = [f"{company}의 {old}년과 {new}년 사업보고서 'II. 사업의 내용' 비교 결과입니다."]
        parts.append(f"- 유지된 핵심 사업: {', '.join(retained_topics) if retained_topics else '공통 분류를 명확히 식별하기 어려움'}")
        if newly_observed_topics:
            parts.append(f"- {new}년 공시에서 새로 관찰된 사업 표현: {', '.join(newly_observed_topics)}")
        if less_observed_topics:
            parts.append(f"- {new}년 공시에서 관찰이 줄어든 사업 표현: {', '.join(less_observed_topics)}")
        parts.append(f"- 유지된 전략 축: {', '.join(retained_signals) if retained_signals else '공통 전략 신호가 적음'}")
        if newly_emphasized:
            parts.append(f"- {new}년에 추가로 강조된 전략 변화: {', '.join(newly_emphasized)}")
        if less_emphasized:
            parts.append(f"- {new}년에 강조가 줄어든 전략 표현: {', '.join(less_emphasized)}")
        mix_changes = analysis.get("revenue_mix_changes", []) if isinstance(analysis, dict) else []
        if mix_changes:
            rendered = []
            for item in mix_changes[:4]:
                change = Decimal(item["change_pp"])
                direction = "증가" if change > 0 else "감소" if change < 0 else "유지"
                rendered.append(f"{item['segment']} {item['old_share']}%→{item['new_share']}% "
                                f"({abs(change)}%p {direction})")
            parts.append(f"- 연결 매출 구성의 확인 가능한 수치 변화: {', '.join(rendered)}")
        else:
            parts.append("- 매출 구성 수치 변화: 두 기간을 같은 범위·단위로 비교할 구조화 표가 없어 판단을 유보했습니다.")
        receipts = [item.get("rcept_no") for item in evidence if item.get("rcept_no")]
        parts.append("'새로 관찰'·'강조 감소'는 공시 표현의 변화이며 사업 중단을 뜻하지 않습니다. "
                     "신규 진출이나 사업 축소도 추가 근거 없이 단정하지 않습니다. "
                     f"근거 접수번호: {', '.join(dict.fromkeys(receipts))}.")
        return "\n".join(parts)

    @staticmethod
    def _business_document_change_answer(plan: Dict[str, Any], analysis: Dict[str, Any]) -> Optional[str]:
        evidence = analysis.get("evidence") or []
        profiles = analysis.get("profiles") or {}
        annual = profiles.get("annual") or {}
        quarter = profiles.get("quarter") or {}
        if not evidence or not annual or not quarter:
            return "같은 기준연도의 사업보고서와 분기보고서 'II. 사업의 내용' 근거를 모두 확인할 수 없습니다."
        annual_signals = set(annual.get("signals") or [])
        quarter_signals = set(quarter.get("signals") or [])
        annual_topics = set(annual.get("topics") or [])
        quarter_topics = set(quarter.get("topics") or [])
        common = sorted(annual_signals & quarter_signals)
        annual_only = sorted(signal for signal in annual_signals - quarter_signals
                             if int((annual.get("signal_counts") or {}).get(signal, 0)) >= 2)
        quarter_only = sorted(signal for signal in quarter_signals - annual_signals
                              if int((quarter.get("signal_counts") or {}).get(signal, 0)) >= 2)
        company = (plan.get("companies") or [{}])[0].get("corp_name", "해당 기업")
        year = (plan.get("years") or [""])[0]
        lines = [f"{company}의 {year}년 사업보고서와 분기보고서 'II. 사업의 내용'을 같은 분류로 비교했습니다."]
        common_topics = sorted(annual_topics & quarter_topics)
        annual_only_topics = sorted(topic for topic in annual_topics - quarter_topics
                                    if int((annual.get("topic_counts") or {}).get(topic, 0)) >= 2)
        quarter_only_topics = sorted(topic for topic in quarter_topics - annual_topics
                                     if int((quarter.get("topic_counts") or {}).get(topic, 0)) >= 2)
        lines.append(f"- 두 보고서의 공통 핵심 사업: {', '.join(common_topics) if common_topics else '명확한 공통 사업 분류가 적음'}")
        if annual_only_topics:
            lines.append(f"- 사업보고서에서만 확인된 사업 표현: {', '.join(annual_only_topics)}")
        if quarter_only_topics:
            lines.append(f"- 분기보고서에서만 확인된 사업 표현: {', '.join(quarter_only_topics)}")
        lines.append(f"- 두 보고서에서 공통으로 확인된 사업·전략 축: {', '.join(common) if common else '명확한 공통 신호가 적음'}")
        lines.append(f"- 사업보고서에서만 상대적으로 확인된 표현: {', '.join(annual_only) if annual_only else '없음'}")
        lines.append(f"- 분기보고서에서만 상대적으로 확인된 표현: {', '.join(quarter_only) if quarter_only else '없음'}")
        lines.append("보고서별 표현 차이는 작성 시점과 상세도 차이일 수 있으며, 신규 진출이나 사업 중단으로 단정하지 않습니다.")
        receipts = [item.get("rcept_no") for item in evidence if item.get("rcept_no")]
        lines.append(f"근거 접수번호: {', '.join(dict.fromkeys(receipts))}.")
        return "\n".join(lines)

    @staticmethod
    def _business_overview_answer(plan: Dict[str, Any], evidence: List[Dict[str, Any]]) -> Optional[str]:
        if not evidence:
            return None
        company = (plan.get("companies") or [{}])[0].get("corp_name") or evidence[0].get("corp_name") or "해당 기업"
        report = evidence[0].get("report_nm") or "공시"
        question = plan.get("question") or ""
        lines = [f"{company}의 {report} 'II. 사업의 내용' 기준 요약입니다."]
        topics = list(dict.fromkeys(topic for item in evidence for topic in item.get("topics", [])))
        if topics:
            lines.append(f"- 핵심 사업: {', '.join(topics)}")
        texts = [DisclosureAgent._strip_business_prefix(item.get("text") or "", item) for item in evidence]
        primary_text = DisclosureAgent._business_excerpt(texts[0], question, max_chars=6000) if texts else ""
        primary_sentences = DisclosureAgent._business_sentences(primary_text)
        all_sentences = DisclosureAgent._business_sentences(" ".join(texts))
        # The report's own business-overview section has already won retrieval
        # ranking.  Keep sentence selection inside it so a later price, capacity
        # or sales-route paragraph cannot replace the company-wide description.
        detail = DisclosureAgent._select_business_sentence(primary_sentences, question, strategy=False)
        if not detail:
            detail = DisclosureAgent._select_business_sentence(all_sentences, question, strategy=False)
        strategy = ("" if any(token in question for token in ("만 간결", "축만", "사업만")) else
                    DisclosureAgent._select_business_sentence(all_sentences, question, strategy=True, exclude=detail))
        if detail:
            lines.append(f"- 주요 제품·서비스: {DisclosureAgent._clip_sentence(detail, 420)}")
        if strategy:
            lines.append(f"- 전략·현황: {DisclosureAgent._clip_sentence(strategy, 300)}")
        used = evidence[:2]
        receipts = [item.get("rcept_no") for item in used if item.get("rcept_no")]
        if receipts:
            lines.append(f"근거 접수번호: {', '.join(dict.fromkeys(receipts))}.")
        return "\n".join(lines) if detail or topics else None

    @staticmethod
    def _strip_business_prefix(text: str, item: Dict[str, Any]) -> str:
        prefix = f"{item.get('corp_name')} | {item.get('report_nm')} | {item.get('section_path')}"
        value = (text or "").strip()
        return value[len(prefix):].lstrip() if value.startswith(prefix) else value

    @staticmethod
    def _business_sentences(text: str) -> List[str]:
        cleaned = " ".join((text or "").split())
        chunks = re.split(r"(?<=[.!?])\s+|(?<=다\.)", cleaned)
        result, seen = [], set()
        for chunk in chunks:
            sentence = chunk.strip(" ·-")
            key = re.sub(r"\s+", "", sentence)
            if len(sentence) < 25 or key in seen:
                continue
            seen.add(key); result.append(sentence)
        return result

    @staticmethod
    def _select_business_sentence(sentences: List[str], question: str, strategy: bool,
                                  exclude: Optional[str] = None) -> str:
        focus = [token for token in re.findall(r"[0-9A-Za-z가-힣]+", question)
                 if len(token) > 1 and token not in {"기준으로", "분기보고서", "사업보고서", "정리해줘", "설명해줘"}]
        strategy_terms = ("전략", "목표", "추진", "투자", "확대", "강화", "성장", "전환", "R&D", "AI", "2030")
        content_terms = ("사업", "구성", "주력", "제품", "서비스", "생산", "판매", "영위", "매출")
        ranked = []
        for index, sentence in enumerate(sentences):
            if sentence == exclude:
                continue
            def has_term(term: str) -> bool:
                if re.fullmatch(r"[A-Za-z&]+", term):
                    return bool(re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", sentence, re.IGNORECASE))
                return term.lower() in sentence.lower()

            strategy_hits = sum(has_term(term) for term in strategy_terms)
            if strategy and not strategy_hits:
                continue
            score = 4 * strategy_hits if strategy else 4 * sum(term in sentence for term in content_terms)
            score += 2 * sum(token.lower() in sentence.lower() for token in focus)
            if not strategy and any(token in question for token in ("대표 게임", "대표 제품", "주력 제품")):
                score += 12 * sum(term in sentence for term in ("주력", "대표", "제품으로", "게임으로"))
            if not strategy and "게임" in question and "주력" in sentence and "게임" in sentence:
                score += 16
            score += min(8, len(re.findall(r"\b[A-Z][A-Za-z0-9&.-]*\b", sentence)))
            if 70 <= len(sentence) <= 520:
                score += 4
            if not strategy and any(term in sentence for term in ("참조", "K-IFRS", "자료는")):
                score -= 8
            # Sales-route and pricing paragraphs frequently contain the word
            # "전략", but they are poor company-level strategy summaries.
            if strategy and any(term in sentence for term in ("판매조직", "판매경로", "판매방법", "가격정책", "판매조건")):
                score -= 15
            if strategy and (len(sentence) > 520 or sentence.count(")") >= 5):
                score -= 8
            ranked.append((score, -index, sentence))
        return max(ranked, default=(0, 0, ""))[2]

    @staticmethod
    def _clip_sentence(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        clipped = text[:max_chars]
        boundary = max(clipped.rfind(","), clipped.rfind("."), clipped.rfind(" "))
        if boundary >= int(max_chars * 0.7):
            clipped = clipped[:boundary]
        return clipped.rstrip(" ,") + "…"

    @staticmethod
    def _business_excerpt(text: str, question: str, max_chars: int = 1000) -> str:
        cleaned = " ".join((text or "").split())
        if any(token in question for token in ("차량", "자동차")) and "[차량부문]" in cleaned:
            cleaned = cleaned.split("[차량부문]", 1)[1]
            cleaned = cleaned.split("[기타부문]", 1)[0]
        if len(cleaned) <= max_chars:
            return cleaned
        clipped = cleaned[:max_chars]
        sentence_end = max(clipped.rfind("다."), clipped.rfind("니다."), clipped.rfind("습니다."))
        if sentence_end >= max_chars // 2:
            clipped = clipped[:sentence_end + 2]
        return clipped.rstrip() + ("…" if len(cleaned) > len(clipped) else "")

    @staticmethod
    def _comparison_numeric(item: Dict[str, Any], plan: Dict[str, Any]) -> Decimal:
        value = Decimal(DisclosureAgent._exact_numeric(item)) * Decimal(str(item.get("unit_scale") or 0))
        return abs(value) if plan.get("metric") == "capex" else value

    @staticmethod
    def _decimal_from_item(item: Dict[str, Any]) -> Decimal:
        return DisclosureAgent._decimal_text(item.get("original_text") or item.get("numeric_value") or "0")

    @staticmethod
    def _decimal_text(value: Any) -> Decimal:
        text = str(value).strip().replace(",", "")
        negative = text.startswith(("△", "▲")) or (text.startswith("(") and text.endswith(")"))
        text = text.lstrip("△▲").strip().strip("()").rstrip("%").strip()
        number = Decimal(text)
        return -number if negative and number > 0 else number

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
                    key=lambda item: DisclosureAgent._comparison_numeric(item, plan),
                    reverse=True,
                )
                top = ranked[:min(3, len(ranked))]
                details = ", ".join(
                    f"{item['corp_name']} {item.get('original_text')} {item.get('unit_raw') or '(단위 미상)'}" for item in top
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
                    normalized = [DisclosureAgent._comparison_numeric(item, plan) for item in selected]
                    winner_index = max(range(len(selected)), key=lambda index: normalized[index])
                    details = ", ".join(
                        f"{item['corp_name']} "
                        f"{DisclosureAgent._absolute_original(item) if plan.get('metric') == 'capex' else item.get('original_text')} "
                        f"{item.get('unit_raw') or '(단위 미상)'}"
                        for item in selected
                    )
                    receipts = ", ".join(item.get("rcept_no") or "" for item in selected)
                    basis = ("연결 현금흐름표의 '유형자산의 취득' 현금유출액(절대값) 기준으로 "
                             if plan.get("metric") == "capex" else "")
                    period = DisclosureAgent._period_label(plan)
                    metric_label = metric_definition(plan.get("metric")).get("label", "요청 지표")
                    return (f"{period}{metric_label}은 {basis}{details}이며, 동일 통화·단위로 환산하면 "
                            f"{selected[winner_index]['corp_name']}의 규모가 더 큽니다. "
                            f"근거 접수번호: {receipts}.")
                details = ", ".join(
                    f"{item['corp_name']} {item.get('original_text')} {item.get('unit_raw') or '(단위 미상)'}"
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
            display_unit = DisclosureAgent._display_unit(unit)
            scope_phrase = f"{scope} 기준 " if scope not in {None, "", "기준 미상"} else ""
            requested_label = DisclosureAgent._requested_metric_label(question, plan, cell.get("row_label") or "해당 값")
            period = DisclosureAgent._period_label(plan, cell)
            return (f"{cell['corp_name']}의 {period}{scope_phrase}"
                    f"{DisclosureAgent._topic(requested_label)} {cell.get('original_text')}{display_unit}입니다. "
                    f"공시 단위: {unit}. "
                    f"근거: {cell.get('report_nm')}, 접수번호 {cell.get('rcept_no')}, "
                    f"{cell.get('table_title') or '표 제목 미상'}의 해당 행·열.")
        if event_fields:
            field = event_fields[0]
            unit = ("원" if field.get("field_key", "").endswith("_krw") else
                    "%" if field.get("field_key", "").endswith("_pct") else "")
            label = DisclosureAgent._requested_metric_label(question, plan, field.get("label") or "해당 값")
            period = DisclosureAgent._period_label(plan).strip()
            period_prefix = f"{period} " if period else ""
            return (f"{field['corp_name']}의 {period_prefix}{DisclosureAgent._topic(label)} {field.get('original_text')}{unit}입니다. "
                    f"근거: {field.get('report_nm')}, 접수번호 {field.get('rcept_no')}의 정형 공시 항목.")
        first = contexts[0]
        citation = first.get("citation", {})
        excerpt = " ".join(first.get("content", "").split())[:500]
        return (f"가장 관련성이 높은 공시 근거는 다음과 같습니다: {excerpt} "
                f"(출처: {citation.get('corp_name','')}, {citation.get('report_nm','')}, "
                f"접수번호 {citation.get('rcept_no','')}). 추가 수치 판단은 이 근거 범위 안에서만 가능합니다.")

    @staticmethod
    def _calculation_answer(plan: Dict[str, Any], cells: List[Dict[str, Any]], event_fields: List[Dict[str, Any]]) -> Optional[str]:
        calculation = plan.get("calculation") or {}
        operation = calculation.get("operation")
        if not operation:
            return None
        if operation == "difference" and len(plan.get("companies") or []) >= 2:
            return DisclosureAgent._company_pair_difference_answer(plan, cells)
        if operation in {"growth_rate", "difference", "growth_amount_and_rate"}:
            return DisclosureAgent._year_pair_calculation_answer(plan, cells, operation)
        if operation == "ratio":
            return DisclosureAgent._ratio_calculation_answer(plan, cells)
        if operation == "sum" and cells:
            selected = DisclosureAgent._best_cells_by_metric_year(cells)
            if not selected:
                return None
            compatible, reason = DisclosureAgent._compatible_numeric_cells(selected, require_scope=False)
            if not compatible:
                return f"검색된 값들의 {reason}이 일치하지 않아 합계를 안전하게 계산할 수 없습니다."
            total = calculate("sum", [DisclosureAgent._normalized_numeric(cell) for cell in selected])
            unit = DisclosureAgent._normalized_unit_label(selected[0])
            receipts = ", ".join(cell.get("rcept_no") or "" for cell in selected)
            return f"조건에 맞는 값 {len(selected)}개의 합계는 {DecimalFormatter.comma(total['result'])}{unit}입니다. 계산식: {total['formula']}. 근거 접수번호: {receipts}."
        return None

    @staticmethod
    def _year_pair_calculation_answer(plan: Dict[str, Any], cells: List[Dict[str, Any]], operation: str) -> Optional[str]:
        calculation = plan.get("calculation") or {}
        target_year = calculation.get("target_year")
        baseline_year = calculation.get("baseline_year")
        metric = (plan.get("required_metrics") or [plan.get("metric")])[0]
        if not target_year or not baseline_year or not metric:
            return None
        current_candidates = [cell for cell in cells if cell.get("metric") == metric and cell.get("base_year") == target_year]
        baseline_candidates = [cell for cell in cells if cell.get("metric") == metric and cell.get("base_year") == baseline_year]
        compatible_pairs = []
        for current_item in current_candidates:
            for baseline_item in baseline_candidates:
                compatible, _ = DisclosureAgent._compatible_numeric_cells([current_item, baseline_item], require_scope=True)
                if not compatible:
                    continue
                score = current_item.get("selection_score", 0) + baseline_item.get("selection_score", 0)
                if current_item.get("unit_scale") == baseline_item.get("unit_scale"):
                    score += 8
                if current_item.get("scope") == baseline_item.get("scope"):
                    score += 5
                if current_item.get("table_title") == baseline_item.get("table_title"):
                    score += 6
                if re.sub(r"\s*\(.*?\)", "", current_item.get("row_label") or "") == re.sub(
                        r"\s*\(.*?\)", "", baseline_item.get("row_label") or ""):
                    score += 4
                compatible_pairs.append((score, current_item, baseline_item))
        compatible_pairs.sort(key=lambda item: item[0], reverse=True)
        if compatible_pairs:
            _, current, baseline = compatible_pairs[0]
        else:
            current = DisclosureAgent._best_cell(cells, metric=metric, year=target_year)
            baseline = DisclosureAgent._best_cell(cells, metric=metric, year=baseline_year)
        if not current or not baseline:
            return None
        compatible, reason = DisclosureAgent._compatible_numeric_cells([current, baseline], require_scope=True)
        if not compatible:
            return f"{target_year}년과 {baseline_year}년 값의 {reason}이 일치하지 않아 계산할 수 없습니다."
        same_scale = current.get("unit_scale") == baseline.get("unit_scale")
        current_value = (DisclosureAgent._exact_numeric(current) if same_scale
                         else DisclosureAgent._normalized_numeric(current))
        baseline_value = (DisclosureAgent._exact_numeric(baseline) if same_scale
                          else DisclosureAgent._normalized_numeric(baseline))
        calculation_basis = ""
        if metric == "capex":
            # Cash-flow statements express acquisitions as cash outflows.  For
            # an investment-size comparison the economically meaningful value
            # is the magnitude, while the original signed text remains in the
            # evidence and formula inputs for auditability.
            current_value = format(abs(Decimal(str(current_value))), "f")
            baseline_value = format(abs(Decimal(str(baseline_value))), "f")
            calculation_basis = " 연결 현금흐름표의 현금유출 절대값 기준입니다."
        calculate_operation = "growth_rate" if operation == "growth_amount_and_rate" else operation
        result = calculate(calculate_operation, [current_value, baseline_value])
        difference_result = calculate("difference", [current_value, baseline_value])
        unit = "%" if operation == "growth_rate" else DisclosureAgent._normalized_unit_label(current)
        verb = "증감률" if operation == "growth_rate" else "차이"
        rendered = f"{result['result_float']:.2f}%" if operation == "growth_rate" else f"{DecimalFormatter.comma(result['result'])}{unit}"
        direction = ""
        if operation == "growth_rate":
            direction = " 증가" if Decimal(result["result"]) > 0 else " 감소" if Decimal(result["result"]) < 0 else " 변동 없음"
        inputs = (
            f"{target_year}년 {current.get('original_text')} ({current.get('unit_raw') or '단위 미상'}), "
            f"{baseline_year}년 {baseline.get('original_text')} ({baseline.get('unit_raw') or '단위 미상'})"
        )
        comparison_label = "전년 동기 대비 " if plan.get("quarter") and baseline_year == target_year - 1 else ""
        if operation == "growth_amount_and_rate":
            raw_difference = calculate("difference", [current_value, baseline_value])
            difference_unit = (DisclosureAgent._display_unit(current.get("unit_raw") or "") if same_scale
                               else DisclosureAgent._normalized_unit_label(current))
            difference_display = f"{DecimalFormatter.comma(raw_difference['result'])}{difference_unit}"
            direction = "증가" if Decimal(difference_result["result"]) > 0 else "감소" if Decimal(difference_result["result"]) < 0 else "변동 없음"
            scope = {"consolidated": "연결 기준 ", "separate": "별도 기준 ", "unknown": "기준 미상 "}.get(
                current.get("scope"), ""
            )
            return (
                f"{current['corp_name']}의 {target_year}년 {plan.get('quarter')}분기 전년 동기 대비 {scope}{DisclosureAgent._topic(current.get('row_label') or '요청 지표')} "
                f"{difference_display} {direction}했고, 증감률은 {result['result_float']:.2f}%입니다. "
                f"입력값은 {inputs}입니다.{calculation_basis} 계산식: "
                f"차이={raw_difference['formula']}; 증감률={result['formula']}. "
                f"근거 접수번호: {current.get('rcept_no')}, {baseline.get('rcept_no')}."
            )
        if operation == "growth_rate":
            difference_unit = (DisclosureAgent._display_unit(current.get("unit_raw") or "") if same_scale
                               else DisclosureAgent._normalized_unit_label(current))
            difference_display = f"{DecimalFormatter.comma(difference_result['result'])}{difference_unit}"
            direction_word = "증가" if Decimal(difference_result["result"]) > 0 else "감소" if Decimal(difference_result["result"]) < 0 else "변동 없음"
            period_label = DisclosureAgent._period_label(plan).strip() or f"{target_year}년"
            rate_display = abs(result["result_float"])
            return (
                f"{current['corp_name']}의 {period_label} {comparison_label}{DisclosureAgent._topic(current.get('row_label') or '요청 지표')} "
                f"{difference_display} {direction_word}했고, 전년 동기 대비 {rate_display:.2f}% {direction_word}한 수준입니다. "
                f"입력값은 {inputs}입니다.{calculation_basis} 계산식: "
                f"차이={difference_result['formula']}; 증감률={result['formula']}. "
                f"근거 접수번호: {current.get('rcept_no')}, {baseline.get('rcept_no')}."
            )
        return (
            f"{current['corp_name']}의 {target_year}년 {comparison_label}{current.get('row_label')} {verb}은(는) {rendered}{direction}입니다. "
            f"입력값은 {inputs}입니다.{calculation_basis} 계산식: {result['formula']}. "
            f"근거 접수번호: {current.get('rcept_no')}, {baseline.get('rcept_no')}."
        )

    @staticmethod
    def _ratio_calculation_answer(plan: Dict[str, Any], cells: List[Dict[str, Any]]) -> Optional[str]:
        required_metrics = plan.get("required_metrics") or []
        if len(required_metrics) != 2:
            return None
        year = max(plan.get("years") or []) if plan.get("years") else None
        numerator = DisclosureAgent._best_cell(cells, metric=required_metrics[0], year=year)
        denominator = DisclosureAgent._best_cell(cells, metric=required_metrics[1], year=year)
        if not numerator or not denominator:
            return None
        compatible, reason = DisclosureAgent._compatible_numeric_cells([numerator, denominator], require_scope=True)
        if not compatible:
            return f"{numerator.get('row_label')}과 {denominator.get('row_label')}의 {reason}이 일치하지 않아 비율을 계산할 수 없습니다."
        same_scale = numerator.get("unit_scale") == denominator.get("unit_scale")
        numerator_value = (DisclosureAgent._exact_numeric(numerator) if same_scale
                           else DisclosureAgent._normalized_numeric(numerator))
        denominator_value = (DisclosureAgent._exact_numeric(denominator) if same_scale
                             else DisclosureAgent._normalized_numeric(denominator))
        result = calculate("ratio", [numerator_value, denominator_value])
        label = {
            "operating_margin": "영업이익률",
            "net_margin": "순이익률",
            "debt_ratio": "부채비율",
            "roe": "ROE",
        }.get(plan.get("metric"), "비율")
        period = DisclosureAgent._period_label(plan)
        scope = {"consolidated": "연결 기준 ", "separate": "별도 기준 ", "unknown": "기준 미상 "}.get(
            numerator.get("scope"), ""
        )
        return (
            f"{numerator['corp_name']}의 {period}{scope}{DisclosureAgent._topic(label)} {result['result_float']:.2f}%입니다. "
            f"입력값은 {numerator.get('row_label')} {numerator.get('original_text')} ({numerator.get('unit_raw') or '단위 미상'}), "
            f"{denominator.get('row_label')} {denominator.get('original_text')} ({denominator.get('unit_raw') or '단위 미상'})입니다. "
            f"계산식: {result['formula']}. 근거 접수번호: {numerator.get('rcept_no')}, {denominator.get('rcept_no')}."
        )

    @staticmethod
    def _company_pair_difference_answer(plan: Dict[str, Any], cells: List[Dict[str, Any]]) -> Optional[str]:
        selected = []
        for company in plan.get("companies") or []:
            candidate = DisclosureAgent._best_cell(
                [cell for cell in cells if cell.get("corp_code") == company.get("corp_code")],
                metric=plan.get("metric"), year=max(plan.get("years") or []) if plan.get("years") else None,
            )
            if candidate:
                selected.append(candidate)
        if len(selected) != 2:
            return None
        compatible, reason = DisclosureAgent._compatible_numeric_cells(selected, require_scope=True)
        if not compatible:
            return f"두 기업 값의 {reason}이 일치하지 않아 차이를 계산할 수 없습니다."
        first_value = Decimal(str(DisclosureAgent._exact_numeric(selected[0])))
        second_value = Decimal(str(DisclosureAgent._exact_numeric(selected[1])))
        difference = abs(first_value - second_value)
        unit = DisclosureAgent._display_unit(selected[0].get("unit_raw") or "")
        metric_label = metric_definition(plan.get("metric")).get("label", plan.get("metric") or "요청 지표")
        period = DisclosureAgent._period_label(plan)
        scope = {"consolidated": "연결", "separate": "별도", "unknown": "기준 미상"}.get(selected[0].get("scope"), "")
        larger = selected[0] if first_value >= second_value else selected[1]
        details = ", ".join(f"{item['corp_name']} {item.get('original_text')}{DisclosureAgent._display_unit(item.get('unit_raw') or '')}"
                            for item in selected)
        receipts = ", ".join(item.get("rcept_no") or "" for item in selected)
        return (f"{period}{scope} 기준 {metric_label}은 {details}이며, 두 기업의 차이는 "
                f"{DecimalFormatter.comma(format(difference, 'f'))}{unit}입니다. {larger['corp_name']}가 더 큽니다. "
                f"계산식: abs({first_value} - {second_value}). 근거 접수번호: {receipts}.")

    @staticmethod
    def _requested_metric_label(question: str, plan: Dict[str, Any], fallback: str) -> str:
        aliases = metric_definition(plan.get("metric")).get("aliases") or []
        matches = [alias for alias in aliases if alias and alias.lower() in question.lower()]
        return max(matches, key=len) if matches else metric_definition(plan.get("metric")).get("label", fallback)

    @staticmethod
    def _period_label(plan: Dict[str, Any], cell: Optional[Dict[str, Any]] = None) -> str:
        year = max(plan.get("years") or []) if plan.get("years") else (cell or {}).get("base_year")
        if not year:
            return ""
        if plan.get("quarter"):
            aggregation = {"ytd": " 누적", "three_month": " 3개월"}.get(plan.get("period_aggregation"), "")
            return f"{year}년 {plan['quarter']}분기{aggregation} "
        month = (cell or {}).get("base_month")
        return f"{year}년 {month}월 " if month else f"{year}년 "

    @staticmethod
    def _best_cell(cells: List[Dict[str, Any]], metric: Optional[str], year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        candidates = [cell for cell in cells if (not metric or cell.get("metric") == metric)]
        if year is not None:
            candidates = [cell for cell in candidates if cell.get("base_year") == year]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda cell: (
                cell.get("selection_score", 0),
                1 if cell.get("period_role") == "current" else 0,
                cell.get("base_year") or 0,
                cell.get("base_month") or 0,
                cell.get("rcept_dt") or "",
            ),
            reverse=True,
        )[0]

    @staticmethod
    def _best_cells_by_metric_year(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        best: Dict[tuple, Dict[str, Any]] = {}
        for cell in cells:
            key = (cell.get("corp_code"), cell.get("metric"), cell.get("base_year"), cell.get("base_month"))
            if key not in best or cell.get("selection_score", 0) > best[key].get("selection_score", 0):
                best[key] = cell
        return list(best.values())

    @staticmethod
    def _compatible_numeric_cells(cells: List[Dict[str, Any]], require_scope: bool) -> tuple[bool, str]:
        if any(cell.get("unit_currency") is None or cell.get("unit_scale") is None for cell in cells):
            return False, "통화 또는 단위"
        currencies = {cell.get("unit_currency") for cell in cells}
        if len(currencies) != 1:
            return False, "통화"
        if require_scope:
            scopes = {cell.get("scope") for cell in cells}
            if len(scopes) != 1:
                return False, "연결/별도 기준"
        return True, ""

    @staticmethod
    def _normalized_numeric(cell: Dict[str, Any]) -> str:
        return str(Decimal(DisclosureAgent._exact_numeric(cell)) * Decimal(str(cell.get("unit_scale") or 1)))

    @staticmethod
    def _normalized_unit_label(cell: Dict[str, Any]) -> str:
        currency = cell.get("unit_currency")
        return "원" if currency == "KRW" else f" {currency}" if currency else ""

    @staticmethod
    def _display_unit(unit_raw: str) -> str:
        match = re.search(r"(조원|억원|백만원|천원|원|천주|주|명|건|%)", unit_raw or "")
        return match.group(1) if match else ""

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
    def _absolute_original(item: Dict[str, Any]) -> str:
        value = abs(Decimal(DisclosureAgent._exact_numeric(item)))
        return DecimalFormatter.comma(format(value, "f"))

    @staticmethod
    def _topic(label: str) -> str:
        last = label.rstrip()[-1]
        code = ord(last)
        has_final_consonant = 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0
        return label + ("은" if has_final_consonant else "는")

    @staticmethod
    def _valid_generated_citations(answer: str, context_count: int) -> bool:
        cited = [int(value) for value in re.findall(r"\[근거\s*(\d+)\]", answer)]
        return bool(cited) and all(1 <= index <= context_count for index in cited)

    def _response(self, question_id: str, question: str, contexts: List[Dict[str, Any]], trace: List[str],
                  answer: str, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        plan = plan or {}
        artifacts = self.guardrail.evaluate(answer, contexts, plan)
        if not artifacts["validation"]["passed"] and not self.guardrail.is_limit_answer(answer):
            answer = self.guardrail.safe_failure_answer(artifacts["validation"])
            artifacts["validation"]["action"] = "blocked"
            trace = trace + ["answer_guardrail_blocked"]
            artifacts["limitations"].append({
                "code": "answer_blocked_by_guardrail",
                "message": "근거·수치·필수 요구사항 검증을 통과하지 못한 초안을 차단했습니다.",
            })
        else:
            trace = trace + ["answer_guardrail_passed"]
            resolution_action = (plan.get("resolution") or {}).get("action")
            if "prompt_injection_or_secret_request_blocked" in trace:
                artifacts["validation"]["action"] = "blocked"
            elif resolution_action == "clarify":
                artifacts["validation"]["action"] = "clarify"
            elif self.guardrail.is_limit_answer(answer):
                artifacts["validation"]["action"] = "limit"
        return {"question_id": question_id, "question": question, "retrieved_context": contexts,
                "think_trace": {"steps": trace, "query_plan": plan,
                                "note": "내부 추론 원문이 아닌 실행 단계 요약입니다."}, "answer": answer,
                **artifacts}

    def close(self) -> None:
        self.retriever.close(); self._analyzer_conn.close()

    @staticmethod
    def _requires_candidate_documents(plan: Dict[str, Any]) -> bool:
        if plan.get("query_type") == "correction_history":
            return False
        return bool(plan.get("companies") or plan.get("years") or plan.get("doc_groups") or plan.get("doc_subtypes"))

    @staticmethod
    def _clarification_answer(plan: Dict[str, Any]) -> Optional[tuple[str, str]]:
        """Return a concrete reverse question only when a core route cannot be executed safely."""
        query_type = plan.get("query_type")
        core_types = {
            "financial_metric", "investment_plan", "capex_comparison", "financing_history",
            "contract_termination", "business_change", "business_overview", "correction_history",
        }
        missing = set(plan.get("missing_slots") or [])
        warnings = set(plan.get("warnings") or [])
        if "ambiguous_return_metric" in warnings:
            return "ambiguous_metric", "어떤 수익률을 뜻하는지 확인이 필요합니다. 영업이익률, 순이익률, ROE 중 하나를 지정해 주세요."
        if "ambiguous_topic" in warnings:
            return "missing_topic", "알고 싶은 항목을 지정해 주세요. 예: 연결 매출액, 영업이익, 주요 사업, 공급계약."
        if query_type == "generic" and "metric" in missing and any(
            token in plan.get("question", "") for token in ("재무", "수치", "실적", "계정", "지표")
        ):
            return "missing_metric", "조회할 재무 지표를 지정해 주세요. 예: 연결 매출액, 영업이익, 당기순이익."
        if query_type not in core_types:
            return None
        if "company" in missing:
            return "missing_company", "제공 코퍼스에서 대상 기업을 확인할 수 없습니다. 정확한 회사명 또는 종목코드를 입력해 주세요."
        if "comparison_target" in missing:
            return "missing_comparison_target", "비교할 기업이 부족합니다. 두 기업의 회사명 또는 종목코드를 모두 입력해 주세요."
        if "comparison_periods" in missing:
            return "missing_comparison_periods", "사업 변화를 비교할 두 기준연도를 입력해 주세요. 예: 2023년과 2025년."
        if "period" in missing:
            return "missing_period", "조회 기준연도를 입력해 주세요. 분기·반기 질의라면 분기 또는 기준월도 함께 지정해 주세요."
        if "metric" in missing:
            return "missing_metric", "조회할 재무 지표를 지정해 주세요. 예: 연결 매출액, 영업이익, 당기순이익."
        return None

    @staticmethod
    def _can_template_with_cells(plan: Dict[str, Any], financial_metrics: set[str]) -> bool:
        if plan.get("metric") in financial_metrics:
            return True
        calculation = plan.get("calculation") or {}
        return calculation.get("operation") in {"growth_rate", "difference"} and len(plan.get("required_metrics") or []) == 1


class DecimalFormatter:
    @staticmethod
    def comma(value: str) -> str:
        integer, dot, fraction = value.partition(".")
        rendered = f"{int(integer):,}"
        return rendered + (dot + fraction.rstrip("0") if dot and fraction.rstrip("0") else "")
