# Changelog

All notable changes to RunCore are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.11.0] — 2026-06-25

### Added — certify from the dashboard, no terminal

- **"Run Certification" button** that runs entirely server-side: the dashboard starts a background
  job (`POST /app/certify/run`) and polls `GET /app/certify/status/{job_id}` until done — no CLI,
  no local files.
- **Per-tenant provider keys** (`tenant_keys` table): each company saves its own Groq/Gemini key in
  Settings; the cert runner applies it to the environment for the duration of the run (serialized so
  concurrent tenants never clobber each other's keys).
- **Bring your own agent**: `HttpAgentProvider` drives a company's own OpenAI-compatible endpoint
  through the benchmark suite, so they certify their real production agent (measured tokens/cost/
  success), not just a generic model. New "Bring your own agent" tab on the certify page.
- **Reports in the dashboard**: view and download each certificate online, tenant-scoped
  (`/app/certify/report/{cert_id}` + `/download`); cross-tenant access returns 404.
- **Automatic email** (`runcore/server/email_send.py`): on completion the result + grade + badge are
  emailed with the HTML certificate attached. Env-driven (`RUNCORE_SMTP_*`); a safe no-op when SMTP
  is unconfigured so certification never fails on email.
- Dashboard now shows the **product/agent name** and an **embeddable badge snippet** for certified
  results.

### Fixed

- **SQLite writes were silently rolled back**: the `_db()` context manager never committed, so
  tenant registrations, keys and certifications did not persist on SQLite. It now commits on clean
  exit (Postgres paths already committed explicitly — the extra commit is a no-op).
- **Cloud/SDK ingest auth was broken**: a second `_require_tenant` (cookie-based) shadowed the
  Bearer-API-key version, so every `/cloud/*` endpoint bound a `Request` object as a SQL parameter.
  The dashboard helper is renamed `_require_session_tenant`; `/cloud/*` API-key auth works again.

## [0.10.1] — 2026-06-23

### Fixed — guards no longer break the agent they optimize

Running real certifications (Groq) exposed that the dedup guard **raised and aborted the whole
run** on the first duplicate tool call, which destroyed task success *and* produced fake "savings"
(the early abort just sent fewer tokens). Fixed end-to-end:

- **Cooperative dedup** (`Capture.dedup_check`, `benchmarks/agents/base.py`): a duplicate call now
  serves a compact reference from cache and the agent **keeps going** — real token saving (~13–28%
  reproducible), success preserved. No more aborts.
- **Robust success detection**: ground truth is now "did the agent call the expected tools", with
  keyword phrasing as a secondary signal (a correct answer worded differently no longer fails).
- **Certification success gate** (`MIN_SUCCESS_FOR_CERT = 60%`): an agent that fails most tasks can
  never be certified, regardless of cost/token savings — efficiency requires correctness.
- **Free-provider cost dimension**: $0-cost providers (Groq/Gemini/Ollama) now score cost via token
  reduction instead of a degenerate 0%.
- **`is_available()`** for Groq/Gemini now also checks the SDK package is importable (was reporting
  "ready" without it, causing silent run failures).
- Deterministic benchmark waste: the support suite now mandates a re-verification lookup so the
  dedup saving is reproducible rather than dependent on LLM whim.

Methodology changes documented in `docs/RUNCORE_SCORE_SPEC.md`.

## [0.10.0] — 2026-06-23

### Repositioned — "The efficiency standard for AI agents"

RunCore is now positioned around the **RunCore Score™** (the product) with the runtime engine as
the mechanism that earns it. No engine code removed — narrative + new standard surfaces.

### Added
- `docs/RUNCORE_SCORE_SPEC.md` — open, auditable Score methodology (weights, curve, CpST, fingerprint, versioning)
- `docs/MANIFESTO.md` — "Why AI agent benchmarks lie about cost" position paper
- `docs/STRATEGY.md`, `docs/REPOSITION_AUDIT.md`, `docs/GO_TO_MARKET.md` — market analysis + action plan
- `GET /leaderboard` — public efficiency leaderboard, ranked by RunCore Score, with "get listed" CTA
- `GET /badge/{grade}.svg` + `GET /badge/score/{value}.svg` — embeddable certification badges; copyable markdown in cert reports
- Settings panel: in-dashboard API-key management (`/settings/keys`, `/settings/status`) and one-click test runner (`/tests/run`)
- `Setup.command` / `RunCore.command` — double-click macOS launchers (no-terminal workflow)
- `runcore/server/config.py` — local key store (`.runcore/config.json`, gitignored)

### Changed
- README, PITCH, CLI help, certification report footer, pricing & dashboard copy lead with the efficiency-standard positioning
- Nav across all pages now includes **Leaderboard**

## [0.9.0] — 2026-06-17

### Added

**Fase 19: Real LLM Benchmark Zone**
- `runcore/providers/base.py` — `BaseProvider`, `ProviderResponse`, `ToolDefinition`, `Message` dataclasses
- `runcore/providers/groq.py` — Groq adapter (free, llama-3.1-8b-instant) via `pip install runcore[groq]`
- `runcore/providers/gemini.py` — Gemini adapter (free, gemini-1.5-flash-8b) via `pip install runcore[gemini]`
- `runcore/providers/ollama.py` — Ollama adapter (local/free, llama3.2) via `pip install runcore[ollama]`
- `benchmarks/tasks.py` — 5 standardized benchmark tasks (3 support, 2 research) with canned tool responses and deliberate inefficiency patterns that RunCore's guards catch
- `benchmarks/agents/base.py` — `BaseAgent` agentic loop: real LLM reasoning + deterministic tool responses; all spans recorded into RunCore Capture
- `benchmarks/runner.py` — full benchmark runner: baseline vs guarded, saves ATIR traces to `benchmarks/results/`, prints comparison table
- `benchmarks/reporter.py` — dark-theme HTML report generator showing before/after metrics + OptimizationAdvisor prescriptions
- `benchmarks/run_benchmark.py` — CLI entry point: `python -m benchmarks.run_benchmark run --provider groq --suite support`
- `tests/integration/conftest.py` — skip decorators (`requires_groq`, `requires_gemini`, `requires_ollama`) for CI
- `tests/integration/test_groq.py` — Groq integration tests (skipped when no API key)
- `tests/integration/test_gemini.py` — Gemini integration tests
- `tests/integration/test_ollama.py` — Ollama integration tests
- `pyproject.toml` — new optional deps: `groq`, `gemini`, `ollama`, all included in `[all]`

---

## [0.8.0] — 2026-06-17

### Added

**Fase 18: Documentation + Developer Experience**
- `README.md` — complete rewrite: value proposition, comparison table, quickstart, guards, advisor, adapters, cloud push, metrics, ATIR format, CLI reference, architecture, benchmarks
- `docs/cookbook/01_first_trace.md` — first trace in 60 seconds (no API keys)
- `docs/cookbook/02_guards.md` — runtime guards: dedup, loop break, context compression
- `docs/cookbook/03_advisor.md` — OptimizationAdvisor: prescription types, JSON export, HTTP API
- `docs/cookbook/04_cloud_push.md` — Cloud auto-push setup, env vars, tier limits
- `docs/cookbook/05_frameworks.md` — all 4 adapters with complete examples
- `docs/METRICS.md` — CpST, LRS, priority score formulas + ATIR aggregates reference
- `docs/BUSINESS.md` — business analysis: what you have, what it analyzes, what it improves, how to monetize, GitHub strategy, go-to-market

---

## [0.7.0] — 2026-06-17

### Added

**Fase 14: SDK Auto-Push to Cloud**
- `runcore/sdk/cloud.py` — cloud push configuration and fire-and-forget upload:
  - `configure(api_key, endpoint, timeout_s, on_error)` — enable auto-push globally; validates `rc_` prefix
  - `is_configured()` — True after configure() is called with a valid key
  - `push_trace(trace, block=False)` — push a single ATIR trace; runs on daemon thread by default so callers are never blocked
  - `get_config()` / `reset()` — introspection and test teardown helpers
  - `push_stats()` — `{"pushed": N, "errors": N}` counters for observability
  - Error modes: `"warn"` (default, prints warning), `"raise"`, `"silent"`
  - `RUNCORE_API_KEY` + `RUNCORE_CLOUD_ENDPOINT` env vars as alternative to `configure()`
  - Uses stdlib `urllib` only — no extra dependencies
- `runcore.configure()` exported at top level — one-line setup in any codebase
- `Capture.__exit__` now calls `push_trace(self.get_atir())` automatically when configured
- `runcore/__init__.py` exports: `configure`, `get_config`, `is_configured`, `push_trace`, `reset_cloud`

**Tests**
- `tests/unit/test_cloud_push.py` — 25 tests: configure() validation, push_trace() mock paths, stats counters, error modes (warn/raise/silent), Capture auto-push integration, _push_sync HTTP payload format (local HTTPServer), 429 error handling

---

## [0.6.0] — 2026-06-17

### Added

**Fase 12: Render.com Deploy**
- `render.yaml` — one-click deploy config for Render.com: web service + 1 GB persistent disk at `/data/cloud.db`
- `storage._DB_PATH` now reads `RUNCORE_DB_PATH` env var so the same code runs locally (`.runcore/cloud.db`) and in production (`/data/cloud.db`)

**Fase 13: Postgres Support + Billing Tiers**
- `runcore/server/billing.py` — tier model:
  - `TierLimits` dataclass with `traces_per_month`, `retention_days`, `seats`, `price_usd_month`, `features`
  - `TIERS` dict: Free (500 traces/mo, $0), Team (10k/mo, $49), Enterprise (unlimited, $299)
  - `get_limits(plan)` — lookup with free fallback
  - `check_ingest_allowed(plan, usage, batch)` — returns `(allowed, reason)` tuple
  - `has_feature(plan, feature)` — feature-flag check
  - `TIER_COMPARISON` — structured list for pricing page rendering
- `runcore/server/stripe_billing.py` — Stripe integration:
  - `create_checkout_session(tenant_id, plan, email)` — creates Stripe Checkout or returns dev placeholder
  - `create_portal_session(stripe_customer_id)` — customer billing portal URL
  - `verify_webhook(payload, sig_header)` — signature verification (dev mode: skip)
  - `handle_webhook_event(event, storage)` — handles `checkout.session.completed`, `customer.subscription.deleted/updated`, `invoice.payment_failed`
  - Fully operational in dev mode without Stripe keys; activates automatically when `STRIPE_SECRET_KEY` is set
- `runcore/server/storage.py` — Postgres + billing:
  - `DATABASE_URL` env var switches backend from SQLite → Postgres (`psycopg2`)
  - Both backends share identical DDL and all public functions
  - New columns: `stripe_customer_id`, `stripe_subscription_id`, `traces_this_month`, `month_key`
  - `upgrade_tenant_plan(tenant_id, plan, stripe_customer_id, stripe_subscription_id)`
  - `downgrade_tenant_by_customer(stripe_customer_id, plan)`
  - `get_monthly_usage(tenant_id)` — usage counter with automatic month rollover
  - `ingest_trace()` now increments `traces_this_month` counter and resets on new month
- Cloud API updates:
  - `POST /cloud/ingest` — tier limit enforced before insert; returns `usage` field with `traces_this_month`, `limit`, `plan`; returns 429 with `trace_limit_exceeded` on breach
  - `GET /cloud/stats` — now includes `plan`, `traces_this_month`, `traces_limit`
  - `POST /cloud/billing/checkout` — create Stripe Checkout Session (auth required)
  - `POST /cloud/billing/portal` — Stripe Customer Portal redirect (requires Stripe customer)
  - `POST /cloud/billing/webhook` — Stripe webhook receiver
  - `GET /cloud/billing/plans` — HTML pricing page with plan comparison table
  - `GET /cloud/billing/dev-checkout` — dev-mode placeholder when Stripe is not configured

**Tests**
- `tests/unit/test_billing.py` — 36 tests covering: tier limits logic, storage billing fields, ingest enforcement (429 on breach), stats billing fields, all billing HTTP endpoints, Stripe webhook event handling

---

## [0.5.0] — 2026-06-17

### Added

**Fase 10: Demo + Pitch**
- `examples/demo_runcore.py` — self-contained demo script (no API keys needed); shows real CpST improvement via simulated agents: **92% CpST reduction**, 11.7% token reduction, 5 advisor prescriptions with combined ~55% estimated savings; prints rich before/after table; exports ATIR trace to `demo_trace.json`
- `PITCH.md` — enterprise pitch one-pager: problem/solution/metrics/stack/integrations/business model/market timing; designed for CTOs and engineering directors

**Fase 11: RunCore Cloud SaaS**
- `runcore/server/storage.py` — SQLite-backed multi-tenant storage:
  - `create_tenant()`, `get_tenant_by_key()`, `get_tenant_by_id()`, `list_tenants()`
  - `ingest_trace()` — ATIR trace storage with upsert; full tenant isolation
  - `list_traces()` — paginated, sorted by started_at DESC
  - `get_trace()` — tenant-scoped single trace fetch
  - `tenant_stats()` — aggregate KPIs: CpST, total_cost, success_rate, agent count
- Cloud API endpoints (all under `/cloud/`):
  - `POST /cloud/tenants` — create tenant, returns API key (shown once)
  - `GET /cloud/tenants` — admin list (no API keys exposed)
  - `POST /cloud/ingest` — ingest 1+ ATIR traces; Bearer API key auth; returns trace_ids + errors
  - `GET /cloud/traces` — paginated trace list for authenticated tenant
  - `GET /cloud/traces/{id}` — full ATIR JSON for single trace
  - `GET /cloud/dashboard` — HTML dashboard with KPIs, trace table, quick-start code
  - `GET /cloud/stats` — JSON KPI summary
- Strict tenant isolation: `_require_tenant()` middleware; cross-tenant trace access returns 404
- `tests/unit/test_cloud.py` — 30 tests covering storage + all HTTP endpoints; each test gets isolated SQLite via pytest fixture

---

## [0.4.0] — 2026-06-17

### Added

**LangChain / LCEL Adapter (`runcore.sdk.adapters.langchain`)**
- `RunCoreLangChainTracer` — owns a Capture; consistent API with LangGraph/CrewAI/AutoGen adapters
  - `.wrap(runnable)` — transparent LCEL proxy that auto-injects the callback into every `invoke()` / `ainvoke()` call
  - `.callback` property — exposes `_RunCoreHandler` to pass manually into `chain.invoke(config={"callbacks": [...]})`
  - `.record_llm()`, `.record_tool()`, `.set_quality()`, `.set_success()` — manual recording API
- `RunCoreLangChainCallback` — attaches to an active `runcore.capture()` context via thread-local context stack; events silently dropped when no context is active; safe for concurrent use with multiple nested captures
- `trace_chain(agent_name, task, guards)` — convenience context manager mirroring `trace_crew()`
- `_RunCoreHandler` — internal `BaseCallbackHandler` subclass shared by both classes; records: `on_llm_start/end/error`, `on_tool_start/end/error`, `on_chain_start/end/error`
- Token extraction from both LangChain `LLMResult.llm_output.token_usage` and per-generation `generation_info.usage`
- Cost calculation via `runcore.trace.cost.calculate_llm_cost()` with fallback to `$3/Mtok`
- Graceful degradation: all classes instantiate and run without `langchain-core` installed; `ImportError` raised only when `.callback` is accessed
- `runcore.sdk.adapters.__init__` now exports all four adapter classes + helpers

**Tests**
- `tests/unit/test_adapters_langchain.py` — 35 tests covering: `_RunCoreHandler` hooks, zero-token skip, `RunCoreLangChainTracer` context manager + wrap + async + guards, `RunCoreLangChainCallback` global context forwarding + silent drop + nested captures, `trace_chain` context manager
- Fake `langchain_core` injected via `sys.modules` + `importlib.reload` so tests run without the package installed

---

## [0.3.0] — 2026-06-17

### Added

**Ecosystem Adapters (`runcore.sdk.adapters`)**
- `RunCoreLangGraphTracer` — wraps any compiled LangGraph with zero code changes via `tracer.wrap(graph.compile())`; records every node execution and LLM call as ATIR spans; supports async `ainvoke`
- `RunCoreLangGraphCallback` — alternative LangChain-style callback for `graph.compile(callbacks=[...])`
- `RunCoreCrewCallback` — full CrewAI lifecycle hooks (`on_task_start/end`, `on_tool_start/end/error`, `on_llm_start/end/error`, `on_crew_end/error`); LangChain LLMResult token extraction
- `trace_crew()` — context manager shorthand for tracing `crew.kickoff()` calls
- `RunCoreAutoGenTracer` — traces AutoGen `ConversableAgent.initiate_chat()` conversations; records message exchanges, function/tool calls, and LLM usage from AutoGen's cost tracking
- `_WrappedAutoGenAgent` — transparent proxy intercepting `generate_reply()` and `execute_function()` per agent
- All adapters support runtime `GuardConfig` guards for dedup blocking and loop detection
- `runcore.sdk.adapters.__init__` now exports all adapter classes

**Tests**
- `tests/unit/test_adapters.py` — 44 tests covering all three adapters: context manager lifecycle, span recording, error paths, async invoke, guards integration, quality scores, no-capture safety

---

## [0.2.0] — 2026-06-17

### Added

**Runtime Guards (`runcore.sdk.guards`)**
- `GuardConfig` — configure dedup, loop break, and context compression guards
- `GuardEngine` — stateful guard engine attached to a `Capture` session
- `DuplicateToolCallError` — raised when a duplicate tool call is blocked at runtime
- `LoopBreakError` — raised when Loop Risk Score exceeds the configured threshold
- `SavingsReport` — tracks blocked calls, tokens saved, and USD saved during a run
- `Capture.new_turn()` — resets turn-scoped dedup state between LLM turns
- `Capture.check_loop_risk(score)` — programmatic loop break check
- `Capture.compress_messages(messages, tokens)` — auto-compress context via guard
- `Capture.savings_report()` — returns the `SavingsReport` for the session
- `runcore.capture(..., guards=GuardConfig())` — activate guards in 3-line integration

**ATIR v1 additions**
- `ATIRTrace.savings` field — embeds guard savings report in the trace
- `ATIR_SPEC.md` — standalone spec document for external implementors

**OptimizationAdvisor**
- `POST /advice` server endpoint — analyze ATIR traces via HTTP
- `GET /runs/{run_id}/advice` — retrieve advisor report for a completed benchmark run
- `build_profile_from_atir()` — closes the loop: external traces → OptimizationProfile

**Monitoring (`runcore.monitor`)**
- `MonitorWatcher` — sliding window CpST and loop risk monitoring
- `MonitorDaemon` — polling loop with SIGINT/SIGTERM handling
- `MonitorConfig` — configurable thresholds for all alert types
- `Alert`, `AlertSeverity`, `AlertType` — structured alert models
- `ConsoleNotifier`, `WebhookNotifier`, `SlackNotifier` — multi-channel alerting
- `runcore watch` CLI command — continuous monitoring daemon

**Multi-provider benchmarking**
- `ProviderBench` — run the same tasks across multiple providers, ranked by CpST
- `ProviderConfig`, `ProviderResult` — fluent interface for provider setup
- `runcore compare-providers` CLI command — ASCII leaderboard output

**SDK**
- `auto_instrument()` — zero-code monkey-patch for Anthropic and OpenAI SDKs
- `@instrument` decorator — wrap any function with automatic capture
- `instrument_object()` — wrap a method on an existing instance
- `RunCoreLangChainCallback` — LangChain callback adapter
- `capture_from_response()` — create a trace from a single API response object
- Thread-local context stack for concurrent capture isolation

**Server (FastAPI dashboard)**
- SSE streaming — live benchmark progress via `GET /runs/{run_id}/stream`
- `POST /compare` — head-to-head config comparison by CpST
- Live progress bar in dashboard UI (updates via SSE)
- OptimizationAdvisor panel shown after each benchmark run

**CLI**
- `runcore atir validate|show|convert` — ATIR file inspection
- `runcore import` — import traces from Anthropic, OpenAI, or ATIR format
- `runcore instrument <script.py>` — auto-instrument and run any Python script

**Project**
- `PATENT_CLAIMS.md` — 6 patent claims with prior art analysis
- `README.md` — full documentation with integration examples and CLI reference
- `LICENSE` — Apache 2.0
- `pyproject.toml` — optional dependency groups: `[anthropic]`, `[openai]`, `[langchain]`, `[all]`, `[dev]`
- GitHub Actions CI — test matrix across Python 3.10, 3.11, 3.12

### Changed
- `runcore.capture()` now accepts `guards=GuardConfig()` parameter
- `ATIRTrace` gains optional `savings` field (backwards compatible)
- `compute_aggregates()` duplicate detection uses full argument values (not just keys)
- `pyproject.toml` version bumped to 0.2.0
- `runcore/__init__.__version__` set to `"0.2.0"`

### Fixed
- `agent_trace_to_atir()` correctly passes `trace_id=trace.run_id` (was `trace.trace_id`)
- `_prescribe_replacements` uses `pattern['pattern_type']` key (was `pattern['type']`)
- `suggest_code_replacement()` generates real implementation patterns per type (lookup, validate, transform, compute, http, regex) instead of `raise NotImplementedError`

---

## [0.1.0] — 2026-05-01

### Added

**Core engine**
- `AgentTrace`, `ToolCall`, `LLMCall` — internal trace models
- `TraceCollector` — records LLM and tool call spans during agent execution
- `BenchmarkRunner` — baseline + optimized benchmark pipeline with `ThreadPoolExecutor`
- `BenchmarkMetrics`, `BenchmarkComparison` — metrics and comparison models
- `ReportGenerator` — HTML, JSON, and text report generation

**Optimization modules**
- `ContextCompiler` — semantic deduplication and context compression (~28% avg token reduction)
- `LoopDetector` — 4-signal loop risk detection (duplicate calls, errors, cycles, cross-turn)
- `ReplacementDetector` — identify tool calls replaceable by deterministic Python
- `ToolOptimizer`, `ToolRegistry`, `ToolRanker` — tool schema management and ranking
- `OptimizationProfile` — derived from baseline traces; drives optimized runs

**Agents**
- `BaseAgent` — abstract agent with optimization integration
- `SupportAgent`, `ResearchAgent`, `CodingAgent` — simulated agents for benchmarking
- `RealSupportAgent` — agent using the live Anthropic SDK

**Web dashboard**
- FastAPI server with HTML dashboard, benchmark history, and report viewer
- `runcore serve` CLI command

**CLI**
- `runcore init`, `runcore profile`, `runcore benchmark`, `runcore report`
- `runcore compile`, `runcore run-real`, `runcore serve`

---

[0.2.0]: https://github.com/ptpaulinho/RunCore/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ptpaulinho/RunCore/releases/tag/v0.1.0
