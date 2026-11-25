"""Bash command policy for swarm workers and verifiers (PreToolUse hook, see guard.py).

Why: `--allowedTools "Bash(python3:*)"` only matches commands that START with python3, so ordinary commands such as
`PYTHONPATH=src python3 -c ...`, `cd src && python3 ...` or `mkdir -p x` were denied and verifiers could not verify.
This module parses the command itself and allows it only if EVERY simple command in it is permitted.

Parsing is deliberately strict: command substitution ($(...), backticks), unquoted redirections (except `2>&1` and
`>/dev/null`), background `&`, subshells and grouping are rejected. `;`, `&&`, `||`, `|` and newlines split a command
into simple commands that are checked one by one. `cd` is tracked and must stay inside the worker's worktree.

Shell variables: a segment made only of assignments (`P=/path/to/python3`) sets a variable for the rest of the command,
and later `$P` / `${P}` are expanded before the next segments are checked, so what runs is what was judged. Values must
be plain text (no quotes, `$`, globs, `~` or backslashes) and names that already exist in the environment (SWARM_*, PATH,
HOME, ...) or that change how the shell or tools behave are refused. An unknown `$VAR` is not allowed where it would
decide a path (`cd`, mkdir/touch, git add) or the program to run.

Read-only extras (found by a real run, where critics and verifiers were denied harmless reads): `sed -n` with print-only
scripts, `find` without actions (-exec, -delete, ...), and `git -C <dir>` with a read subcommand inside this swarm's worktrees.

Loops and edits (found by the orders-bugs eval, where a worker was denied `for f in shop/*.py; do cat -n $f; done`,
`sed -i` and `git stash`):
- `for NAME in WORDS; do ...; done` is unrolled: every body command is checked once per word, with $NAME replaced by it.
  No nested loops, no `cd` in the body, no `$` in the words.
- `sed -i` (workers only) edits files the worker OWNS, with substitution scripts only (`s/a/b/g`; flags g, I and digits).
  Other sed commands could write (`w`) or, in GNU sed, run (`e`) anything. A backup suffix is not allowed (`-i ''` is).
- `git restore --source REV <paths>` / `git checkout REV -- <paths>` on owned paths, e.g. to check that a new test fails on
  the old code. `git stash` stays denied: the stash is shared by every worktree of the repo, so one worker's
  `git stash pop` could apply another worker's changes.

Granted tools come from `define_worker(tools=[...])` as `Bash(prog:*)` / `Bash(prog sub:*)` / exact `Bash(full cmd)`.
A grant such as `python3` is still arbitrary code execution (python3 -c can do anything): grant narrowly
(`Bash(python3 -m pytest:*)`). This policy removes friction; it is not a sandbox.
"""
from __future__ import annotations

import os
import re
import shlex

ENV_OK = {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "NODE_ENV", "CI", "PYTEST_ADDOPTS", "TZ", "LC_ALL", "LANG"}
READ_PROGS = {"ls", "pwd", "cat", "head", "tail", "wc", "echo", "grep", "diff", "sort", "uniq", "true", "false", "date",
              "which", "basename", "dirname", "printf", "stat", "file"}
WRITE_PROGS = {"mkdir", "touch"}  # every path argument must stay inside the worktree
GIT_READ = {"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "branch", "blame", "shortlog"}
GIT_WRITE = {"add", "commit"}
GIT_MERGE_FLAGS = {"--no-edit", "--no-ff", "--ff", "--ff-only", "--no-commit"}
_REDIRECT_OK = re.compile(r"(?<=\s)(?:\d?>&\d|\d?>>?\s?/dev/null|&>\s?/dev/null)(?=[\s;|&]|$)")  # may be followed directly by ; | && (found by a real run: `ls 2>&1; git status`)
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@%+=, -]*$")
_SHELL_NAMES = {"PATH", "IFS", "HOME", "CDPATH", "ENV", "BASH_ENV", "PS1", "PS2", "PS4", "PROMPT_COMMAND", "SHELL", "SHELLOPTS",
                "BASHOPTS", "TMPDIR", "PWD", "OLDPWD", "USER", "LOGNAME", "EDITOR", "VISUAL", "PAGER", "GLOBIGNORE", "HISTFILE",
                "FPATH", "ZDOTDIR", "TERM", "OPTIND", "OPTARG", "MAIL", "MAILPATH", "PROMPT", "RPROMPT", "precmd", "preexec"}
_SHELL_PREFIXES = ("SWARM_", "CLAUDE", "ANTHROPIC", "GIT_", "LD_", "DYLD_", "PYTHON", "NODE", "NPM_", "npm_", "BASH", "ZSH", "SSH_",
                   "UV_", "PIP_", "VIRTUAL_ENV", "CONDA", "LC_", "XDG_")
