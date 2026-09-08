"""Store tests: the goal-slug filename scheme
`<goal-slug>__<capability_id>.json`, with the id after `__` staying the key
load()/list_ids() use, and legacy bare `<id>.json` files still resolving.
"""

from __future__ import annotations

import pytest

from backend import store


def _files():
    return sorted(p.name for p in store.ARTIFACTS_DIR.glob("*.json"))


def test_filename_embeds_goal_slug(make_capability):
    path = store.save(make_capability(goal="Create a ParaBank account for a new user"))
    assert path.name == "create-a-parabank-account-for-a-new-user__cap_abc123def456.json"


def test_empty_goal_falls_back_to_bare_id(make_capability):
    assert store.save(make_capability(goal="")).name == "cap_abc123def456.json"


def test_roundtrip_load_by_id(make_capability):
    cap = make_capability(goal="Pay a saved payee")
    store.save(cap)
    assert store.load(cap.id) == cap


def test_load_resolves_legacy_bare_file(make_capability):
    cap = make_capability(id="cap_legacy00000", goal="whatever")
    store.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    (store.ARTIFACTS_DIR / "cap_legacy00000.json").write_text(
        cap.model_dump_json(indent=2), encoding="utf-8"
    )
    assert store.load("cap_legacy00000") == cap


def test_load_missing_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        store.load("cap_does_not_exist")


def test_list_ids_returns_bare_ids(make_capability):
    store.save(make_capability(id="cap_slugged00001", goal="Open an account"))
    (store.ARTIFACTS_DIR / "cap_legacy00002.json").write_text(
        make_capability(id="cap_legacy00002").model_dump_json(), encoding="utf-8"
    )
    assert store.list_ids() == ["cap_legacy00002", "cap_slugged00001"]


def test_resave_with_changed_goal_replaces_stale_file(make_capability):
    cap = make_capability(goal="First goal")
    store.save(cap)
    store.save(cap.model_copy(update={"goal": "Second goal"}))
    assert _files() == ["second-goal__cap_abc123def456.json"]
