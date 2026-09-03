# Testing agent-lens

agent-lens is the interactive debugger for LLM agents: pause, inspect, and fork
any agent mid-run (that is the project's own `pyproject.toml` description,
verbatim). This document explains how the test suite is organized, why the
lines between categories were drawn where they were, and what the suite does
and does not actually verify. Every number below is followed immediately by
the command that produced it, so any of them can be re-run and checked.

## Where these numbers came from

```
$ python -V
Python 3.13.0
$ python -m pytest --version
pytest 9.1.1
```

This is a single local run on one interpreter. CI runs a matrix across
multiple operating systems and Python versions, and the real vendor SDKs
(`openai`, `anthropic`, `langchain-core`, `llama-index-core`) are not
installed in this local environment — which is why a number of tests below
are reported as skipped rather than run.

## What is tested and how

The full file inventory under `tests/`:

```
$ find tests -name '*.py' | sort
tests/__init__.py
tests/conftest.py
tests/integration/__init__.py
tests/integration/conftest.py
tests/integration/test_concurrency.py
tests/integration/test_e2e_anthropic.py
tests/integration/test_e2e_openai.py
tests/integration/test_overhead.py
tests/integration/test_pause_fork.py
tests/integrations/__init__.py
tests/integrations/conftest.py
tests/integrations/test_anthropic_integration.py
tests/integrations/test_openai_integration.py
tests/integrations/test_payload_helpers.py
tests/integrations/test_pricing.py
tests/integrations/test_sdk_surface.py
tests/security/__init__.py
tests/security/test_security.py
tests/test_cli.py
tests/test_compare.py
tests/test_control.py
tests/test_export.py
tests/test_integrations.py
tests/test_mcp_server.py
tests/test_server.py
tests/test_store_search.py
tests/test_tracer.py
```

That is 27 `.py` files. Seven of them are `__init__.py`/`conftest.py`
scaffolding rather than test modules with content of their own — one
`__init__.py` and one `conftest.py` per directory except `tests/security/`,
which has only an `__init__.py`:

```
$ find tests -name '*.py' ! -name '__init__.py' ! -name 'conftest.py' | wc -l
      20
```

That leaves 20 real test modules: 9 flat, 5 in `tests/integration/`, 5 in
`tests/integrations/`, 1 in `tests/security/`. There are four real locations,
and each one exists for a distinct reason.

### `tests/*.py` — unit tests

These run offline, with no network access and no vendor SDK installed. They
exercise agent-lens's own code — the tracer, the store, the control-plane
(pause/resume/fork/inject), the CLI, the export formats, the FastAPI server,
and search — against fixtures and mocks. `tests/test_integrations.py` lives
here despite its name: it drives the OpenAI/Anthropic/LangChain/LlamaIndex
wrapper code against synthesized stub modules installed into `sys.modules`,
so it is unit-shaped even though it covers integration code. Its own
docstring history is why: those wrapper modules previously had 0% coverage
from this kind of test, because the tests that did exist required a live API
key and skipped without one.

```
$ python -m pytest tests --collect-only -q --ignore=tests/integration --ignore=tests/integrations --ignore=tests/security
... (169 individual test node IDs omitted here; they are one per test function/method across the 9 flat modules listed above)
169 tests collected in 1.34s
```

### `tests/integration/` — integration and end-to-end

This directory exercises whole flows rather than single functions: the full
pause/fork/diverge cycle and parent-span sharing (`test_pause_fork.py`),
concurrent agents writing to the same store (`test_concurrency.py`), and the
request path through the real OpenAI/Anthropic SDK request builders against a
mocked transport (`test_e2e_openai.py`, `test_e2e_anthropic.py`). The e2e pair
uses `pytest.importorskip`, so it skips outright — not fails — when the
vendor SDK package isn't installed, which is the case in this environment:

```
$ python -m pytest tests/integration --collect-only -q -rs
... (11 individual test node IDs omitted here; they are the tests in the 5 modules listed above)
SKIPPED [1] tests/integration/test_e2e_anthropic.py:21: anthropic SDK not installed
SKIPPED [1] tests/integration/test_e2e_openai.py:21: openai SDK not installed
11 tests collected in 0.14s
```

`test_overhead.py` is also in this directory: a timing budget, not a load
test, asserting that 100 traced calls complete under 500ms on a developer
machine. Its own skip condition is covered in "What this suite does not
catch" below.

### `tests/integrations/` — vendor integration surface

Four of the five modules here (`test_openai_integration.py`,
`test_anthropic_integration.py`, `test_payload_helpers.py`,
`test_pricing.py`) prove the wrapper logic — patch/unpatch lifecycle, event
capture, redaction, cost estimation — against a stub SDK installed into
`sys.modules`, deliberately kept stubbed so the multi-OS, multi-Python matrix
stays offline and isn't coupled to how often a vendor ships a release.
`test_sdk_surface.py` is the odd one out: it runs against the real vendor
packages when they're present, and is covered separately below because it
carries its own history.

