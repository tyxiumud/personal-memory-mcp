import concurrent.futures
import copy
import json
import sqlite3

import pytest

from personal_memory.archive import decode, encode
from personal_memory.models import MemoryInput, Search
from personal_memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "记忆.sqlite3")


def save(store, **kwargs):
    title = kwargs.pop("title", "Preference")
    content = kwargs.pop("content", "Use Python for tooling")
    return store.store(MemoryInput(title=title, content=content, **kwargs))


def test_scope_isolation_and_global_context(store):
    global_memory = save(store)
    local = save(store, scope="project", scope_id="alpha")
    save(store, scope="project", scope_id="beta")
    domain = save(store, scope="domain", scope_id="learning")
    assert [m["id"] for m in store.search(Search())] == [global_memory["id"]]
    assert {m["id"] for m in store.search(Search(scope="project", scope_id="alpha"))} == {
        global_memory["id"],
        local["id"],
    }
    assert [
        m["id"] for m in store.search(Search(scope="domain", scope_id="learning", include_global=False))
    ] == [domain["id"]]


def test_english_chinese_and_safe_queries(store):
    english = save(store)
    chinese = store.store(MemoryInput(title="编程偏好", content="用户喜欢使用中文交流和轻量工具"))
    assert store.search(Search(query="python"))[0]["id"] == english["id"]
    assert store.search(Search(query="中文交流"))[0]["id"] == chinese["id"]
    assert store.search(Search(query="中"))[0]["id"] == chinese["id"]
    assert store.search(Search(query='" OR 1=1 --')) == []
    assert store.search(Search(query="***")) == []


def test_exact_active_duplicate_is_not_inserted(store):
    first = save(store, source={"client": "codex", "trigger": "autonomous"})
    duplicate = save(store, source={"client": "deepseek-harness", "trigger": "autonomous"})
    assert duplicate["id"] == first["id"]
    assert duplicate["deduplicated"] is True
    assert store.status()["total"] == 1
    assert len(store.history(first["id"])) == 1


def test_exact_active_duplicate_without_trigger_is_not_inserted(store):
    first = save(store, source={"client": "importer"})
    duplicate = save(store, source={"client": "another-importer"})
    assert duplicate["id"] == first["id"]
    assert duplicate["deduplicated"] is True
    assert store.status()["total"] == 1


def test_revision_conflict_history_and_fts_update(store):
    record = save(store)
    updated = store.update(record["id"], {"content": "Use Rust"}, 1)
    assert updated["revision"] == 2
    assert store.search(Search(query="Python")) == []
    assert len(store.search(Search(query="Rust"))) == 1
    with pytest.raises(ValueError, match="Revision conflict"):
        store.update(record["id"], {"content": "stale"}, 1)
    history = store.history(record["id"])
    assert [row["action"] for row in history] == ["update", "store"]
    assert history[-1]["memory"]["content"] == record["content"]


def test_validity_and_supersession(store):
    old = save(store, valid_from="2020-01-01T00:00:00Z")
    new = save(store, supersedes=old["id"], valid_from="2021-01-01T08:00:00+08:00")
    assert store.search(Search(as_of="2020-12-31T23:59:59Z"))[0]["id"] == old["id"]
    assert store.search(Search(as_of="2021-01-01T00:00:00Z"))[0]["id"] == new["id"]
    assert store.history(old["id"])[0]["action"] == "supersede"
    future = save(
        store,
        title="Future preference",
        content="Use a future-only tool",
        valid_from="2099-01-01T00:00:00Z",
    )
    assert future["id"] not in {r["id"] for r in store.search(Search())}


