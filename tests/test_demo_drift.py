"""Guard: scripts/check_demo_drift.py fails a change to demo.tape or the demo script
that does not also change demo.gif, unless a commit carries a Demo-Gif-Override trailer.

Every case runs the real script against a real throwaway git repository with real
commits; nothing is mocked.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_demo_drift.py"

GIT_BASE = [
    "-c", "user.name=t",
    "-c", "user.email=t@example.invalid",
    "-c", "commit.gpgsign=false",
    "-c", "core.autocrlf=false",
]  # fmt: skip
ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "PYTHONIOENCODING": "utf-8"}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *GIT_BASE, *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        env=ENV,
        timeout=30,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout.strip()


def _commit(repo: Path, files: dict[str, bytes | str], *paragraphs: str) -> str:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    msg: list[str] = []
    for p in paragraphs or ("change",):
        msg += ["-m", p]
    _git(repo, "commit", "-q", "--no-verify", *msg)
    return _git(repo, "rev-parse", "HEAD")


def _run(repo: Path, base: str, head: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base", base, "--head", head],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        env=ENV,
        timeout=30,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # Not tmp_path itself: the autouse fixture in conftest writes tmp_path/test.db.
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _commit(
        path,
        {
            "demo.tape": "Type hello\n",
            "demo.gif": b"GIF89a-0",
            "examples/07_demo_mock.py": "print('demo')\n",
            "README.md": "readme\n",
        },
        "base",
    )
    return path


def _base(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _assert_drift_message(res: subprocess.CompletedProcess[str], filename: str) -> None:
    assert res.returncode == 1, res.stdout + res.stderr
    assert filename in res.stderr
    assert "demo.gif" in res.stderr
    assert "Demo-Gif-Override" in res.stderr


def test_only_tape_changed_fails(repo):
    base = _base(repo)
    head = _commit(repo, {"demo.tape": "Type bye\n"})
    _assert_drift_message(_run(repo, base, head), "demo.tape")


def test_only_demo_script_changed_fails(repo):
    base = _base(repo)
    head = _commit(repo, {"examples/07_demo_mock.py": "print('changed')\n"})
    _assert_drift_message(_run(repo, base, head), "examples/07_demo_mock.py")


def test_tape_then_unrelated_commit_fails(repo):
    base = _base(repo)
    _commit(repo, {"demo.tape": "Type bye\n"})
    head = _commit(repo, {"README.md": "updated\n"})
    _assert_drift_message(_run(repo, base, head), "demo.tape")


def test_lookalike_gif_path_does_not_satisfy_fails(repo):
    base = _base(repo)
    head = _commit(repo, {"demo.tape": "Type bye\n", "docs/demo.gif.md": "notes\n"})
    _assert_drift_message(_run(repo, base, head), "demo.tape")


def test_tape_and_gif_together_passes(repo):
    base = _base(repo)
    head = _commit(repo, {"demo.tape": "Type bye\n", "demo.gif": b"GIF89a-1"})
    assert _run(repo, base, head).returncode == 0


def test_script_and_gif_together_passes(repo):
    base = _base(repo)
    head = _commit(
        repo, {"examples/07_demo_mock.py": "print('x')\n", "demo.gif": b"GIF89a-2"}
    )
    assert _run(repo, base, head).returncode == 0


def test_only_readme_changed_passes(repo):
    base = _base(repo)
    head = _commit(repo, {"README.md": "updated\n"})
    assert _run(repo, base, head).returncode == 0


def test_only_gif_changed_passes(repo):
    base = _base(repo)
    head = _commit(repo, {"demo.gif": b"GIF89a-3"})
    assert _run(repo, base, head).returncode == 0


def test_tape_then_gif_in_later_commit_passes(repo):
    base = _base(repo)
    _commit(repo, {"demo.tape": "Type bye\n"})
    head = _commit(repo, {"demo.gif": b"GIF89a-4"})
    assert _run(repo, base, head).returncode == 0


def test_lookalike_tape_path_passes(repo):
    base = _base(repo)
    head = _commit(repo, {"demo.tape.bak": "old\n"})
    assert _run(repo, base, head).returncode == 0


def test_override_with_reason_passes(repo):
    base = _base(repo)
    head = _commit(
        repo,
        {"demo.tape": "Type bye\n"},
        "tweak tape comments",
        "Demo-Gif-Override: comment-only edit",
    )
    res = _run(repo, base, head)
    assert res.returncode == 0, res.stderr
    assert "comment-only edit" in res.stdout


def test_override_on_earlier_commit_in_range_passes(repo):
    base = _base(repo)
    _commit(repo, {"README.md": "x\n"}, "docs", "Demo-Gif-Override: timing neutral")
    head = _commit(repo, {"demo.tape": "Type bye\n"})
    res = _run(repo, base, head)
    assert res.returncode == 0, res.stderr
    assert "timing neutral" in res.stdout


@pytest.mark.parametrize("trailer", ["Demo-Gif-Override:", "Demo-Gif-Override:   "])
def test_override_empty_reason_fails(repo, trailer):
    base = _base(repo)
    head = _commit(repo, {"demo.tape": "Type bye\n"}, "tweak", trailer, "unrelated line")
    _assert_drift_message(_run(repo, base, head), "demo.tape")


def test_override_outside_range_fails(repo):
    # The trailer sits on the base commit itself, so it is not in base..head.
    _commit(repo, {"README.md": "x\n"}, "docs", "Demo-Gif-Override: too early")
    base = _base(repo)
    head = _commit(repo, {"demo.tape": "Type bye\n"})
    _assert_drift_message(_run(repo, base, head), "demo.tape")


def test_merge_base_ignores_changes_landed_on_main_passes(repo):
    _git(repo, "switch", "-q", "-c", "branch")
    _git(repo, "switch", "-q", "main")
    _commit(repo, {"demo.tape": "Type main\n", "demo.gif": b"GIF89a-5"}, "main tape+gif")
    _commit(repo, {"demo.tape": "Type main again\n"}, "main tape only")
    _git(repo, "switch", "-q", "branch")
    _commit(repo, {"README.md": "branch\n"}, "branch readme")

    two_dot = _git(repo, "diff", "--name-only", "main", "branch").splitlines()
    assert "demo.tape" in two_dot  # without three-dot this would be misattributed

    res = _run(repo, "main", "branch")
    assert res.returncode == 0, res.stderr


def test_bad_ref_exits_2(repo):
    res = _run(repo, "nonexistent", "HEAD")
    assert res.returncode == 2
    assert "nonexistent" in res.stderr


def test_not_a_git_repo_exits_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res = _run(empty, "main", "HEAD")
    assert res.returncode == 2
