# Changelog

All notable changes to RunCore are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.12.0] — 2026-09-23

### Added
- **Zero-code capture** — `import runcore.auto` records every OpenAI/Anthropic call; with
  `RUNCORE_API_KEY` runs go to the dashboard, otherwise to `runcore_trace.json`.
- **Replay** — `runcore replay` re-sends recorded requests to another model and compares tool
  decisions, text (optional `--judge`), cost and latency. `recorded_tools()` re-runs an agent
  with tool results served from the recording.
- **Verified fixes** — `runcore fix` tries cheaper models and dropping never-used tools,
  verifies each by replay, writes `runcore.fix.json`; the SDK applies it at runtime.
- **Signed certificates** — `runcore verify cert.json` checks a RunCore certificate offline
  (Ed25519, 90-day expiry).

### Changed
- Traces now include the request and decision of each LLM call and full tool results
  (opt out: `RUNCORE_RECORD_CONTENT=0`).

Earlier versions were released before the open-core split.
