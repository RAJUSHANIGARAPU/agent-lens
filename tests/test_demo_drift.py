"""Guard: a change to demo.tape or the demo script must come with a change to demo.gif.

The script under test, scripts/check_demo_drift.py, is run as a real subprocess against
real throwaway git repositories, so shallow clones, merge-base handling and git's trailer
parser are exercised rather than mocked.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_demo_drift.py"

# The trailer name that was replaced; it must no longer waive the check.
OLD_TRAILER = "Demo-Gif-Override"

TAPE = "demo.tape"
DEMO_SCRIPT = "examples/07_demo_mock.py"
GIF = "demo.gif"


def _env(tmp_path: Path) -> dict[str, str]:
    """Environment isolated from the developer's and the runner's git configuration."""
    cfg = tmp_path / "gitconfig"
    cfg.write_text("", encoding="utf-8")
    env = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    env["GIT_CONFIG_GLOBAL"] = str(cfg)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout


def _commit(repo: Path, env: dict[str, str], files: dict[str, bytes], message: str) -> None:
    for rel, data in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    # Message lives outside the repo so "git add -A" cannot commit it.
    msg = repo.parent / "msg.txt"
    msg.write_text(message, encoding="utf-8")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-F", str(msg))


def _make_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    env = _env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, env, "init", "-q")
    _git(repo, env, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, env, "config", "user.name", "Test User")
    _git(repo, env, "config", "user.email", "test@example.com")
    _git(repo, env, "config", "commit.gpgsign", "false")
    _commit(
        repo,
        env,
        {
            TAPE: b"Type hello\n",
            GIF: b"GIF89a",
            DEMO_SCRIPT: b"print('demo')\n",
            "README.md": b"readme\n",
        },
        "chore: initial",
    )
    return repo, env


def _run(repo: Path, env: dict[str, str], base: str, head: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--base", base, "--head", head],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout + proc.stderr


def _feature(repo: Path, env: dict[str, str], files: dict[str, bytes], message: str) -> None:
    _git(repo, env, "checkout", "-q", "-b", "feature")
    _commit(repo, env, files, message)


def _tape_change(repo: Path) -> dict[str, bytes]:
    return {TAPE: (repo / TAPE).read_bytes() + b"Sleep 1s\n"}


def test_tape_only_change_fails(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, _tape_change(repo), "feat: tweak tape")
    code, out = _run(repo, env, "main", "feature")
    assert code == 1
    assert TAPE in out
    assert "Demo-Drift-Override" in out


def test_script_only_change_fails(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, {DEMO_SCRIPT: b"print('changed')\n"}, "feat: change demo script")
    code, out = _run(repo, env, "main", "feature")
    assert code == 1
    assert DEMO_SCRIPT in out


def test_both_guarded_without_gif_fails(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    files = {**_tape_change(repo), DEMO_SCRIPT: b"print('changed')\n"}
    _feature(repo, env, files, "feat: change both")
    code, out = _run(repo, env, "main", "feature")
    assert code == 1
    assert TAPE in out
    assert DEMO_SCRIPT in out


def test_tape_with_gif_also_changed_passes(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    files = {**_tape_change(repo), GIF: b"GIF89a-new"}
    _feature(repo, env, files, "feat: tweak tape and gif")
    code, out = _run(repo, env, "main", "feature")
    assert code == 0, out


def test_neither_guarded_file_changed_passes(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, {"README.md": b"readme v2\n"}, "docs: readme")
    code, out = _run(repo, env, "main", "feature")
    assert code == 0, out


def test_override_valid_trailer_passes(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    msg = "chore: tape comment\n\nDemo-Drift-Override: comment only\n"
    _feature(repo, env, _tape_change(repo), msg)
    code, out = _run(repo, env, "main", "feature")
    assert code == 0, out


def test_override_body_line_not_trailer_fails(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    msg = (
        "chore: tape\n\n"
        "Demo-Drift-Override: x\n"
        "This paragraph explains the change.\n\n"
        "Closing prose, not a trailer.\n"
    )
    _feature(repo, env, _tape_change(repo), msg)
    code, out = _run(repo, env, "main", "feature")
    assert code == 1, out


def test_override_empty_value_fails(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, _tape_change(repo), "chore: tape\n\nDemo-Drift-Override:\n")
    code, out = _run(repo, env, "main", "feature")
    assert code == 1, out


def test_override_old_name_demo_gif_override_fails(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, _tape_change(repo), f"chore: tape\n\n{OLD_TRAILER}: comment only\n")
    code, out = _run(repo, env, "main", "feature")
    assert code == 1, out


def test_shallow_clone_cannot_determine(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, _tape_change(repo), "feat: tweak tape")
    _git(repo, env, "checkout", "-q", "main")
    _commit(repo, env, {"README.md": b"one\n"}, "docs: one")
    _commit(repo, env, {"README.md": b"two\n"}, "docs: two")
    clone = tmp_path / "clone"
    _git(
        tmp_path,
        env,
        "clone",
        "-q",
        "--depth",
        "1",
        "--no-single-branch",
        repo.as_uri(),
        str(clone),
    )
    code, out = _run(clone, env, "origin/main", "origin/feature")
    assert code == 2
    assert code != 0
    assert "cannot determine" in out


def test_unknown_ref_cannot_determine(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, _tape_change(repo), "feat: tweak tape")
    code, out = _run(repo, env, "no-such-ref", "feature")
    assert code == 2
    assert "cannot determine" in out


def test_merge_base_missing_cannot_determine(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _git(repo, env, "checkout", "-q", "--orphan", "other")
    _commit(repo, env, {"unrelated.txt": b"x\n"}, "chore: unrelated root")
    code, out = _run(repo, env, "main", "other")
    assert code == 2
    assert "cannot determine" in out
    assert "merge-base" in out
    assert "unknown ref" not in out


def test_moved_base_unrelated_pr_passes(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, {"README.md": b"readme v2\n"}, "docs: readme")
    _git(repo, env, "checkout", "-q", "main")
    _commit(repo, env, _tape_change(repo), "feat: tape change on main")
    code, out = _run(repo, env, "main", "feature")
    assert code == 0, out


def test_moved_base_own_tape_edit_fails(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, _tape_change(repo), "feat: tweak tape")
    _git(repo, env, "checkout", "-q", "main")
    _commit(repo, env, {"README.md": b"moved on\n"}, "docs: main moves on")
    code, out = _run(repo, env, "main", "feature")
    assert code == 1, out


def test_check_returns_result_without_exiting(tmp_path: Path) -> None:
    repo, env = _make_repo(tmp_path)
    _feature(repo, env, _tape_change(repo), "feat: tweak tape")
    spec = importlib.util.spec_from_file_location("check_demo_drift", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_demo_drift"] = mod
    try:
        spec.loader.exec_module(mod)
        result = mod.check("main", "feature", cwd=repo)
    finally:
        sys.modules.pop("check_demo_drift", None)
    assert int(result.verdict) == 1
    assert result.message
