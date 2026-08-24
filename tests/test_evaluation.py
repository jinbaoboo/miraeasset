import unittest
from pathlib import Path

from eval.evaluate_agent import expand_cases, load_cases


class GoldenEvaluationTests(unittest.TestCase):
    def test_curated_cases_expand_to_150_robustness_queries(self):
        base = load_cases(Path("eval/golden_questions.jsonl"))
        expanded = expand_cases(base)
        self.assertEqual(len(base), 37)
        self.assertEqual(len(expanded), 150)
        self.assertEqual(len({case["question_id"] for case in expanded}), 150)
        self.assertTrue(all(case.get("base_question_id") and case.get("variant_type") for case in expanded))


if __name__ == "__main__":
    unittest.main()