def test_failed_supersession_rolls_back(store):
    old = save(store, valid_from="2020-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="same scope"):
        save(store, supersedes=old["id"], scope="project", scope_id="other")
    assert store.history(old["id"])[0]["revision"] == 1
    assert store.status()["total"] == 1


def test_forget_excludes_retrieval_but_retains_history(store):
    record = save(store)
    result = store.forget(record["id"], 1)
    assert result["history_retained"] is True
    assert store.search(Search()) == []
    assert store.context(Search())["memories"] == []
    assert store.export_bundle()["memories"] == []
    assert store.export_bundle(True)["memories"][0]["id"] == record["id"]
    assert store.history(record["id"])[0]["action"] == "forget"
    with pytest.raises(ValueError, match="forgotten"):
        store.update(record["id"], {"content": "restore"}, 2)


@pytest.mark.parametrize(
    "fields",
    [
        {"scope": "project"},
        {"scope_id": "oops"},
        {"confidence": 1.1},
        {"importance": float("nan")},
        {"valid_from": "2026-01-01"},
        {"valid_from": "2026-02-01T00:00:00Z", "valid_to": "2026-01-01T00:00:00Z"},
        {"type": "other"},
        {"tags": [" "]},
    ],
)
def test_invalid_metadata_rejected(store, fields):
    with pytest.raises(ValueError):
        save(store, **fields)
    assert store.status()["total"] == 0


def test_context_budget_and_pagination(store):
    for index in range(3):
        save(store, title=f"Preference {index}", content=f"Use tool {index} for tooling")
    context = store.context(Search(), 900)
    assert len(json.dumps(context["memories"], ensure_ascii=False)) <= 900
    assert context["omitted_from_page"] > 0
    first = store.search(Search(limit=1))
    second = store.search(Search(limit=1, offset=1))
    assert first[0]["id"] != second[0]["id"]


@pytest.mark.parametrize("format", ["json", "markdown"])
def test_archive_round_trip_with_history_and_fences(store, tmp_path, format):
    record = store.store(
        MemoryInput(title="中文", content="```json\n{}\n```\n````\n", source={"model": "test"})
    )
    store.update(record["id"], {"importance": 0.9}, 1)
    forgotten = save(store)
    store.forget(forgotten["id"], 1)
    bundle = store.export_bundle(True)
    restored = MemoryStore(tmp_path / "restored.sqlite3")
    result = restored.import_bundle(decode(encode(bundle, format), format))
    assert result["inserted"] == 2
    assert restored.history(record["id"]) == store.history(record["id"])
    assert restored.export_bundle(True)["memories"] == bundle["memories"]
    assert restored.import_bundle(bundle)["skipped_identical"] == 2


def test_import_is_atomic_on_conflict(store, tmp_path):
    record = save(store)
    other = MemoryStore(tmp_path / "other.sqlite3")
    other.import_bundle(store.export_bundle())
    save(other)
    store.update(record["id"], {"content": "new value"}, 1)
    with pytest.raises(ValueError, match="Import conflict"):
        store.import_bundle(other.export_bundle())
    assert store.status()["total"] == 1


def test_invalid_history_rolls_back(store, tmp_path):
    save(store)
    bundle = copy.deepcopy(store.export_bundle())
    bundle["history"][0]["memory"]["content"] = "tampered snapshot"
    restored = MemoryStore(tmp_path / "invalid.sqlite3")
    with pytest.raises(ValueError, match="snapshot"):
        restored.import_bundle(bundle)
    assert restored.status()["total"] == 0


def test_concurrent_clients_and_lost_update_protection(store):
    record = save(store)

    def writer(index):
        client = MemoryStore(store.path)
        try:
            return client.update(record["id"], {"content": str(index)}, 1)["revision"]
        except ValueError:
            return "conflict"

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(writer, range(4)))
    assert results.count(2) == 1
    assert results.count("conflict") == 3
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_newer_schema_refused(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="newer"):
        MemoryStore(path)


def test_supersession_cannot_branch(store):
    old = save(store, valid_from="2020-01-01T00:00:00Z")
    save(store, supersedes=old["id"], valid_from="2022-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="already has a replacement"):
        save(store, supersedes=old["id"], valid_from="2021-01-01T00:00:00Z")
    assert store.status()["total"] == 2
