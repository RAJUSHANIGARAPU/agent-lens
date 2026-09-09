"""Regression guard for the README quickstart block.

The quickstart is the first thing a new reader runs, and its promise is narrow: a single
copy-pasteable bash block, installed with pip, needing no Docker, no API key and no vendor
SDK, and printing a verdict line. These checks read README.md at test time and pin exactly
those properties, so an unrelated README edit cannot quietly break them.
"""

from pathlib import Path

README_PATH = Path(__file__).resolve().parent.parent / "README.md"
QUICKSTART_HEADING = "## Quickstart (60 seconds, no API key)"
BASH_FENCE = "```bash"
FENCE = "```"
PIP_INSTALL = "pip install agentlens-tracer"
VERDICT_PREFIX = "Verdict: "  # 9 chars: the word, a colon, exactly one space
FORBIDDEN_IN_QUICKSTART = (
    "docker",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "import openai",
    "import anthropic",
)


def _quickstart_section() -> list[str]:
    lines = README_PATH.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == QUICKSTART_HEADING]
    assert len(starts) == 1, (
        f"expected exactly one {QUICKSTART_HEADING!r} heading in README.md, found {len(starts)}"
    )
    body = lines[starts[0] + 1 :]
    for offset, line in enumerate(body):
        if line.startswith("## "):
            return body[:offset]
    return body


def _bash_blocks(section: list[str]) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in section:
        if current is None:
            if line.strip() == BASH_FENCE:
                current = []
        elif line.strip() == FENCE:
            blocks.append("\n".join(current))
            current = None
        else:
            current.append(line)
    assert current is None, "unterminated ```bash fence in the README quickstart section"
    return blocks


def _quickstart_block() -> str:
    blocks = _bash_blocks(_quickstart_section())
    assert len(blocks) == 1, (
        f"expected exactly one bash block in the quickstart section, found {len(blocks)}"
    )
    return blocks[0]


def test_quickstart_section_has_exactly_one_block():
    blocks = _bash_blocks(_quickstart_section())
    assert len(blocks) == 1, (
        "the quickstart section must hold exactly one bash block for a reader to paste; "
        f"found {len(blocks)}"
    )


def test_quickstart_block_pip_install_is_the_only_setup_step():
    assert PIP_INSTALL in _quickstart_block(), (
        f"the quickstart must install the published package with {PIP_INSTALL!r}"
    )


def test_quickstart_block_verdict_string_keeps_its_trailing_space():
    block = _quickstart_block()
    assert VERDICT_PREFIX in block, (
        "the quickstart must print the verdict as 'Verdict: ' - capital V, colon, one "
        "trailing space. The trailing space is pinned on purpose: 'Verdict:' or "
        "'Verdict : ' reads as a different output line, so this is a character-for-character "
        "substring check and not a loose or whitespace-tolerant match."
    )


def test_quickstart_block_needs_no_docker_no_vendor_sdk():
    block_lower = _quickstart_block().lower()
    found = [term for term in FORBIDDEN_IN_QUICKSTART if term.lower() in block_lower]
    assert not found, (
        "the quickstart must run with no Docker, no API key and no vendor SDK; "
        f"found: {found}"
    )
