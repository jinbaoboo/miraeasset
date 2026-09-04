"""Deterministic query metadata extraction used before optional LLM planning."""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional

from src.domain.metric_ontology import METRICS


COMPANY_ALIASES = {
    "00266961": ("네이버",),       # NAVER
    "00258689": ("JYP", "제이와이피"),
    "00261443": ("엔씨", "엔씨소프트"),
}

COMPANY_NAME_ALIASES = {
    "삼성전자": ("삼전", "삼섬전자", "삼성전자주식회사", "samsung electronics"),
    "NAVER": ("네이바",),
    "SK하이닉스": ("하이닉스", "SK하이닉쓰", "sk hynix", "skhynix"),
    "현대자동차": ("현차", "현대차", "현대자돟차", "hyundai motor", "hyundai motor company"),
    "기아": ("kia", "kia corporation"),
    "카카오": ("카까오", "kakao"),
    "HMM": ("에이치엠엠",),
    "JYP Ent": ("제이와피엔터",),
    "POSCO홀딩스": ("포스코홀딩스", "posco holdings"),
    "LG이노텍": ("엘지이노텍", "엘지이노택", "lg innotek"),
    "삼성바이오로직스": ("samsung biologics",),
    "HD현대일렉트릭": ("hd hyundai electric",),
    "CJ제일제당": ("cj cheiljedang",),
    "LG유플러스": ("lg u+", "lgu+"),
}

CALCULATION_PATTERNS = {
    "growth_rate": ["전년대비", "전년 대비", "전년동기대비", "전년 동기 대비", "증감률", "증가율", "감소율", "성장률", "yoy"],
    "difference": ["차이", "격차", "얼마나 증가", "얼마나 감소", "얼마 더 큰", "증감액"],
    "sum": ["합계", "총액", "총합"],
    "average": ["평균"],
    "ratio": ["비중", "비율"],
}

DERIVED_CALCULATIONS = {
    "operating_margin": {
        "aliases": ["영업이익률", "영업익률"],
        "operation": "ratio",
        "required_metrics": ["operating_profit", "revenue"],
        "formula": "영업이익 / 매출액 * 100",
    },
    "net_margin": {
        "aliases": ["순이익률", "당기순이익률"],
        "operation": "ratio",
        "required_metrics": ["net_income", "revenue"],
        "formula": "당기순이익 / 매출액 * 100",
    },
    "debt_ratio": {
        "aliases": ["부채비율"],
        "operation": "ratio",
        "required_metrics": ["liabilities", "equity"],
        "formula": "부채총계 / 자본총계 * 100",
    },
    "roe": {
        "aliases": ["ROE", "자기자본이익률"],
        "operation": "ratio",
        "required_metrics": ["net_income", "equity"],
        "formula": "당기순이익 / 자본총계 * 100",
    },
}