```
$ python -m pytest tests/integrations --collect-only -q
... (90 individual test node IDs omitted here; they are the tests in the 5 modules listed above)
90 tests collected in 0.25s
```

### `tests/security/` — security tests

One module, `test_security.py`. Its docstring lists five concerns: API keys
never appearing in SQLite after a traced call, the server binding to
`127.0.0.1` rather than `0.0.0.0` by default, tool output containing
`<script>` being escaped on HTML export, path traversal on replay being
rejected, and no pickle/eval paths in trace-data deserialization.

```
$ python -m pytest tests/security --collect-only -q
... (17 individual test node IDs omitted here; they are the tests in test_security.py)
17 tests collected in 0.38s
```

## Totals, coverage and CI

```
$ python -m pytest --collect-only -q
... (287 individual test node IDs omitted here; the coverage table pytest prints on a collect-only run is also omitted)
287 tests collected in 0.42s
```

(169 + 11 + 90 + 17 = 287, matching the four per-directory collections above.
Note this 287 will not equal "passed + skipped" from the run below, 289 — two
of the skips, `test_e2e_openai.py` and `test_e2e_anthropic.py`, use
`pytest.importorskip` and are skipped at collection time, before they become
individually numbered items, so `--collect-only` reports "0 items / 2 skipped"
for those two files rather than counting them among the 287.)

Full run, with coverage (the terminal `--cov-report=term-missing` table is
already wired up via `addopts` in `pyproject.toml`, so a plain `pytest -q`
produces it):

```
$ python -m pytest -q
... (per-test pass/skip lines and warning details omitted; the coverage table below is complete and unedited)
Name                                    Stmts   Miss  Cover   Missing
---------------------------------------------------------------------
agent_lens/__init__.py                     17      0   100%
agent_lens/_textutil.py                    45     10    78%   26, 28, 34-42, 65, 69, 76
agent_lens/cli.py                         161      6    96%   118-120, 292-293, 298
agent_lens/compare.py                      45      0   100%
agent_lens/control.py                     113      1    99%   100
agent_lens/dashboard_launcher.py           37     25    32%   48-91, 96, 101
agent_lens/export.py                       75      0   100%
agent_lens/integrations/__init__.py         0      0   100%
agent_lens/integrations/_pricing.py        16      0   100%
agent_lens/integrations/anthropic.py      143      0   100%
agent_lens/integrations/langchain.py      129     15    88%   23, 27, 96, 137, 145, 168, 196, 206, 227, 235, 258, 286, 296, 317, 325
agent_lens/integrations/llamaindex.py      63      4    94%   26-27, 45, 117
agent_lens/integrations/openai.py         120      0   100%
agent_lens/mcp_server.py                   48      0   100%
agent_lens/models.py                       69      1    99%   73
agent_lens/server.py                      243     28    88%   141, 171-174, 184, 206-207, 342, 354, 366, 386, 401, 440, 468-482
agent_lens/store.py                       214     11    95%   133, 135, 144-146, 305, 318-320, 332, 347, 544
agent_lens/tracer.py                      232     18    92%   83-84, 149-155, 192, 392-400, 457-459
---------------------------------------------------------------------
TOTAL                                    1770    119    93%
272 passed, 17 skipped, 195 warnings in 6.25s
```

`agent_lens/dashboard_launcher.py` is the weakest module in this table at 32%
covered — none of the suite drives the actual browser-launch path. The
weakest line in the wrapper code is `_textutil.py` at 78%.

The 17 skips break down as: the 2 real-SDK e2e tests shown above, plus 15 in
`test_sdk_surface.py` (checked with `-rs`) — all of them skip because none of
`openai`, `anthropic`, `langchain-core` or `llama-index-core` is installed in
this environment. That is expected here and is the subject of the first item
under "What this suite does not catch" below.

CI job names, from the workflow file:

```
$ grep -nE '^  [a-z-]+:$' .github/workflows/ci.yml
4:  push:
10:  test:
54:  sdk-surface:
83:  security:
112:  build:
```

That regex also matches the `push:` key under the workflow's `on:` trigger
block, which is why it returns five lines rather than four — the four actual
jobs are `test`, `sdk-surface`, `security`, and `build`. `test` runs the
default suite shown above; `sdk-surface` re-runs `tests/integrations/` with
the real vendor SDKs installed; `security` runs the security tests plus a
dependency audit; `build` packages the project.

## Why one test file runs against the real SDKs

Everything in `tests/integrations/` runs against stub SDKs except
`test_sdk_surface.py`. That split exists because of this project's own
history: the OpenAI integration once patched `Completions.acreate`, an
attribute no `openai>=1.0` has ever defined, and that broke
`agent_lens.install()` for every real user. A stub SDK could not catch it,
because a stub only proves the wrapper logic is internally consistent — it
cannot prove the wrapper is bolted to attributes the vendor package actually
ships.

