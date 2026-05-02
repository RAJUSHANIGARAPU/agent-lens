# Problem Statement: agent-lens

## One-Page Problem Statement

Every LLM agent is a black box the moment it starts running.

You write a multi-step agent—research, plan, execute, verify. You test it. It works. You push it to staging. It fails. The failure is somewhere inside a chain of ten LLM calls, three tool invocations, and two conditional branches. Your observability stack shows you tokens in, tokens out, and a latency spike. It tells you *that* something failed. It tells you nothing about *why*, and it gives you no way to fix the specific decision point and re-run from there.

This is the state of AI agent debugging in 2026. Every practitioner building agents—whether with LangChain, raw OpenAI calls, or custom frameworks—debugs by adding print statements, re-running the whole agent from scratch, and hoping the random temperature doesn't produce a different failure path. When they need to test a hypothesis ("what if I had said X instead of Y at step 3?"), they edit the source code, restart the run, wait through all the preceding steps, and check the result. A 90-second agent run with five LLM calls costs 90 seconds *minimum* per hypothesis. Real debugging sessions routinely take hours.

Existing observability tools (Langfuse, LangSmith, Phoenix, AgentOps, Helicone, Lunary, Braintrust, OpenLLMetry, Opik) all solve the same problem: *logging and replay for analysis*. They capture traces. They let you view what happened. Some let you define experiments. None of them let you **pause a live running agent, edit its state mid-execution, and fork a new run from that exact point**—without restarting, without re-running preceding steps, without changing your application code.

agent-lens is the interactive debugger for LLM agents. Like `pdb` changed Python debugging by letting you stop execution and inspect/modify state at any line, agent-lens changes agent debugging by letting you stop at any LLM call, edit the messages or tool results, and fork a parallel execution branch. This is not a logging tool. It is a runtime debugger.

The product is zero-infrastructure: one `pip install`, one `agent_lens.install()` call, and a local web dashboard is running at `localhost:7878`. No cloud accounts. No API keys for the tool itself. No data leaves the machine. This matters because agents often handle sensitive data—customer information, internal documents, credentials—and practitioners rightly refuse to route that through third-party cloud services.

---

## User Personas

### Persona 1: Maya — ML Engineer at a 40-person startup

**Role**: The only ML engineer. Owns the customer-facing AI assistant built on LangChain + GPT-4o. The assistant does multi-step research and drafts reports.

**Current workflow**: When something breaks, Maya drops `print(messages)` into the chain, re-runs with a hardcoded test query, and manually inspects the terminal output. She uses LangSmith for production traces but the free tier is rate-limited. When she needs to test whether a different system prompt at step 2 would have fixed step 4, she edits `chain.py`, re-runs the full 45-second pipeline, checks the result. Six iterations per debugging session minimum.

**Pain**: 4-6 hours lost per complex bug. Can't test counterfactuals without restarting. LangSmith shows her what happened but not how to fix it interactively. The cloud data residency question is a blocker for enterprise customers she's pitching.

**Willingness to pay**: $29-49/month for a local-first tool that removes cloud data exposure concerns. Would expense it without approval needed at that price point.

---

### Persona 2: Arjun — Senior Software Engineer at a 200-person enterprise

**Role**: Tech lead on an internal agent that processes HR documents using Claude. The agent chains three tool calls and two LLM reasoning steps.

**Current workflow**: Agent failures are reported by HR business users via Jira. Arjun tries to reproduce locally, usually can't because input data is PII-restricted. He gets a sanitized version of the input, runs the agent, sees a different failure. He instruments with structured logging, deploys to staging, gets approval for a production trace (takes a day), reviews the trace, forms a hypothesis, updates the code, goes through the PR process.

**Pain**: Single debugging cycle is 1-3 days due to process overhead. Cannot load production data into cloud observability tools (GDPR + internal policy). Has no way to test "what if the tool returned X instead of Y" without mocking.

**Willingness to pay**: $99-199/month as a team seat, or $2k-5k/year for an on-premise enterprise license. Corporate card, no individual approval needed for tooling under $500/year.

---

### Persona 3: Sofia — Independent AI consultant

**Role**: Builds bespoke AI pipelines for 3-4 clients at a time. Uses everything: OpenAI, Anthropic, LangChain, custom frameworks.

**Current workflow**: Each client has different infra. She uses LangSmith on some projects, nothing on others, custom logging on a few. Debugging is a mix of Jupyter notebooks, print statements, and re-running pipelines. Billing visibility matters—she charges clients by token usage.

**Pain**: No single tool that works across all her frameworks. LangSmith requires a separate account per client. She can't share traces with clients without giving them cloud credentials. Token cost breakdowns are manual.

**Willingness to pay**: $15-25/month for a personal license. Would pay more for a "share a trace" feature that generates a portable HTML file (no cloud needed).

---

## Competitive Matrix

