"""Functional tests for the admin.* tools using the in-memory fakes."""

from __future__ import annotations

import pytest

from tests.conftest import Harness, acting_as


async def test_create_requires_admin(harness: Harness) -> None:
    with acting_as("bob"), pytest.raises(Exception, match="ERR_FORBIDDEN"):
        await harness.call("admin.create_project", project_id="proj-x", owner="bob")


async def test_create_provisions_and_lists(harness: Harness) -> None:
    with acting_as("alice"):
        created = await harness.call("admin.create_project", project_id="proj-x", owner="carol")
        assert created["owner"] == "carol"
        assert "proj-x" in harness.manager.provisioned
        all_projects = await harness.call("admin.list_all_projects")
    assert any(p["project_id"] == "proj-x" for p in all_projects["projects"])


async def test_invalid_project_id(harness: Harness) -> None:
    with acting_as("alice"), pytest.raises(Exception, match="ERR_INVALID_ARGUMENT"):
        await harness.call("admin.create_project", project_id="No_Good", owner="alice")


async def test_member_lifecycle(harness: Harness) -> None:
    with acting_as("alice"):
        await harness.call("admin.create_project", project_id="proj-m", owner="alice")
        await harness.call("admin.add_member", project_id="proj-m", person="bob")
        members = await harness.call("admin.list_members", project_id="proj-m")
        assert {m["person"] for m in members["members"]} == {"alice", "bob"}
    with acting_as("bob"):
        # bob is now a member and can read the volume roots
        roots = await harness.call("fs.list_allowed_roots", mount_id="proj-m")
        assert roots["roots"][0]["mount_id"] == "proj-m"
    with acting_as("alice"):
        await harness.call("admin.remove_member", project_id="proj-m", person="bob")
        members = await harness.call("admin.list_members", project_id="proj-m")
    assert {m["person"] for m in members["members"]} == {"alice"}


async def test_delete_deprovisions(harness: Harness) -> None:
    with acting_as("alice"):
        await harness.call("admin.create_project", project_id="proj-d", owner="alice")
        deleted = await harness.call("admin.delete_project", project_id="proj-d")
    assert deleted["deleted"] is True
    assert "proj-d" in harness.manager.deprovisioned


async def test_list_users_admin_only(harness: Harness) -> None:
    with acting_as("alice"):
        await harness.call("admin.create_project", project_id="proj-u", owner="dora")
        users = await harness.call("admin.list_users")
    persons = {u["person"]: u["is_admin"] for u in users["users"]}
    assert persons.get("alice") is True
    assert persons.get("dora") is False