```
$ sed -n '1,19p' tests/integrations/test_sdk_surface.py
"""
Vendor SDK surface.

The rest of the integration suite runs against stubs, which proves the wrapper
logic but cannot prove the wrapper is bolted to attributes the vendors actually
ship. That gap is exactly how the OpenAI integration came to patch
``Completions.acreate`` — an attribute no openai >=1.0 has ever defined — and
crash ``agent_lens.install()`` for every real user.

The callback integrations have the same seam in a different shape. They do not
patch anything; they subclass a vendor base class and override the methods the
framework calls. If a vendor renames one, the override is simply never invoked —
no error, no span, no trace. That is the identical failure mode as the async bug,
and equally invisible to a stub, because a stub base class accepts any override
name we care to define.

These tests close both. They skip when the vendor is absent, so the default
offline run is unaffected, and they fail loudly in the job that installs them.
"""
```

The `sdks` extra pins the four real vendor packages:

```
$ grep -n 'sdks = \[' -A5 pyproject.toml
53:sdks = [
54-    "openai>=1.0.0",
55-    "anthropic>=0.30.0",
56-    "langchain-core>=0.2.0",
57-    "llama-index-core>=0.11.0",
58-]
```

and the CI job that installs it promotes a skip to a hard failure, because a
skipped surface check would otherwise look indistinguishable from a pass:

```
$ grep -n 'sdk-surface' -A15 .github/workflows/ci.yml
54:  sdk-surface:
55-    name: Vendor SDK surface
56-    runs-on: ubuntu-latest
57-    timeout-minutes: 10
58-    steps:
59-      - uses: actions/checkout@v4
60-
61-      - name: Set up Python 3.12
62-        uses: actions/setup-python@v5
63-        with:
64-          python-version: "3.12"
65-          cache: "pip"
66-
67-      # The integration suite runs against stubs everywhere else, which proves
68-      # the wrapper logic but not that it is bolted to attributes the vendors
69-      # still ship. This job installs the real SDKs so that check actually runs
```

(The job continues with `pip install -e ".[dev,sdks]"` and then
`pytest tests/integrations/ -v` with `AGENT_LENS_REQUIRE_SDKS=1` set, which is
what turns a skip into a failure inside that job.)

The callback-based integrations (LangChain, LlamaIndex) have the same gap in
a different shape: they subclass a vendor base class and override the hooks
the framework calls, and if a vendor renames one of those hooks, the override
is simply never invoked — no exception, no missing span, nothing a stub base
class could ever surface, since a stub accepts any override name.

## What this suite does not catch

- **The default offline run never imports the real vendor SDKs.** As the
  `pytest -q` output above shows, `test_e2e_openai.py`, `test_e2e_anthropic.py`
  and all of `test_sdk_surface.py` are skipped in a normal local or default-CI
  run, because none of `openai`, `anthropic`, `langchain-core` or
  `llama-index-core` is installed. API drift against those packages is caught
  only when the separate `sdk-surface` job runs with the `[sdks]` extra
  installed — if that job is ever skipped, disabled, or its failure ignored,
  drift goes undetected until a real user hits it, which is exactly what
  happened before `test_sdk_surface.py` existed.

- **`pip-audit` in the security job does not fail the build.**

  ```
  $ grep -n 'continue-on-error' .github/workflows/ci.yml
  110:        continue-on-error: true
  ```

  A dependency vulnerability finding is logged, not enforced. Someone has to
  go read the job output; a green check mark on that job does not mean the
  audit came back clean.

- **The performance budget is never checked in CI.**

  ```
  $ sed -n '19,22p' tests/integration/test_overhead.py
  @pytest.mark.skipif(
      sys.platform == "win32" or bool(os.environ.get("CI")),
      reason="Performance benchmarks are only meaningful on local hardware",
  )
  ```

  `test_overhead.py` skips whenever the `CI` environment variable is set (or
  on Windows), so the "100 traced calls under 500ms" budget only actually
  holds, and is only actually checked, on whatever machine a developer
  happens to run it on locally. It is not part of what makes CI green.

- **Two modules are weakly covered even where tests do run.**
  `agent_lens/dashboard_launcher.py` sits at 32% in the coverage table above,
  and `agent_lens/_textutil.py` at 78% — neither number is enforced as a
  floor, so a regression in either could ship without any test noticing.

## Noted but not actioned here

`tests/integrations/conftest.py` states that the stub SDK's shape is pinned
by `test_sdk_surface.py::test_stub_matches_real_sdk_surface`; no test of that
name exists in that file today (the real pinning is done per-class, across
several differently-named tests). This is a documentation task, not a test
change, so the stale cross-reference is left as-is rather than fixed here.
