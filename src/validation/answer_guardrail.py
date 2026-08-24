"""Build auditable answer artifacts and block unsupported factual output."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from .requirements import evaluate_requirements


NUMBER_RE = re.compile(r"(?<![0-9A-Za-z])(?:\(?[+-]?[0-9][0-9,]*(?:\.[0-9]+)?\)?)(?:%|원|건|명|주|차례|년|월|분기)?")
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
LIMIT_MARKERS = ("확인할 수 없", "찾지 못", "단위 미상", "기준 미상", "자동 판단하지 않",
                 "질문이 비어", "보안상", "공시 코퍼스만으로는", "입력해 주세요", "지정해 주세요",
                 "확인이 필요합니다")


class AnswerGuardrail:
    def evaluate(self, answer: str, contexts: List[Dict[str, Any]], plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        plan = plan or {}
        citations = self._citations(contexts)
        calculations = self._calculations(answer, contexts, plan)
        claims = self._claims(answer, contexts, calculations)
        requirements = evaluate_requirements(plan, answer, contexts)
        grounding = self._grounding(answer, contexts, calculations, plan)
        limitations = self._limitations(answer, plan, requirements, grounding)
        security_ok = not any(term in answer.lower() for term in ("api key=", "api_key=", "secret=", "password="))
        factual_answer = bool(claims) and not self._is_refusal(answer)
        explicit_absence = any(token in answer for token in ("공시 없음", "찾지 못했습니다", "확인되지 않았습니다"))
        citation_ok = bool(citations) or explicit_absence if factual_answer else True
        checks = [
            {"name": "requirements_complete", "passed": requirements["passed"], "details": requirements.get("missing", [])},
            {"name": "numeric_grounding", "passed": grounding["passed"], "details": grounding.get("unsupported_numbers", [])},
            {"name": "claim_evidence", "passed": all(claim.get("verified") for claim in claims) if claims else True,
             "details": [claim["claim_id"] for claim in claims if not claim.get("verified")]},
            {"name": "citation_present", "passed": citation_ok, "details": [] if citation_ok else ["missing_citation"]},
            {"name": "secret_leak_check", "passed": security_ok, "details": [] if security_ok else ["credential_pattern"]},
        ]
        passed = all(check["passed"] for check in checks)
        confidence = self._confidence(passed, citations, limitations, plan)
        return {
            "claims": claims,
            "calculations": calculations,
            "citations": citations,
            "limitations": limitations,
            "confidence": confidence,
            "validation": {"passed": passed, "checks": checks, "requirements": requirements,
                           "grounding": grounding, "action": "allow" if passed else "review"},
        }

    @staticmethod
    def is_limit_answer(answer: str) -> bool:
        return AnswerGuardrail._is_refusal(answer)

    @staticmethod
    def safe_failure_answer(validation: Dict[str, Any]) -> str:
        missing = validation.get("requirements", {}).get("missing", [])
        unsupported = validation.get("grounding", {}).get("unsupported_numbers", [])
        if unsupported:
            return ("답변에 근거로 확인되지 않은 수치가 포함될 수 있어 자동 답변을 보류했습니다. "
                    f"확인이 필요한 수치: {', '.join(unsupported)}.")
        if "compatible_units" in missing:
            return "비교 대상의 통화·단위·기준을 동일하게 확인할 수 없어 규모를 비교하지 않았습니다."
        if "evidence_for_each_period" in missing:
            return "비교할 두 기간의 공시 근거가 모두 갖춰지지 않아 변화를 판단하지 않았습니다."
        return ("질문에 필요한 근거 항목을 모두 확인할 수 없어 답변을 보류했습니다. "
                f"누락 항목: {', '.join(missing) if missing else '근거 검증'}.")

    def _claims(self, answer: str, contexts: List[Dict[str, Any]], calculations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._is_refusal(answer):
            return []
        segments = [segment.strip(" -\n") for segment in re.split(r"\n+|(?<=[다요])\.\s+", answer) if segment.strip(" -\n")]
        result = []
        calculation_outputs = {calculation.get("display") for calculation in calculations}
        business_profile_ids = [item.get("record_id") for item in contexts if item.get("kind") == "business_profile"]
        qualitative_fact_markers = ("유지된 핵심", "추가로 강조", "강조가 줄어든", "연결 신뢰도", "전체 해지", "일부 해지")
        for index, segment in enumerate(segments, start=1):
            if re.fullmatch(r"\[근거\s*\d+\]", segment) or segment.startswith(("근거 접수번호:", "근거:")):
                continue
            if not any(char.isdigit() for char in segment) and not any(
                token in segment for token in ("더 큽니다", "없습니다", "확인됩니다") + qualitative_fact_markers
            ):
                continue
            comparative_business_claim = any(marker in segment for marker in qualitative_fact_markers[:3])
            evidence = business_profile_ids if comparative_business_claim and len(business_profile_ids) >= 2 else self._best_evidence(segment, contexts)
            calculated = any(display and display in segment for display in calculation_outputs)
            result.append({"claim_id": f"claim_{index:03d}", "text": segment,
                           "claim_type": "calculated_fact" if calculated else "extracted_fact",
                           "evidence_ids": evidence, "verified": bool(evidence) or calculated})
        return result

    def _grounding(self, answer: str, contexts: List[Dict[str, Any]], calculations: List[Dict[str, Any]],
                   plan: Dict[str, Any]) -> Dict[str, Any]:
        evidence_numbers = {self._canonical_number(value) for value in self._numbers(
            " ".join(item.get("content", "") for item in contexts))}
        evidence_numbers.update(self._canonical_number(value) for value in self._numbers(
            " ".join(str(value) for item in contexts for value in (item.get("citation") or {}).values())))
        evidence_numbers.update(self._absolute_number(value) for value in list(evidence_numbers) if value.startswith("-"))
        calculated_numbers = {self._canonical_number(value) for value in self._numbers(
            " ".join(str(value) for item in calculations for value in
                     (item.get("result"), item.get("display"), item.get("formula")) if value is not None))}
        allowed = evidence_numbers | calculated_numbers | {str(year) for year in plan.get("years", [])}
        allowed.update(str(index) for index in range(0, len(contexts) + 1))
        unsupported = []
        for number in self._numbers(answer):
            if number.endswith(("년", "월", "분기")):
                continue
            plain = self._canonical_number(number)
            if not plain or plain in allowed or self._is_metadata_number(plain):
                continue
            unsupported.append(number)
        unsupported = list(dict.fromkeys(unsupported))
        return {"passed": not unsupported, "unsupported_numbers": unsupported,
                "evidence_number_count": len(evidence_numbers), "calculated_number_count": len(calculated_numbers)}

    def _calculations(self, answer: str, contexts: List[Dict[str, Any]], plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        if plan.get("query_type") == "investment_plan":
            for item in contexts:
                if item.get("kind") != "investment_plan":
                    continue
                for line in item.get("content", "").splitlines():
                    plan_match = re.search(r"투자\s*계획:\s*([0-9,]+)", line)
                    actual_match = re.search(r"1분기\s*실적:\s*([0-9,]+)", line)
                    label = line.split(":", 1)[0]
                    if not plan_match or not actual_match:
                        continue
                    planned = Decimal(plan_match.group(1).replace(",", ""))
                    actual = Decimal(actual_match.group(1).replace(",", ""))
                    if planned == 0:
                        continue
                    calculated = actual / planned * Decimal(100)
                    display = f"{calculated:.1f}%"
                    if display not in answer:
                        continue
                    result.append({"calculation_id": f"calc_{len(result)+1:03d}", "name": "execution_rate",
                                   "inputs": [str(actual), str(planned)], "formula": f"{actual} / {planned} * 100",
                                   "result": format(calculated, "f"), "display": display,
                                   "evidence_ids": [item.get("record_id")], "label": label})
        if plan.get("query_type") == "financing_history":
            by_instrument: Dict[str, List[Decimal]] = {}
            for item in contexts:
                if item.get("kind") != "financing":
                    continue
                match = re.search(r"\|\s*(equity|CB|BW|EB)\s*\|.*?조달 결정금액\s*([0-9,]+)원", item.get("content", ""), re.I)
                if match:
                    by_instrument.setdefault(match.group(1), []).append(Decimal(match.group(2).replace(",", "")))
            for instrument, values in by_instrument.items():
                total = sum(values)
                display = f"{int(total):,}원"
                if display in answer:
                    result.append({"calculation_id": f"calc_{len(result)+1:03d}", "name": "funding_sum",
                                   "inputs": [str(value) for value in values], "formula": " + ".join(str(value) for value in values),
                                   "result": str(total), "display": display,
                                   "evidence_ids": [item.get("record_id") for item in contexts if item.get("kind") == "financing"],
                                   "label": instrument})
        if plan.get("query_type") == "contract_termination":
            summary = next((item for item in contexts if item.get("kind") == "contract_lifecycle"), None)
            if summary:
                for label, token in (("contract_chain_count", "원계약 체인"), ("linked_termination_count", "연결된 해지")):
                    match = re.search(rf"{token}\s*(\d+)건", summary.get("content", ""))
                    if match and f"{match.group(1)}건" in answer:
                        inputs = (summary.get("contract_chain_ids") if label == "contract_chain_count"
                                  else summary.get("matched_chain_ids")) or []
                        result.append({"calculation_id": f"calc_{len(result)+1:03d}", "name": label,
                                       "inputs": inputs,
                                       "formula": "count(unique lifecycle records)", "result": match.group(1),
                                       "display": f"{match.group(1)}건", "evidence_ids": [summary.get("record_id")]})
        formula_matches = re.findall(r"계산식:\s*(.+?)(?:\.\s*근거 접수번호:|\n|$)", answer)
        for formula in formula_matches:
            result_value = self._evaluate_formula(formula)
            display = None
            if result_value is not None:
                percent = re.search(r"[+-]?[0-9,]+(?:\.[0-9]+)?%", answer)
                display = percent.group(0) if percent else None
            result.append({"calculation_id": f"calc_{len(result)+1:03d}", "name": plan.get("calculation", {}).get("operation") or "calculation",
                           "inputs": self._numbers(formula), "formula": formula.strip(), "result": result_value, "display": display,
                           "evidence_ids": [item.get("record_id") for item in contexts[:2]]})
        return result

    @staticmethod
    def _citations(contexts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in contexts:
            citation = dict(item.get("citation") or {})
            if not citation:
                continue
            key = (citation.get("doc_id"), citation.get("rcept_no"), item.get("record_id"))
            if key in seen:
                continue
            seen.add(key)
            citation["citation_id"] = f"citation_{len(result)+1:03d}"
            citation["record_id"] = item.get("record_id")
            citation["kind"] = item.get("kind")
            result.append(citation)
        return result

    @staticmethod
    def _limitations(answer: str, plan: Dict[str, Any], requirements: Dict[str, Any], grounding: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = []
        if any(marker in answer for marker in LIMIT_MARKERS):
            result.append({"code": "explicit_answer_limit", "message": "답변에 데이터 범위 또는 해석 한계가 명시되었습니다."})
        resolution = plan.get("resolution") or {}
        if resolution.get("action") != "clarify":
            for missing in requirements.get("missing", []):
                result.append({"code": f"missing_{missing}", "message": f"필수 항목 {missing}을(를) 확인하지 못했습니다."})
        if grounding.get("unsupported_numbers"):
            result.append({"code": "unsupported_numeric_claim", "message": "근거와 계산에서 확인되지 않은 수치가 있습니다."})
        for warning in plan.get("warnings", []):
            result.append({"code": warning, "message": warning})
        if resolution.get("reason_code"):
            result.append({"code": resolution["reason_code"], "message": "질문의 필수 조건을 확인해야 합니다."})
        if plan.get("query_type") == "financing_history" and "실제 조달 완료 여부와 완료 금액은 확인할 수 없습니다" in answer:
            result.append({
                "code": "financing_completion_not_covered",
                "message": "제공 코퍼스에는 납입 완료·발행 결과 공시 유형이 없어 결정과 완료를 구분했습니다.",
            })
        if "연결 신뢰도 medium" in answer:
            result.append({"code": "correction_chain_inferred", "message": "원본 식별자 대신 공시 유형·결정일로 정정 체인을 연결했습니다."})
        if "연결 신뢰도 low" in answer:
            result.append({"code": "correction_chain_unlinked", "message": "원 공시를 안전하게 식별하지 못해 정정본을 독립 보존했습니다."})
        return result

    @staticmethod
    def _confidence(passed: bool, citations: List[Dict[str, Any]], limitations: List[Dict[str, Any]], plan: Dict[str, Any]) -> str:
        if not passed or not citations:
            return "low"
        if limitations or plan.get("query_type") == "business_change":
            return "medium"
        return "high"

    @staticmethod
    def _best_evidence(claim: str, contexts: List[Dict[str, Any]]) -> List[str]:
        claim_tokens = {token.lower() for token in TOKEN_RE.findall(claim) if len(token) > 1}
        claim_numbers = set(AnswerGuardrail._numbers(claim))
        scored = []
        for item in contexts:
            content = item.get("content", "") + " " + " ".join(str(value) for value in (item.get("citation") or {}).values())
            tokens = {token.lower() for token in TOKEN_RE.findall(content) if len(token) > 1}
            numbers = set(AnswerGuardrail._numbers(content))
            score = len(claim_tokens & tokens) + 4 * len(claim_numbers & numbers)
            score += sum(1 for token in tokens if len(token) >= 3 and token in claim.lower())
            if score:
                scored.append((score, item.get("record_id")))
        scored.sort(reverse=True)
        return [record_id for _, record_id in scored[:2] if record_id]

    @staticmethod
    def _numbers(text: str) -> List[str]:
        return [match.group(0).strip() for match in NUMBER_RE.finditer(text or "")]

    @staticmethod
    def _canonical_number(value: str) -> str:
        text = re.sub(r"(?:%|원|건|명|주|차례|년|월|분기)$", "", value.strip()).replace(",", "")
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            number = Decimal(text)
        except InvalidOperation:
            return ""
        if negative:
            number = -abs(number)
        return format(number.normalize(), "f")

    @staticmethod
    def _absolute_number(value: str) -> str:
        try:
            return format(abs(Decimal(value)).normalize(), "f")
        except InvalidOperation:
            return value

    @staticmethod
    def _evaluate_formula(formula: str) -> Optional[str]:
        compact = formula.replace(",", "").strip()
        growth = re.search(r"\(?([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\)?\s*/\s*abs\(\2\)\s*\*\s*100", compact)
        if growth:
            current, baseline = Decimal(growth.group(1)), Decimal(growth.group(2))
            return None if baseline == 0 else format(((current - baseline) / abs(baseline) * 100).normalize(), "f")
        ratio = re.search(r"([+-]?\d+(?:\.\d+)?)\s*/\s*([+-]?\d+(?:\.\d+)?)\s*\*\s*100", compact)
        if ratio:
            numerator, denominator = Decimal(ratio.group(1)), Decimal(ratio.group(2))
            return None if denominator == 0 else format((numerator / denominator * 100).normalize(), "f")
        difference = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)", compact)
        if difference:
            return format((Decimal(difference.group(1)) - Decimal(difference.group(2))).normalize(), "f")
        return None

    @staticmethod
    def _is_metadata_number(value: str) -> bool:
        digits = value.lstrip("-").replace(".", "")
        if len(digits) >= 8:
            return True
        try:
            number = Decimal(value)
        except InvalidOperation:
            return False
        return Decimal(1900) <= number <= Decimal(2100)

    @staticmethod
    def _is_refusal(answer: str) -> bool:
        return any(marker in answer for marker in (
            "확인할 수 없습니다", "찾지 못했습니다", "답변할 수 없습니다", "식별할 수 없습니다",
            "후보 공시 문서를 찾지 못했습니다", "보안상", "공시 코퍼스만으로는",
            "입력해 주세요", "지정해 주세요", "확인이 필요합니다",
        ))
