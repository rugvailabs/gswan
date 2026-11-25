# minidb eval fixture: hidden checks, copied in only after the run.
# Imports happen inside each test so one missing piece fails only the tests that need it (the score is the pass share).
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

SETUP = """
CREATE TABLE emp (id INTEGER PRIMARY KEY, name TEXT NOT NULL, dept TEXT, salary REAL, age INTEGER);
INSERT INTO emp VALUES (1, 'Ann', 'eng', 120.5, 31), (2, 'bob', 'eng', 99, 45), (3, 'Cy', 'ops', NULL, 28);
INSERT INTO emp (id, name, dept, age) VALUES (4, 'dee', NULL, 52);
INSERT INTO emp VALUES (5, 'Eve', 'ops', 80, NULL);
"""


def mk(setup=SETUP):
    from minidb import Database
    db = Database()
    db.executescript(setup)
    return db


def q(db, sql):
    return db.execute(sql).rows


def err():
    from minidb import SQLError
    return SQLError


class Basics(unittest.TestCase):
    def test_select_star_and_columns(self):
        r = mk().execute("SELECT * FROM emp WHERE id = 1")
        self.assertEqual(r.columns, ["id", "name", "dept", "salary", "age"])
        self.assertEqual(r.rows, [(1, "Ann", "eng", 120.5, 31)])
        self.assertEqual(r.count, 1)

    def test_result_counts_for_changes(self):
        from minidb import Database
        db = Database()
        self.assertEqual(db.execute("CREATE TABLE t (a INTEGER)").count, 0)
        r = db.execute("INSERT INTO t VALUES (1), (2), (3);")
        self.assertEqual((r.columns, r.rows, r.count), ([], [], 3))
        self.assertEqual(db.execute("UPDATE t SET a = a WHERE a > 1").count, 2)
        self.assertEqual(db.execute("DELETE FROM t WHERE a = 1").count, 1)
        self.assertEqual(q(db, "SELECT a FROM t"), [(2,), (3,)])

    def test_keywords_and_identifiers_are_case_insensitive(self):
        db = mk()
        r = db.execute("select NAME as Who from EMP where ID = 2")
        self.assertEqual(r.columns, ["who"])
        self.assertEqual(r.rows, [("bob",)])

    def test_output_names(self):
        r = mk().execute("SELECT id, age + 1, COUNT(*) AS n, salary*2 FROM emp WHERE id = 1 GROUP BY id, age, salary")
        self.assertEqual(r.columns, ["id", "age + 1", "n", "salary*2"])

    def test_select_without_from(self):
        r = mk().execute("SELECT 1 + 2, 'a' || 'b', NULL")
        self.assertEqual(r.rows, [(3, "ab", None)])

    def test_execute_needs_exactly_one_statement(self):
        db = mk()
        with self.assertRaises(err()):
            db.execute("SELECT 1; SELECT 2")
        with self.assertRaises(err()):
            db.execute("  ;  ")

    def test_executescript_returns_results_and_stops_at_error(self):
        from minidb import Database
        db = Database()
        res = db.executescript("CREATE TABLE t (a INTEGER);; INSERT INTO t VALUES (1); SELECT a FROM t;")
        self.assertEqual(len(res), 3)
        self.assertEqual(res[2].rows, [(1,)])
        with self.assertRaises(err()):
            db.executescript("INSERT INTO t VALUES (2); INSERT INTO nope VALUES (1); INSERT INTO t VALUES (3)")
        self.assertEqual(q(db, "SELECT a FROM t"), [(1,), (2,)])


