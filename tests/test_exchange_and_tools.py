import tempfile
import unittest
from pathlib import Path

from src.parser.exchange_parser import ExchangeParser
from src.parser.event_normalizer import mask_sensitive_text
from src.tools.calculator import calculate


class ExchangeParserTests(unittest.TestCase):
    def test_html_form_correction_and_structured_field(self):
        html = """<html><head><meta charset='euc-kr'></head><body>
        <table><tr><td>정정항목</td><td>정정전</td><td>정정후</td></tr>
        <tr><td>계약상대</td><td>비공개</td><td>테스트사</td></tr></table>
        <table><tr><td rowspan='3'>2. 계약내역</td><td>계약금액(원)</td><td>1,234,000</td></tr>
        <tr><td>최근매출액(원)</td><td>10,000,000</td></tr>
        <tr><td>매출액대비(%)</td><td>12.34</td></tr></table></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / "raw/exchange/테스트/1"; folder.mkdir(parents=True)
            (folder / "1.xml").write_text(html, encoding="utf-8")
            record = {"doc_id": "exchange_1", "corp_code": "00000001", "corp_name": "테스트",
                      "listed_name": "테스트", "report_nm": "[기재정정]단일판매ㆍ공급계약체결",
                      "rcept_no": "1", "rcept_dt": "20250101", "doc_group": "exchange",
                      "doc_subtype": "단일판매공급계약체결", "base_year": None, "base_month": None,
                      "is_correction": True, "file_path": "raw/exchange/테스트/1", "file_format": "xml"}
            result = ExchangeParser(root).parse_document(record)
        self.assertEqual(result["parse_log"]["status"], "success")
        self.assertEqual(result["corrections"][0]["correction_items"][0]["after"]["original_text"], "테스트사")
        amount = next(field for field in result["events"][0]["fields"] if field["field_key"] == "contract_amount_krw")
        self.assertEqual(amount["numeric_value"], 1234000)


class PrivacyTests(unittest.TestCase):
    def test_sensitive_identifiers_are_masked_in_search_text(self):
        value = mask_sensitive_text("주민번호 900101-1234567 전화 010-1234-5678 a@b.com")
        self.assertNotIn("900101-1234567", value)
        self.assertNotIn("010-1234-5678", value)
        self.assertNotIn("a@b.com", value)


class CalculatorTests(unittest.TestCase):
    def test_growth_rate_keeps_audit_formula(self):
        result = calculate("growth_rate", [120, 100])
        self.assertEqual(result["result_float"], 20.0)
        self.assertIn("abs(100)", result["formula"])

    def test_missing_is_not_zero(self):
        with self.assertRaises(ValueError):
            calculate("sum", [1, None])


if __name__ == "__main__":
    unittest.main()
