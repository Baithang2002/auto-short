"""Persistent state for resumable pipeline runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"


@dataclass
class StageRecord:
    name: str
    status: StageStatus = StageStatus.PENDING
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_sec: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_sec": self.elapsed_sec,
            "error": self.error,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageRecord":
        try:
            status = StageStatus(data.get("status", StageStatus.PENDING.value))
        except ValueError:
            status = StageStatus.PENDING
        return cls(
            name=str(data["name"]),
            status=status,
            inputs=dict(data.get("inputs", {})),
            outputs=dict(data.get("outputs", {})),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            elapsed_sec=(
                float(data["elapsed_sec"])
                if data.get("elapsed_sec") is not None
                else None
            ),
            error=data.get("error"),
            warnings=[str(warning) for warning in data.get("warnings", [])],
        )


@dataclass
class PipelineRunState:
    run_id: str
    fingerprint: str
    topic: str = ""
    status: StageStatus = StageStatus.PENDING
    current_stage: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    stages: dict[str, StageRecord] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def record_for(self, stage_name: str) -> StageRecord:
        record = self.stages.get(stage_name)
        if record is None:
            record = StageRecord(name=stage_name)
            self.stages[stage_name] = record
        return record

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "fingerprint": self.fingerprint,
            "topic": self.topic,
            "status": self.status.value,
            "current_stage": self.current_stage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stages": {
                name: record.to_dict()
                for name, record in self.stages.items()
            },
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineRunState":
        try:
            status = StageStatus(data.get("status", StageStatus.PENDING.value))
        except ValueError:
            status = StageStatus.PENDING
        return cls(
            version=int(data.get("version", 1)),
            run_id=str(data["run_id"]),
            fingerprint=str(data["fingerprint"]),
            topic=str(data.get("topic", "")),
            status=status,
            current_stage=str(data.get("current_stage", "")),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            stages={
                str(name): StageRecord.from_dict(record)
                for name, record in data.get("stages", {}).items()
            },
            metadata=dict(data.get("metadata", {})),
        )


class PipelineStateStore:
    """Atomic JSON store for pipeline run state."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> PipelineRunState | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return PipelineRunState.from_dict(data)

    def save(self, state: PipelineRunState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state.updated_at = utc_now_iso()
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
