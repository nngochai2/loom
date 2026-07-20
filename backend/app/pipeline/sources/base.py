from typing import Protocol, runtime_checkable

from app.pipeline.types import LoadedDoc, SourceDoc


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
