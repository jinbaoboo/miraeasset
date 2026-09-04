"""Build 100 questions for correction, contract, and financing lifecycle behavior."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval" / "lifecycle_qa_100_questions.jsonl"
CITATION_FIELDS = ["doc_id", "corp_name", "report_nm", "rcept_no", "record_id", "kind"]


SEEDS = [
    {
        "name": "samsung_current_counterparty", "company": "삼성전자", "years": [2025],
        "question": "삼성전자 2025년 반도체 위탁생산 공급계약의 현재 유효한 계약상대는 누구야?",
        "query_type": "correction_history", "answerability": "answerable", "actions": ["allow"],
        "contains": ["현재 유효", "테슬라", "접수번호"],
        "docs": ["exchange_20250728800035", "exchange_20250731800028"],
    },
    {
        "name": "samsung_before_after", "company": "삼성전자", "years": [2025],
        "question": "삼성전자 2025년 공급계약 원공시와 정정공시의 계약상대 차이는 뭐야?",
        "query_type": "correction_history", "answerability": "answerable", "actions": ["allow"],
        "contains": ["정정 전", "글로벌 대형기업", "테슬라"],
        "docs": ["exchange_20250728800035", "exchange_20250731800028"],
    },
    {
        "name": "samsung_no_termination", "company": "삼성전자", "years": [2025],
        "question": "삼성전자 2025년 공급계약 이후 명확히 연결되는 해지 공시가 있어?",
        "query_type": "contract_termination", "answerability": "answerable", "actions": ["allow"],
        "contains": ["명확히 연결되는 해지 건은 확인되지", "법적 단정"],
        "docs": ["exchange_20250731800028"],
    },
    {
        "name": "hyosung_termination", "company": "효성중공업", "years": [2024],
        "question": "효성중공업이 2024년에 체결한 주요 계약 가운데 이후 해지된 계약이 존재해?",
        "query_type": "contract_termination", "answerability": "answerable", "actions": ["allow"],
        "contains": ["예.", "Hornsea Four", "해지 공시"],
        "docs": ["exchange_20241104800041", "exchange_20250508800712"],
    },
    {
        "name": "lg_energy_unlinked_termination", "company": "LG에너지솔루션", "years": [2025],
        "question": "LG에너지솔루션 2025년 공급계약과 명확히 연결되는 해지 공시가 확인돼?",
        "query_type": "contract_termination", "answerability": "answerable", "actions": ["allow"],
        "contains": ["명확히 연결되는 해지 건은 확인되지", "제공 코퍼스"],
        "docs": ["exchange_20251217800800"],
    },
    {
        "name": "woori_financing_decisions", "company": "우리기술", "years": [2025],
        "question": "우리기술의 2025년 자금조달 결정 내역을 유상증자·CB·BW·EB별로 정리해줘.",
        "query_type": "financing_history", "answerability": "answerable", "actions": ["allow"],
        "contains": ["CB(전환사채): 3건", "37,800,000,000원", "결정 공시"],
        "docs": ["major_20250428000705", "major_20250717000413", "major_20251120000565"],
    },
    {
        "name": "woori_completion_limit", "company": "우리기술", "years": [2025],
        "question": "우리기술이 2025년에 실제 납입 완료한 자금조달 금액은 얼마야?",
        "query_type": "financing_history", "answerability": "unanswerable", "actions": ["limit"],
        "contains": ["실제 조달 완료", "확인할 수 없습니다", "결정 공시"],
        "docs": ["major_20250428000705", "major_20250717000413", "major_20251120000565"],
    },
    {
        "name": "samsung_electro_correction", "company": "삼성전기", "years": [2023],
        "question": "삼성전기 2023년 사업보고서 정정 내용을 원본과 정정본 기준으로 설명해줘.",
        "query_type": "correction_history", "answerability": "answerable", "actions": ["allow"],
        "contains_any": ["정정 전", "정정 후", "정정공시"],
        "docs": ["periodic_20240329002895"],
    },
    {
        "name": "lg_innotek_correction", "company": "LG이노텍", "years": [2024],
        "question": "LG이노텍 2024년 반기보고서 정정 전후와 현재 유효 값을 구분해줘.",
        "query_type": "correction_history", "answerability": "answerable", "actions": ["allow"],
        "contains": ["정정 전", "정정 후", "1,223,131", "534,414"],
        "docs": ["periodic_20240814002436", "periodic_20240913000803"],
    },
    {
        "name": "korea_zinc_correction", "company": "고려아연", "years": [],
        "question": "고려아연 대량보유상황보고서 정정 내용과 현재 유효 값을 설명해줘.",
        "query_type": "correction_history", "answerability": "answerable", "actions": ["allow"],
        "contains_any": ["정정 전", "정정 후", "현재 유효"],
        "docs": ["holding_20250107000540"],
    },
]

WRAPPERS = (
    lambda question, _: f"[기간·버전 감사] {question} 최초·변경·현재 상태를 섞지 말고 답해.",
    lambda question, _: f"추측 금지. {question} 결정일과 후속 공시의 접수번호도 확인해.",
    lambda question, _: f"검증요청={{ {question} }}; 원문 체인에 없는 상태는 단정하지 마.",
    lambda question, decoy: f"[제외 조건: {decoy}, 2022년] {question} 제외 대상은 결과에 포함하지 마.",
    lambda question, _: f"실무자가 다시 묻는다. {question} 계획·정정·완료 상태를 정확히 나눠 줘.",
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _decoy(company: str) -> str:
    return "현대자동차" if company == "삼성전자" else "삼성전자"


def build_cases() -> list[dict]:
    cases = []
    for category in ("close", "open"):
        index = 0
        for seed in SEEDS:
            for variant, wrapper in enumerate(WRAPPERS, start=1):
                index += 1
                case = {
                    "question_id": f"lifecycle-{category}-{index:03d}",
                    "category": category, "format": category,
                    "question": f"[{'판정형' if category == 'close' else '서술형'}] "
                                f"{wrapper(seed['question'], _decoy(seed['company']))}",
                    "source_case_id": seed["name"], "variant": variant,
                    "authorship": "self_authored_lifecycle_stress",
                    "expected_query_type": seed["query_type"],
                    "expected_answerability": seed["answerability"],
                    "expected_validation_actions": seed["actions"],
                    "expected_companies": [seed["company"]],
                    "expected_exact_companies": [seed["company"]],
                    "expected_years": seed["years"],
                    "expected_exact_years": seed["years"],
                    "required_doc_ids": seed["docs"],
                    "required_citation_fields": CITATION_FIELDS,
                    "max_answer_chars": 6000,
                    "max_answer_lines": 40,
                    "expected_answer_contains": seed.get("contains", []),
                    "expected_answer_any": seed.get("contains_any", []),
                }
                cases.append(case)
    if len(cases) != 100:
        raise AssertionError(f"expected 100 cases, got {len(cases)}")
    if len({_normalized(case["question"]) for case in cases}) != 100:
        raise AssertionError("duplicate lifecycle question detected")
    existing = set()
    for path in (ROOT / "eval").glob("*questions.jsonl"):
        if path == OUTPUT:
            continue
        existing.update(_normalized(json.loads(line)["question"])
                        for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if {_normalized(case["question"]) for case in cases} & existing:
        raise AssertionError("exact overlap with existing questions")
    return cases


def main() -> None:
    cases = build_cases()
    OUTPUT.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "total": 100, "close": 50, "open": 50}, ensure_ascii=False))


if __name__ == "__main__":
    main()