_SED_PRINT = re.compile(r"^\s*(?:(?:\d+|\$|/[^/]*/)(?:\s*,\s*(?:\d+|\$|/[^/]*/|\+\d+))?\s*)?p\s*$")
_SED_FLAGS = {"-n", "-E", "-r", "--quiet", "--silent", "-s", "-u"}
_REV = re.compile(r"^[A-Za-z0-9_./~^@{}-]+$")
_FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}


READ_ONLY_ROLES = ("verifier", "researcher", "critic")


class Denied(Exception):
    pass


def split_commands(cmd: str) -> list[str]:
    """Split on unquoted ; && || | and newlines. Raises Denied for anything we refuse to reason about."""
    cmd = _REDIRECT_OK.sub(" ", " " + cmd).strip()
    segs, cur, quote, i, n = [], [], None, 0, len(cmd)
    while i < n:
        ch, nxt = cmd[i], cmd[i + 1] if i + 1 < n else ""
        if quote == "'":
            if ch == "'":
                quote = None
            cur.append(ch)
        elif quote == '"':
            if ch == "\\" and nxt:
                cur.append(ch + nxt); i += 2; continue
            if ch == "`" or (ch == "$" and nxt == "("):
                raise Denied("command substitution is not allowed")
            if ch == '"':
                quote = None
            cur.append(ch)
        else:
            if ch in "'\"":
                quote = ch; cur.append(ch)
            elif ch == "\\" and nxt:
                cur.append(ch + nxt); i += 2; continue
            elif ch == "`" or (ch == "$" and nxt == "("):
                raise Denied("command substitution is not allowed")
            elif ch in "<>":
                raise Denied("redirections are not allowed (only 2>&1 and >/dev/null)")
            elif ch == "$" and nxt == "{" and (m := _VAR.match(cmd, i)) and m.group(1):
                cur.append(m.group(0)); i = m.end(); continue
            elif ch in "(){}":
                raise Denied("subshells and grouping are not allowed")
            elif ch == "&":
                if nxt != "&":
                    raise Denied("background commands (&) are not allowed")
                segs.append("".join(cur)); cur = []; i += 2; continue
            elif ch == "|":
                segs.append("".join(cur)); cur = []; i += 2 if nxt == "|" else 1; continue
            elif ch in ";\n":
                segs.append("".join(cur)); cur = []
            else:
                cur.append(ch)
        i += 1
    if quote:
        raise Denied("unterminated quote")
    segs.append("".join(cur))
    return [s.strip() for s in segs if s.strip()]


def parse_grant(tool: str) -> tuple[list[str], bool] | None:
    """'Bash(python3 -m pytest:*)' -> (['python3','-m','pytest'], prefix=True); 'Bash(ls)' -> (['ls'], False)."""
    m = re.fullmatch(r"Bash\((.*)\)", tool.strip())
    if not m:
        return None
    body, prefix = m.group(1), False
    if body.endswith(":*"):
        body, prefix = body[:-2], True
    try:
        return shlex.split(body), prefix
    except ValueError:
        return None


def _inside(path: str, root: str) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _matches(words: list[str], grants: list[tuple[list[str], bool]]) -> bool:
    for pat, prefix in grants:
        if not pat or len(words) < len(pat):
            continue
        head = [os.path.basename(words[0])] + words[1:len(pat)]
        want = [os.path.basename(pat[0])] + pat[1:]
        if head == want and (prefix or len(words) == len(pat)):
            return True
    return False


def _expand(seg: str, shell_vars: dict[str, str]) -> str:
    """Replace $NAME / ${NAME} of variables set earlier in this command, outside single quotes (as the shell does)."""
    out, quote, i = [], None, 0
    while i < len(seg):
        ch = seg[i]
        if ch == "\\" and quote != "'" and i + 1 < len(seg):
            out.append(seg[i:i + 2]); i += 2; continue
        if ch in "'\"" and (quote is None or quote == ch):
            quote = None if quote else ch
        elif ch == "$" and quote != "'" and (m := _VAR.match(seg, i)):
            name = m.group(1) or m.group(2)
            if name in shell_vars:
                out.append(shell_vars[name]); i = m.end(); continue
        out.append(ch); i += 1
    return "".join(out)


def _settable(name: str, env) -> bool:
    if name in ENV_OK:
        return True
    return not (name in env or name in _SHELL_NAMES or name.startswith(_SHELL_PREFIXES))


