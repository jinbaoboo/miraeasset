import unittest
from decimal import Decimal
from pathlib import Path

from eval.evaluate_agent import expand_cases, load_cases
from eval.evaluate_manual_qa import _answerability_check, _effective_plan, _numeric_expectations


class GoldenEvaluationTests(unittest.TestCase):
    def test_curated_cases_expand_to_150_robustness_queries(self):
        base = load_cases(Path("eval/golden_questions.jsonl"))
        expanded = expand_cases(base)
        self.assertEqual(len(base), 37)
        self.assertEqual(len(expanded), 150)
        self.assertEqual(len({case["question_id"] for case in expanded}), 150)
        self.assertTrue(all(case.get("base_question_id") and case.get("variant_type") for case in expanded))

    def test_strong_gold_numeric_tolerance_uses_display_values(self):
        passed, details = _numeric_expectations(
            "증가액은 260,728백만원이고 증가율은 10.32%입니다.",
            [{"value": 260728, "tolerance": 1}, {"value": 10.3, "tolerance": 0.05}],
        )
        self.assertTrue(passed)
        self.assertEqual(Decimal(details[1]["matched"]), Decimal("10.32"))

    def test_strong_gold_answerability_distinguishes_limit_from_answer(self):
        limited = {"validation": {"action": "limit"}}
        allowed = {"validation": {"action": "allow"}}
        self.assertTrue(_answerability_check("자료를 확인할 수 없습니다.", limited, "unanswerable"))
        self.assertTrue(_answerability_check("근거에 따른 답변입니다.", allowed, "answerable"))
        self.assertFalse(_answerability_check("자료를 확인할 수 없습니다.", limited, "answerable"))

    def test_composite_evaluation_uses_base_analyzer_plan(self):
        response = {"think_trace": {"query_plan": {
            "is_composite": True,
            "base_plan": {"query_type": "financial_metric", "years": [2025]},
            "subtasks": [{"task_id": "task_001"}, {"task_id": "task_002"}],
        }}}
        self.assertEqual(_effective_plan(response)["query_type"], "financial_metric")
        self.assertEqual(_effective_plan(response)["years"], [2025])

    def test_operational_edge_set_covers_runtime_boundaries(self):
        cases = load_cases(Path("eval/operational_edge_questions.jsonl"))
        self.assertEqual(len(cases), 18)
        self.assertEqual(len({case["question_id"] for case in cases}), 18)
        self.assertEqual(
            {case["category"] for case in cases},
            {"input_normalization", "clarification", "multi_intent", "safety_limit"},
        )


if __name__ == "__main__":
    unittest.main()
