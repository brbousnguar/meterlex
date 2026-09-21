"""Which project a reply counts toward: its repository, and the nested
repository it worked on when the session started in a parent one."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import select

from models import Machine, UsageTurn
import ingest
import machines

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("meterlex_collector_attr", ROOT / "collector" / "meterlex_collector.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


@pytest.fixture()
def server(tmp_path):
    """~/Server is a repository holding other repositories."""
    mc._root_cache.clear()
    base = tmp_path / "Server"
    for repo in (base, base / "webapps" / "minerva", base / "apps" / "localingo",
                 base / "apps" / "localingo" / ".build" / "checkouts" / "swift-log"):
        (repo / ".git").mkdir(parents=True)
    (base / "webapps" / "minerva" / "frontend" / "src").mkdir(parents=True)
    (base / "notes").mkdir()
    return base


def line(uuid, msg_id, cwd, tools=(), branch="main"):
    content = [{"type": "tool_use", "name": n, "input": i} for n, i in tools]
    return {
        "type": "assistant", "uuid": uuid, "sessionId": "s1", "timestamp": "2026-09-21T10:00:00Z",
        "cwd": str(cwd), "gitBranch": branch, "entrypoint": "cli",
        "message": {"id": msg_id, "model": "claude-opus-5", "content": content,
                    "usage": {"input_tokens": 1, "output_tokens": 1}},
    }


def read(tmp_path, rows, state=None):
    fp = tmp_path / "projects" / "p" / "s1.jsonl"
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "a") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    state = state if state is not None else {"files": {}, "snapshots": {}}
    return {t["turn_key"]: t for t in mc.read_claude_code(tmp_path / "projects", state, False)}, state


def test_a_folder_rolls_up_to_its_repository(server):
    assert mc.project_root(str(server / "webapps" / "minerva" / "frontend" / "src")) == str(server / "webapps" / "minerva")
    assert mc.project_root(str(server / "notes")) == str(server)


def test_a_dependency_checkout_counts_toward_the_project_using_it(server):
    dep = server / "apps" / "localingo" / ".build" / "checkouts" / "swift-log" / "Sources"
    assert mc.project_root(str(dep)) == str(server / "apps" / "localingo")


def test_a_folder_the_repository_ignores_is_its_own_project(server):
    import subprocess
    subprocess.run(["git", "init", "-q", str(server)], check=True)
    (server / ".gitignore").write_text("scratch/\ndist/\n")
    app = server / "scratch" / "time-tracker"
    (app / "src").mkdir(parents=True)
    (server / "scratch" / "other-app" / ".git").mkdir(parents=True)   # scratch/ is a shelf of projects
    (server / "dist" / "assets").mkdir(parents=True)                    # dist/ holds no repository
    mc._root_cache.clear()
    mc._ignore_cache.clear()
    assert mc.project_root(str(app / "src")) == str(app)
    assert mc.project_root(str(server / "scratch")) == str(server)          # the shelf itself
    assert mc.project_root(str(server / "dist" / "assets")) == str(server)  # build output: part of it
    assert mc.project_root(str(server / "notes")) == str(server)            # not ignored: part of it
    assert mc.resolve_stored(str(server / "scratch" / "gone-app")) is None  # gone: keeps its name


def test_a_touched_file_counts_by_its_folder_and_cd_stops_at_the_command_end(server, tmp_path):
    turns, _ = read(tmp_path, [
        line("u1", "m1", server, [("Edit", {"file_path": str(server / "CLAUDE.md")})]),
        line("u2", "m2", server, [("Bash", {"command": "cd webapps/minerva; npm test"})]),
    ])
    assert (turns["m1"]["project"], turns["m2"]["project"]) == (str(server), str(server / "webapps" / "minerva"))


def test_a_folder_outside_every_repository_or_gone_stays_as_it_is(tmp_path):
    mc._root_cache.clear()
    assert mc.project_root(str(tmp_path / "loose")) == str(tmp_path / "loose")
    assert mc.project_root("") == ""


def test_edits_in_a_nested_repository_count_toward_it(server, tmp_path):
    f = server / "webapps" / "minerva" / "frontend" / "src" / "App.tsx"
    turns, _ = read(tmp_path, [line("u1", "m1", server, [("Edit", {"file_path": str(f)})])])
    assert turns["m1"]["project"] == str(server / "webapps" / "minerva")
    assert turns["m1"]["branch"] == "main"


def test_a_cd_into_a_nested_repository_counts_toward_it(server, tmp_path):
    turns, _ = read(tmp_path, [line("u1", "m1", server, [("Bash", {"command": "cd apps/localingo && swift build"})])])
    assert turns["m1"]["project"] == str(server / "apps" / "localingo")


def test_a_reply_without_tools_stays_with_the_repository_just_worked_on(server, tmp_path):
    f = str(server / "webapps" / "minerva" / "README.md")
    turns, state = read(tmp_path, [
        line("u1", "m1", server, [("Read", {"file_path": f})]),
        line("u2", "m2", server),                                   # explains the change
    ])
    assert turns["m2"]["project"] == str(server / "webapps" / "minerva")
    # the next pass resumes with the same focus
    turns, _ = read(tmp_path, [line("u3", "m3", server)], state)
    assert turns["m3"]["project"] == str(server / "webapps" / "minerva")


def test_a_worktree_counts_toward_its_main_checkout(server, tmp_path):
    main = server / "webapps" / "minerva"
    wt = server / "webapps" / "minerva-wt" / "42"
    wt.mkdir(parents=True)
    (main / ".git" / "worktrees" / "42").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / '42'}\n")
    assert mc.project_root(str(wt / "src")) == str(main)


def test_a_worktree_that_is_gone_counts_toward_its_repository(server):
    gone = server / "webapps" / "minerva-wt" / "7" / "frontend"
    assert mc.project_root(str(gone)) == str(server / "webapps" / "minerva")


def test_a_repository_outside_the_working_folder_counts(server, tmp_path):
    other = tmp_path / "Projects" / "brb-git-cli"
    (other / ".git").mkdir(parents=True)
    turns, _ = read(tmp_path, [line("u1", "m1", server, [("Edit", {"file_path": str(other / "src" / "a.js")})])])
    assert turns["m1"]["project"] == str(other)


def test_scratch_files_name_no_project(server, tmp_path):
    turns, _ = read(tmp_path, [
        line("u1", "m1", server, [("Edit", {"file_path": str(server / "webapps" / "minerva" / "a.ts")})]),
        line("u2", "m2", server, [("Write", {"file_path": "/private/tmp/scratch/notes.md"})]),
    ])
    assert turns["m2"]["project"] == str(server / "webapps" / "minerva")


def test_work_on_the_parent_repository_itself_ends_the_focus(server, tmp_path):
    turns, _ = read(tmp_path, [
        line("u1", "m1", server, [("Edit", {"file_path": str(server / "webapps" / "minerva" / "a.ts")})]),
        line("u2", "m2", server, [("Edit", {"file_path": str(server / "CLAUDE.md")})]),
        line("u3", "m3", server),
    ])
    assert [turns[k]["project"] for k in ("m1", "m2", "m3")] == [
        str(server / "webapps" / "minerva"), str(server), str(server)]


def test_the_most_touched_nested_repository_wins(server, tmp_path):
    a, b = server / "webapps" / "minerva", server / "apps" / "localingo"
    turns, _ = read(tmp_path, [line("u1", "m1", server, [
        ("Read", {"file_path": str(b / "x.swift")}),
        ("Edit", {"file_path": str(a / "one.ts")}), ("Edit", {"file_path": str(a / "two.ts")}),
    ])])
    assert turns["m1"]["project"] == str(a)


def test_head_outside_a_repository_is_no_branch(server, tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    turns, _ = read(tmp_path, [line("u1", "m1", loose, branch="HEAD"),
                               line("u2", "m2", server / "webapps" / "minerva", branch="HEAD"),
                               line("u3", "m3", loose, branch="main")])
    assert [turns[k].get("branch") for k in ("m1", "m2", "m3")] == [None, None, None]


def test_a_hash_machine_sends_its_branches_hashed():
    assert mc.label_branch("feat/secret-thing", "hash", "salt").startswith("b-")
    assert mc.label_branch("feat/x", "basename", "salt") == "feat/x"


# ── hub ───────────────────────────────────────────────────────────────────────

def _machine(session, labels="full"):
    machines.create(session, "laptop", labels)
    return session.get(Machine, "laptop")


def _turn(**over):
    t = {"source": "claude-code", "session_id": "s1", "turn_key": "msg_1", "project": "/Server",
         "model_id": "claude-opus-5", "ts": "2026-09-21T10:00:00", "input_tokens": 1, "output_tokens": 1}
    t.update(over)
    return t


def test_the_hub_stores_the_branch_but_not_head(session):
    ingest.ingest_turns(session, _machine(session), [_turn(branch="feat/x"), _turn(turn_key="m2", branch="HEAD")])
    assert {r.turn_key: r.branch for r in session.exec(select(UsageTurn)).all()} == {"msg_1": "feat/x", "m2": None}


def test_a_reply_keeps_its_first_project_unless_reattributed(session):
    m = _machine(session)
    ingest.ingest_turns(session, m, [_turn()])
    ingest.ingest_turns(session, m, [_turn(project="/Server/webapps/minerva")])
    assert session.exec(select(UsageTurn)).one().project == "/Server"
    ingest.ingest_turns(session, m, [_turn(project="/Server/webapps/minerva", branch="main", reattribute=True)])
    row = session.exec(select(UsageTurn)).one()
    assert (row.project, row.branch) == ("/Server/webapps/minerva", "main")


def test_stored_folders_resolve_only_when_this_disk_can_tell(server):
    minerva = server / "webapps" / "minerva"
    assert mc.resolve_stored(str(minerva / "frontend" / "src")) == str(minerva)
    assert mc.resolve_stored(str(minerva)) is None                          # already a repository
    assert mc.resolve_stored(str(server / "notes")) == str(server)          # a plain folder of ~/Server
    # gone: it might have been a repository of its own, so it keeps its name
    assert mc.resolve_stored(str(server / "webapps" / "nutrition-track")) is None
    # a gone worktree whose repository exists counts toward it
    assert mc.resolve_stored(str(server / "webapps" / "minerva-wt" / "9" / "src")) == str(minerva)
    assert mc.resolve_stored(str(server / "webapps" / "gone-wt" / "1")) is None
    assert mc.resolve_stored("minerva") is None                             # not a path


def test_a_machine_renames_only_its_own_rows(session):
    old = dict(source="claude-code", session_id="old", model_id="claude-opus-5", ts=datetime(2026, 8, 1))
    laptop = _machine(session)
    machines.create(session, "desk", "full")
    session.add_all([
        UsageTurn(turn_key="a", project="/Server/webapps/minerva/frontend", machine="laptop", **old),
        UsageTurn(turn_key="b", project="/Server/webapps/minerva/frontend", machine="desk", **old),
    ])
    session.commit()
    assert ingest.machine_projects(session, laptop) == ["/Server/webapps/minerva/frontend"]
    result = ingest.rename_projects(session, laptop, {"/Server/webapps/minerva/frontend": "/Server/webapps/minerva",
                                                      "": "/x", "/y": 3})
    assert result == {"folders_moved": 1, "rows_moved": 1}
    by_key = {r.turn_key: r.project for r in session.exec(select(UsageTurn)).all()}
    assert by_key == {"a": "/Server/webapps/minerva", "b": "/Server/webapps/minerva/frontend"}


def test_renames_keep_a_machines_label_policy(session):
    machines.create(session, "private", "basename")
    m = session.get(Machine, "private")
    session.add(UsageTurn(source="claude-code", session_id="s", turn_key="a", project="src", machine="private",
                          model_id="claude-opus-5", ts=datetime(2026, 8, 1)))
    session.commit()
    ingest.rename_projects(session, m, {"src": "/Users/me/secret-repo"})
    assert session.exec(select(UsageTurn)).one().project == "secret-repo"


def test_the_rename_endpoints_need_a_machine_key(client):
    assert client.get("/api/projects/mine").status_code == 401
    assert client.post("/api/projects/rename", json={"renames": {}}).status_code == 401
