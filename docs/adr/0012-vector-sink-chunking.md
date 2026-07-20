# 0012 — Vector sink embeds whole-document chunks, independent of the rule engine

## Status
Accepted

## Context
§2/§10 Phase 5 says ChromaDB support should "touch only `pipeline/sinks/vector.py`," but never specifies what gets embedded or at what granularity. Per ADR-0001, entity/relationship extraction is deterministic regex over table rows / wikilink text — it does not chunk prose, so there's no existing chunking concept to reuse for the vector sink.

## Decision
The vector sink chunks each source document's raw text (simple fixed-size or paragraph-based chunking) independently of the entity-extraction rule engine, and embeds those chunks — classic semantic search over document content. Chunking parameters live in sink-specific config (`pipeline/sinks/vector.py` + its own config section), not in the docx/Obsidian rule YAML (§7).

## Consequences
- The `ExtractionResult` passed to `SinkAdapter.write` (§4.1) must carry (or the vector sink must independently have access to) the document's raw text, not just extracted entities/relationships.
- Choice of embedding model/function is an implementation detail for Phase 5, not decided here.
- This keeps the "design test" in §4.1 intact: `DryRunSink`/preview still only concerns extraction, not vector chunking, since chunking is sink-side.
