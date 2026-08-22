import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from src.parser.correction_parser import parse_correction
from src.parser.table_parser import TableParser, build_grid, infer_scope, parse_numeric, parse_unit
from src.parser.xml_recovery import parse_xml_file, recover_xml_text


SOURCE = {
    "doc_id": "periodic_test", "corp_code": "00000000", "corp_name": "테스트",
    "listed_name": "테스트", "report_nm": "분기보고서 (2023.03)",
    "rcept_no": "20230000000000", "rcept_dt": "20230515", "doc_group": "periodic",
    "doc_subtype": "quarter", "base_year": 2023, "base_month": 3,
    "is_correction": False, "file_path": "raw/periodic/test",
}


class RecoveryTests(unittest.TestCase):
    def test_bare_ampersand_and_angle_text_are_recovered_in_memory(self):
        xml = """<?xml version='1.0' encoding='utf-8'?>
        <DOCUMENT><BODY><SECTION-1><TITLE>II. 사업의 내용</TITLE>
        <P>R&D 및 S&P 자료</P><P>< TV 시장점유율 추이 ></P>
        </SECTION-1></BODY></DOCUMENT>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xml"
            path.write_text(xml, encoding="utf-8")
            result = parse_xml_file(path)
            self.assertFalse(result.strict_xml_valid)
            self.assertTrue(result.repair_applied)
            self.assertIn("R&D", "".join(result.root.itertext()))
            self.assertIn("< TV 시장점유율 추이 >", "".join(result.root.itertext()))
            self.assertEqual(path.read_text(encoding="utf-8"), xml)

    def test_duplicate_attribute_quotes_are_recovered(self):
        xml = '<DOCUMENT><BODY><TABLE><TR><TH ENG=""Other receivables">값</TH><TH ENG="Other"">값2</TH><TH ENG="　"Proceeds from disposal">값3</TH></TR></TABLE></BODY></DOCUMENT>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.xml"; path.write_text(xml, encoding="utf-8")
            result = parse_xml_file(path)
            self.assertTrue(result.repair_applied)
            self.assertEqual(result.repair_counts["malformed_attribute_quotes"], 3)
            self.assertEqual("".join(result.root.itertext()), "값값2값3")

    def test_duplicate_attribute_quote_before_spaced_value_is_recovered(self):
        source = '<DOCUMENT><P ENG="" KB Insurance Co., Ltd ">한글</P><P CLASS="" USERMARK="B">정상</P></DOCUMENT>'
        repaired, counts, _ = recover_xml_text(source)
        root = ET.fromstring(repaired)
        paragraphs = list(root)
        self.assertEqual(paragraphs[0].attrib["ENG"], " KB Insurance Co., Ltd ")
        self.assertEqual(paragraphs[1].attrib["CLASS"], "")
        self.assertGreaterEqual(counts["malformed_attribute_quotes"], 1)

    def test_quoted_acronym_inside_attribute_is_recovered(self):
        source = '<DOCUMENT><P ENG="Financial assets ("FVTPL")">값</P></DOCUMENT>'
        repaired, counts, _ = recover_xml_text(source)
        root = ET.fromstring(repaired)
        self.assertEqual(root[0].attrib["ENG"], 'Financial assets ("FVTPL")')
        self.assertEqual(counts["malformed_attribute_quotes"], 2)

    def test_mismatched_quoted_acronym_inside_attribute_is_recovered(self):
        source = '<DOCUMENT><P ENG="Financial assets ("FVOCI\') values">값</P></DOCUMENT>'
        repaired, counts, _ = recover_xml_text(source)
        root = ET.fromstring(repaired)
        self.assertEqual(root[0].attrib["ENG"], 'Financial assets ("FVOCI") values')
        self.assertEqual(counts["malformed_attribute_quotes"], 2)


class TableTests(unittest.TestCase):
    def test_korean_and_foreign_currency_scales(self):
        self.assertEqual(parse_unit(["(단위 : 십억원)"])["scale"], 1_000_000_000)
        self.assertEqual(parse_unit(["(단위 : 조원)"])["scale"], 1_000_000_000_000)
        usd = parse_unit(["(단위 : 천USD)"])
        self.assertEqual((usd["currency"], usd["scale"]), ("USD", 1_000))

    def test_conflicting_consolidated_and_separate_title_stays_unknown(self):
        scope, evidence = infer_scope("연결 및 별도 재무제표", ["III. 재무"], "NT_C")
        self.assertEqual(scope, "unknown")
        self.assertIn("conflicting_scope", evidence)

    def test_rowspan_colspan_and_multilevel_headers(self):
        table = ET.fromstring("""
        <TABLE><THEAD>
          <TR><TH ROWSPAN='2'>항목</TH><TH COLSPAN='2'>제55기 반기</TH></TR>
          <TR><TH>3개월</TH><TH>누적</TH></TR>
        </THEAD><TBODY>
          <TR><TD>영업이익</TD><TD>668,547</TD><TD>1,308,725</TD></TR>
        </TBODY></TABLE>""")
        physical = build_grid(table)
        self.assertEqual(len(physical.grid[0]), 3)
        self.assertEqual(physical.grid[0][0].rowspan, 2)
        parser = TableParser(SOURCE, "sample.xml", "main")
        logical, cells = parser.parse_logical_table(
            "periodic_test:main:table:1", [table], None,
            ["III. 재무에 관한 사항", "2. 연결재무제표", "연결 손익계산서"],
            preceding_text="연결 손익계산서 (단위 : 백만원)",
        )
        self.assertEqual(logical["scope"], "consolidated")
        self.assertEqual(logical["unit"]["scale"], 1_000_000)
        self.assertEqual(cells[1]["column_path"], ["제55기 반기", "누적"])
        self.assertEqual(cells[1]["numeric_value"], 1_308_725)

    def test_missing_and_negative_values_are_distinct(self):
        self.assertIsNone(parse_numeric("-")["numeric_value"])
        self.assertEqual(parse_numeric("△13,045")["numeric_value"], -13045)
        self.assertEqual(parse_numeric("10.9%")["value_type"], "percent")


class CorrectionTests(unittest.TestCase):
    def test_before_after_and_current_effective_value(self):
        root = ET.fromstring("""
        <DOCUMENT><BODY><CORRECTION>
          <P>1. 정정대상 공시서류 : 사업보고서</P>
          <P>2. 정정대상 공시서류의 최초제출일 : 2024년 03월 12일</P>
          <TABLE><THEAD><TR><TH>항목</TH><TH>정정사유</TH><TH>정정 전</TH><TH>정정 후</TH></TR></THEAD>
          <TBODY><TR><TD>리스부채</TD><TD>오류</TD><TD>100</TD><TD>120</TD></TR></TBODY></TABLE>
        </CORRECTION></BODY></DOCUMENT>""")
        source = dict(SOURCE, doc_id="periodic_correction", is_correction=True)
        records = parse_correction(root, source, {
            "original_doc_id": "periodic_original", "supersedes_doc_id": "periodic_original",
            "superseded_by_doc_id": None, "is_latest_version": True,
        })
        item = records[0]["correction_items"][0]
        self.assertEqual(item["before"]["original_text"], "100")
        self.assertEqual(item["after"]["original_text"], "120")
        self.assertEqual(item["current_effective_value"]["source"], "after")

    def test_superseded_correction_does_not_claim_old_after_as_current(self):
        root = ET.fromstring("""
        <DOCUMENT><BODY><CORRECTION><TABLE><THEAD><TR>
        <TH>항목</TH><TH>정정 전</TH><TH>정정 후</TH></TR></THEAD><TBODY>
        <TR><TD>매출</TD><TD>100</TD><TD>120</TD></TR></TBODY></TABLE>
        </CORRECTION></BODY></DOCUMENT>""")
        source = dict(SOURCE, doc_id="periodic_correction_1", is_correction=True)
        item = parse_correction(root, source, {
            "original_doc_id": "periodic_original", "supersedes_doc_id": "periodic_original",
            "superseded_by_doc_id": "periodic_correction_2", "is_latest_version": False,
        })[0]["correction_items"][0]
        self.assertEqual(item["current_effective_value"]["source"], "superseded_by")
        self.assertEqual(item["current_effective_value"]["doc_id"], "periodic_correction_2")
        self.assertIsNone(item["current_effective_value"]["original_text"])


if __name__ == "__main__":
    unittest.main()
