# agent-lens — working rules

## Checks CI runs (`.github/workflows/ci.yml`)

```bash
pip install -e ".[dev]"
ruff check agent_lens/ tests/
pytest tests/ --cov=agent_lens --cov-report=term-missing
# the sdk-surface job, against the real vendor SDKs (skips become failures):
pip install -e ".[dev,sdks]" && AGENT_LENS_REQUIRE_SDKS=1 pytest tests/integrations/ -v
```

## Rules

- Renamed a test file, test function, test class or CI job? Grep `docs/`, `README.md` and
  `CONTRIBUTING.md` for the old name and fix every hit — the docs cite tests and jobs by name
  and have drifted three times (7a7f149, 45e59b9, 5141e6b). `tests/test_quickstart_invariants.py`
  only guards the README quickstart block, not these references.
- The CI perf limits in `tests/integration/test_overhead.py` (`TestOverheadBenchmark`) and
  `tests/test_tracer.py` (`TestOverhead`) are anchored to observed ubuntu-latest run timings,
  cited in the comments next to the assertions. Never loosen a threshold to get a green run;
  if a limit fails, find the regression or re-anchor from new ubuntu runs and cite them.
- Tests must never make real vendor API calls. Inject a mocked `http_client` on the SDK client
  (see `tests/integration/conftest.py`); patching `httpx` globally silently stopped working
  once the SDKs moved to `httpx2` (b60b44b, 23c7a8d).
- Pushing a `v*` tag runs `.github/workflows/release.yml` and publishes to PyPI. A published
  version cannot be replaced — bump the version in both `pyproject.toml` and
  `agent_lens/__init__.py` first, and only tag on explicit instruction.