def _no_var(words: list[str], what: str) -> None:
    if any("$" in w for w in words):
        raise Denied(f"an unknown $variable may not decide {what}; set it earlier in the same command (NAME=value) or write the value")


def _owned(path: str, owns: list[str]) -> bool:
    return any(_inside(path, o) for o in owns)


def _unroll(segs: list[str]) -> list[str]:
    """`for NAME in WORDS` / `do CMD` / ... / `done` segments -> the body segments once per word, $NAME replaced."""
    out, i = [], 0
    while i < len(segs):
        seg = segs[i]
        try:
            words = shlex.split(seg)
        except ValueError:
            raise Denied(f"cannot parse: {seg[:80]}")
        if words[:1] in (["do"], ["done"]):
            raise Denied("`do`/`done` outside a for loop")
        if words[:1] != ["for"]:
            out.append(seg); i += 1; continue
        if len(words) < 4 or words[2] != "in" or not _ASSIGN.match(words[1] + "="):
            raise Denied("only `for NAME in WORD ...; do ...; done` loops are allowed")
        name, items = words[1], words[3:]
        if any("$" in w for w in items):
            raise Denied("for-loop words may not contain $variables")
        body, i, closed = [], i + 1, False
        while i < len(segs):
            b = segs[i].strip()
            i += 1
            if b == "done":
                closed = True
                break
            if b == "do":
                continue
            if b.startswith("do ") and not body:
                b = b[3:].strip()
            first = b.split(None, 1)[0] if b else ""
            if first in ("for", "while", "until", "if", "do", "done"):
                raise Denied("nested loops or conditionals are not allowed inside a for loop")
            if first == "cd":
                raise Denied("cd is not allowed inside a for loop")
            body.append(b)
        if not closed or not body:
            raise Denied("for loop without `do ...; done`")
        for item in items:
            out.extend(_expand(b, {name: shlex.quote(item)}) for b in body)
    return out


def check(command: str, *, role: str, worktree: str, cwd: str | None, grants: list[str], cli_python: str | None,
          branch_prefix: str | None, env=None, owns: list[str] | None = None) -> None:
    """Raise Denied (with a model-readable reason) unless every simple command in `command` is allowed.
    `env` is the environment the shell runs with (default: this process's), used to refuse overriding existing variables."""
    env = os.environ if env is None else env
    parsed = [g for g in (parse_grant(t) for t in grants) if g]
    cur = cwd or worktree
    if not _inside(cur, worktree):
        raise Denied(f"working directory {cur} is outside your worktree")
    owns = owns or []
    shell_vars: dict[str, str] = {}
    for seg in _unroll(split_commands(command)):
        seg = _expand(seg, shell_vars)
        try:
            words = shlex.split(seg)
        except ValueError:
            raise Denied(f"cannot parse: {seg[:80]}")
        if words and all(_ASSIGN.match(w) for w in words):  # `P=value` alone: a shell variable for later segments
            for w in words:
                name, value = w.split("=", 1)
                if not _settable(name, env):
                    raise Denied(f"variable {name} may not be set (it exists in the environment or changes how the shell or tools behave); use another name")
                if not _SAFE_VALUE.match(value):
                    raise Denied(f"variable {name}: only plain values are allowed (no quotes, $, *, ?, ~ or backslashes)")
                shell_vars[name] = value
            continue
        while words and (m := _ASSIGN.match(words[0])):
            if m.group(1) not in ENV_OK:
                raise Denied(f"environment variable {m.group(1)} may not be set (allowed: {', '.join(sorted(ENV_OK))})")
            words.pop(0)
        if words and os.path.basename(words[0]) == "env":  # `env VAR=val prog args` is just an env prefix
            words.pop(0)
            while words and (m := _ASSIGN.match(words[0])):
                if m.group(1) not in ENV_OK:
                    raise Denied(f"environment variable {m.group(1)} may not be set (allowed: {', '.join(sorted(ENV_OK))})")
                words.pop(0)
            if not words or words[0].startswith("-"):
                raise Denied("env is only allowed as `env VAR=value command ...`")
        if not words:
            continue
        _no_var(words[:1], "the program to run")
        prog = os.path.basename(words[0])
        if prog == "cd":
            _no_var(words[1:], "where cd goes")
            if len(words) != 2 or words[1].startswith("~") or words[1] == "-":
                raise Denied("use `cd <relative dir>` inside your worktree")
            cur = os.path.realpath(os.path.join(cur, words[1]))
            if not _inside(cur, worktree):
                raise Denied(f"cd outside your worktree: {words[1]}")
            continue
        if cli_python and len(words) >= 3 and os.path.realpath(words[0]) == os.path.realpath(cli_python) and words[1:3] == ["-m", "swarmd.cli"]:
            continue  # the board CLI (post, digest, mention, verdict...) — role checks live in the CLI itself
        if prog in READ_PROGS:
            if prog == "sort" and any(a.startswith("-o") for a in words[1:]):
                raise Denied("sort -o writes files")
            continue
        if prog == "sed":
            _check_sed(words, role, cur, owns)
            continue
        if prog == "find":
            if bad := [a for a in words[1:] if a in _FIND_ACTIONS]:
                raise Denied(f"find {bad[0]} is not allowed (find may only list files)")
            continue
        if prog in WRITE_PROGS:
            if role in READ_ONLY_ROLES:
                raise Denied("verifiers and researchers are read-only")
            _no_var(words[1:], f"{prog}'s paths")
            _no_git_dir(words[1:])
            for a in (x for x in words[1:] if not x.startswith("-")):
                if not _inside(os.path.join(cur, a), worktree):
                    raise Denied(f"{prog} outside your worktree: {a}")
            continue
        if prog == "git":
            _check_git(words, role, cur, worktree, branch_prefix, owns)
            continue
        if _matches(words, parsed):
            continue
        shown = ", ".join(" ".join(p) + (" ..." if pre else "") for p, pre in parsed) or "none granted"
        raise Denied(f"`{words[0]}` is not allowed. Granted commands: {shown}. Always allowed: board CLI, git (status, diff, log, add, commit), "
                     f"mkdir, touch, ls, cat, grep, echo and similar read commands. If you need another command, post a `blocked` event.")


