# minidb — specification

A stdlib-only Python package (it must run on Python 3.9+) `minidb/` implementing a small SQL database. Everything below is required.

## Public API (`from minidb import Database, Result, SQLError`)

- `SQLError(Exception)`: raised for every syntax, schema, type or constraint error.
- `Result`: a dataclass with `columns: list[str]`, `rows: list[tuple]`, `count: int`.
  - SELECT: `columns` are the output column names, `rows` the result rows, `count == len(rows)`.
  - INSERT / UPDATE / DELETE: `columns == []`, `rows == []`, `count` = rows inserted / changed / deleted (UPDATE counts rows matched by WHERE).
  - CREATE / DROP: all empty, `count == 0`.
- `Database(path: str | None = None)`: `None` = in memory only. With a path, an existing file is loaded (an unreadable or corrupt file raises `SQLError`), and the whole database is saved to that file after every successful CREATE, DROP, INSERT, UPDATE or DELETE (write a temporary file, then rename it over the old one). Reopening the same path gives the same tables, column definitions, rows and row order. The file format is your choice.
- `Database.execute(sql) -> Result`: exactly one statement (a trailing `;` is fine); zero or several statements raise `SQLError`.
- `Database.executescript(sql) -> list[Result]`: statements separated by `;`; empty statements are skipped. It stops at the first error and raises it; earlier statements stay applied.
- A statement that raises changes nothing (all or nothing per statement).

## Lexical rules

- Keywords are case-insensitive. Identifiers (table, column, alias names) match `[A-Za-z_][A-Za-z0-9_]*`, are case-insensitive and are reported in lowercase.
- Integer literals `42`; real literals need digits on both sides of the dot: `1.5`. A number directly followed by a letter is an error.
- Strings use single quotes; `''` inside a string is one quote. An unterminated string is an error.
- `--` starts a comment to the end of the line.
- `NULL`, `TRUE` (= integer 1), `FALSE` (= integer 0).
- Any other character outside a string (e.g. `"`, `@`, `[`) is an error.

## Values and types

Values are `None` (NULL), `int`, `float` or `str`. Column types:

- `INTEGER` accepts ints only (a float such as 2.0 is rejected).
- `REAL` accepts ints and floats and stores a float.
- `TEXT` accepts strings only.
- Anything else is a `SQLError` ("type mismatch").

NULL is accepted by any column unless it is `NOT NULL` or `PRIMARY KEY`.

## Statements

- `CREATE TABLE [IF NOT EXISTS] name (col TYPE [NOT NULL] [PRIMARY KEY], ...)`
  - Constraints may come in either order.
  - At most one PRIMARY KEY column; its values must be unique and non-NULL.
  - Duplicate column names and creating an existing table are errors, unless the statement says IF NOT EXISTS (then it does nothing).
- `DROP TABLE [IF EXISTS] name`
- `INSERT INTO name [(col, ...)] VALUES (expr, ...), (expr, ...)`
  - Columns left out get NULL.
  - These are errors: an unknown or repeated column, or a wrong number of values in a row.
  - Value expressions may not reference columns.
- `UPDATE name SET col = expr, ... [WHERE cond]`
  - Every SET expression is evaluated against the row as it was before the statement.
  - Constraints are checked on the result.
- `DELETE FROM name [WHERE cond]`
- `SELECT [DISTINCT] (* | expr [AS alias], ...) [FROM name] [WHERE cond] [GROUP BY col, ...] [HAVING cond] [ORDER BY expr [ASC|DESC], ...] [LIMIT n [OFFSET m]]`
  - Without FROM, the query has one row and no columns. `SELECT *` needs FROM.
  - `*` means all columns in table order.
  - Output column name:
    - the alias if given;
    - else the column's name for a bare column;
    - else the expression's source text exactly as written, trimmed (e.g. `COUNT(*)`, `a + 1`).
  - WHERE (and HAVING) keep a row only when the condition is true. NULL and false drop it.
  - Aggregates are not allowed in WHERE.
  - Grouping applies when there is a GROUP BY, a HAVING, or an aggregate in the select list or ORDER BY.
    - GROUP BY takes column names.
    - Outside aggregate arguments, the select list and HAVING may only use the GROUP BY columns.
    - `*` is not allowed.
    - Groups come out in the order their first row appears.
    - Without GROUP BY, the whole input is one group, even when it is empty (so `SELECT COUNT(*) FROM t WHERE 0` gives one row `(0,)`).
  - DISTINCT removes duplicate output rows, keeping the first one.
  - ORDER BY terms are one of:
    - an integer literal: a 1-based output column position, an error if out of range;
    - a bare name equal to an output column name (alias, or column name in the output): that output value;
    - otherwise an expression evaluated on the row (or group).
  - With DISTINCT, only the first two forms are allowed.
  - Sort order is NULL < any number < any text. Numbers compare by value, text by code point.
  - DESC reverses the order, so NULLs come last. Sorting is stable across terms and rows.
  - LIMIT and OFFSET are non-negative integer literals and apply after ordering.
