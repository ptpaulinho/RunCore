# Changelog

All notable changes to RunCore are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
Versioning follows [Semantic Versioning](https://semver.org/).

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
