"""
Guards the README's "Quickstart (60 seconds, no API key)" section against
silently regressing back into the two-block, API-key-requiring shape it
replaced (see friday-forge task al-001).

These are the same mechanical checks the al-001 spec phrased as
grep/awk one-liners, re-expressed as pytest assertions so they run on every
`pytest tests/ -q` instead of depending on someone re-running them by hand.

Deliberately narrow: this is not a general README linter. It only encodes
the five al-001 acceptance criteria that are pure static checks on
README.md. Criterion 2 (actually executing the extracted block in a fresh
venv and capturing its stdout) is intentionally NOT automated here — the
al-001 spec explicitly rules out adding a CI job that executes the README
block on every push; that live-execution check remains a manual step.
"""

import re
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"

QUICKSTART_HEADING = "## Quickstart (60 seconds, no API key)"
OLD_HEADING = "## Install in 30 seconds"


def _readme_text() -> str:
    return README.read_text()


def _quickstart_section(text: str) -> str:
    """Mirror of: awk '/^## Quickstart .../,/^---$/' README.md"""
    lines = text.splitlines()
    section = []
    in_section = False
    for line in lines:
        if line.startswith(QUICKSTART_HEADING):
            in_section = True
        if in_section:
            section.append(line)
            if in_section and line == "---" and section[0] != line:
                break
    return "\n".join(section)


def _extract_bash_block(text: str) -> str:
    """Mirror of: awk '/^```bash$/{f=1;next} /^```$/{f=0} f' README.md"""
    lines = text.splitlines()
    collected = []
    fenced = False
    for line in lines:
        if line == "```bash":
            fenced = True
            continue
        if line == "```":
            fenced = False
            continue
        if fenced:
            collected.append(line)
    return "\n".join(collected)


def test_quickstart_heading_present():
    text = _readme_text()
    assert QUICKSTART_HEADING in text, (
        "README.md must contain the "
        f"{QUICKSTART_HEADING!r} heading (al-001 criterion 1)."
    )


def test_old_install_heading_is_gone():
    """
    Regression guard: the old two-block, key-requiring heading must not
    reappear (e.g. via a revert or a merge that resurrects it alongside the
    new section).
    """
    text = _readme_text()
    assert OLD_HEADING not in text, (
        f"README.md still contains the old {OLD_HEADING!r} heading; "
        "the Quickstart section must replace it, not sit alongside it."
    )


def test_quickstart_section_has_exactly_one_fenced_block():
    """al-001 criterion 1: exactly one fenced code block (one open + one
    close fence -> 2 lines starting with ``` ) inside the Quickstart
    section."""
    text = _readme_text()
    section = _quickstart_section(text)
    assert section, "Could not locate the Quickstart section in README.md."
    fence_lines = [line for line in section.splitlines() if line.startswith("```")]
    assert len(fence_lines) == 2, (
        f"Expected exactly one fenced block (2 fence lines) in the "
        f"Quickstart section, found {len(fence_lines)}: {fence_lines}"
    )


def test_readme_has_exactly_one_bash_fence():
    """
    Guards the extraction itself: if a second ```bash fence is ever added
    anywhere in README.md (e.g. inside the demoted OpenAI section), the
    naive `awk '/^```bash$/.../^```$/'` extraction a reader's script would
    use concatenates two blocks together, which would silently break every
    other check in this file (plan-al-001.md, "where this is most likely to
    go wrong").
    """
    text = _readme_text()
    bash_fences = len(re.findall(r"^```bash$", text, flags=re.MULTILINE))
    assert bash_fences == 1, (
        f"Expected exactly one '```bash' fence in README.md, found {bash_fences}."
    )


def test_quickstart_block_has_no_api_key_or_vendor_sdk():
    """al-001 criterion 3: the extracted quickstart block must not
    reference an API key or an LLM vendor SDK."""
    text = _readme_text()
    block = _extract_bash_block(text)
    assert block, "Extracted quickstart bash block is empty."
    forbidden = re.compile(
        r"api[_-]?key|from openai|import openai|from anthropic|import anthropic"
        r"|OPENAI_|ANTHROPIC_",
        re.IGNORECASE,
    )
    matches = forbidden.findall(block)
    assert not matches, (
        f"Quickstart block must not require an API key or vendor SDK, "
        f"found forbidden token(s): {matches}"
    )


def test_quickstart_block_has_no_docker_and_uses_a_throwaway_db():
    """al-001 criterion 4: no Docker / no extra infra, and the block must
    create its own temp SQLite path rather than depending on a
    pre-existing ~/.agent-lens/ directory."""
    text = _readme_text()
    block = _extract_bash_block(text)
    assert "docker" not in block.lower(), "Quickstart block must not mention Docker."
    assert "tempfile.mktemp" in block, (
        "Quickstart block must create its own throwaway database via "
        "tempfile.mktemp(...) rather than relying on ~/.agent-lens/runs.db."
    )
    assert "~/.agent-lens" not in block, (
        "Quickstart block must not hardcode the default ~/.agent-lens path."
    )


def test_openai_api_key_requirement_is_disclosed_somewhere():
    """al-001 criterion 5: the demoted real-OpenAI example must name the
    OPENAI_API_KEY requirement explicitly, so it is not silently deleted or
    stripped of its disclosure."""
    text = _readme_text()
    assert text.count("OPENAI_API_KEY") >= 1, (
        "README.md must name OPENAI_API_KEY at least once, disclosing the "
        "key requirement for the demoted real-OpenAI example."
    )
