"""Documentary visual grammar and composition activation policy.

The grammar engine is an editorial decision layer between ShotPlan and the
HybridVisualComposer. It does not fetch media or render scenes; it decides when
composition is appropriate and records why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VisualGrammar:
    """Reusable documentary grammar for a family of scenes."""

    grammar_id: str
    label: str
    trigger_terms: tuple[str, ...]
    preferred_visual_sequence: tuple[str, ...]
    preferred_media_types: tuple[str, ...]
    acceptable_composition_templates: tuple[str, ...]
    preferred_provider_priority: tuple[str, ...]
    fallback_order: tuple[str, ...]
    max_composition_ratio: float = 0.25
    max_generic_motion_ratio: float = 0.10
    min_real_archive_ratio: float = 0.75
    documentary_pacing: str = "real_visuals_first"
    diversity_expectations: tuple[str, ...] = ("avoid_repeated_templates", "alternate_real_and_diagram")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the grammar for diagnostics."""

        return {
            "grammar_id": self.grammar_id,
            "label": self.label,
            "preferred_visual_sequence": list(self.preferred_visual_sequence),
            "preferred_media_types": list(self.preferred_media_types),
            "acceptable_composition_templates": list(self.acceptable_composition_templates),
            "preferred_provider_priority": list(self.preferred_provider_priority),
            "fallback_order": list(self.fallback_order),
            "max_composition_ratio": self.max_composition_ratio,
            "max_generic_motion_ratio": self.max_generic_motion_ratio,
            "min_real_archive_ratio": self.min_real_archive_ratio,
            "documentary_pacing": self.documentary_pacing,
            "diversity_expectations": list(self.diversity_expectations),
        }


