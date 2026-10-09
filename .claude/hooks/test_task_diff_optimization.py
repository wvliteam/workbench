"""任务 diff 回归：python3 .claude/hooks/test_task_diff_optimization.py。"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import wb_cli
from wb_core import find_task, load_state
from wb_selfcheck import _isolated_workspace, quiet


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout


def init_repo(root, *, commit=True):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Task diff test")
    git(root, "config", "user.email", "task-diff@example.invalid")
    (root / ".gitignore").write_text(".workbench/\nrepos/.source/\n")
    (root / "app.txt").write_text("base\n")
    if commit:
        git(root, "add", ".")
        git(root, "commit", "-qm", "fixture")


def task(root, files, task_id="T1"):
    result = {"id": task_id, "phase": "develop", "artifacts": files}
    result["baselines"] = wb_cli.capture_task_baselines(root, result, "main")
    return result


def diff(root, item):
    output = wb_cli.record_task_diff(root, item, "main")
    data = json.loads(output.read_text())
    assert set(data) == {"task_id", "flow", "phase", "recorded_at", "summary", "files"}
    assert data["summary"]["total_files"] == len(data["files"])
    return {file["path"]: file for file in data["files"]}


def check_incremental(root):
    init_repo(root)
    special = ['quote".txt', "空 格.txt", "tab\tname.txt", "line\nname.txt",
               "a b/c.txt", "[wild]*.txt", ":(glob)literal.txt"]
    for name in special + ["unchanged.txt", "delete.txt", "binary.bin", "rename.txt"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"old\0binary" if name == "binary.bin" else b"old\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "paths")
    (root / "app.txt").write_text("base\nstaged\n")
    git(root, "add", "app.txt")
    (root / "app.txt").write_text("base\nstaged\nprevious dirty\n")
    (root / "scratch").mkdir()
    (root / "scratch/old.txt").write_text("previous untracked\n")
    (root / "scratch/gone.txt").write_text("untracked deletion\n")
    (root / "scratch/promoted.txt").write_text("before stage\n")
    (root / "same-untracked.txt").write_text("unchanged\n")
    (root / "link").symlink_to("app.txt")
    files = ["app.txt", "unchanged.txt", "delete.txt", "scratch/old.txt",
             "scratch/gone.txt", "scratch/promoted.txt", "same-untracked.txt", "link",
             "new.txt", "empty.txt", "binary.bin", "rename.txt", "renamed.txt", *special]
    index = (root / ".git/index").read_bytes()
    refs = git(root, "show-ref")
    baseline = task(root, files)
    assert index == (root / ".git/index").read_bytes()
    assert refs == git(root, "show-ref")
    assert not (root / ".workbench/artifacts/main/develop/tasks/.baselines").exists()
    (root / "app.txt").write_text("base\nstaged\nprevious dirty\nown change\n")
    (root / "scratch/old.txt").write_text("previous untracked\nown scratch\n")
    (root / "scratch/gone.txt").unlink()
    (root / "scratch/promoted.txt").write_text("before stage\nafter stage\n")
    git(root, "add", "scratch/promoted.txt")
    (root / "delete.txt").unlink()
    (root / "rename.txt").rename(root / "renamed.txt")
    (root / "new.txt").write_text("++content\n--content\nno newline")
    (root / "empty.txt").touch()
    (root / "binary.bin").write_bytes(b"new\0binary")
    (root / "link").unlink()
    (root / "link").symlink_to("new.txt")
    for name in special:
        (root / name).write_text("old\nown path change\n")
    index = (root / ".git/index").read_bytes()
    rows = diff(root, baseline)
    assert index == (root / ".git/index").read_bytes()
    assert refs == git(root, "show-ref")
    assert "unchanged.txt" not in rows and "same-untracked.txt" not in rows
    assert rows["app.txt"]["status"] == "modified"
    assert rows["app.txt"]["additions"] == 1
    assert "+previous dirty" not in rows["app.txt"]["diff"]
    assert rows["scratch/old.txt"]["additions"] == 1
    assert rows["scratch/gone.txt"]["status"] == "deleted"
    assert rows["scratch/promoted.txt"]["additions"] == 1
    assert rows["delete.txt"]["status"] == "deleted"
    assert rows["rename.txt"]["status"] == "deleted" and rows["renamed.txt"]["status"] == "added"
    assert rows["new.txt"]["additions"] == 3
    assert "\\ No newline at end of file" in rows["new.txt"]["diff"]
    assert rows["empty.txt"]["status"] == "added"
    assert "Binary files" in rows["binary.bin"]["diff"]
    assert rows["binary.bin"]["additions"] == 0
    assert rows["link"]["status"] == "modified"
    assert all(rows[name]["additions"] == 1 for name in special)


def check_repositories(root):
    init_repo(root)
    external = root.parent / "mounted"
    init_repo(external)
    project = root / "repos/.source/test"
    project.mkdir(parents=True)
    (project / "one").symlink_to(external, target_is_directory=True)
    (project / "alias").symlink_to(external, target_is_directory=True)
    worktree = project / "worktree"
    git(external, "worktree", "add", "--detach", str(worktree))
    nested = root / "nested"
    init_repo(nested)
    for repo in (root, external, worktree, nested):
        (repo / "app.txt").write_text("base\nprevious\n")
    files = ["app.txt", "nested/app.txt", *[f"repos/.source/test/{name}/app.txt"
                         for name in ("one", "alias", "worktree")]]
    with patch.object(wb_cli, "_task_repo_snapshot", wraps=wb_cli._task_repo_snapshot) as snapshot:
        item = task(root, files)
        assert snapshot.call_count == 4, snapshot.call_args_list
    assert item["baselines"]["repos"]["repos/.source/test/one"] == item["baselines"]["repos"]["repos/.source/test/alias"]
    for repo in (root, external, worktree, nested):
        (repo / "app.txt").write_text("base\nprevious\nown\n")
    with patch.object(wb_cli, "_task_repo_snapshot", wraps=wb_cli._task_repo_snapshot) as snapshot:
        rows = diff(root, item)
        assert snapshot.call_count == 4, snapshot.call_args_list
    assert set(rows) == set(files)
    assert all(row["additions"] == 1 for row in rows.values())


def check_legacy_and_unborn(root):
    init_repo(root)
    (root / "app.txt").write_text("base\nprevious\n")
    old_base = git(root, "stash", "create").decode().strip()
    backup = root / ".workbench/artifacts/main/develop/tasks/.baselines/T1/scratch.txt"
    backup.parent.mkdir(parents=True)
    backup.write_text("previous untracked\n")
    (root / "app.txt").write_text("base\nprevious\nown\n")
    (root / "scratch.txt").write_text("previous untracked\nown\n")
    rows = diff(root, {"id": "T1", "phase": "develop",
                      "baselines": {"repos": {".": old_base}},
                      "artifacts": ["app.txt", "scratch.txt"]})
    assert all(row["additions"] == 1 for row in rows.values())
    assert not backup.parent.exists()
    unborn = root.parent / "unborn"
    init_repo(unborn, commit=False)
    item = task(unborn, ["app.txt", "new.txt"])
    assert not (unborn / ".git/index").exists()
    (unborn / "app.txt").write_text("base\nown\n")
    (unborn / "new.txt").write_text("new\n")
    rows = diff(unborn, item)
    assert rows["app.txt"]["additions"] == 1 and rows["new.txt"]["status"] == "added"
    assert not (unborn / ".git/index").exists()
    no_git = root.parent / "no-git"
    no_git.mkdir()
    (no_git / "file.txt").write_text("no newline")
    rows = diff(no_git, {"id": "T1", "artifacts": ["file.txt"]})
    assert rows["file.txt"]["status"] == "added" and rows["file.txt"]["additions"] == 1


def check_process_count(root):
    init_repo(root)
    files = [f"file{i}.txt" for i in range(100)]
    for name in files:
        (root / name).write_text("base\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "many files")
    item = task(root, files)
    (root / files[0]).write_text("base\nown\n")
    real_run = subprocess.run
    with patch.object(wb_cli.subprocess, "run", wraps=real_run) as run:
        rows = diff(root, item)
        commands = [call.args[0] for call in run.call_args_list]
    assert list(rows) == [files[0]], rows
    assert len(commands) <= 6, commands
    assert not any("ls-files" in cmd or "--is-inside-work-tree" in cmd for cmd in commands)
    print(f"PASS: 100 artifacts / 1 change -> {len(commands)} Git calls")


def check_failures(root):
    init_repo(root)
    item = task(root, ["app.txt"])
    (root / "app.txt").write_text("own\n")
    with patch.object(wb_cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)):
        try:
            diff(root, item)
        except RuntimeError as exc:
            assert "任务 diff" in str(exc)
        else:
            raise AssertionError("Git 超时不能被当成空 diff")
    backup = root / ".workbench/artifacts/main/develop/tasks/.baselines/T1/app.txt"
    backup.parent.mkdir(parents=True)
    backup.write_text("old\n")
    with patch.object(wb_cli, "atomic_write_text", side_effect=OSError("write failure")):
        try:
            diff(root, item)
        except OSError:
            pass
        else:
            raise AssertionError("落盘失败应上报")
    assert backup.is_file(), "落盘失败不得删除旧基线"
    with _isolated_workspace() as workspace:
        init_repo(workspace)
        assert quiet("init", "--name", "diff-errors")[0] == 0
        assert quiet("task", "add", "T1", "--title", "diff", "--role", "backend-developer")[0] == 0
        with patch.object(wb_cli, "capture_task_baselines", side_effect=RuntimeError("snapshot failure")):
            assert quiet("task", "start", "T1", "--role-lock")[0] == 1
        assert find_task(load_state(workspace), "T1")["status"] == "todo"
        assert not (workspace / ".workbench/role").exists()
        assert quiet("task", "start", "T1")[0] == 0
        baseline = find_task(load_state(workspace), "T1")["baselines"]
        with patch.object(wb_cli, "record_task_diff", side_effect=RuntimeError("diff failure")):
            assert quiet("task", "done", "T1")[0] == 1
        persisted = find_task(load_state(workspace), "T1")
        assert persisted["status"] == "doing" and persisted["baselines"] == baseline
        assert quiet("task", "done", "T1")[0] == 0


if __name__ == "__main__":
    for check in (check_incremental, check_repositories, check_legacy_and_unborn,
                  check_process_count, check_failures):
        with tempfile.TemporaryDirectory(prefix="wb-diff-test-") as directory:
            check(Path(directory) / "workspace")
        print(f"PASS: {check.__name__}")