| Tool | License | Setup Time | Killer Feature | Weakness | Categorical Difference from agent-lens |
|------|---------|-----------|----------------|----------|----------------------------------------|
| **Langfuse** | MIT (self-host) / SaaS | 30–120 min (Docker or cloud account) | Production-grade tracing + evaluations + prompt management | Cloud or self-hosted Docker required; no live intervention; read-only debug | Observability platform, not a runtime debugger |
| **LangSmith** | Proprietary SaaS | 15–30 min (API key + SDK) | Tight LangChain integration; dataset management; human feedback loops | LangChain-only tight coupling; data leaves your machine; no pause/fork | Dataset & evaluation workflow, not interactive debugging |
| **Phoenix (Arize)** | Apache 2.0 | 20–40 min | Visual span trees; embedding drift detection; UMAP visualizations | Arize ecosystem lock-in for advanced features; no intervention capability | ML observability & drift detection, not agent debugging |
| **AgentOps** | MIT | 5–15 min | Fast setup; multi-agent session tracking; replay visualization | Limited framework support; no live control; cloud-only storage | Agent monitoring & analytics, not interactive intervention |
| **Braintrust** | Proprietary SaaS | 20–45 min | Prompt playground + evaluation experiments + CI integration | Expensive at scale; requires cloud; evaluation-centric, not run-time | Evaluation & experimentation platform, not real-time control |
| **Helicone** | Apache 2.0 | 5–10 min (proxy swap) | Zero-code integration via proxy; caching; rate limiting | Proxy architecture adds latency; limited to HTTP-level visibility; no agent semantics | LLM proxy & gateway, not agent-aware debugger |
| **Lunary** | Apache 2.0 | 10–20 min | Open-source LangChain + OpenAI tracing; user-session tracking | Small ecosystem; limited framework support; no intervention | Session logging & analytics, not interactive control |
| **OpenLLMetry** | Apache 2.0 | 15–30 min | OpenTelemetry-based; integrates with existing OTEL infrastructure | Requires OTEL backend (Grafana/Jaeger/etc.); no agent-specific intervention | OTEL instrumentation layer, not standalone agent tool |
| **Opik** | Apache 2.0 | 15–30 min | LLM evaluation + annotation + dataset management | Comet ML-backed; evaluation-heavy, not debug-heavy; no live control | Evaluation & dataset curation, not debugging |

**Summary of categorical difference**: Every competitor is an *observability and analytics* tool—they capture what happened so you can analyze it later. agent-lens is a *runtime control* tool—it lets you intervene in what is happening now.

---

## Falsifiable Hypotheses

**H1 — The pause-fork feature will be the primary acquisition driver.**
*Falsifiable by*: Run a 90-day cohort study. If >60% of new users cite "pause and fork" as their reason for trying agent-lens (via onboarding survey), the hypothesis holds. If the majority cite "local-first / no cloud" or "easy setup," it falsifies the acquisition driver hypothesis.

**H2 — Zero-infrastructure local-first deployment removes the #1 adoption blocker for enterprise practitioners.**
*Falsifiable by*: Survey 50 potential enterprise users with this question: "What is the primary reason you haven't adopted a cloud-based agent observability tool?" If fewer than 40% cite data residency, security policy, or setup complexity, the hypothesis is falsified. If >40% cite those reasons AND agent-lens's local model resolves them (confirmed by follow-up), the hypothesis holds.

**H3 — The 5ms per call overhead budget is achievable without compromising trace fidelity.**
*Falsifiable by*: Run the `tests/integration/test_overhead.py` benchmark suite on Python 3.10, 3.11, 3.12 across 1000 traced no-op calls. If median overhead per call exceeds 5ms on any supported Python version, the hypothesis is falsified. Fidelity is measured by comparing stored event counts to expected counts (zero event loss).

---

## Red-Team: Five Reasons This Might Fail

**1. The pause-fork feature is technically impressive but rarely needed in practice.**
Most agent bugs are reproducible from scratch in under 30 seconds. Practitioners may find it faster to just re-run. The feature is a differentiator in demos but may not appear in daily usage patterns. If "pause and fork" stays a demo feature, agent-lens is just another observability tool with more complexity and less ecosystem support than Langfuse.

**2. Framework fragmentation is a distribution trap.**
LangChain, LlamaIndex, AutoGen, CrewAI, custom raw-SDK agents—the list of "agent frameworks" grows weekly. Maintaining quality integrations for all of them is O(n) engineering work. If agent-lens only works well with OpenAI + LangChain, the addressable market is smaller than the pitch suggests. Competitors with larger teams (LangSmith, Langfuse) can out-integrate.

**3. The local-first model is a feature for privacy and a bug for collaboration.**
"No data leaves your machine" is compelling for solo debugging but actively harmful for team debugging. When Maya (Persona 1) wants to share a trace with a colleague or a contractor, she has no easy path. Cloud tools solve this trivially. Every "share trace" feature in agent-lens requires either a file export (friction) or a cloud relay (contradicts the value prop).

**4. The target user has high tolerance for bad DX but zero tolerance for overhead.**
ML engineers will tolerate a rough UI. They will not accept a tool that makes their agents 10% slower, or that occasionally drops events, or that crashes their agent process with an unhandled exception in the tracer. Any reliability or overhead issue will be treated as a show-stopper and the tool will be ripped out. The quality bar for "invisible" infrastructure tooling is extremely high.

**5. The SQLite single-file approach breaks at scale.**
A single SQLite database works beautifully for debugging solo. At 1000 concurrent events per second (plausible for an automated pipeline running dozens of agents in CI), SQLite's write lock becomes a bottleneck. If agent-lens is positioned as a CI/CD observability tool (which several competitors target), the storage layer will be the first thing that needs replacing—and if it requires a migration to PostgreSQL, the "zero infrastructure" pitch is gone.