- Unknown table or column: `SQLError`.

## Expressions

Precedence, lowest first:

1. `OR`
2. `AND`
3. `NOT`
4. comparisons: `= != <> < <= > >=`, `IS [NOT] NULL`, `[NOT] IN (e, ...)`, `[NOT] BETWEEN a AND b`, `[NOT] LIKE p`. These do not chain.
5. `+ - ||`
6. `* / %`
7. unary `-` / `+`
8. literals, columns, function calls, `( expr )`

Binary operators are left-associative.

- **NULL:** any arithmetic, comparison, `||`, LIKE, BETWEEN or IN with a NULL operand gives NULL, except as below.
  - `AND`/`OR` use three-valued logic: `NULL AND FALSE = 0`, `NULL OR TRUE = 1`, otherwise NULL.
  - `NOT NULL` is NULL.
  - `IS [NOT] NULL` is always 0 or 1.
  - `x IN (...)` is 1 if some item equals x, else NULL if some item is NULL, else 0.
- **Results:** comparisons and logic return the integers 1/0 (or NULL).
- **Conditions:** a number is true when non-zero. Text used as a condition is an error.
- **Arithmetic:** `+ - * / %` need numbers; text is an error.
  - int with int gives an int, otherwise a float.
  - Integer `/` truncates toward zero (`-7/2 = -3`).
  - Integer `%` takes the sign of the dividend (`-7%3 = -1`, `7%-3 = 1`).
  - Float `%` is `math.fmod`.
  - Division or modulo by zero gives NULL.
  - Unary minus on text is an error.
- **Comparisons:** numbers with numbers (`1 = 1.0` is true), text with text (case-sensitive). Comparing text with a number is an error. `!=` and `<>` are the same.
- **`||`:** concatenates the text forms. An int prints as `str()`, a float as `repr()` (`2.0` -> `'2.0'`).
- **LIKE:** text only (a number is an error). `%` matches any run of characters, `_` exactly one. It is case-insensitive and must match the whole string.
- **Functions** (names case-insensitive; unknown function = error):
  - `LOWER`, `UPPER` and `LENGTH` take text only.
  - `ABS` takes numbers only.
  - The above all take exactly one argument and give NULL for NULL.
  - `COALESCE(a, ...)` takes one or more arguments and returns the first non-NULL.
- **Aggregates:** `COUNT(*)`, `COUNT(e)`, `SUM(e)`, `AVG(e)`, `MIN(e)`, `MAX(e)`.
  - All except `COUNT(*)` ignore NULLs.
  - `COUNT` gives 0 for no values; the others give NULL.
  - `SUM` keeps int if all inputs are ints. `AVG` is always a float.
  - `SUM`/`AVG` on text is an error. `MIN`/`MAX` use the comparison rules above.
  - `*` is only allowed in `COUNT(*)`.
  - Nested aggregates are errors.

## Command line

`python3 -m minidb DBFILE [SQL]`

- Runs the statements in `SQL`, or read from stdin if it is omitted, against `DBFILE`. `:memory:` means no file.
- For each SELECT it prints a header line (column names joined by `|`), then one line per row with values joined by `|`.
  - NULL prints as `NULL`, a float as `repr()`, an int as `str()`, text as is.
  - Other statements print nothing.
- An error prints `error: <message>` to stderr and continues with the next statement.
- Exit code: 0 if no statement failed, 1 otherwise, 2 for wrong arguments (with a usage line on stderr).
- `minidb/cli.py` must define `main(argv: list[str]) -> int` (argv without the program name); `minidb/__main__.py` calls it.

## Tests

Write unittest tests in `tests/` (run with `python3 -m unittest discover -s tests`).
