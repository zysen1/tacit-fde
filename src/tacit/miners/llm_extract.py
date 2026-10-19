"""Optional LLM text extractor.

Offline runs never construct this. There is no network call and no fabricated score.
"""

from __future__ import annotations

from typing import Protocol


class LLMExtractor(Protocol):
    def extract(self, text: str, path: str) -> list[dict]:
        """Return candidate rule dicts for one document."""


class LLMExtractionUnavailable(RuntimeError):
    """Raised when `--llm` is requested without an injected extractor."""


class UnavailableLLMExtractor:
    def extract(self, text: str, path: str) -> list[dict]:
        raise LLMExtractionUnavailable(
            "LLM extraction is optional and not configured. "
            "Offline mode uses the pattern miner; no API key is read or required."
        )
