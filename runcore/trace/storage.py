"""Trace storage utilities for RunCore trace module."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from runcore.core.models import AgentTrace


def save_trace(trace: AgentTrace, path: str) -> None:
    """Serialize an AgentTrace to a JSON file.

    Args:
        trace: The AgentTrace to persist.
        path: Destination file path. Parent directories are created if needed.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(trace.model_dump_json(indent=2), encoding="utf-8")


def load_trace(path: str) -> AgentTrace:
    """Deserialize an AgentTrace from a JSON file.

    Args:
        path: Path to the JSON file produced by save_trace.

    Returns:
        Reconstructed AgentTrace instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file cannot be parsed as a valid AgentTrace.
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"Trace file not found: {path}")
    data = json.loads(src.read_text(encoding="utf-8"))
    return AgentTrace.model_validate(data)


def list_traces(directory: str) -> list[str]:
    """List all JSON trace files in a directory (non-recursive).

    Args:
        directory: Directory to scan.

    Returns:
        Sorted list of absolute file paths ending in .json.
    """
    dirpath = Path(directory)
    if not dirpath.is_dir():
        return []
    return sorted(str(p.resolve()) for p in dirpath.glob("*.json"))


class TraceStore:
    """Combined in-memory and optional file-backed trace store."""

    def __init__(self, persist_dir: Optional[str] = None) -> None:
        """Initialise the store.

        Args:
            persist_dir: If provided, traces are automatically saved to this
                directory whenever they are added.
        """
        self._traces: dict[str, AgentTrace] = {}
        self._persist_dir: Optional[Path] = Path(persist_dir) if persist_dir else None
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, trace: AgentTrace) -> None:
        """Add a trace to the in-memory store (and persist if configured).

        Args:
            trace: AgentTrace to store. Keyed by trace.run_id.
        """
        self._traces[trace.run_id] = trace
        if self._persist_dir:
            path = self._persist_dir / f"{trace.run_id}.json"
            save_trace(trace, str(path))

    def get(self, run_id: str) -> Optional[AgentTrace]:
        """Retrieve a trace by run_id.

        Returns None if not found.
        """
        return self._traces.get(run_id)

    def all(self) -> list[AgentTrace]:
        """Return all stored traces, ordered by start time (best-effort)."""
        traces = list(self._traces.values())
        # Sort by the timestamp of the first LLM call if available, else unsorted
        def _sort_key(t: AgentTrace) -> float:
            if t.llm_calls:
                return t.llm_calls[0].timestamp.timestamp()
            if t.tool_calls:
                return t.tool_calls[0].timestamp.timestamp()
            return 0.0

        return sorted(traces, key=_sort_key)

    def delete(self, run_id: str) -> bool:
        """Remove a trace by run_id.

        Returns True if removed, False if not found.
        """
        if run_id not in self._traces:
            return False
        del self._traces[run_id]
        if self._persist_dir:
            path = self._persist_dir / f"{run_id}.json"
            if path.exists():
                path.unlink()
        return True

    def list_run_ids(self) -> list[str]:
        """Return all stored run IDs."""
        return list(self._traces.keys())

    def __len__(self) -> int:
        return len(self._traces)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_from_disk(self) -> None:
        """Load any existing traces from the persist directory on startup."""
        if not self._persist_dir:
            return
        for file_path in list_traces(str(self._persist_dir)):
            try:
                trace = load_trace(file_path)
                self._traces[trace.run_id] = trace
            except Exception:
                # Skip corrupt files silently; callers can inspect the directory
                pass
