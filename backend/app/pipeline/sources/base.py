from typing import Any, Protocol, runtime_checkable

from app.pipeline.types import ExtractionResult, LoadedDoc, SourceDoc


@runtime_checkable
class SourceAdapter(Protocol):
    source_type: str  # "obsidian" | "docx" | ...

    def discover(self, source_path: str) -> list[SourceDoc]:
        """Enumerate documents under `source_path`."""
        ...

    def load(self, doc: SourceDoc) -> LoadedDoc:
        """Read one document into text + structural metadata.

        For Obsidian: wikilinks are emitted as explicit edges here, tagged
        origin='explicit' — they bypass extraction inference.
        """
        ...

    def extract(self, loaded: LoadedDoc, config: Any) -> ExtractionResult:
        """Turn a loaded document into entities/relationships.

        Not in the spec's original two-method sketch — added because
        Obsidian's wikilink-target resolution needs cross-document state
        (a title -> node_id map built once per vault in `discover()`) that
        only the adapter instance can hold. Docx implements this too, just
        without needing that state.
        """
        ...