class QueryAnalyzer:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def analyze(self, question: str) -> Dict[str, Any]:
        original_question = question
        question = self._positive_intent_text(question)
        compact_question = re.sub(r"\s+", "", question).lower()
        companies = self._companies(question)
        years = {int(value) for value in re.findall(r"(20\d{2})\s*년?", question)}
        years.update(2000 + int(value) for value in re.findall(r"(?<!\d)(2[3-6])\s*년", question))
        years.update(2000 + int(value) for value in re.findall(r"(?<!\d)['’](2[3-6])(?:\s*년)?", question))
        years = sorted(years)
        months = [int(value) for value in re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*월", question)]
        quarter_match = re.search(r"([1-4])\s*(?:사분기|/\s*4\s*분기|분기|[Qq])|[Qq]\s*([1-4])", question)
        if not quarter_match and "첫 분기" in question:
            quarter_match = re.search(r"(첫)\s*분기", question)
        quarter_token = next((group for group in (quarter_match.groups() if quarter_match else ()) if group), None)
        quarter = (1 if quarter_token == "첫" else int(quarter_token) if quarter_token else None)
        doc_subtypes: List[str] = []
        if re.search(r"사업\s*보고서", question):
            doc_subtypes.append("annual")
        if re.search(r"반기\s*보고서", question) or re.search(r"\b반기\b", question) or "상반기" in question:
            doc_subtypes.append("half")
        if re.search(r"분기\s*보고서", question) or quarter:
            doc_subtypes.append("quarter")
        scope = "consolidated" if "연결" in question else "separate" if any(x in question for x in ("별도", "개별")) else None
        query_type = self._query_type(question, compact_question)
        cross_corpus = any(x in question for x in ("가장", "상위", "하위", "전체 기업", "기업 중", "순위"))
        metric = self._metric(compact_question)
        if (any(token in question for token in ("공급계약", "계약액", "계약금액")) and
                "최근매출액" in compact_question and
                any(token in compact_question for token in ("몇퍼센트", "매출액대비", "비율", "비중"))):
            metric = "contract_ratio"
        calculation = self._calculation(question, compact_question, metric, years)
        required_metrics = calculation.get("required_metrics", [metric] if metric else []) if calculation else ([metric] if metric else [])
        if calculation and calculation.get("derived_metric"):
            metric = calculation["derived_metric"]
        groups: List[str] = []
        if any(x in question for x in ("사업보고서", "반기보고서", "분기보고서", "재무", "매출", "영업이익", "당기순이익", "연구개발", "설비투자", "CAPEX", "이익률", "부채비율", "ROE", "자기자본이익률")):
            groups.append("periodic")
        if doc_subtypes and "periodic" not in groups:
            groups.append("periodic")
        if any(x in question for x in ("유상증자", "자기주식", "합병", "분할", "사채", "주요사항", "자금조달",
                                       "CB", "BW", "EB", "전환사채", "신주인수권", "교환사채")):
            groups.append("major")
        if (any(x in question for x in ("공급계약", "계약액", "계약금액", "위탁생산 계약", "주요 계약", "계약 해지", "수주", "시설투자", "투자판단", "거래소", "콜옵션", "주주간계약"))
                or any(x in compact_question for x in ("공급계약", "계약상대", "파운드리계약"))):
            groups.append("exchange")
        if any(x in question for x in ("대량보유", "지분", "보유비율", "특별관계자")):
            groups.append("holding")
        # High-value QA routes use an explicit corpus so a narrative phrase
        # cannot drift into an unrelated full-text-search result.
        if query_type in {"investment_plan", "capex_comparison", "business_change", "business_overview"}:
            groups = ["periodic"]
        elif query_type == "financial_metric":
            groups = (["exchange"] if "exchange" in groups else ["holding"] if "holding" in groups
                      else ["major"] if "major" in groups else ["periodic"])
        elif query_type == "financing_history":
            groups = ["major"]
        elif query_type == "contract_termination":
            groups = ["exchange"]
        if (query_type in {"capex_comparison", "business_change"} or
                metric == "capex" and len(years) >= 2) and not doc_subtypes:
            doc_subtypes.append("annual")
        intent = "comparison" if cross_corpus or any(x in question for x in ("비교", "차이", "증가", "감소", "증감", "대비", "더 큰", "더 많", "더 적")) else "lookup"
        if calculation or any(x in question for x in ("합계", "총액", "평균", "비중", "증감률")):
            intent = "calculation"
        plan = {
            "question": question, "query_type": query_type, "companies": companies, "years": years, "months": months,
            "quarter": quarter, "scope": scope, "metric": metric, "doc_subtypes": list(dict.fromkeys(doc_subtypes)),
            "cross_corpus": cross_corpus,
            "doc_groups": list(dict.fromkeys(groups)), "intent": intent,
            "period_basis": self._period_basis(question, groups),
            "required_metrics": list(dict.fromkeys(required_metrics)),
            "calculation": calculation,
            "requires_current_effective": not any(x in question for x in ("정정 전", "변경 전", "최초")),
        }
        if question != original_question:
            plan["original_question"] = original_question
            plan["excluded_context_removed"] = True
        plan["period_aggregation"] = self._period_aggregation(question)
        plan["dimensions"] = self._dimensions(question, metric)
        if query_type == "business_change":
            plan["comparison_axis"] = (
                "doc_subtype" if len(years) == 1 and {"annual", "quarter"}.issubset(set(doc_subtypes)) else "period"
            )
        if query_type == "financing_history":
            plan["funding_instruments"] = self._funding_instruments(question)
            plan["funding_status_requested"] = self._funding_status(question)
        if query_type == "correction_history":
            plan["correction_view"] = self._correction_view(question)
        if query_type in {"investment_plan", "business_change", "business_overview"}:
            plan["section_filters"] = ["II. 사업의 내용"]
        plan["missing_slots"] = self._missing_slots(plan)
        plan["warnings"] = self._warnings(question, plan)
        return plan

    @staticmethod
    def _positive_intent_text(question: str) -> str:
        """Remove explicitly negative metadata before entity and period extraction.

        Users often clarify a request with phrases such as "A와 2022년은 제외".
        Treating those values as positive filters creates a false multi-company or
        multi-period query.  Only labelled exclusion containers are removed; free
        prose remains untouched so ordinary business questions keep their context.
        """
        value = re.sub(
            r"\[(?:제외\s*조건|제외\s*대상|검색\s*제외|비교\s*금지|참조\s*금지)\s*:[^\]]*\]\s*",
            "", question, flags=re.IGNORECASE,
        )
        value = re.sub(
            r"\((?=[^)]*(?:비교\s*금지|참조\s*금지|제외\s*대상))[^)]*\)\.?",
            "", value, flags=re.IGNORECASE,
        )
        value = re.sub(
            r"(?:앞의\s*)?제외\s*대상은[^.!?]*(?:포함하지\s*마|제외해)[.!?]?",
            "", value, flags=re.IGNORECASE,
        )
        return " ".join(value.split()).strip()

    @staticmethod
    def _metric(compact_question: str) -> Optional[str]:
        matches = []
        for key, aliases in METRICS.items():
            for alias in aliases:
                normalized = re.sub(r"\s+", "", alias).lower()
                if normalized in compact_question:
                    matches.append((len(normalized), key))
        return max(matches, default=(0, None))[1]

    def _companies(self, question: str) -> List[Dict[str, Optional[str]]]:
        rows = self.conn.execute(
            "SELECT corp_code,stock_code,corp_name,listed_name,corp_eng_name "
            "FROM companies ORDER BY length(corp_name) DESC"
        ).fetchall()
        found = []
        for row in rows:
            names = {
                row["corp_name"], row["listed_name"], row["corp_eng_name"], row["stock_code"],
                *COMPANY_ALIASES.get(row["corp_code"], ()),
                *self._company_name_aliases(row),
            }
            if any(name and self._company_name_in_question(str(name), question) for name in names):
                found.append(dict(row))
        return found

    @staticmethod
    def _company_name_in_question(name: str, question: str) -> bool:
        """Match spacing/case variants without crossing unrelated Korean words.

        Removing all spaces made the short alias ``현차`` match ``표현 차이``.
        A whitespace-flexible pattern keeps support for ``삼성 전자`` while
        requiring a real word boundary before the company name.
        """
        candidate = name.strip().casefold()
        if not candidate:
            return False
        if candidate.isdigit():
            return re.search(rf"(?<!\d){re.escape(candidate)}(?!\d)", question.casefold()) is not None
        compact = re.sub(r"\s+", "", candidate)
        pattern = r"\s*".join(re.escape(char) for char in compact)
        suffix = r"(?=$|[^A-Za-z가-힣]|(?:은|는|이|가|의|을|를|와|과|에서|에게|보다|으로|로)(?=$|[^A-Za-z가-힣]))"
        return re.search(rf"(?<![A-Za-z가-힣]){pattern}{suffix}", question.casefold()) is not None

    @staticmethod
    def _company_name_aliases(row: sqlite3.Row) -> tuple[str, ...]:
        aliases: List[str] = []
        for value in (row["corp_name"], row["listed_name"], row["corp_eng_name"]):
            aliases.extend(COMPANY_NAME_ALIASES.get(value or "", ()))
        return tuple(dict.fromkeys(aliases))

    @staticmethod
    def _normalized_company_text(value: str) -> str:
        """Normalize harmless user variations without fuzzy company matching."""
        return re.sub(r"\s+", "", value or "").casefold()

    @staticmethod
    def _dimensions(question: str, metric: Optional[str]) -> List[str]:
        if not metric:
            return []
        dimensions = re.findall(r"([0-9A-Za-z가-힣]+(?:부문|기지|사업장|지역))\s*(?=연결\s*|별도\s*|매출|영업이익|순이익|자산|부채)", question)
        excluded = {"연결부문", "별도부문", "사업부문"}
        return list(dict.fromkeys(value for value in dimensions if value not in excluded))

    @staticmethod
    def _query_type(question: str, compact_question: str) -> str:
        if ("계약상대" in compact_question and
                any(token in question for token in ("지금", "최신", "현재 유효", "정정 후", "공개", "밝혀", "확인됐"))):
            return "correction_history"
        if ("공급계약" in question and "계약상대" in question and
                any(token in question for token in ("공개", "밝혀", "확인됐"))):
            return "correction_history"
        if ("정정" in question and any(token in question for token in (
                "정정 내용", "정정 사항", "원공시", "원 공시", "원본", "정정본", "설명",
                "정정된", "정정 전 후",
        ))):
            return "correction_history"
        if (("정정" in question or any(token in question for token in ("현재 유효", "최신 값", "최신 유효", "현재 값"))) and
                any(token in question for token in (
            "정정 전", "정정 후", "변경 전", "변경 후", "현재 유효", "최신 값", "최신 유효", "최초", "정정 내역", "정정공시",
        ))):
            return "correction_history"
        if "자금조달" in question and (any(
                token.lower() in compact_question
                for token in ("유상증자", "cb", "bw", "eb", "전환사채", "신주인수권", "교환사채")
        ) or any(token in question for token in (
                "내역", "유형", "완료", "납입", "발행", "실시", "결정", "계획", "금액",
        ))):
            return "financing_history"
        if any(token in question for token in ("주요 계약", "공급계약", "판매계약")) and "해지" in question:
            return "contract_termination"
        years = re.findall(r"20\d{2}\s*년?", question)
        report_type_comparison = (
            "사업보고서" in question and "분기보고서" in question and
            any(token in question for token in ("비교", "대조", "공통점", "달라진", "달라졌", "변화", "강조"))
        )
        if (len(years) >= 2 and any(token in question for token in (
                "핵심 사업", "사업은 어떻게", "사업 변화", "사업의 내용", "투자 방향", "사업 전략", "전략의 변화",
                "유지 사업", "유지된 사업", "새 강조", "새로 강조", "매출 비중 변화"
        ))) or report_type_comparison:
            return "business_change"
        if any(token in question for token in (
            "사업의 내용", "주요 사업", "사업 내용", "주요 서비스", "주요 제품과 서비스", "자동차 부문 관련",
            "주요 사업부문", "대표 제품", "주력 제품", "주력 메모리 제품", "제품과 사업", "사업 현황과 전략",
            "플랫폼과 콘텐츠 사업", "사업 포트폴리오", "온라인·모바일 게임", "사업 실적 개요",
            "무엇을 파는지", "병행 사업", "수익모델", "서비스 축", "세부 사업", "주요 게임",
            "뭐로 돈 버는지", "무슨 사업을 하는지",
        )):
            return "business_overview"
        # Narrative business questions vary much more than numeric lookups.
        # Require both a descriptive action and a concrete business topic so
        # ordinary requests such as "영업이익을 설명해줘" remain financial.
        narrative_action = any(token in question for token in (
            "정리", "설명", "요약", "분류", "구분", "분석",
        ))
        narrative_topic = any(token in question for token in (
            "핵심 서비스", "대표 서비스", "제품군", "대표 제품", "주요 제품", "주력 제품",
            "게임 사업", "서비스 사업", "사업 구성", "사업 영역", "사업 활동", "사업모델",
            "사업 포트폴리오", "사업 전략", "성장 전략", "중장기 방향", "추진 전략",
            "연구개발 방향", "R&D 초점", "수익 사업", "두 축", "세부 항목",
            "각 사업부문", "사업부문별", "전력기기 사업", "건설 사업", "바이오 사업",
            "CDMO 사업", "자동차 사업", "차량부문", "플랫폼·콘텐츠", "플랫폼과 콘텐츠",
        ))
        has_financial_metric = any(
            re.sub(r"\s+", "", alias).lower() in compact_question
            for aliases in METRICS.values() for alias in aliases
        )
        narrative_topic = narrative_topic or (
            ("사업" in question or "서비스" in question) and not has_financial_metric
        )
        if narrative_action and narrative_topic:
            return "business_overview"
        if (any(token in question for token in ("주요 투자 계획", "투자 계획", "주요 투자 현황")) or
                ("투자" in question and "집행" in question) or
                "투자계획" in compact_question or "투자현황" in compact_question):
            return "investment_plan"
        if any(token.lower() in compact_question for token in ("설비투자", "capex")) and any(
            token in question for token in ("비교", "더 큰", "더 많")
        ):
            return "capex_comparison"
        if any(alias.replace(" ", "").lower() in compact_question for aliases in METRICS.values() for alias in aliases):
            return "financial_metric"
        return "generic"

    @staticmethod
    def _funding_instruments(question: str) -> List[str]:
        compact = re.sub(r"\s+", "", question).upper()
        detected: List[str] = []
        rules = {
            "equity": ("유상증자",),
            "CB": ("CB", "전환사채"),
            "BW": ("BW", "신주인수권부사채", "신주인수권"),
            "EB": ("EB", "교환사채"),
        }
        for instrument, aliases in rules.items():
            if any(alias.upper() in compact for alias in aliases):
                detected.append(instrument)
        return detected or ["equity", "CB", "BW", "EB"]

    @staticmethod
    def _funding_status(question: str) -> str:
        """Separate a financing decision from actual completion evidence."""
        if any(token in question for token in ("실제 조달", "조달 완료", "납입 완료", "납입된", "발행 완료", "발행 결과", "실시한")):
            return "completed"
        if any(token in question for token in ("결정", "계획")):
            return "decision"
        return "any"

    @staticmethod
    def _correction_view(question: str) -> str:
        compact = re.sub(r"\s+", "", question)
        if any(token in compact for token in (
                "정정전후", "정정전·후", "정정전/후", "변경전후", "정정내역",
                "원공시와정정공시", "원본과정정본", "전이랑후",
        )):
            return "before_after"
        if (any(token in compact for token in ("지금", "현재유효", "최신값", "최신유효", "현재값")) or
                ("계약상대" in question and "최신" in question)):
            return "current"
        if any(token in question for token in ("정정 전", "변경 전", "최초")):
            return "original"
        if "계약상대" in question and any(token in question for token in ("공개", "밝혀", "확인됐")):
            return "current"
        return "history"

    @staticmethod
    def _calculation(question: str, compact_question: str, metric: Optional[str], years: List[int]) -> Optional[Dict[str, Any]]:
        for name, spec in DERIVED_CALCULATIONS.items():
            if any(re.sub(r"\s+", "", alias).lower() in compact_question for alias in spec["aliases"]):
                return {
                    "name": name,
                    "operation": spec["operation"],
                    "required_metrics": spec["required_metrics"],
                    "formula": spec["formula"],
                    "derived_metric": name,
                }
        operation = None
        # UI/evaluation labels such as "복합계산" describe the request shape.
        # Their embedded substring "합계" must not imply a sum operation.
        calculation_text = compact_question.replace("복합계산", "")
        year_comparison = any(token in compact_question for token in (
            "전년동기", "전년대비", "전년같은분기", "yoy",
        ))
        amount_and_rate = (
            any(token in compact_question for token in ("금액", "증감액", "증가액", "감소액")) and
            any(token in compact_question for token in ("비율", "증가율", "감소율", "증감률", "몇%"))
        ) or ("얼마" in compact_question and any(token in compact_question for token in ("몇%", "몇퍼센트")))
        if year_comparison and amount_and_rate:
            operation = "growth_amount_and_rate"
        for candidate, aliases in CALCULATION_PATTERNS.items():
            if operation:
                break
            if any(re.sub(r"\s+", "", alias).lower() in calculation_text for alias in aliases):
                operation = candidate
                break
        if operation is None:
            return None
        calculation: Dict[str, Any] = {"operation": operation}
        if operation in {"growth_rate", "growth_amount_and_rate"}:
            calculation["formula"] = "(current - baseline) / abs(baseline) * 100"
            if metric:
                calculation["required_metrics"] = [metric]
            if years:
                target_year = max(years)
                calculation["target_year"] = target_year
                calculation["baseline_year"] = min(years) if len(years) > 1 else target_year - 1
        return calculation

    @staticmethod
    def _period_basis(question: str, groups: List[str]) -> str:
        if any(x in question for x in ("체결", "해지", "공시한", "공시일", "자금조달", "유상증자", "CB", "BW", "EB")):
            return "disclosure_or_event_date"
        if "periodic" in groups or any(x in question for x in ("사업보고서", "반기보고서", "분기보고서", "재무", "매출", "영업이익")):
            return "base_period"
        return "unspecified"

    @staticmethod
    def _period_aggregation(question: str) -> Optional[str]:
        compact = re.sub(r"\s+", "", question).lower()
        if any(token in compact for token in ("누적", "연초이후", "ytd", "1~3분기", "1-3분기")):
            return "ytd"
        if any(token in compact for token in ("3개월", "당분기만", "분기단독", "분기자체")):
            return "three_month"
        return None

    @staticmethod
    def _missing_slots(plan: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        if not plan.get("companies") and not plan.get("cross_corpus"):
            missing.append("company")
        specialized = plan.get("query_type") in {
            "investment_plan", "financing_history", "contract_termination", "business_change", "correction_history",
            "business_overview",
        }
        needs_period = plan.get("query_type") in {
            "investment_plan", "capex_comparison", "financing_history", "contract_termination", "business_change",
        } or (plan.get("query_type") == "financial_metric" and plan.get("doc_groups") == ["periodic"])
        if needs_period and not plan.get("years"):
            missing.append("period")
        if (plan.get("query_type") == "business_change" and plan.get("comparison_axis") != "doc_subtype"
                and len(plan.get("years") or []) < 2):
            missing.append("comparison_periods")
        if plan.get("query_type") == "capex_comparison" and not plan.get("cross_corpus") and len(plan.get("companies") or []) < 2:
            missing.append("comparison_target")
        if plan.get("intent") == "comparison" and not specialized and not plan.get("cross_corpus") and len(plan.get("companies") or []) < 2:
            missing.append("comparison_target")
        if not specialized and plan.get("intent") in {"lookup", "comparison", "calculation"} and not plan.get("metric") and not plan.get("required_metrics"):
            missing.append("metric")
        calculation = plan.get("calculation") or {}
        if calculation.get("operation") == "growth_rate" and not calculation.get("baseline_year"):
            missing.append("baseline_period")
        return list(dict.fromkeys(missing))

    @staticmethod
    def _warnings(question: str, plan: Dict[str, Any]) -> List[str]:
        warnings: List[str] = []
        if "수익률" in question and not any(x in question for x in ("영업이익률", "순이익률", "ROE", "자기자본이익률")):
            warnings.append("ambiguous_return_metric")
        if any(year < 2023 or year > 2026 for year in plan.get("years", [])):
            warnings.append("year_outside_corpus_range")
        if QueryAnalyzer._has_relative_period(question):
            warnings.append("relative_period_needs_explicit_year")
        if plan.get("query_type") == "generic" and QueryAnalyzer._topicless_generic_question(question, plan):
            warnings.append("ambiguous_topic")
        return warnings

    @staticmethod
    def _has_relative_period(question: str) -> bool:
        compact = re.sub(r"\s+", "", question)
        if "최근매출액" in compact:
            return False
        return any(re.search(pattern, question) for pattern in (
            r"최근\s*(?:분기|반기|사업보고서|보고서|공시)",
            r"최신\s*(?:분기|반기|사업보고서|보고서|공시)",
            r"올해|금년|작년|지난해",
        ))

    @staticmethod
    def _topicless_generic_question(question: str, plan: Dict[str, Any]) -> bool:
        """Detect a company/period shell that contains no subject to retrieve.

        Generic narrative questions must remain searchable, so this is
        deliberately narrower than treating every metric-less question as
        incomplete.
        """
        residual = question.casefold()
        for company in plan.get("companies") or []:
            for value in (company.get("corp_name"), company.get("listed_name"), company.get("stock_code")):
                if value:
                    compact_name = re.sub(r"\s+", "", value.casefold())
                    residual = re.sub("".join(f"{re.escape(char)}\\s*" for char in compact_name), "", residual)
        residual = re.sub(r"20\d{2}\s*년(?:도)?|(?<!\d)2[3-6]\s*년(?:도)?", "", residual)
        residual = re.sub(r"[1-4]\s*(?:사분기|/\s*4\s*분기|분기|q)|첫\s*분기", "", residual)
        residual = re.sub(r"사업보고서|반기보고서|분기보고서|공시|자료|연결|별도|개별|기준", "", residual)
        residual = re.sub(r"알려\s*줘|말해\s*줘|뭐야|무엇이야|어때", "", residual)
        residual = re.sub(r"[\s\W_]+", "", residual, flags=re.UNICODE)
        return not residual or residual in {"은", "는", "이", "가", "의", "에", "에서", "를", "을"}
