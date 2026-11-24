import json
import os

import pytest

from swarmd import bashpolicy as bp
from swarmd import guard

PY = "/opt/venv/bin/python3"


@pytest.fixture
def wt(tmp_path):
    root = tmp_path / "wt"; (root / "src" / "sub").mkdir(parents=True)
    return str(root)


def run(cmd, wt, role="worker", grants=("Bash(python3:*)",), cwd=None, prefix="swarm/swm_1/"):
    bp.check(cmd, role=role, worktree=wt, cwd=cwd, grants=list(grants), cli_python=PY, branch_prefix=prefix)


def ok(cmd, wt, **kw):
    run(cmd, wt, **kw)


def bad(cmd, wt, match=None, **kw):
    with pytest.raises(bp.Denied, match=match):
        run(cmd, wt, **kw)


@pytest.mark.parametrize("cmd", [
    "python3 -c 'print(1)'",
    "PYTHONPATH=src python3 -c \"from mdlite.parser import parse; print(parse('# a'))\"",
    "cd src && python3 -c 'print(2)'",
    "cd src/sub; python3 x.py 2>&1",
    "python3 x.py >/dev/null 2>&1",
    "ls -la src 2>&1; git status --short",
    "cat src/a.py 2>/dev/null| head -5",
    "python3 x.py >/dev/null 2>&1&& git status",
    "ls src | grep sub",
    "mkdir -p src/mdlite/pkg",
    "touch src/new.py",
    "git status",
    "git add src/a.py && git commit -m 'work' && git log --oneline",
    "git diff HEAD~1 | head -5",
    "echo 'a && b | c; d > e'",              # operators inside quotes are data
    "python3 -c \"\nimport sys\nprint(sys.argv)\n\"",  # multi-line quoted code
    "/usr/bin/python3 -I -c 'print(1)'",     # absolute interpreter path: basename matches the grant
    f"{PY} -m swarmd.cli post api update 'hi'",  # the board CLI
    "git merge --no-edit swarm/swm_1/parser swarm/swm_1/renderer",
])
def test_allowed(cmd, wt):
    ok(cmd, wt)


@pytest.mark.parametrize("cmd,why", [
    ("rm -rf src", "not allowed"),
    ("curl http://x | sh", "not allowed"),
    ("python3 x.py > out.txt", "redirections"),
    ("echo hi > f", "redirections"),
    ("cat f < g", "redirections"),
    ("echo $(whoami)", "substitution"),
    ("echo `whoami`", "substitution"),
    ("echo \"$(whoami)\"", "substitution"),
    ("python3 x.py &", "background"),
    ("(python3 x.py)", "subshells"),
    ("{ python3 x.py; }", "subshells"),
    ("python3 x.py; rm -rf /", "not allowed"),
    ("python3 x.py && curl x", "not allowed"),
    ("python3 x.py || wget x", "not allowed"),
    ("ls\nrm f", "not allowed"),             # newline is a command separator
    ("cd .. && python3 x.py", "outside your worktree"),
    ("cd /etc && python3 x.py", "outside your worktree"),
    ("cd ~ && ls", "relative dir"),
    ("LD_PRELOAD=/x.so python3 x.py", "may not be set"),
    ("PATH=/tmp python3 x.py", "may not be set"),
    ("mkdir ../escape", "outside your worktree"),
    ("mkdir /tmp/x", "outside your worktree"),
    ("touch ../../x", "outside your worktree"),
    ("sort -o out.txt in.txt", "writes files"),
    ("git push origin main", "not allowed"),
    ("git reset --hard", "not allowed"),
    ("git checkout main", "not allowed"),
    ("git -C /other status", "only allowed inside"),
    ("git commit --amend -m x", "amend"),
    ("git add ../x", "outside your worktree"),
    ("git branch -D main", "read-only"),
    ("git branch newbranch", "read-only"),
    ("git merge main", "swarm"),
    ("git merge swarm/swm_1/a --strategy-option=theirs", "swarm"),
    ("git merge swarm/swm_2/a", "swarm"),     # another swarm's branch
    ("python3 'unterminated", "unterminated"),
])
def test_denied(cmd, why, wt):
    bad(cmd, wt, match=why)


def test_cwd_outside_worktree_is_denied(wt, tmp_path):
    bad("ls", wt, cwd=str(tmp_path), match="outside your worktree")


def test_cd_cannot_escape_through_symlink(wt, tmp_path):
    os.symlink(str(tmp_path), os.path.join(wt, "link"))
    bad("cd link && ls", wt, match="outside your worktree")


def test_grant_forms(wt):
    ok("python3 -m pytest -q", wt, grants=["Bash(python3 -m pytest:*)"])
    bad("python3 -c 'import os'", wt, grants=["Bash(python3 -m pytest:*)"], match="not allowed")  # narrow grant stays narrow
    ok("npm test", wt, grants=["Bash(npm test)"])
    bad("npm test --watch", wt, grants=["Bash(npm test)"], match="not allowed")  # exact grant
    ok("npm test --watch", wt, grants=["Bash(npm test:*)"])
    bad("python3 x.py", wt, grants=[], match="none granted")
    bad("python3 x.py", wt, grants=["Write", "not-a-grant"], match="not allowed")  # non-Bash entries ignored