class Lexing(unittest.TestCase):
    def test_strings_comments_and_quotes(self):
        db = mk()
        self.assertEqual(q(db, "SELECT 'it''s' -- a comment\n , 'x;y'"), [("it's", "x;y")])

    def test_semicolon_inside_string_does_not_split(self):
        from minidb import Database
        db = Database()
        db.executescript("CREATE TABLE t (s TEXT); INSERT INTO t VALUES ('a;b'); INSERT INTO t VALUES ('c')")
        self.assertEqual(q(db, "SELECT s FROM t"), [("a;b",), ("c",)])

    def test_bad_tokens(self):
        db = mk()
        for bad in ["SELECT 'open", 'SELECT "x"', "SELECT 12abc", "SELECT @"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                db.execute(bad)

    def test_true_false_and_reals(self):
        self.assertEqual(q(mk(), "SELECT TRUE, FALSE, 1.25, 10"), [(1, 0, 1.25, 10)])


class Types(unittest.TestCase):
    def test_column_types(self):
        from minidb import Database
        db = Database()
        db.execute("CREATE TABLE t (i INTEGER, r REAL, s TEXT)")
        db.execute("INSERT INTO t VALUES (1, 2, 'x')")
        row = q(db, "SELECT i, r, s FROM t")[0]
        self.assertEqual(row, (1, 2.0, "x"))
        self.assertIsInstance(row[1], float)
        for bad in ["INSERT INTO t VALUES (2.0, 1, 'x')", "INSERT INTO t VALUES ('1', 1, 'x')",
                    "INSERT INTO t VALUES (1, 'a', 'x')", "INSERT INTO t VALUES (1, 1, 5)"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                db.execute(bad)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM t").rows, [(1,)])

    def test_not_null_and_primary_key(self):
        db = mk()
        for bad in ["INSERT INTO emp (id) VALUES (9)", "INSERT INTO emp VALUES (NULL, 'z', NULL, NULL, NULL)",
                    "INSERT INTO emp VALUES (1, 'dup', NULL, NULL, NULL)", "UPDATE emp SET id = 2 WHERE id = 1",
                    "UPDATE emp SET name = NULL"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                db.execute(bad)
        self.assertEqual(q(db, "SELECT COUNT(*) FROM emp"), [(5,)])
        self.assertEqual(q(db, "SELECT name FROM emp WHERE id = 1"), [("Ann",)])

    def test_insert_is_all_or_nothing(self):
        db = mk()
        with self.assertRaises(err()):
            db.execute("INSERT INTO emp VALUES (10, 'ok', NULL, NULL, NULL), (11, NULL, NULL, NULL, NULL)")
        with self.assertRaises(err()):
            db.execute("INSERT INTO emp VALUES (12, 'a', NULL, NULL, NULL), (12, 'b', NULL, NULL, NULL)")
        self.assertEqual(q(db, "SELECT COUNT(*) FROM emp WHERE id >= 10"), [(0,)])

    def test_update_is_all_or_nothing_and_uses_old_values(self):
        from minidb import Database
        db = Database()
        db.executescript("CREATE TABLE p (a INTEGER, b INTEGER); INSERT INTO p VALUES (1, 2), (3, 4)")
        db.execute("UPDATE p SET a = b, b = a")
        self.assertEqual(q(db, "SELECT a, b FROM p"), [(2, 1), (4, 3)])
        db.execute("CREATE TABLE k (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO k VALUES (1), (2), (3)")
        db.execute("UPDATE k SET id = id + 10")  # unique after the whole update
        self.assertEqual(q(db, "SELECT id FROM k"), [(11,), (12,), (13,)])
        with self.assertRaises(err()):
            db.execute("UPDATE k SET id = 5")
        self.assertEqual(q(db, "SELECT id FROM k"), [(11,), (12,), (13,)])


class Schema(unittest.TestCase):
    def test_create_drop_if_exists(self):
        db = mk()
        with self.assertRaises(err()):
            db.execute("CREATE TABLE emp (a INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS emp (a INTEGER)")
        self.assertEqual(len(db.execute("SELECT * FROM emp").columns), 5)
        db.execute("DROP TABLE emp")
        with self.assertRaises(err()):
            db.execute("SELECT * FROM emp")
        with self.assertRaises(err()):
            db.execute("DROP TABLE emp")
        db.execute("DROP TABLE IF EXISTS emp")

    def test_bad_schemas(self):
        from minidb import Database
        db = Database()
        for bad in ["CREATE TABLE t (a INTEGER, a TEXT)", "CREATE TABLE t (a INTEGER PRIMARY KEY, b INTEGER PRIMARY KEY)",
                    "CREATE TABLE t (a BLOB)", "CREATE TABLE t ()"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                db.execute(bad)
        db.execute("CREATE TABLE t (a INTEGER PRIMARY KEY NOT NULL, b TEXT NOT NULL)")  # constraints in either order
        db.execute("CREATE TABLE u (a INTEGER NOT NULL PRIMARY KEY)")

    def test_insert_errors(self):
        db = mk()
        for bad in ["INSERT INTO emp (id, nope) VALUES (9, 1)", "INSERT INTO emp (id, id) VALUES (9, 9)",
                    "INSERT INTO emp (id, name) VALUES (9)", "INSERT INTO emp (id, name) VALUES (9, name)",
                    "INSERT INTO nope VALUES (1)"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                db.execute(bad)

    def test_unknown_column(self):
        for bad in ["SELECT nope FROM emp", "SELECT id FROM emp WHERE nope = 1", "UPDATE emp SET nope = 1",
                    "SELECT id FROM emp ORDER BY nope"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                mk().execute(bad)


class Expressions(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(q(mk(), "SELECT 1 + 2 * 3, (1 + 2) * 3, 10 - 4 - 3, 2 * 3 % 4, -2 * 3, 1 + 2 || 'x'"),
                         [(7, 9, 3, 2, -6, "3x")])

    def test_logic_precedence(self):
        self.assertEqual(q(mk(), "SELECT 1 OR 0 AND 0, NOT 0 AND 0, NOT 1 = 2"), [(1, 0, 1)])

    def test_integer_and_float_arithmetic(self):
        self.assertEqual(q(mk(), "SELECT 7 / 2, -7 / 2, 7 % -3, -7 % 3, 7.0 / 2, 1 / 0, 5 % 0, 2 * 1.5"),
                         [(3, -3, 1, -1, 3.5, None, None, 3.0)])

    def test_null_propagation_and_three_valued_logic(self):
        self.assertEqual(q(mk(), "SELECT NULL + 1, NULL = NULL, NULL AND 0, NULL AND 1, NULL OR 1, NULL OR 0, NOT NULL, "
                                 "NULL IS NULL, 1 IS NOT NULL, NULL || 'a'"),
                         [(None, None, 0, None, 1, None, None, 1, 1, None)])

    def test_where_drops_null(self):
        db = mk()
        self.assertEqual(q(db, "SELECT id FROM emp WHERE salary > 90"), [(1,), (2,)])
        self.assertEqual(q(db, "SELECT id FROM emp WHERE NOT salary > 90"), [(5,)])
        self.assertEqual(q(db, "SELECT id FROM emp WHERE dept != 'eng'"), [(3,), (5,)])
        self.assertEqual(q(db, "SELECT id FROM emp WHERE dept IS NULL"), [(4,)])

    def test_in_and_between(self):
        self.assertEqual(q(mk(), "SELECT 2 IN (1, 2), 3 IN (1, 2), 3 IN (1, NULL), 1 IN (1, NULL), NULL IN (1), 3 NOT IN (1, 2), "
                                 "3 NOT IN (1, NULL)"), [(1, 0, None, 1, None, 1, None)])
        self.assertEqual(q(mk(), "SELECT 2 BETWEEN 1 AND 3, 5 BETWEEN 1 AND 3, 5 NOT BETWEEN 1 AND 3, NULL BETWEEN 1 AND 2, "
                                 "5 BETWEEN NULL AND 3, 1.5 BETWEEN 1 AND 2"), [(1, 0, 1, None, None, 1)])
        self.assertEqual(q(mk(), "SELECT id FROM emp WHERE age BETWEEN 30 AND 50 AND dept IN ('eng', 'ops')"), [(1,), (2,)])

    def test_like(self):
        self.assertEqual(q(mk(), "SELECT 'Hello' LIKE 'h%', 'Hello' LIKE 'h_llo', 'Hello' LIKE 'ell', 'a.c' LIKE 'a_c', "
                                 "'abc' LIKE 'a.c', 'x' NOT LIKE 'y', NULL LIKE 'a', '50%' LIKE '50%'"), [(1, 1, 0, 1, 0, 1, None, 1)])
        with self.assertRaises(err()):
            mk().execute("SELECT 5 LIKE '5'")

    def test_comparisons(self):
        self.assertEqual(q(mk(), "SELECT 1 = 1.0, 2 <> 3, 2 != 2, 'a' < 'b', 'B' < 'a', 3 >= 3, 'abc' = 'ABC'"),
                         [(1, 1, 0, 1, 1, 1, 0)])
        for bad in ["SELECT 1 < 'a'", "SELECT 'a' = 1", "SELECT 'a' + 1", "SELECT -'a'", "SELECT id FROM emp WHERE name"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                mk().execute(bad)

    def test_concat_text_forms(self):
        self.assertEqual(q(mk(), "SELECT 1 || '-' || 2.0 || '-' || 2.5, name || '/' || age FROM emp WHERE id = 1"), [("1-2.0-2.5", "Ann/31")])

    def test_functions(self):
        self.assertEqual(q(mk(), "SELECT LOWER('AbC'), upper('x'), LENGTH('hello'), ABS(-3), abs(-2.5), COALESCE(NULL, NULL, 7, 8), "
                                 "LOWER(NULL), COALESCE(NULL)"), [("abc", "X", 5, 3, 2.5, 7, None, None)])
        for bad in ["SELECT nope(1)", "SELECT LENGTH(5)", "SELECT ABS('x')", "SELECT LOWER('a', 'b')", "SELECT COALESCE()",
                    "SELECT SUM(*) FROM emp", "SELECT SUM(COUNT(*)) FROM emp"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                mk().execute(bad)

    def test_syntax_errors(self):
        for bad in ["SELEC 1", "SELECT", "SELECT 1 +", "SELECT (1", "SELECT id FROM emp WHERE", "SELECT 1 2",
                    "SELECT id FROM emp LIMIT -1", "SELECT id FROM emp LIMIT 1.5", "SELECT 1 < 2 < 3", "SELECT 1 NOT 2"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                mk().execute(bad)


class Queries(unittest.TestCase):
    def test_order_by_with_nulls_and_desc(self):
        db = mk()
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY salary"), [(3,), (4,), (5,), (2,), (1,)])
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY salary DESC"), [(1,), (2,), (5,), (3,), (4,)])

    def test_order_by_multiple_terms_and_stability(self):
        db = mk()
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY dept DESC, age"), [(5,), (3,), (1,), (2,), (4,)])
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY dept IS NULL"), [(1,), (2,), (3,), (5,), (4,)])

    def test_order_by_alias_position_and_expression(self):
        db = mk()
        self.assertEqual(q(db, "SELECT id, age * -1 AS neg FROM emp WHERE age IS NOT NULL ORDER BY neg"), [(4, -52), (2, -45), (1, -31), (3, -28)])
        self.assertEqual(q(db, "SELECT name, age FROM emp WHERE age IS NOT NULL ORDER BY 2 DESC"), [("dee", 52), ("bob", 45), ("Ann", 31), ("Cy", 28)])
        self.assertEqual(q(db, "SELECT name FROM emp ORDER BY LOWER(name)"), [("Ann",), ("bob",), ("Cy",), ("dee",), ("Eve",)])
        self.assertEqual(q(db, "SELECT name FROM emp ORDER BY name"), [("Ann",), ("Cy",), ("Eve",), ("bob",), ("dee",)])
        with self.assertRaises(err()):
            db.execute("SELECT id FROM emp ORDER BY 2")

    def test_order_mixed_types(self):
        from minidb import Database
        db = Database()
        db.executescript("CREATE TABLE m (a INTEGER, b TEXT); INSERT INTO m VALUES (1, 'x'), (NULL, NULL), (2, '10'), (3, NULL)")
        self.assertEqual(q(db, "SELECT COALESCE(b, a) FROM m ORDER BY 1"), [(None,), (3,), ("10",), ("x",)])

    def test_limit_offset(self):
        db = mk()
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY id LIMIT 2"), [(1,), (2,)])
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY id LIMIT 2 OFFSET 3"), [(4,), (5,)])
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY id LIMIT 0"), [])
        self.assertEqual(q(db, "SELECT id FROM emp ORDER BY id DESC LIMIT 10 OFFSET 4"), [(1,)])

    def test_distinct(self):
        db = mk()
        self.assertEqual(q(db, "SELECT DISTINCT dept FROM emp"), [("eng",), ("ops",), (None,)])
        self.assertEqual(q(db, "SELECT DISTINCT dept FROM emp ORDER BY dept DESC"), [("ops",), ("eng",), (None,)])
        self.assertEqual(q(db, "SELECT DISTINCT dept AS d FROM emp ORDER BY 1"), [(None,), ("eng",), ("ops",)])
        with self.assertRaises(err()):
            db.execute("SELECT DISTINCT dept FROM emp ORDER BY age")


class Aggregates(unittest.TestCase):
    def test_whole_table(self):
        self.assertEqual(q(mk(), "SELECT COUNT(*), COUNT(salary), SUM(age), AVG(age), MIN(name), MAX(salary), SUM(salary) FROM emp"),
                         [(5, 3, 156, 39.0, "Ann", 120.5, 299.5)])

    def test_empty_input(self):
        r = q(mk(), "SELECT COUNT(*), COUNT(age), SUM(age), AVG(age), MIN(age), MAX(age) FROM emp WHERE id > 100")
        self.assertEqual(r, [(0, 0, None, None, None, None)])

    def test_sum_keeps_int_avg_is_float(self):
        row = q(mk(), "SELECT SUM(age), AVG(id) FROM emp")[0]
        self.assertIsInstance(row[0], int)
        self.assertIsInstance(row[1], float)
        self.assertEqual(row[1], 3.0)

    def test_group_by_in_first_seen_order(self):
        self.assertEqual(q(mk(), "SELECT dept, COUNT(*), SUM(salary) FROM emp GROUP BY dept"),
                         [("eng", 2, 219.5), ("ops", 2, 80.0), (None, 1, None)])

    def test_group_order_is_first_seen_not_sorted(self):
        from minidb import Database
        db = Database()
        db.executescript("CREATE TABLE g (k TEXT, n INTEGER); INSERT INTO g VALUES ('z', 1), ('a', 2), ('z', 3), ('m', 4), ('a', 5)")
        self.assertEqual(q(db, "SELECT k, SUM(n) FROM g GROUP BY k"), [("z", 4), ("a", 7), ("m", 4)])
        self.assertEqual(q(db, "SELECT k, n % 2 AS odd, COUNT(*) FROM g GROUP BY k, n"), [("z", 1, 1), ("a", 0, 1), ("z", 1, 1), ("m", 0, 1), ("a", 1, 1)])

    def test_group_by_with_having_and_order(self):
        db = mk()
        self.assertEqual(q(db, "SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept HAVING COUNT(*) > 1 ORDER BY dept DESC"),
                         [("ops", 2), ("eng", 2)])
        self.assertEqual(q(db, "SELECT dept FROM emp GROUP BY dept ORDER BY MAX(age) DESC"), [(None,), ("eng",), ("ops",)])
        self.assertEqual(q(db, "SELECT dept, MAX(age) - MIN(age) AS spread FROM emp WHERE dept IS NOT NULL GROUP BY dept ORDER BY spread DESC"),
                         [("eng", 14), ("ops", 0)])

    def test_aggregate_errors(self):
        for bad in ["SELECT name, COUNT(*) FROM emp", "SELECT dept, name FROM emp GROUP BY dept",
                    "SELECT * FROM emp GROUP BY dept", "SELECT id FROM emp WHERE COUNT(*) > 1",
                    "SELECT SUM(name) FROM emp", "SELECT dept FROM emp GROUP BY nope",
                    "SELECT dept FROM emp GROUP BY dept HAVING age > 1"]:
            with self.subTest(bad=bad), self.assertRaises(err()):
                mk().execute(bad)

    def test_aggregate_of_expression(self):
        self.assertEqual(q(mk(), "SELECT SUM(age * 2), COUNT(dept || 'x'), MAX(LENGTH(name)) FROM emp"), [(312, 4, 3)])


class Persistence(unittest.TestCase):
    def test_reopen_keeps_everything(self):
        from minidb import Database
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.db")
            db = Database(p)
            db.executescript(SETUP)
            db.execute("DELETE FROM emp WHERE id = 3")
            db2 = Database(p)
            self.assertEqual(db2.execute("SELECT * FROM emp").rows, db.execute("SELECT * FROM emp").rows)
            self.assertIsInstance(db2.execute("SELECT salary FROM emp WHERE id = 2").rows[0][0], float)
            with self.assertRaises(err()):
                db2.execute("INSERT INTO emp VALUES (1, 'dup', NULL, NULL, NULL)")  # constraints survive reopening
            with self.assertRaises(err()):
                db2.execute("INSERT INTO emp VALUES (9, 'x', NULL, NULL, 1.5)")

    def test_failed_statement_is_not_saved_and_corrupt_file_raises(self):
        from minidb import Database
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.db")
            db = Database(p)
            db.executescript("CREATE TABLE t (a INTEGER PRIMARY KEY); INSERT INTO t VALUES (1)")
            with self.assertRaises(err()):
                db.execute("INSERT INTO t VALUES (2), (1)")
            self.assertEqual(Database(p).execute("SELECT a FROM t").rows, [(1,)])
            bad = os.path.join(d, "bad.db")
            with open(bad, "w") as f:
                f.write("this is not a database")
            with self.assertRaises(err()):
                Database(bad)


class CommandLine(unittest.TestCase):
    def run_main(self, argv, stdin=""):
        from minidb.cli import main
        out, er = io.StringIO(), io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(er):
                code = main(argv)
        finally:
            sys.stdin = old
        return code, out.getvalue(), er.getvalue()

    def test_prints_selects(self):
        code, out, er = self.run_main([":memory:", "CREATE TABLE t (a INTEGER, b REAL, c TEXT); INSERT INTO t VALUES (1, 2, 'x'), (NULL, 0.5, NULL); "
                                                   "SELECT * FROM t; SELECT COUNT(*) AS n FROM t"])
        self.assertEqual((code, er), (0, ""))
        self.assertEqual(out, "a|b|c\n1|2.0|x\nNULL|0.5|NULL\nn\n2\n")

    def test_errors_continue_and_exit_1(self):
        code, out, er = self.run_main([":memory:"], "SELECT 1 AS one; SELECT nope FROM t; SELECT 2 AS two")
        self.assertEqual(code, 1)
        self.assertEqual(out, "one\n1\ntwo\n2\n")
        self.assertTrue(er.startswith("error: "), er)

    def test_usage_and_file(self):
        self.assertEqual(self.run_main([])[0], 2)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.db")
            self.assertEqual(self.run_main([p, "CREATE TABLE t (a TEXT); INSERT INTO t VALUES ('hi')"])[0], 0)
            r = subprocess.run([sys.executable, "-m", "minidb", p, "SELECT a FROM t"], capture_output=True, text=True,
                               env=dict(os.environ, PYTHONPATH=os.getcwd()))
            self.assertEqual((r.returncode, r.stdout), (0, "a\nhi\n"))


if __name__ == "__main__":
    unittest.main()
