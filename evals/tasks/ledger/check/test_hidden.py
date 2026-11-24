# ledger eval fixture: hidden checks, copied in only after the run
import contextlib
import datetime
import io
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

from ledger import Entry, balance, by_category, monthly, parse
from ledger.cli import main

TEXT = """# household
2026-01-03 | groceries | -54.20 | Food

2026-01-15 | salary | 3000 | Income
   # indented comment
2026-02-01 | rent | -1200.00 | Housing
2026-02-09 | cinema | -12.5 | fun
2026-01-20 | groceries again | -10 | FOOD
"""


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class Hidden(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(TEXT)

    def tearDown(self):
        os.unlink(self.path)

    def test_parse(self):
        es = parse(TEXT)
        self.assertEqual(len(es), 5)
        self.assertEqual(es[0], Entry(datetime.date(2026, 1, 3), "groceries", Decimal("-54.20"), "food"))
        self.assertEqual(es[1].amount, Decimal("3000"))
        self.assertEqual(es[1].category, "income")
        self.assertEqual(es[3].amount, Decimal("-12.5"))
        self.assertEqual(parse(""), [])
        with self.assertRaises(Exception):
            es[0].amount = Decimal("1")  # frozen

    def test_parse_errors_name_the_line(self):
        cases = {
            "2026-01-03 | x | 1\n": "line 1:",
            "# c\n\n2026-13-01 | x | 1 | a\n": "line 3:",
            "2026-01-03 | x | 1.234 | a\n": "line 1:",
            "2026-01-03 | x | abc | a\n": "line 1:",
            "2026-01-03 |  | 1 | a\n": "line 1:",
            "ok\n": "line 1:",
            "2026-01-03 | x | 1 | a\n2026-1-3 | x | 1 | a\n": "line 2:",
        }
        for text, prefix in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as cm:
                    parse(text)
                self.assertTrue(str(cm.exception).startswith(prefix), str(cm.exception))

    def test_reports(self):
        es = parse(TEXT)
        self.assertEqual(balance(es), Decimal("1723.30"))
        self.assertEqual(balance([]), Decimal("0"))
        bc = by_category(es)
        self.assertEqual(list(bc), ["food", "fun", "housing", "income"])
        self.assertEqual(bc["food"], Decimal("-64.20"))
        m = monthly(es)
        self.assertEqual(list(m), ["2026-01", "2026-02"])
        self.assertEqual(m["2026-01"], Decimal("2935.80"))
        self.assertEqual(m["2026-02"], Decimal("-1212.50"))
        self.assertEqual(monthly([]), {})

    def test_cli_summary_and_monthly(self):
        code, out, _ = run(["summary", self.path])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["food: -64.20", "fun: -12.50", "housing: -1200.00", "income: 3000.00", "total: 1723.30"])
        code, out, _ = run(["monthly", self.path])
        self.assertEqual((code, out.splitlines()), (0, ["2026-01: 2935.80", "2026-02: -1212.50"]))

    def test_cli_errors(self):
        code, out, err = run(["summary", self.path + ".missing"])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: "))
        self.assertEqual(run(["frobnicate", self.path])[0], 1)
        bad = self.path + ".bad"
        with open(bad, "w") as f:
            f.write("2026-01-01 | a | 1 | x\n2026-01-02 | b | oops | y\n")
        try:
            code, _, err = run(["summary", bad])
            self.assertEqual(code, 1)
            self.assertIn("line 2:", err)
            self.assertTrue(err.startswith("error: "))
        finally:
            os.unlink(bad)

    def test_empty_file_prints_zero_total(self):
        empty = self.path + ".empty"
        open(empty, "w").close()
        try:
            self.assertEqual(run(["summary", empty])[1].splitlines(), ["total: 0.00"])
        finally:
            os.unlink(empty)

    def test_runs_as_a_module(self):
        p = subprocess.run([sys.executable, "-m", "ledger", "monthly", self.path], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=os.getcwd()))
        self.assertEqual((p.returncode, p.stdout.splitlines()), (0, ["2026-01: 2935.80", "2026-02: -1212.50"]))


if __name__ == "__main__":
    unittest.main()
