import unittest
from decimal import Decimal
from pathlib import Path

from eval.evaluate_agent import expand_cases, load_cases
from eval.evaluate_manual_qa import _answerability_check, _numeric_expectations


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


if __name__ == "__main__":
    unittest.main()