@dataclass(frozen=True)
class VisualGrammarDecision:
    """Per-scene editorial decision consumed by media fallback code."""

    scene_index: int
    grammar_id: str
    grammar_label: str
    grammar_confidence: float
    scene_importance: str
    visual_goal: str
    composition_template: str
    composition_suitability: float
    composition_confidence: str
    should_compose: bool
    preferred_media_types: tuple[str, ...] = ()
    repaired_queries: tuple[str, ...] = ()
    reason: str = ""
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision for MediaAsset diagnostics."""

        return {
            "scene_index": self.scene_index,
            "grammar_id": self.grammar_id,
            "grammar_label": self.grammar_label,
            "grammar_confidence": round(self.grammar_confidence, 3),
            "scene_importance": self.scene_importance,
            "visual_goal": self.visual_goal,
            "composition_template": self.composition_template,
            "composition_suitability": round(self.composition_suitability, 3),
            "composition_confidence": self.composition_confidence,
            "should_compose": self.should_compose,
            "preferred_media_types": list(self.preferred_media_types),
            "repaired_queries": list(self.repaired_queries),
            "reason": self.reason,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class CompositionBudget:
    """Track composition ratio across one video generation run."""

    total_scenes: int
    max_composition_ratio: float = 0.25
    max_generic_motion_ratio: float = 0.10
    min_real_archive_ratio: float = 0.75
    real_count: int = 0
    archive_count: int = 0
    composed_count: int = 0
    generic_motion_count: int = 0
    explainer_count: int = 0

    @property
    def max_composed_scenes(self) -> int:
        return max(1, int(self.total_scenes * self.max_composition_ratio))

    @property
    def max_generic_motion_scenes(self) -> int:
        return max(0, int(self.total_scenes * self.max_generic_motion_ratio))

    def to_dict(self) -> dict[str, Any]:
        """Serialize current budget usage."""

        denominator = max(1, self.total_scenes)
        return {
            "total_scenes": self.total_scenes,
            "max_composition_ratio": self.max_composition_ratio,
            "max_generic_motion_ratio": self.max_generic_motion_ratio,
            "min_real_archive_ratio": self.min_real_archive_ratio,
            "max_composed_scenes": self.max_composed_scenes,
            "max_generic_motion_scenes": self.max_generic_motion_scenes,
            "real_count": self.real_count,
            "archive_count": self.archive_count,
            "composed_count": self.composed_count,
            "generic_motion_count": self.generic_motion_count,
            "explainer_count": self.explainer_count,
            "real_footage_percent": round((self.real_count / denominator) * 100, 2),
            "archive_percent": round((self.archive_count / denominator) * 100, 2),
            "composed_percent": round((self.composed_count / denominator) * 100, 2),
            "explainer_percent": round((self.explainer_count / denominator) * 100, 2),
        }


class VisualGrammarEngine:
    """Choose reusable documentary grammar and gate Hybrid Composer activation."""

    def __init__(
        self,
        *,
        topic: str,
        total_scenes: int,
        grammars: tuple[VisualGrammar, ...] | None = None,
    ) -> None:
        self.topic = topic
        self.grammars = grammars or default_visual_grammars()
        self.selected_grammar, self.grammar_confidence = self._select_grammar(topic)
        self.budget = CompositionBudget(
            total_scenes=max(1, total_scenes),
            max_composition_ratio=self.selected_grammar.max_composition_ratio,
            max_generic_motion_ratio=self.selected_grammar.max_generic_motion_ratio,
            min_real_archive_ratio=self.selected_grammar.min_real_archive_ratio,
        )
        self.decisions: list[dict[str, Any]] = []

    def decide(
        self,
        *,
        scene_index: int,
        narration: str,
        queries: tuple[str, ...] | list[str],
        shot_intent: Any | None = None,
    ) -> VisualGrammarDecision:
        """Return the scene-level grammar decision before provider search."""

        visual_goal = _enum_value(getattr(shot_intent, "visual_goal", ""), "show")
        scene_importance = str(getattr(shot_intent, "scene_importance", "") or "supporting")
        text = _norm(" ".join([self.topic, narration, " ".join(queries)]))
        scene_grammar, scene_confidence = self._select_grammar(text)
        if scene_confidence > self.grammar_confidence:
            self._adopt_grammar(scene_grammar, scene_confidence)
        template, suitability, confidence = self._template_for(text, visual_goal)
        should_compose = bool(template) and confidence != "low"
        rejection_reason = ""
        if not template:
            rejection_reason = "no suitable grammar composition template"
        elif confidence == "low":
            should_compose = False
            rejection_reason = "low confidence composition"
        elif scene_importance in {"hook", "main_reveal"} and confidence != "high":
            should_compose = False
            rejection_reason = "critical scene requires high composition confidence"
        reason = (
            f"{self.selected_grammar.label} grammar recommends {template}"
            if should_compose else
            f"{self.selected_grammar.label} grammar prefers real/archive media"
        )
        decision = VisualGrammarDecision(
            scene_index=scene_index,
            grammar_id=self.selected_grammar.grammar_id,
            grammar_label=self.selected_grammar.label,
            grammar_confidence=self.grammar_confidence,
            scene_importance=scene_importance,
            visual_goal=visual_goal,
            composition_template=template,
            composition_suitability=suitability,
            composition_confidence=confidence,
            should_compose=should_compose,
            preferred_media_types=self.selected_grammar.preferred_media_types,
            repaired_queries=self._repair_queries(text, tuple(queries)),
            reason=reason,
            rejection_reason=rejection_reason,
        )
        self.decisions.append({"event": "decision", **decision.to_dict()})
        return decision

    def allow_composition(self, decision: VisualGrammarDecision, *, scene_type: str) -> tuple[bool, str]:
        """Return whether the composer may create an asset for this decision."""

        is_generic = scene_type == "documentary_explainer" or decision.composition_template == "generic_motion"
        if not decision.should_compose:
            reason = decision.rejection_reason or "grammar rejected composition"
            self.decisions.append({"event": "composition_rejected", "scene_index": decision.scene_index, "reason": reason})
            return False, reason
        if self.budget.composed_count >= self.budget.max_composed_scenes:
            reason = "composition budget exceeded"
            self.decisions.append({"event": "composition_rejected", "scene_index": decision.scene_index, "reason": reason})
            return False, reason
        if is_generic and self.budget.generic_motion_count >= self.budget.max_generic_motion_scenes:
            reason = "generic motion graphics budget exceeded"
            self.decisions.append({"event": "composition_rejected", "scene_index": decision.scene_index, "reason": reason})
            return False, reason
        return True, "composition allowed"

    def register_real_asset(self, *, provider: str) -> None:
        """Record a real provider/local asset selection."""

        provider_key = str(provider or "").lower()
        self.budget.real_count += 1
        if provider_key in {"wikimedia", "smithsonian", "europeana", "loc", "internet_archive", "nasa", "noaa", "usgs"}:
            self.budget.archive_count += 1

    def register_composed_asset(self, *, scene_type: str) -> None:
        """Record a composed visual selection."""

        self.budget.composed_count += 1
        if scene_type == "documentary_explainer":
            self.budget.generic_motion_count += 1

    def register_explainer(self) -> None:
        """Record a final fallback explainer/card selection."""

        self.budget.explainer_count += 1

    def report(self) -> dict[str, Any]:
        """Return video-level visual grammar diagnostics."""

        return {
            "topic": self.topic,
            "selected_grammar": self.selected_grammar.to_dict(),
            "grammar_confidence": round(self.grammar_confidence, 3),
            "composition_budget": self.budget.to_dict(),
            "grammar_decisions": self.decisions,
        }

    def _adopt_grammar(self, grammar: VisualGrammar, confidence: float) -> None:
        self.selected_grammar = grammar
        self.grammar_confidence = confidence
        self.budget.max_composition_ratio = grammar.max_composition_ratio
        self.budget.max_generic_motion_ratio = grammar.max_generic_motion_ratio
        self.budget.min_real_archive_ratio = grammar.min_real_archive_ratio

    def _select_grammar(self, topic: str) -> tuple[VisualGrammar, float]:
        text = _norm(topic)
        best = self.grammars[-1]
        best_score = 0
        for grammar in self.grammars:
            score = sum(1 for term in grammar.trigger_terms if term in text)
            if score > best_score:
                best = grammar
                best_score = score
        confidence = min(1.0, 0.35 + best_score * 0.22) if best_score else 0.35
        return best, confidence

    def _template_for(self, text: str, visual_goal: str) -> tuple[str, float, str]:
        grammar_id = self.selected_grammar.grammar_id
        if grammar_id == "historical_maritime":
            if _has(text, {"iceberg", "collision", "ripped", "hull"}):
                return "ship_collision_diagram", 0.92, "high"
            if _has(text, {"two miles", "deep", "plunged", "darkness", "lost", "wreck"}):
                return "wreck_depth_map", 0.86, "high"
            if _has(text, {"bacteria", "rust", "decay", "dissolve", "metal eating"}):
                return "wreck_decay_diagram", 0.88, "high"
            if _has(text, {"safe", "claimed", "famous", "fascinates"}):
                return "archive_headline", 0.72, "medium"
            if _has(text, {"shoe", "shoes", "passenger", "passengers", "artifact"}):
                return "artifact_evidence", 0.76, "medium"
            if _has(text, {"ship", "vessel", "ocean liner"}):
                return "ship_diagram", 0.7, "medium"
        if grammar_id == "ancient_engineering":
            if _has(text, {"aqueduct", "arch", "gravity", "channel", "road", "bridge"}):
                return "engineering_cutaway", 0.9, "high"
        if grammar_id == "wildlife_behaviour":
            if _has(text, {"dance", "hive", "bee", "communicate", "camouflage", "hunt", "behavior", "behaviour"}):
                return "behaviour_diagram", 0.84, "high"
        if grammar_id == "astronomy":
            if _has(text, {"solar", "magnetosphere", "aurora", "magnetic", "northern lights"}):
                return "space_weather_diagram", 0.9, "high"
            if _has(text, {"orbit", "planet", "moon", "jupiter"}):
                return "astronomy_scale_diagram", 0.86, "high"
        if grammar_id == "geology":
            if _has(text, {"volcano", "lava", "magma", "eruption", "rock", "land"}):
                return "geology_cross_section", 0.9, "high"
        if grammar_id == "ocean_science":
            if _has(text, {"current", "ocean", "map", "coriolis", "circulation"}):
                return "science_map", 0.82, "high"
        if visual_goal in {"explain", "prove"}:
            return "generic_motion", 0.35, "low"
        return "", 0.0, "low"

    def _repair_queries(self, text: str, queries: tuple[str, ...]) -> tuple[str, ...]:
        base = queries[0] if queries else self.topic
        grammar_id = self.selected_grammar.grammar_id
        if grammar_id == "historical_maritime":
            if _has(text, {"iceberg", "collision", "hull"}):
                return _dedupe((base, "ocean liner iceberg collision diagram", "historic ship iceberg"))
            if _has(text, {"deep", "lost", "wreck"}):
                return _dedupe((base, "shipwreck sonar map deep ocean", "underwater wreck expedition"))
            if _has(text, {"bacteria", "rust", "decay", "dissolve"}):
                return _dedupe((base, "shipwreck rusticles corrosion", "underwater metal corrosion"))
            if _has(text, {"safe", "claimed", "famous"}):
                return _dedupe((base, "historic ocean liner newspaper headline", "archive passenger ship"))
            if _has(text, {"shoe", "passenger", "artifact"}):
                return _dedupe((base, "shipwreck debris field shoes artifact", "maritime museum artifact"))
        if grammar_id == "astronomy":
            return _dedupe((base, *queries[1:]))
        if grammar_id == "geology":
            return _dedupe((base, "USGS volcano geology", "volcano cross section diagram"))
        return _dedupe((base, *queries[1:]))


def default_visual_grammars() -> tuple[VisualGrammar, ...]:
    """Return reusable documentary grammars in priority order."""

    return (
        VisualGrammar(
            "historical_maritime",
            "Historical Maritime",
            ("shipwreck", "ship", "ocean liner", "iceberg", "maritime", "wreck", "passenger ship", "vessel"),
            ("archive photo", "route map", "ship diagram", "newspaper headline", "sonar imagery", "wreck footage"),
            ("archive_video", "archive_image", "map", "diagram", "wreck_footage"),
            ("ship_collision_diagram", "wreck_depth_map", "wreck_decay_diagram", "archive_headline", "artifact_evidence", "ship_diagram"),
            ("wikimedia", "loc", "internet_archive", "smithsonian", "europeana", "pexels", "pixabay"),
            ("real footage", "archive image", "map", "diagram", "composer", "explainer"),
        ),
        VisualGrammar(
            "ancient_engineering",
            "Ancient Engineering",
            ("aqueduct", "roman", "bridge", "road", "ancient engineering", "arch"),
            ("archive photo", "map", "engineering cutaway", "ruins close-up"),
            ("archive_image", "architecture_video", "map", "engineering_diagram"),
            ("engineering_cutaway", "route_map", "archive_headline"),
            ("wikimedia", "loc", "europeana", "smithsonian", "pexels", "pixabay"),
            ("archive image", "real footage", "map", "diagram", "composer", "explainer"),
        ),
        VisualGrammar(
            "wildlife_behaviour",
            "Wildlife Behaviour",
            ("bee", "octopus", "shark", "sharks", "animal", "wildlife", "hive", "camouflage", "behavior", "behaviour"),
            ("macro", "habitat", "behaviour", "close-up", "interaction"),
            ("wildlife_video", "macro_video", "archive_image", "behaviour_diagram"),
            ("behaviour_diagram", "habitat_map"),
            ("pexels", "pixabay", "wikimedia", "usfws", "smithsonian"),
            ("real footage", "archive image", "diagram", "composer", "explainer"),
        ),
        VisualGrammar(
            "ocean_science",
            "Ocean Science",
            ("ocean", "current", "undersea", "wave", "marine", "coriolis", "circulation"),
            ("satellite imagery", "maps", "underwater footage", "flow diagrams"),
            ("ocean_video", "satellite", "map", "scientific_diagram"),
            ("science_map", "flow_diagram"),
            ("noaa", "nasa", "wikimedia", "pexels", "pixabay"),
            ("real footage", "government imagery", "map", "diagram", "composer", "explainer"),
        ),
        VisualGrammar(
            "astronomy",
            "Astronomy",
            ("space", "planet", "solar", "aurora", "northern lights", "magnetosphere", "magnetic", "jupiter", "moon", "orbit"),
            ("NASA imagery", "telescope imagery", "orbit diagram", "scale comparison"),
            ("space_video", "astronomy", "scientific_diagram", "archive_image"),
            ("astronomy_scale_diagram", "orbit_diagram", "space_weather_diagram"),
            ("nasa", "esa", "wikimedia", "internet_archive", "pexels"),
            ("NASA imagery", "archive video", "diagram", "composer", "explainer"),
        ),
        VisualGrammar(
            "geology",
            "Geology",
            ("volcano", "lava", "magma", "eruption", "geology", "earthquake", "rock"),
            ("eruption footage", "cross-section", "maps", "satellite imagery", "timeline"),
            ("geology", "volcano_video", "volcano_images", "scientific_diagram"),
            ("geology_cross_section", "timeline_diagram"),
            ("usgs", "nasa", "wikimedia", "pexels", "pixabay"),
            ("real footage", "government imagery", "diagram", "composer", "explainer"),
        ),
        VisualGrammar(
            "generic_documentary",
            "Generic Documentary",
            (),
            ("real footage", "archive image", "diagram"),
            ("real_video", "archive_image", "diagram"),
            (),
            ("pexels", "pixabay", "wikimedia"),
            ("real footage", "archive image", "explainer"),
            max_composition_ratio=0.10,
            max_generic_motion_ratio=0.0,
            min_real_archive_ratio=0.90,
        ),
    )


def _enum_value(value: Any, default: str) -> str:
    return str(getattr(value, "value", value) or default).lower()


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())


def _has(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _dedupe(queries: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        cleaned = " ".join(str(query or "").split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return tuple(ordered)
