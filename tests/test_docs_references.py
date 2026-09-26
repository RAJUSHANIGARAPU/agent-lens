"""Guard: the docs cite only repo paths and tests that exist.

The docs name test files, test functions, test classes and workflow files, and those
references have gone stale after renames more than once (7a7f149, 5141e6b).
``test_quickstart_invariants.py`` only guards the README quickstart block; this covers
every path and test reference in the top-level docs and ``docs/*.md``.

Checked:
- repo paths such as ``tests/...py``, ``agent_lens/...py``, ``examples/...``,
  ``docs/...md`` and ``.github/workflows/...yml``, anywhere in the text. Globs must
  match at least one file and ``{a,b}`` alternatives are expanded; ``<placeholder>``
  paths are skipped.
- backticked test references: ``test_x.py`` (must exist under ``tests/``),
  ``test_x.py::TestY::test_z`` (each part defined in that file), and bare
  ``test_*`` / ``Test*`` names (defined somewhere under ``tests/``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

DOC_FILES = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "CLAUDE.md"] + sorted(
    (ROOT / "docs").glob("*.md")
)

_PATH_RE = re.compile(
    r"(?<![\w./-])((?:tests|agent_lens|examples|docs|\.github/workflows)/[\w./{},*<>-]*[\w}*>])"
)
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_TEST_REF_RE = re.compile(r"^(?:[\w./-]*/)?test_\w+\.py(?:::\w+)*$|^(?:test_\w+|Test[A-Z]\w*)$")


def _expand_braces(path: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", path)
    if not m:
        return [path]
    head, tail = path[: m.start()], path[m.end() :]
    return [p for alt in m.group(1).split(",") for p in _expand_braces(head + alt + tail)]


def _path_resolves(path: str) -> bool:
    return all(any(ROOT.glob(p)) if "*" in p else (ROOT / p).exists() for p in _expand_braces(path))


def _defined_names(py: Path) -> set[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _test_index() -> tuple[dict[str, list[Path]], set[str]]:
    by_filename: dict[str, list[Path]] = {}
    all_names: set[str] = set()
    for py in TESTS.rglob("*.py"):
        by_filename.setdefault(py.name, []).append(py)
        all_names |= _defined_names(py)
    return by_filename, all_names


def _test_ref_resolves(ref: str, by_filename: dict[str, list[Path]], all_names: set[str]) -> bool:
    file_part, *names = ref.split("::")
    if not file_part.endswith(".py"):
        return file_part in all_names
    if "/" in file_part:
        candidates = [ROOT / file_part] if (ROOT / file_part).is_file() else []
    else:
        candidates = by_filename.get(file_part, [])
    return any(set(names) <= _defined_names(c) for c in candidates)


def find_broken_references(docs: dict[str, str]) -> list[str]:
    """Return one ``doc: reference`` line per reference that does not resolve."""
    by_filename, all_names = _test_index()
    broken: list[str] = []
    for doc, text in docs.items():
        for m in _PATH_RE.finditer(text):
            path = m.group(1)
            if "<" not in path and not _path_resolves(path):
                broken.append(f"{doc}: path {path}")
        for m in _BACKTICK_RE.finditer(text):
            ref = m.group(1).strip()
            if _TEST_REF_RE.match(ref) and not _test_ref_resolves(ref, by_filename, all_names):
                broken.append(f"{doc}: test {ref}")
    return sorted(set(broken))


def _real_docs() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in DOC_FILES}


def test_docs_reference_only_existing_paths_and_tests() -> None:
    broken = find_broken_references(_real_docs())
    assert not broken, "Docs reference paths or tests that do not exist:\n" + "\n".join(broken)


@pytest.mark.parametrize(
    "snippet",
    [
        "See tests/test_nope.py for details.",
        "Covered by `test_tracer.py::test_does_not_exist`.",
        "Covered by `test_nope.py`.",
        "The `test_no_such_function` test pins it.",
        "The `TestNoSuchClass` suite pins it.",
        "Workflow .github/workflows/nope.yml runs it.",
        "Mirror agent_lens/integrations/{openai,nope}.py.",
    ],
)
def test_guard_detects_drift(snippet: str) -> None:
    docs = {"README.md": _real_docs()["README.md"] + "\n" + snippet + "\n"}
    assert find_broken_references(docs), f"guard missed a stale reference: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        "See tests/test_tracer.py and `tests/integration/test_overhead.py`.",
        "Pinned by `test_server.py::test_lineage_fork_chain`.",
        "Mirror agent_lens/integrations/{openai,anthropic}.py or tests/*.py.",
        "Put yours at tests/<your_file>.py.",
        "Run `pytest tests/ -v`.",
    ],
)
def test_guard_accepts_valid_references(snippet: str) -> None:
    assert find_broken_references({"X.md": snippet}) == []