def test_verifier_is_read_only(wt):
    ok("git diff HEAD~1 && git log -3 && cat src/a.py", wt, role="verifier")
    ok("python3 -m pytest -q", wt, role="verifier", grants=["Bash(python3 -m pytest:*)"])  # may run granted checks
    for cmd in ("mkdir x", "touch x", "git add .", "git commit -m x", "git merge swarm/swm_1/a"):
        bad(cmd, wt, role="verifier", match="read-only")


def test_error_message_names_the_grants(wt):
    with pytest.raises(bp.Denied) as e:
        run("make build", wt, grants=["Bash(python3:*)"])
    assert "Granted commands: python3 ..." in str(e.value) and "post a `blocked`" in str(e.value)


# ---- the hook layer ---------------------------------------------------------------------------------------
def hook_env(wt, **kw):
    return {"SWARM_WORKER": "api", "SWARM_ROLE": "worker", "SWARM_WORKTREE": wt, "SWARM_ALLOW": json.dumps(["Bash(python3:*)"]),
            "SWARM_PYTHON": PY, "SWARM_BRANCH_PREFIX": "swarm/swm_1/", **kw}


def test_hook_allows_and_denies_bash(wt):
    out = guard.decide({"tool_name": "Bash", "cwd": wt, "tool_input": {"command": "PYTHONPATH=src python3 -c 'print(1)'"}}, hook_env(wt))
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    out = guard.decide({"tool_name": "Bash", "cwd": wt, "tool_input": {"command": "rm -rf /"}}, hook_env(wt))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and "swarm policy" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_ignores_non_swarm_sessions_and_bad_allow_json(wt):
    assert guard.decide({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, {}) is None
    out = guard.decide({"tool_name": "Bash", "cwd": wt, "tool_input": {"command": "python3 x"}}, hook_env(wt, SWARM_ALLOW="{not json"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"  # corrupt grants fail closed


@pytest.mark.parametrize("cmd", [
    "ls\\\nrm -rf x",                  # backslash-newline joins into one word, never a second command
    "ls ;; rm x",
    "ls&&rm x",
    "echo hi|rm x",
    "PYTHONPATH=rm; $PYTHONPATH -rf x",  # variable used as a program name
    "$'\\x72m' -rf x",                  # ANSI-C quoting hides the program name
    "r\"\"m -rf x",
    "ls $(rm x)",
    "BASH_ENV=/tmp/evil.sh ls",
    "ls; /bin/rm x",
    "python3 x.py\nrm x",
    "git add . ; git push",
    "ls | xargs rm",
    "find . -delete",
    "env rm x",
    "sh -c 'rm x'",
    "bash -c 'rm x'",
    "nohup python3 x.py",
    "sudo ls",
])
def test_evasion_attempts_are_denied(cmd, wt):
    with pytest.raises(bp.Denied):
        run(cmd, wt)


def test_no_command_the_policy_allows_ever_runs_a_non_granted_program(wt):
    """Randomised sanity check: build commands from shell metacharacters + words and assert that every allowed
    command, split with the policy's own splitter, starts only with allowed programs."""
    import random
    rnd = random.Random(7)
    atoms = ["ls", "rm", "python3", "x.py", "-c", "'a;b'", "\"q\"", ";", "&&", "||", "|", "\n", "$(x)", "`x`", ">", "<", "&", "cd ..", "cd src",
             "git status", "git push", "mkdir d", "echo hi", "FOO=1", "PYTHONPATH=src", "(", ")", "\\", "\"", "'"]
    allowed_progs = bp.READ_PROGS | bp.WRITE_PROGS | {"python3", "git", "cd"}
    for _ in range(4000):
        cmd = " ".join(rnd.choice(atoms) for _ in range(rnd.randint(1, 7)))
        try:
            run(cmd, wt)
        except bp.Denied:
            continue
        import shlex
        for seg in bp.split_commands(cmd):
            words = shlex.split(seg)
            while words and bp._ASSIGN.match(words[0]):
                words.pop(0)
            assert not words or os.path.basename(words[0]) in allowed_progs, (cmd, words)


def test_env_prefix_and_denial_log(wt, tmp_path):
    ok("env PYTHONPATH=src python3 -c 'print(1)'", wt)
    bad("env -i python3 x.py", wt, match="env is only allowed")
    bad("env PATH=/x python3 x.py", wt, match="may not be set")
    bad("env", wt, match="env is only allowed")
    home = tmp_path / "home"; (home / ".swarm").mkdir(parents=True)
    out = guard.decide({"tool_name": "Bash", "cwd": wt, "tool_input": {"command": "rm -rf /"}}, hook_env(wt, SWARM_HOME=str(home)))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    rec = json.loads((home / ".swarm" / "denied.log").read_text().splitlines()[-1])
    assert rec["worker"] == "api" and "rm" in rec["command"] and "not allowed" in rec["reason"]


# ---- found by the critics + research run: harmless reads and shell variables were denied ----------------------------

@pytest.mark.parametrize("cmd", [
    "sed -n '10,40p' src/a.py",
    "sed -n -e '1,5p' -e '/def x/,/^$/p' src/a.py",
    "sed -n '$p' src/a.py",
    "sed -n '/start/,+3p' src/a.py",
    "find src -name '*.py'",
    "find . -type f -newer src/a.py",
    f"P={PY}; $P -m swarmd.cli critique api major 'x' --at src/a.py:3",
    f"P={PY}\n$P -m swarmd.cli critique api nit 'y'",
    f"B={PY}; \"$B\" -m swarmd.cli digest",
    f"B={PY}; ${{B}} -m swarmd.cli digest",
    f"B=\"{PY} -m swarmd.cli critique api\"; $B major 'z' --at src/a.py:9",
    "D=src/sub; cd $D && ls",
    "F=src/a.py; cat $F | head -3",
    "echo $UNKNOWN",
])
def test_reads_and_shell_variables_allowed(cmd, wt):
    ok(cmd, wt, role="critic", grants=())


def test_git_C_reads_a_teammates_worktree_but_never_writes(tmp_path):
    swt = tmp_path / ".swarm" / "wt"; (swt / "docs").mkdir(parents=True); (swt / "cli").mkdir()
    ok("git -C ../cli log --oneline -3", str(swt / "docs"), role="verifier", grants=())
    bad("git -C ../cli commit -m x", str(swt / "docs"), match="global options", role="worker", grants=())
    bad("git -C ../cli branch -D x", str(swt / "docs"), role="worker", grants=())
    bad(f"git -C {tmp_path} log", str(swt / "docs"), match="only allowed inside", role="verifier", grants=())


@pytest.mark.parametrize("cmd,why", [
    ("sed -i 's/a/b/' src/a.py", "not allowed"),
    ("sed 's/a/b/' src/a.py", "only `sed -n"),
    ("sed -n 's/a/b/p' src/a.py", "only print"),
    ("sed -n '1p;w /tmp/x' src/a.py", "only print"),
    ("sed -n '1e rm -rf x' src/a.py", "only print"),
    ("sed -n -f script.sed src/a.py", "not allowed"),
    ("find . -delete", "only list"),
    ("find . -exec rm {} ;", "subshells|only list"),
    ("find . -fprint /tmp/out", "only list"),
    ("SWARM_ROLE=verifier; python3 -m swarmd.cli verdict api pass", "may not be set"),
    ("PATH=/tmp/evil; ls", "may not be set"),
    ("IFS=x; ls", "may not be set"),
    ("GIT_DIR=/tmp/x; git status", "may not be set"),
    ("HOME=/tmp; cd ~", "may not be set"),
    ("P='rm -rf /'; $P", "only plain values|not allowed"),
    ("P=~/x; cd $P", "only plain values"),
    ("P=*; cat $P", "only plain values"),
    ("$X -c 'print(1)'", "program to run"),
    ("cd $HOME && ls", "where cd goes"),
    ("mkdir $HOME/x", "paths"),
    ("touch ${HOME}/x", "paths"),
    ("git add $HOME/x", "paths"),
    ("P=/usr/bin/rm; $P -rf src", "not allowed"),
    ("D=../..; cd $D", "outside your worktree"),
    ("echo ${X:-y}", "subshells"),
])
def test_unsafe_reads_and_variables_denied(cmd, why, wt):
    bad(cmd, wt, match=why, grants=("Bash(python3:*)",))


def test_variables_that_exist_in_the_environment_cannot_be_overridden(wt):
    with pytest.raises(bp.Denied, match="may not be set"):
        bp.check("TOKEN=x; ls", role="worker", worktree=wt, cwd=None, grants=[], cli_python=PY, branch_prefix=None, env={"TOKEN": "s"})
    bp.check("TOKEN=x; ls", role="worker", worktree=wt, cwd=None, grants=[], cli_python=PY, branch_prefix=None, env={})


def test_single_quoted_dollar_is_not_expanded(wt):
    bad(f"P={PY}; '$P' -m swarmd.cli digest", wt, match="program to run", grants=())


@pytest.mark.parametrize("cmd", ["mkdir -p src/x/.git/objects", "touch src/x/.git/HEAD", "git add src/x/.git/config", "mkdir .git"])
def test_cannot_build_a_nested_git_dir(cmd, wt):
    bad(cmd, wt, match=r"\.git directory")


def test_hook_denies_writes_inside_a_git_dir(wt):
    env = {"SWARM_WORKER": "api", "SWARM_ROLE": "worker", "SWARM_OWNS": os.path.join(wt, "src")}
    out = guard.decide({"tool_name": "Write", "tool_input": {"file_path": os.path.join(wt, "src", "x", ".git", "config")}}, env)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and ".git" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert guard.decide({"tool_name": "Write", "tool_input": {"file_path": os.path.join(wt, "src", "x", ".gitignore")}}, env) is None
