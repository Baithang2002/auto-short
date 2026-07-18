import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autovideo.pipeline import (
    PipelineContext,
    PipelineOrchestrator,
    PipelineStage,
    PipelineStateStore,
    StageRecord,
    StageResult,
    StageStatus,
)


class PipelineOrchestratorTests(unittest.TestCase):
    def make_context(self, root: Path, *, fingerprint: str = "fp") -> PipelineContext:
        return PipelineContext(
            root_dir=root,
            output_dir=root / "output",
            run_id="run-1",
            fingerprint=fingerprint,
            topic="test topic",
        )

    def make_orchestrator(self, root: Path, stages: list[PipelineStage]) -> PipelineOrchestrator:
        return PipelineOrchestrator(stages, PipelineStateStore(root / "output" / "pipeline_state.json"))

    def test_records_stage_completion_and_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "output" / "stage.json"

            def run_stage(_ctx):
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("{}", encoding="utf-8")
                return StageResult(outputs={"artifact": str(artifact)})

            context = self.make_context(root)
            result = self.make_orchestrator(root, [PipelineStage("script_generation", run_stage)]).run(context)

            record = result.state.stages["script_generation"]
            self.assertEqual(record.status, StageStatus.COMPLETED)
            self.assertIsNotNone(record.started_at)
            self.assertIsNotNone(record.completed_at)
            self.assertIsNotNone(record.elapsed_sec)
            self.assertEqual(result.executed_stages, ["script_generation"])
            self.assertTrue((root / "output" / "pipeline_state.json").exists())

    def test_resumes_completed_stage_without_rerunning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "output" / "voice.json"
            calls = {"count": 0}

            def run_stage(_ctx):
                calls["count"] += 1
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("{}", encoding="utf-8")
                return {"artifact": str(artifact)}

            def load_stage(ctx, _record):
                ctx.values["loaded"] = True

            stage = PipelineStage("voice_generation", run_stage, load=load_stage)
            orchestrator = self.make_orchestrator(root, [stage])
            orchestrator.run(self.make_context(root))
            result = orchestrator.run(self.make_context(root))

            self.assertEqual(calls["count"], 1)
            self.assertEqual(result.resumed_stages, ["voice_generation"])

    def test_missing_output_reruns_completed_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "output" / "media.json"
            calls = {"count": 0}

            def run_stage(_ctx):
                calls["count"] += 1
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("{}", encoding="utf-8")
                return {"artifact": str(artifact)}

            orchestrator = self.make_orchestrator(root, [PipelineStage("media_selection", run_stage)])
            orchestrator.run(self.make_context(root))
            artifact.unlink()
            orchestrator.run(self.make_context(root))

            self.assertEqual(calls["count"], 2)
            self.assertTrue(artifact.exists())

    def test_missing_upstream_output_reruns_downstream_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream_artifact = root / "output" / "script.json"
            downstream_artifact = root / "output" / "voice.json"
            final_artifact = root / "output" / "timeline.json"
            calls = {"script": 0, "voice": 0, "timeline": 0}

            def script_stage(_ctx):
                calls["script"] += 1
                upstream_artifact.parent.mkdir(parents=True, exist_ok=True)
                upstream_artifact.write_text("{}", encoding="utf-8")
                return {"script": str(upstream_artifact)}

            def voice_stage(_ctx):
                calls["voice"] += 1
                downstream_artifact.parent.mkdir(parents=True, exist_ok=True)
                downstream_artifact.write_text("{}", encoding="utf-8")
                return {"voice": str(downstream_artifact)}

            def timeline_stage(_ctx):
                calls["timeline"] += 1
                final_artifact.parent.mkdir(parents=True, exist_ok=True)
                final_artifact.write_text("{}", encoding="utf-8")
                return {"timeline": str(final_artifact)}

            orchestrator = self.make_orchestrator(root, [
                PipelineStage("script_generation", script_stage),
                PipelineStage("voice_generation", voice_stage),
                PipelineStage("timeline_construction", timeline_stage),
            ])
            orchestrator.run(self.make_context(root))
            upstream_artifact.unlink()
            result = orchestrator.run(self.make_context(root))

            self.assertEqual(calls["script"], 2)
            self.assertEqual(calls["voice"], 2)
            self.assertEqual(calls["timeline"], 2)
            self.assertEqual(result.executed_stages, [
                "script_generation",
                "voice_generation",
                "timeline_construction",
            ])

    def test_resume_after_provider_failure_starts_at_failed_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = root / "output" / "script.json"
            calls = {"script": 0, "provider": 0}

            def script_stage(_ctx):
                calls["script"] += 1
                completed.parent.mkdir(parents=True, exist_ok=True)
                completed.write_text("{}", encoding="utf-8")
                return {"script": str(completed)}

            def provider_stage(_ctx):
                calls["provider"] += 1
                if calls["provider"] == 1:
                    raise RuntimeError("provider timeout")
                return {"provider": "ok"}

            stages = [
                PipelineStage("script_generation", script_stage),
                PipelineStage("voice_generation", provider_stage),
            ]
            orchestrator = self.make_orchestrator(root, stages)
            with self.assertRaises(RuntimeError):
                orchestrator.run(self.make_context(root))
            result = orchestrator.run(self.make_context(root))

            self.assertEqual(calls["script"], 1)
            self.assertEqual(calls["provider"], 2)
            self.assertEqual(result.executed_stages, ["voice_generation"])
            self.assertEqual(result.resumed_stages, ["script_generation"])

    def test_resume_after_rendering_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"timeline": 0, "render": 0}
            timeline_path = root / "output" / "timeline.json"

            def timeline_stage(_ctx):
                calls["timeline"] += 1
                timeline_path.parent.mkdir(parents=True, exist_ok=True)
                timeline_path.write_text("{}", encoding="utf-8")
                return {"timeline": str(timeline_path)}

            def render_stage(_ctx):
                calls["render"] += 1
                if calls["render"] == 1:
                    raise RuntimeError("ffmpeg failed")
                return {"final": str(root / "output" / "final.mp4")}

            stages = [
                PipelineStage("timeline_construction", timeline_stage),
                PipelineStage("rendering", render_stage),
            ]
            orchestrator = self.make_orchestrator(root, stages)
            with self.assertRaises(RuntimeError):
                orchestrator.run(self.make_context(root))
            result = orchestrator.run(self.make_context(root))

            self.assertEqual(calls["timeline"], 1)
            self.assertEqual(calls["render"], 2)
            self.assertEqual(result.executed_stages, ["rendering"])

    def test_resume_after_metadata_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"render": 0, "metadata": 0}
            final_path = root / "output" / "final.mp4"

            def render_stage(_ctx):
                calls["render"] += 1
                final_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.write_bytes(b"video")
                return {"final": str(final_path)}

            def metadata_stage(_ctx):
                calls["metadata"] += 1
                if calls["metadata"] == 1:
                    raise RuntimeError("metadata failed")
                return {"metadata": str(root / "output" / "upload_metadata.json")}

            stages = [
                PipelineStage("rendering", render_stage),
                PipelineStage("metadata", metadata_stage),
            ]
            orchestrator = self.make_orchestrator(root, stages)
            with self.assertRaises(RuntimeError):
                orchestrator.run(self.make_context(root))
            result = orchestrator.run(self.make_context(root))

            self.assertEqual(calls["render"], 1)
            self.assertEqual(calls["metadata"], 2)
            self.assertEqual(result.resumed_stages, ["rendering"])

    def test_keyboard_interrupt_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def interrupt(_ctx):
                raise KeyboardInterrupt()

            orchestrator = self.make_orchestrator(root, [PipelineStage("media_selection", interrupt)])
            with self.assertRaises(KeyboardInterrupt):
                orchestrator.run(self.make_context(root))

            state = PipelineStateStore(root / "output" / "pipeline_state.json").load()
            self.assertIsNotNone(state)
            self.assertEqual(state.status, StageStatus.INTERRUPTED)
            self.assertEqual(state.stages["media_selection"].status, StageStatus.INTERRUPTED)

    def test_force_rerun_ignores_completed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"count": 0}

            def run_stage(_ctx):
                calls["count"] += 1
                return {"value": calls["count"]}

            orchestrator = self.make_orchestrator(root, [PipelineStage("topic_selection", run_stage)])
            orchestrator.run(self.make_context(root))
            orchestrator.run(self.make_context(root), force=True)

            self.assertEqual(calls["count"], 2)

    def test_fingerprint_mismatch_starts_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"count": 0}

            def run_stage(_ctx):
                calls["count"] += 1
                return {"value": calls["count"]}

            orchestrator = self.make_orchestrator(root, [PipelineStage("topic_selection", run_stage)])
            orchestrator.run(self.make_context(root, fingerprint="first"))
            result = orchestrator.run(self.make_context(root, fingerprint="second"))

            self.assertEqual(calls["count"], 2)
            self.assertEqual(result.state.fingerprint, "second")

    def test_structured_logging_contains_stage_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logger = logging.getLogger("test.pipeline")
            logger.setLevel(logging.INFO)
            records: list[logging.LogRecord] = []

            class Handler(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    records.append(record)

            handler = Handler()
            logger.addHandler(handler)
            try:
                orchestrator = PipelineOrchestrator(
                    [PipelineStage("queue_creation", lambda _ctx: {"queue": "ok"})],
                    PipelineStateStore(root / "output" / "pipeline_state.json"),
                    logger=logger,
                )
                orchestrator.run(self.make_context(root))
            finally:
                logger.removeHandler(handler)

            statuses = [getattr(record, "status", None) for record in records]
            self.assertIn("started", statuses)
            self.assertIn("completed", statuses)
            self.assertEqual(getattr(records[-1], "stage"), "queue_creation")

    def test_state_round_trip_preserves_stage_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = PipelineStateStore(root / "output" / "pipeline_state.json")
            orchestrator = PipelineOrchestrator(
                [PipelineStage("metadata", lambda _ctx: {"metadata": "output/upload_metadata.json"})],
                store,
            )
            orchestrator.run(self.make_context(root))

            raw = json.loads((root / "output" / "pipeline_state.json").read_text(encoding="utf-8"))
            loaded = store.load()

            self.assertEqual(raw["stages"]["metadata"]["outputs"]["metadata"], "output/upload_metadata.json")
            self.assertEqual(loaded.stages["metadata"].outputs["metadata"], "output/upload_metadata.json")

    def test_state_save_retries_transient_windows_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "output" / "pipeline_state.json"
            store = PipelineStateStore(state_path)
            state = self.make_orchestrator(Path(tmp), []).run(self.make_context(Path(tmp))).state
            original_replace = os.replace

            with patch(
                "autovideo.pipeline.state.os.replace",
                side_effect=[PermissionError("locked"), original_replace],
            ) as replace_mock, patch("autovideo.pipeline.state.time.sleep") as sleep_mock:
                store.save(state)

            self.assertEqual(replace_mock.call_count, 2)
            sleep_mock.assert_called_once()
            self.assertTrue(state_path.exists())


if __name__ == "__main__":
    unittest.main()