def _check_restore(sub: str, args: list[str], cur: str, owns: list[str]) -> None:
    """`git restore [--source REV] [--staged] [--worktree] [--] paths` or `git checkout REV -- paths`, owned paths only."""
    paths: list[str] = []
    if sub == "checkout":
        if "--" not in args or args.index("--") != 1 or args[0].startswith("-") or not _REV.match(args[0]):
            raise Denied("switching branches is not allowed; git checkout only as `git checkout <rev> -- <your files>`")
        paths = args[2:]
    else:
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--":
                paths.extend(args[i + 1:]); break
            if a in ("-s", "--source") and i + 1 < len(args):
                rev = args[i + 1]; i += 2
            elif a.startswith("--source="):
                rev = a.split("=", 1)[1]; i += 1
            elif a in ("--staged", "-S", "--worktree", "-W"):
                i += 1; continue
            elif a.startswith("-"):
                raise Denied(f"git restore {a} is not allowed")
            else:
                paths.append(a); i += 1; continue
            if rev.startswith("-") or not _REV.match(rev):
                raise Denied("git restore --source needs a plain revision")
    _no_var(paths, "which files git restores")
    if not paths:
        raise Denied(f"git {sub} needs the files to restore")
    _no_git_dir(paths)
    for p in paths:
        if not _owned(os.path.join(cur, p), owns):
            raise Denied(f"git {sub} is not allowed on files you do not own: {p}")


def _no_git_dir(paths: list[str]) -> None:
    """A nested .git/ that a worker built (config with core.fsmonitor or hooks) would make a later `git status` run any command."""
    if any(".git" in os.path.normpath(p).split(os.sep) for p in paths if not p.startswith("-")):
        raise Denied("creating or adding files inside a .git directory is not allowed")


def _subst_only(script: str) -> bool:
    """True if the sed script is one or more `s<d>regex<d>replacement<d>flags` commands (flags g, I, digits)."""
    i, n, count = 0, len(script), 0
    while True:
        while i < n and script[i] in " \t;\n":
            i += 1
        if i >= n:
            return count > 0
        if script[i] != "s" or i + 1 >= n:
            return False
        d = script[i + 1]
        if d.isalnum() or d in "\\\n ;":
            return False
        i, parts = i + 2, 0
        while parts < 2:
            if i >= n:
                return False
            if script[i] == "\\":
                i += 2; continue
            if script[i] == d:
                parts += 1
            i += 1
        while i < n and (script[i] in "gI" or script[i].isdigit()):
            i += 1
        if i < n and script[i] not in " \t;\n":
            return False
        count += 1


