"""Guard: the demo GIF pipeline stays reproducible and cannot push to the repo.

demo.yml once ran against floating "latest" VHS, which dropped the theme name used in
demo.tape, and its last step pushed a ``[skip ci]`` commit. These checks keep the
workflow pinned and read-only, and the tape on a valid theme with a real canvas size.
The ci.yml demo-drift job must keep running the drift script on pull requests.
No YAML dependency: the workflow is checked line by line.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "demo.yml").read_text(encoding="utf-8")
CI_WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
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


def _ci_job_block(name: str) -> str:
    """Text of one ci.yml job, up to the next job key or the end of the file."""
    start = re.search(rf"(?m)^  {re.escape(name)}:\s*$", CI_WORKFLOW)
    assert start, f"ci.yml has no {name} job"
    rest = CI_WORKFLOW[start.end() :]
    nxt = re.search(r"(?m)^  [\w-]+:\s*$", rest)
    return rest[: nxt.start()] if nxt else rest


def test_ci_has_demo_drift_job_running_the_script():
    block = _ci_job_block("demo-drift")
    assert "scripts/check_demo_drift.py" in block
    assert "name: demo-drift" in block


def test_demo_drift_job_fetches_full_history():
    assert re.search(r"fetch-depth:\s*0\b", _ci_job_block("demo-drift"))


def test_demo_drift_job_passes_pr_base_and_head_sha():
    block = _ci_job_block("demo-drift")
    assert "github.event.pull_request.base.sha" in block
    assert "github.event.pull_request.head.sha" in block


def test_demo_drift_job_cannot_be_silenced():
    block = _ci_job_block("demo-drift")
    assert "continue-on-error" not in block
    assert "|| true" not in block


def test_demo_drift_job_runs_on_pull_request():
    block = _ci_job_block("demo-drift")
    ifs = [line for line in block.splitlines() if line.strip().startswith("if:")]
    assert any("pull_request" in line for line in ifs), block
