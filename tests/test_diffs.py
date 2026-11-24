from fastapi.testclient import TestClient

from swarmd import diffs
from swarmd.api import create_app
from swarmd.critics import Critiques
from swarmd.models import WorkerSpec
from swarmd.tasks import Tasks
from test_report import commit, make
from test_runner import git, repo  # noqa: F401

KEY = "sk-" + "a1B2" * 6


def test_diff_lists_files_with_patches_scope_and_counts(repo):
    board, r, w = make(repo)
    commit(w, ["src/api/a.py", "docs/oops.md"])
    d = diffs.worker_diff(board, repo, "api")
    assert d["branch"] == w.branch and d["base"] == w.base_sha and d["note"] is None and d["task"]["status"] == "open"
    by = {f["path"]: f for f in d["files"]}
    assert set(by) == {"src/api/a.py", "docs/oops.md"} and d["totals"] == {"files": 2, "additions": 2, "deletions": 0}
    a = by["src/api/a.py"]
    assert a["status"] == "A" and a["additions"] == 1 and not a["outOfScope"] and a["patch"].startswith("@@") and "+x" in a["patch"]
    assert by["docs/oops.md"]["outOfScope"] is True
    assert d["uncommitted"] == []


def test_diff_modified_deleted_binary_uncommitted_and_truncated(repo, monkeypatch):
    import pathlib
    board, r, w = make(repo)
    wt = pathlib.Path(w.worktree)
    (wt / "README").write_text("changed\n"); (wt / "blob.bin").write_bytes(b"\x00\x01\x02" * 50)
    (wt / "src/api").mkdir(parents=True); (wt / "src/api/big.py").write_text("".join(f"line {i}\n" for i in range(400)))
    (wt / "src/api/cfg.py").write_text(f'KEY = "{KEY}"\ntoken = load()\n')
    commit(w, [])
    (wt / "scratch.txt").write_text("wip")  # not committed
    monkeypatch.setattr(diffs, "MAX_PATCH_CHARS", 500)
    d = diffs.worker_diff(board, repo, "api")
    by = {f["path"]: f for f in d["files"]}
    assert by["README"]["status"] == "M" and by["README"]["deletions"] == 1 and "-x" in by["README"]["patch"]
    assert by["blob.bin"]["binary"] and by["blob.bin"]["patch"] == "" and by["blob.bin"]["additions"] is None
    assert by["src/api/big.py"]["truncated"] and len(by["src/api/big.py"]["patch"]) == 500
    cfg = by["src/api/cfg.py"]
    assert cfg["redacted"] and KEY not in cfg["patch"] and "[REDACTED]" in cfg["patch"] and "+token = load()" in cfg["patch"]  # code stays readable
    assert d["uncommitted"] == ["scratch.txt"]
    git(wt, "rm", "-q", "README"); commit(w, [])
    assert {f["path"]: f["status"] for f in diffs.worker_diff(board, repo, "api")["files"]}["README"] == "D"


def test_diff_carries_findings_and_verdicts_and_handles_no_branch(repo):
    board, r, w = make(repo)
    commit(w, ["src/api/a.py"])
    crit = Critiques(board); crit.begin_round("api"); crit.add("api", "critic-api-security", "blocker", "SQL injection", "src/api/a.py:1")
    r.define(WorkerSpec(name="ui", goal="build ui", owns=["src/ui"]))
    crit.begin_round("ui"); crit.add("ui", "critic-ui-tests", "nit", "no tests")
    t = Tasks(board); t.request_verification("api"); t.submit_verdict("api", False, ["tests fail"])
    d = diffs.worker_diff(board, repo, "api")
    assert d["critiques"] == [{"critic": "critic-api-security", "round": 1, "severity": "blocker", "location": "src/api/a.py:1", "reason": "SQL injection"}]
    assert len(d["verdicts"]) == 1 and d["verdicts"][0]["passed"] is False and "tests fail" in d["verdicts"][0]["text"] and d["verdicts"][0]["by"] == "verify-api"
    ui = diffs.worker_diff(board, repo, "ui")
    assert ui["files"] is None and "no branch yet" in ui["note"] and ui["verdicts"] == [] and ui["totals"]["files"] == 0


def test_diff_endpoint(repo):
    board, r, w = make(repo)
    commit(w, ["src/api/a.py"])
    c = TestClient(create_app(board, "tok")); h = {"Authorization": "Bearer tok"}
    assert c.get("/workers/api/diff").status_code == 401
    assert c.get("/workers/ghost/diff", headers=h).status_code == 404
    body = c.get("/workers/api/diff", headers=h).json()
    assert body["worker"] == "api" and body["files"][0]["path"] == "src/api/a.py" and body["moreFiles"] == 0
