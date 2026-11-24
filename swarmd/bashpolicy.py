"""Bash command policy for swarm workers and verifiers (PreToolUse hook, see guard.py).

Why: `--allowedTools "Bash(python3:*)"` only matches commands that START with python3, so ordinary commands such as
`PYTHONPATH=src python3 -c ...`, `cd src && python3 ...` or `mkdir -p x` were denied and verifiers could not verify.
This module parses the command itself and allows it only if EVERY simple command in it is permitted.

Parsing is deliberately strict: command substitution ($(...), backticks), unquoted redirections (except `2>&1` and
`>/dev/null`), background `&`, subshells and grouping are rejected. `;`, `&&`, `||`, `|` and newlines split a command
into simple commands that are checked one by one. `cd` is tracked and must stay inside the worker's worktree.

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


def check(command: str, *, role: str, worktree: str, cwd: str | None, grants: list[str], cli_python: str | None,
          branch_prefix: str | None) -> None:
    """Raise Denied (with a model-readable reason) unless every simple command in `command` is allowed."""
    parsed = [g for g in (parse_grant(t) for t in grants) if g]
    cur = cwd or worktree
    if not _inside(cur, worktree):
        raise Denied(f"working directory {cur} is outside your worktree")
    for seg in split_commands(command):
        try:
            words = shlex.split(seg)
        except ValueError:
            raise Denied(f"cannot parse: {seg[:80]}")
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
        prog = os.path.basename(words[0])
        if prog == "cd":
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
        if prog in WRITE_PROGS:
            if role in READ_ONLY_ROLES:
                raise Denied("verifiers and researchers are read-only")
            for a in (x for x in words[1:] if not x.startswith("-")):
                if not _inside(os.path.join(cur, a), worktree):
                    raise Denied(f"{prog} outside your worktree: {a}")
            continue
        if prog == "git":
            _check_git(words, role, cur, worktree, branch_prefix)
            continue
        if _matches(words, parsed):
            continue
        shown = ", ".join(" ".join(p) + (" ..." if pre else "") for p, pre in parsed) or "none granted"
        raise Denied(f"`{words[0]}` is not allowed. Granted commands: {shown}. Always allowed: board CLI, git (status, diff, log, add, commit), "
                     f"mkdir, touch, ls, cat, grep, echo and similar read commands. If you need another command, post a `blocked` event.")


def _check_git(words: list[str], role: str, cur: str, worktree: str, branch_prefix: str | None) -> None:
    if len(words) < 2 or words[1].startswith("-"):
        raise Denied("use `git <subcommand> ...` without global options such as -C or -c")
    sub, args = words[1], words[2:]
    if sub in GIT_READ:
        if sub == "branch" and any(not a.startswith("-") or a in ("-d", "-D", "-m", "-M", "--delete") for a in args):
            raise Denied("git branch is read-only here (list only)")
        return
    if role in READ_ONLY_ROLES:
        raise Denied(f"read-only role: git {sub} is not allowed")
    if sub in GIT_WRITE:
        if sub == "add":
            for a in (x for x in args if not x.startswith("-")):
                if not _inside(os.path.join(cur, a), worktree):
                    raise Denied(f"git add outside your worktree: {a}")
        elif any(a in ("--amend", "-C", "-c") or a.startswith("--author") for a in args):
            raise Denied("git commit --amend / --author are not allowed")
        return
    if sub == "merge" and branch_prefix:
        branches = [a for a in args if not a.startswith("-")]
        flags = [a for a in args if a.startswith("-")]
        if branches and all(b.startswith(branch_prefix) for b in branches) and all(f in GIT_MERGE_FLAGS for f in flags):
            return
        raise Denied(f"git merge is only allowed for this swarm's branches ({branch_prefix}*) with flags {sorted(GIT_MERGE_FLAGS)}")
    raise Denied(f"git {sub} is not allowed (allowed: {', '.join(sorted(GIT_READ | GIT_WRITE))}, and merge of {branch_prefix or 'swarm'}* branches)")
