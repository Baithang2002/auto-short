"""Creative reasoning layer."""

from .documentary_viability import (
    DocumentaryViabilityConfig,
    DocumentaryViabilityDecision,
    DocumentaryViabilityEngine,
    DocumentaryViabilityReport,
    ViabilityCategoryScore,
)
from .content_scheduler import (
    AutonomousContentScheduler,
    ContentHistoryRecord,
    ContentHistoryStore,
    ContentSchedulerConfig,
    ScheduledCandidate,
    SchedulerResult,
    SchedulingDecision,
    TopicIdentity,
    topic_identity,
)
from .topic_metadata import (
    TopicCategory,
    TopicClassification,
    TopicMetadata,
    build_topic_metadata,
    classify_topic,
)
from .topic_sources import (
    JsonTopicSource,
    TextTopicSource,
    TopicCandidate,
    TopicSource,
    load_topic_sources,
    topic_source_for_path,
)
from .source_coverage import (
    SceneCoverage,
    SourceCoverageConfig,
    SourceCoverageDecision,
    SourceCoverageEvaluator,
    SourceCoverageReport,
    sample_scene_indexes,
)

__all__ = [
    "DocumentaryViabilityConfig",
    "DocumentaryViabilityDecision",
    "DocumentaryViabilityEngine",
    "DocumentaryViabilityReport",
    "AutonomousContentScheduler",
    "ContentHistoryRecord",
    "ContentHistoryStore",
    "ContentSchedulerConfig",
    "JsonTopicSource",
    "ScheduledCandidate",
    "SchedulerResult",
    "SchedulingDecision",
    "TextTopicSource",
    "TopicCandidate",
    "TopicIdentity",
    "TopicSource",
    "TopicCategory",
    "TopicClassification",
    "TopicMetadata",
    "ViabilityCategoryScore",
    "build_topic_metadata",
    "classify_topic",
    "load_topic_sources",
    "topic_identity",
    "topic_source_for_path",
    "SceneCoverage",
    "SourceCoverageConfig",
    "SourceCoverageDecision",
    "SourceCoverageEvaluator",
    "SourceCoverageReport",
    "sample_scene_indexes",
]
