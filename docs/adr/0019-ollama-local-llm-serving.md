# 0019 — Ollama as local LLM serving, no external API calls

## Status
Accepted

## Context
ADR-0018's prose-extraction path needs a local LLM the backend can call. Source documents are private — confirmed during grilling (2026-07-21) that sending document content to a hosted API (Claude API, OpenAI, etc.) is unacceptable under any circumstances. The backend needs a way to run/reach a model without coupling its lifecycle (loading, memory, crashes) to the FastAPI process itself.

## Decision
Add Ollama as a new service in `docker-compose.yml`, alongside the existing Neo4j service. The FastAPI backend calls it over HTTP on localhost only — document content never leaves the machine, and there is no code path to an external API for extraction. Ollama owns model pulling/version management; the backend does not implement its own model-loading code.

Rejected alternative: an in-process model (e.g. `llama-cpp-python` loaded directly inside the FastAPI server) — rejected because it couples model lifecycle to the API server's own process, so a bad model load or an OOM risks taking down the entire backend, not just the prose-extraction feature.

## Consequences
- `docker-compose.yml` gains an `ollama` service.
- `.env`/config gains the Ollama endpoint and the model name to use — an open operational item, same category as the Neo4j/ChromaDB connection details already flagged in spec §13.
- Model choice/size is an implementation-time decision, not decided here — likely to be revisited once real prose-extraction quality is measured against ADR-0021's test gate.
- Runtime unavailability of Ollama (mid-job) is handled per ADR-0022; this ADR covers only how it's served/reached, not failure behavior.