def _check_sed(words: list[str], role: str = "worker", cur: str = "", owns: list[str] | None = None) -> None:
    """Read-only sed (`sed -n '10,40p' f`, `sed -n '/def x/,/^$/p' f`), or, for workers, `sed -i` substitutions on owned files."""
    args, scripts, files, i, inplace = words[1:], [], [], 0, False
    while i < len(args):
        a = args[i]
        if a == "-e" and i + 1 < len(args):
            scripts.append(args[i + 1]); i += 2; continue
        if a in ("-i", "--in-place"):
            inplace = True
            if a == "-i" and i + 1 < len(args) and args[i + 1] == "":  # BSD sed: `-i ''` (no backup)
                i += 1
            i += 1; continue
        if a.startswith("-i") or a.startswith("--in-place="):
            raise Denied("sed -i with a backup suffix is not allowed (it writes another file); use `sed -i '' ...` or `sed -i ...`")
        if a.startswith("-"):
            if a not in _SED_FLAGS:
                raise Denied(f"sed {a} is not allowed (only `sed -n '<lines>p' file` for reading, or `sed -i 's/a/b/' <owned file>`)")
        elif not scripts:
            scripts.append(a)
        else:
            files.append(a)
        i += 1
    if inplace:
        if role in READ_ONLY_ROLES:
            raise Denied("read-only role: sed -i is not allowed")
        if not scripts or not all(_subst_only(x) for x in scripts):
            raise Denied("sed -i may only run substitutions (`s/old/new/` with flags g, I or a number); edit the file with your editor tool otherwise")
        _no_var(files, "which files sed -i edits")
        if not files:
            raise Denied("sed -i needs the files to edit")
        for f in files:
            if not _owned(os.path.join(cur, f), owns or []):
                raise Denied(f"sed -i is not allowed on files you do not own: {f}")
        _no_git_dir(files)
        return
    if "-n" not in args and "--quiet" not in args and "--silent" not in args:
        raise Denied("only `sed -n '<lines>p' file` is allowed (reading lines)")
    parts = [p for s in scripts for p in s.split(";") if p.strip()]
    if not parts or not all(_SED_PRINT.match(p) for p in parts):
        raise Denied("sed scripts may only print lines (e.g. '10,40p' or '/start/,/end/p')")


def _check_git(words: list[str], role: str, cur: str, worktree: str, branch_prefix: str | None, owns: list[str] | None = None) -> None:
    if len(words) >= 4 and words[1] == "-C" and words[3] in GIT_READ and words[3] != "branch":
        target = os.path.join(cur, words[2])
        swarm_wt = os.path.dirname(os.path.realpath(worktree))  # .swarm/wt holds this swarm's worktrees (a verifier reads a teammate's)
        if _inside(target, worktree) or (os.path.basename(swarm_wt) == "wt" and _inside(target, swarm_wt)):
            return  # read subcommands only, and only inside this swarm's worktrees
        raise Denied(f"git -C is only allowed inside this swarm's worktrees: {words[2]}")
    if len(words) < 2 or words[1].startswith("-"):
        raise Denied("use `git <subcommand> ...` without global options such as -C or -c (git -C <dir> is allowed for reads)")
    sub, args = words[1], words[2:]
    if sub in GIT_READ:
        if sub == "branch" and any(not a.startswith("-") or a in ("-d", "-D", "-m", "-M", "--delete") for a in args):
            raise Denied("git branch is read-only here (list only)")
        return
    if role in READ_ONLY_ROLES:
        raise Denied(f"read-only role: git {sub} is not allowed")
    if sub in GIT_WRITE:
        if sub == "add":
            _no_var(args, "git add's paths")
            _no_git_dir(args)
            for a in (x for x in args if not x.startswith("-")):
                if not _inside(os.path.join(cur, a), worktree):
                    raise Denied(f"git add outside your worktree: {a}")
        elif any(a in ("--amend", "-C", "-c") or a.startswith("--author") for a in args):
            raise Denied("git commit --amend / --author are not allowed")
        return
    if sub in ("restore", "checkout"):
        _check_restore(sub, args, cur, owns or [])
        return
    if sub == "stash":
        raise Denied("git stash is shared by every worker's worktree, so it is not allowed. To run tests against the original "
                     "code, use `git restore --source <base> -- <your files>`, then `git restore --source HEAD -- <your files>` "
                     "to go back (or read it with `git show <base>:<path>`).")
    if sub == "merge" and branch_prefix:
        branches = [a for a in args if not a.startswith("-")]
        flags = [a for a in args if a.startswith("-")]
        if branches and all(b.startswith(branch_prefix) for b in branches) and all(f in GIT_MERGE_FLAGS for f in flags):
            return
        raise Denied(f"git merge is only allowed for this swarm's branches ({branch_prefix}*) with flags {sorted(GIT_MERGE_FLAGS)}")
    raise Denied(f"git {sub} is not allowed (allowed: {', '.join(sorted(GIT_READ | GIT_WRITE))}, and merge of {branch_prefix or 'swarm'}* branches)")
