"""Functional tests for the fs.* tools using the in-memory fakes."""

from __future__ import annotations

import pytest

from tests.conftest import Harness, acting_as


async def _project(harness: Harness, name: str = "proj-a", owner: str = "alice") -> str:
    await harness.store.create_project(name, owner)
    return name


async def test_write_then_read_roundtrip(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        written = await harness.call("fs.write", mount_id=project, path="/hello.txt", content="line1\nline2\n")
        assert written["bytes_written"] == 12
        read = await harness.call("fs.read", mount_id=project, path="hello.txt")
    assert read["total_lines"] == 2
    assert "1\tline1" in read["content"]


async def test_write_no_clobber(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/a.txt", content="x")
        with pytest.raises(Exception, match="ERR_NO_CLOBBER"):
            await harness.call("fs.write", mount_id=project, path="/a.txt", content="y")
        overwritten = await harness.call("fs.write", mount_id=project, path="/a.txt", content="y", overwrite=True)
    assert overwritten["overwritten"] is True


async def test_edit_requires_prior_read(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/c.txt", content="alpha beta")
        harness.ctx.safety._sessions.clear()  # forget that write recorded a read
        with pytest.raises(Exception, match="ERR_EDIT_WITHOUT_PRIOR_READ"):
            await harness.call("fs.edit", mount_id=project, path="/c.txt", old_string="alpha", new_string="ALPHA")
        await harness.call("fs.read", mount_id=project, path="/c.txt")
        edited = await harness.call("fs.edit", mount_id=project, path="/c.txt", old_string="alpha", new_string="ALPHA")
    assert edited["applied"] is True


async def test_edit_ambiguous_match(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/d.txt", content="x x x")
        await harness.call("fs.read", mount_id=project, path="/d.txt")
        with pytest.raises(Exception, match="ERR_AMBIGUOUS_MATCH"):
            await harness.call("fs.edit", mount_id=project, path="/d.txt", old_string="x", new_string="y")
        result = await harness.call(
            "fs.edit", mount_id=project, path="/d.txt", old_string="x", new_string="y", replace_all=True
        )
    assert "diff" in result


async def test_multi_edit_and_dry_run(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/m.txt", content="a\nb\nc\n")
        await harness.call("fs.read", mount_id=project, path="/m.txt")
        result = await harness.call(
            "fs.multi_edit",
            mount_id=project,
            path="/m.txt",
            edits=[{"old_string": "a", "new_string": "A"}, {"old_string": "b", "new_string": "B"}],
            dry_run=True,
        )
        assert result["applied"] is False
        assert harness.volume.files["/m.txt"] == b"a\nb\nc\n"


async def test_search_replace_and_insert(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/s.txt", content="hello\nworld\n")
        await harness.call("fs.read", mount_id=project, path="/s.txt")
        await harness.call(
            "fs.search_replace", mount_id=project, path="/s.txt", search_block="world", replace_block="earth"
        )
        await harness.call("fs.insert_at_line", mount_id=project, path="/s.txt", line=1, content="TOP")
    assert harness.volume.files["/s.txt"].startswith(b"TOP\n")
    assert b"earth" in harness.volume.files["/s.txt"]


async def test_apply_patch_add_update_delete(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/upd.txt", content="old line\nkeep\n")
        await harness.call("fs.read", mount_id=project, path="/upd.txt")
        await harness.call("fs.write", mount_id=project, path="/del.txt", content="bye")
        await harness.call("fs.read", mount_id=project, path="/del.txt")
        patch = (
            "*** Begin Patch\n"
            "*** Add File: /new.txt\n"
            "+brand new\n"
            "*** Update File: /upd.txt\n"
            "-old line\n"
            "+new line\n"
            "*** Delete File: /del.txt\n"
            "*** End Patch\n"
        )
        result = await harness.call("fs.apply_patch", mount_id=project, patch_text=patch)
    ops = {entry["path"]: entry["op"] for entry in result["files"]}
    assert ops == {"/new.txt": "add", "/upd.txt": "update", "/del.txt": "delete"}
    assert harness.volume.files["/new.txt"] == b"brand new"
    assert b"new line" in harness.volume.files["/upd.txt"]
    assert "/del.txt" not in harness.volume.files


async def test_listing_metadata_and_hash(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/dir/f1.txt", content="abc")
        listing = await harness.call("fs.list_dir", mount_id=project, path="/dir")
        tree = await harness.call("fs.tree", mount_id=project, path="/")
        stat = await harness.call("fs.stat", mount_id=project, path="/dir/f1.txt")
        exists = await harness.call("fs.exists", mount_id=project, path="/dir/f1.txt")
        digest = await harness.call("fs.hash", mount_id=project, path="/dir/f1.txt")
    assert listing["total"] == 1
    assert stat["size"] == 3
    assert exists["exists"] is True
    assert digest["algo"] == "sha256"
    assert any(node["name"] == "dir" for node in tree["tree"])


async def test_delete_trash_move_copy(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/x.txt", content="data")
        deleted = await harness.call("fs.delete", mount_id=project, path="/x.txt")
        assert deleted["trashed"] is True
        await harness.call("fs.write", mount_id=project, path="/y.txt", content="d")
        await harness.call("fs.move", mount_id=project, source="/y.txt", destination="/z.txt")
        await harness.call("fs.copy", mount_id=project, source="/z.txt", destination="/z2.txt")
        await harness.call("fs.mkdir", mount_id=project, path="/sub/deep")
    assert "/z.txt" in harness.volume.files
    assert "/z2.txt" in harness.volume.files
    assert "/sub/deep" in harness.volume.dirs


async def test_grep_and_glob(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/code.py", content="def foo():\n    return 1\n")
        glob = await harness.call("fs.glob", mount_id=project, pattern="*.py")
        grep = await harness.call("fs.grep", mount_id=project, pattern="def ", output_mode="content")
        count = await harness.call("fs.grep", mount_id=project, pattern="return", output_mode="count")
    assert "/code.py" in glob["matches"]
    assert grep["matches"][0]["line"] == 1
    assert count["count"] == 1


async def test_find_definition(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/mod.py", content="def hello():\n    pass\n")
        defs = await harness.call("fs.find_definition", mount_id=project, name="hello")
        refs = await harness.call("fs.find_references", mount_id=project, name="hello")
    assert defs["definitions"][0]["name"] == "hello"
    assert refs["references"][0]["line"] == 1


async def test_read_helpers(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/r.txt", content="a\nb\nc\nd\n")
        await harness.call("fs.read", mount_id=project, path="/r.txt")
        head = await harness.call("fs.head", mount_id=project, path="/r.txt", lines=2)
        tail = await harness.call("fs.tail", mount_id=project, path="/r.txt", lines=2)
        count = await harness.call("fs.count_lines", mount_id=project, path="/r.txt")
        rng = await harness.call("fs.read_lines", mount_id=project, path="/r.txt", start_line=2, end_line=3)
        many = await harness.call("fs.read_many", mount_id=project, paths=["/r.txt", "/missing.txt"])
        chunk = await harness.call("fs.read_bytes", mount_id=project, path="/r.txt", length_bytes=3)
    assert count["total_lines"] == 4
    assert "2\tb" in rng["content"]
    assert "b" in head["content"] and "c" in tail["content"]
    assert any("error" in entry for entry in many["files"])
    assert chunk["length"] == 3


async def test_audit_log_and_allowed_roots(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"):
        await harness.call("fs.write", mount_id=project, path="/a.txt", content="x")
        audit = await harness.call("fs.audit_log", mount_id=project)
        roots = await harness.call("fs.list_allowed_roots", mount_id=project)
    assert audit["entries"][-1]["op"] == "write"
    assert roots["roots"][0]["mount_id"] == project


async def test_quota_exceeded(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"), pytest.raises(Exception, match="ERR_WRITE_QUOTA_EXCEEDED"):
        await harness.call("fs.write", mount_id=project, path="/big.txt", content="x" * 20_000)


async def test_forbidden_for_non_member(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("bob"), pytest.raises(Exception, match="ERR_FORBIDDEN"):
        await harness.call("fs.read", mount_id=project, path="/whatever.txt")


async def test_path_out_of_bounds(harness: Harness) -> None:
    project = await _project(harness)
    with acting_as("alice"), pytest.raises(Exception, match="ERR_PATH_OUT_OF_BOUNDS"):
        await harness.call("fs.read", mount_id=project, path="/a\x00b")
