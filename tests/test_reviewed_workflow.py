import subprocess

from personal_memory.models import MemoryInput, Search
from personal_memory.project_identity import normalize_origin, project_identity
from personal_memory.store import MemoryStore


def proposal(title="语言偏好", content="用户喜欢中文", **kwargs):
    return MemoryInput(
        title=title,
        content=content,
        source={"client": "codex", "trigger": "autonomous", "evidence": "用户明确说过"},
        **kwargs,
    )


def test_reviewed_write_keeps_ambiguous_candidates_unwritten(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    first = store.store_reviewed(proposal())
    assert first["disposition"] == "ACCEPT" and first["written"] is True
    assert store.store_reviewed(proposal())["disposition"] == "DROP"
    assert store.review_write(proposal(content="用户改为英语"))["disposition"] == "DEFER"
    assert store.store_reviewed(proposal(content="用户改为英语"))["written"] is False
    assert store.store_reviewed(proposal(title="中文交流"))["disposition"] == "MERGE"
    assert store.status()["total"] == 1


def test_reviewed_write_needs_provenance_and_allows_explicit_supersession(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    unproven = proposal().model_copy(update={"source": {"client": "codex", "trigger": "autonomous"}})
    assert store.store_reviewed(unproven)["reason"] == "missing_autonomous_evidence"
    first = store.store_reviewed(proposal())["memory"]
    replacement = proposal(content="用户改为英语", supersedes=first["id"])
    changed = store.store_reviewed(replacement)
    assert changed["written"] is True
    assert store.history(first["id"])[0]["action"] == "supersede"


def test_assess_reports_partial_without_claiming_entailment(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.store(proposal())
    result = store.assess_evidence(Search(query="中文"), ["用户喜欢中文", "用户住在北京"])
    assert result["status"] == "partial"
    assert [check["lexical_match"] for check in result["checks"]] == [True, False]
    assert store.assess_evidence(Search(query="没有这个词"), ["用户喜欢中文"])["status"] == "insufficient"
    assert store.assess_evidence(Search(query="中文"), ["用户喜欢中文"])["status"] == "review_required"
    store.store(proposal(title="语言", content="用户喜欢中文和英语"))
    limited = store.assess_evidence(Search(query="中文", limit=1), ["用户住在北京"])
    assert limited["status"] == "incomplete_page"
    assert limited["candidate_pool_exhaustive"] is False


def test_project_identity_normalizes_origin_and_worktrees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:Owner/Repo.git"],
        check=True,
    )
    nested = repo / "subdir"
    nested.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "other", str(worktree)],
        check=True,
        capture_output=True,
    )
    first = project_identity(str(repo))
    second = project_identity(str(nested))
    third = project_identity(str(worktree))
    assert first["scope_id"] == second["scope_id"] == third["scope_id"]
    assert first["origin_normalized"] == "github.com/owner/repo"
    assert normalize_origin("https://user:secret@github.com/owner/repo.git") == first["origin_normalized"]
    assert "secret" not in str(first)


def test_project_identity_falls_back_to_path(tmp_path):
    result = project_identity(str(tmp_path))
    assert result["identity_source"] == "local_path"
    assert result["scope_id"].startswith("project:local_path:")
