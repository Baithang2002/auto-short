"""Resumable stage orchestrator for video generation pipelines."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .state import (
    PipelineRunState,
    PipelineStateStore,
    StageRecord,
    StageStatus,
    utc_now_iso,
)

LOGGER = logging.getLogger("autovideo.pipeline")


@dataclass
class StageResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PipelineContext:
    root_dir: Path
    output_dir: Path
    run_id: str
    fingerprint: str
    topic: str = ""
    values: dict[str, Any] = field(default_factory=dict)


StageFn = Callable[[PipelineContext], StageResult | dict[str, Any] | None]
LoadFn = Callable[[PipelineContext, StageRecord], None]
ValidateFn = Callable[[PipelineContext, StageRecord], bool]


@dataclass(frozen=True)
class PipelineStage:
    name: str
    run: StageFn
    inputs: Callable[[PipelineContext], dict[str, Any]] | None = None
    load: LoadFn | None = None
    validate_outputs: ValidateFn | None = None


@dataclass(frozen=True)
class PipelineRunResult:
    state: PipelineRunState
    executed_stages: list[str]
    resumed_stages: list[str]


class PipelineOrchestrator:
    """Execute named pipeline stages and resume from persisted stage state."""

    def __init__(
        self,
        stages: list[PipelineStage],
        state_store: PipelineStateStore,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.stages = list(stages)
        self.state_store = state_store
        self.logger = logger or LOGGER

    def run(
        self,
        context: PipelineContext,
        *,
        resume: bool = True,
        force: bool = False,
    ) -> PipelineRunResult:
        state = self._load_or_create_state(context, resume=resume, force=force)
        executed: list[str] = []
        resumed: list[str] = []

        downstream_invalid = force
        for stage in self.stages:
            record = state.record_for(stage.name)
            inherited_invalid = downstream_invalid
            reran_completed_stage = False
            if (
                resume
                and not downstream_invalid
                and record.status == StageStatus.COMPLETED
                and self._outputs_valid(stage, context, record)
            ):
                if stage.load:
                    stage.load(context, record)
                resumed.append(stage.name)
                self._log(stage.name, "skipped", context, state, warnings=record.warnings)
                continue

            if record.status == StageStatus.COMPLETED and not self._outputs_valid(stage, context, record):
                downstream_invalid = True
                reran_completed_stage = True
                record.warnings.append("completed outputs missing; rerunning stage")

            self._run_stage(stage, context, state, record)
            executed.append(stage.name)
            downstream_invalid = inherited_invalid or reran_completed_stage

        state.status = StageStatus.COMPLETED
        state.current_stage = ""
        self.state_store.save(state)
        return PipelineRunResult(state=state, executed_stages=executed, resumed_stages=resumed)

    def _load_or_create_state(
        self,
        context: PipelineContext,
        *,
        resume: bool,
        force: bool,
    ) -> PipelineRunState:
        state = self.state_store.load() if resume and not force else None
        if state is not None and state.fingerprint == context.fingerprint:
            context.run_id = state.run_id
            return state
        return PipelineRunState(
            run_id=context.run_id or uuid.uuid4().hex,
            fingerprint=context.fingerprint,
            topic=context.topic,
            status=StageStatus.PENDING,
        )

    def _run_stage(
        self,
        stage: PipelineStage,
        context: PipelineContext,
        state: PipelineRunState,
        record: StageRecord,
    ) -> None:
        started = time.perf_counter()
        record.status = StageStatus.RUNNING
        record.inputs = stage.inputs(context) if stage.inputs else {}
        record.outputs = {}
        record.started_at = utc_now_iso()
        record.completed_at = None
        record.elapsed_sec = None
        record.error = None
        state.status = StageStatus.RUNNING
        state.current_stage = stage.name
        self.state_store.save(state)
        self._log(stage.name, "started", context, state)
        try:
            raw_result = stage.run(context)
            result = self._normalize_result(raw_result)
            record.outputs = result.outputs
            record.warnings = result.warnings
            record.status = StageStatus.COMPLETED
            record.completed_at = utc_now_iso()
            record.elapsed_sec = round(time.perf_counter() - started, 3)
            record.error = None
            state.current_stage = stage.name
            self.state_store.save(state)
            self._log(stage.name, "completed", context, state, record.elapsed_sec, result.warnings)
        except KeyboardInterrupt:
            record.status = StageStatus.INTERRUPTED
            record.elapsed_sec = round(time.perf_counter() - started, 3)
            record.error = "KeyboardInterrupt"
            state.status = StageStatus.INTERRUPTED
            state.current_stage = stage.name
            self.state_store.save(state)
            self._log(stage.name, "interrupted", context, state, record.elapsed_sec, ["KeyboardInterrupt"])
            raise
        except BaseException as exc:
            record.status = StageStatus.FAILED
            record.elapsed_sec = round(time.perf_counter() - started, 3)
            record.error = f"{type(exc).__name__}: {exc}"
            state.status = StageStatus.FAILED
            state.current_stage = stage.name
            self.state_store.save(state)
            self._log(stage.name, "failed", context, state, record.elapsed_sec, [type(exc).__name__])
            raise

    def _outputs_valid(
        self,
        stage: PipelineStage,
        context: PipelineContext,
        record: StageRecord,
    ) -> bool:
        if stage.validate_outputs:
            return stage.validate_outputs(context, record)
        for value in record.outputs.values():
            if isinstance(value, str) and self._looks_like_path(value) and not Path(value).exists():
                return False
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and self._looks_like_path(item) and not Path(item).exists():
                        return False
        return True

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        suffixes = (
            ".json", ".mp4", ".mp3", ".wav", ".m4a", ".ass", ".jpg", ".jpeg", ".png",
            ".webp", ".mov", ".avi", ".mkv", ".webm",
        )
        return "\\" in value or "/" in value or value.lower().endswith(suffixes)

    @staticmethod
    def _normalize_result(raw_result: StageResult | dict[str, Any] | None) -> StageResult:
        if raw_result is None:
            return StageResult()
        if isinstance(raw_result, StageResult):
            return raw_result
        return StageResult(outputs=dict(raw_result))

    def _log(
        self,
        stage_name: str,
        status: str,
        context: PipelineContext,
        state: PipelineRunState,
        elapsed_sec: float | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.logger.info(
            "pipeline_stage",
            extra={
                "run_id": state.run_id,
                "topic": context.topic,
                "stage": stage_name,
                "status": status,
                "elapsed_sec": elapsed_sec,
                "warnings": warnings or [],
            },
        )
