"""Guard: the demo GIF pipeline stays reproducible and cannot push to the repo.

demo.yml once ran against floating "latest" VHS, which dropped the theme name used in
demo.tape, and its last step pushed a ``[skip ci]`` commit. These checks keep the
workflow pinned and read-only, and the tape on a valid theme with a real canvas size.
No YAML dependency: the workflow is checked line by line.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "demo.yml").read_text(encoding="utf-8")
TAPE = (ROOT / "demo.tape").read_text(encoding="utf-8")


def _vhs_step_with_block() -> str:
    """Text of the charmbracelet/vhs-action step, up to the next step."""
    rest = WORKFLOW[WORKFLOW.index("charmbracelet/vhs-action@v2") :]
    nxt = re.search(r"(?m)^\s+- (?:name|uses):", rest)
    return rest[: nxt.start()] if nxt else rest


def test_vhs_version_is_a_literal_release():
    block = _vhs_step_with_block()
    m = re.search(r'(?m)^\s+version:\s*"?v?(\d+\.\d+\.\d+)"?\s*$', block)
    assert m, f"vhs-action step has no literal x.y.z version:\n{block}"


def test_no_floating_latest_version():
    assert not re.search(r"(?i)version:\s*\"?latest", WORKFLOW)


def test_workflow_does_not_push_or_skip_ci():
    assert "git push" not in WORKFLOW
    assert "[skip ci]" not in WORKFLOW


def test_workflow_permissions_are_read_only():
    perms = re.findall(r"(?m)^\s*contents:\s*(\S+)", WORKFLOW)
    assert perms, "no contents: permission declared"
    assert set(perms) == {"read"}, perms
    assert not re.search(r"(?m)^\s*[\w-]+:\s*write\b", WORKFLOW)


def test_workflow_uploads_demo_gif_artifact():
    assert re.search(r"upload-artifact", WORKFLOW)
    assert re.search(r"(?m)^\s+path:\s*demo\.gif\s*$", WORKFLOW)


def test_tape_theme_is_not_the_removed_monokai():
    theme = re.search(r'(?m)^Set Theme\s+"?([^"\n]+?)"?\s*$', TAPE)
    assert theme, "demo.tape sets no theme"
    # VHS 0.12.1 has no theme named exactly "Monokai" (suggests "Molokai").
    assert theme.group(1) != "Monokai"


def test_tape_size_is_in_pixels_not_columns():
    # VHS Width/Height are pixels; 120x42 once rendered a blank 120x42 px GIF.
    for key in ("Width", "Height"):
        m = re.search(rf"(?m)^Set {key}\s+(\d+)\s*$", TAPE)
        assert m, f"demo.tape sets no {key}"
        assert int(m.group(1)) >= 400, f"{key}={m.group(1)} looks like cells, not pixels"


def test_tape_still_writes_demo_gif_that_readme_embeds():
    assert re.search(r"(?m)^Output demo\.gif\s*$", TAPE)
    assert "![agent-lens demo](demo.gif)" in (ROOT / "README.md").read_text(encoding="utf-8")
