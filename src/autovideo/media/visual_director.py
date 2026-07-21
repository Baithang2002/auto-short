"""Documentary-style visual planning before provider media selection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .editorial import DocumentaryMode, EditorialCanon
from .scene_entities import (
    SceneEntity,
    SceneEntityPlan,
    SceneEntityPlanner,
    isolated_query_candidates,
)


class VisualGoal(str, Enum):
    SHOW = "show"
    EXPLAIN = "explain"
    PROVE = "prove"
    COMPARE = "compare"
    REVEAL = "reveal"
    TRANSITION = "transition"
    EMPHASIZE = "emphasize"


class MediaMode(str, Enum):
    SHOW = "show"
    PROVE = "prove"
    EXPLAIN = "explain"
    COMPARE = "compare"
    REVEAL = "reveal"
    TRANSITION = "transition"
    CTA = "cta"


class QueryTier(str, Enum):
    EXACT = "exact"
    ENTITY = "entity"
    MECHANISM = "mechanism"
    CONTEXT = "context"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class TieredQueries:
    tier: QueryTier
    queries: tuple[str, ...]
    budget: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "queries": list(self.queries),
            "budget": self.budget,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TieredQueries":
        return cls(
            tier=QueryTier(str(data["tier"])),
            queries=tuple(str(query) for query in data.get("queries", [])),
            budget=int(data.get("budget", 0)),
        )


@dataclass(frozen=True)
class ShotIntent:
    scene_index: int
    scene_importance: str
    visual_goal: VisualGoal
    media_mode: MediaMode
    visual_role: str
    documentary_role: str
    primary_subject: str
    scene_entity: SceneEntity | None = None
    required_entities: tuple[str, ...] = ()
    action: str = ""
    environment: str = ""
    shot_type: str = ""
    preferred_sources: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()
    query_tiers: tuple[TieredQueries, ...] = ()
    minimum_confidence: str = "medium"
    allow_explainer_card: bool = True
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def search_queries(self) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for tier in self.query_tiers:
            for query in tier.queries[:max(0, tier.budget)]:
                key = _normalize(query)
                if key and key not in seen:
                    seen.add(key)
                    ordered.append(_clean_query(query))
        return tuple(ordered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_index": self.scene_index,
            "scene_importance": self.scene_importance,
            "visual_goal": self.visual_goal.value,
            "media_mode": self.media_mode.value,
            "visual_role": self.visual_role,
            "documentary_role": self.documentary_role,
            "primary_subject": self.primary_subject,
            "scene_entity": self.scene_entity.to_dict() if self.scene_entity else None,
            "required_entities": list(self.required_entities),
            "action": self.action,
            "environment": self.environment,
            "shot_type": self.shot_type,
            "preferred_sources": list(self.preferred_sources),
            "negative_terms": list(self.negative_terms),
            "query_tiers": [tier.to_dict() for tier in self.query_tiers],
            "search_queries": list(self.search_queries),
            "minimum_confidence": self.minimum_confidence,
            "allow_explainer_card": self.allow_explainer_card,
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ShotIntent":
        return cls(
            scene_index=int(data["scene_index"]),
            scene_importance=str(data.get("scene_importance", "supporting")),
            visual_goal=VisualGoal(str(data.get("visual_goal", VisualGoal.SHOW.value))),
            media_mode=_media_mode_from_value(data.get("media_mode", data.get("visual_goal", MediaMode.SHOW.value))),
            visual_role=str(data.get("visual_role", "")),
            documentary_role=str(data.get("documentary_role", data.get("visual_role", ""))),
            primary_subject=str(data.get("primary_subject", "")),
            scene_entity=SceneEntity.from_dict(data["scene_entity"]) if data.get("scene_entity") else None,
            required_entities=tuple(str(item) for item in data.get("required_entities", [])),
            action=str(data.get("action", "")),
            environment=str(data.get("environment", "")),
            shot_type=str(data.get("shot_type", "")),
            preferred_sources=tuple(str(item) for item in data.get("preferred_sources", [])),
            negative_terms=tuple(str(item) for item in data.get("negative_terms", [])),
            query_tiers=tuple(TieredQueries.from_dict(item) for item in data.get("query_tiers", [])),
            minimum_confidence=str(data.get("minimum_confidence", "medium")),
            allow_explainer_card=bool(data.get("allow_explainer_card", True)),
            diagnostics=dict(data.get("diagnostics", {})),
        )


@dataclass(frozen=True)
class DocumentaryStyleRules:
    shot_variety: tuple[str, ...]
    explainer_limits: dict[str, Any]
    framing_diversity: tuple[str, ...]
    visual_rhythm: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_variety": list(self.shot_variety),
            "explainer_limits": self.explainer_limits,
            "framing_diversity": list(self.framing_diversity),
            "visual_rhythm": list(self.visual_rhythm),
        }


@dataclass(frozen=True)
class ShotPlan:
    topic: str
    domain_id: str
    primary_subject: str
    supporting_subjects: tuple[str, ...]
    subject_persistence_target: float
    allowed_substitutions: tuple[str, ...]
    forbidden_substitutions: tuple[str, ...]
    visual_identity: tuple[str, ...]
    required_subjects: tuple[str, ...]
    avoid_terms: tuple[str, ...]
    style_rules: DocumentaryStyleRules
    query_budget: dict[str, int]
    intents: tuple[ShotIntent, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def intent_for_index(self, index: int) -> ShotIntent | None:
        for intent in self.intents:
            if intent.scene_index == index:
                return intent
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "domain_id": self.domain_id,
            "primary_subject": self.primary_subject,
            "supporting_subjects": list(self.supporting_subjects),
            "subject_persistence_target": self.subject_persistence_target,
            "allowed_substitutions": list(self.allowed_substitutions),
            "forbidden_substitutions": list(self.forbidden_substitutions),
            "visual_identity": list(self.visual_identity),
            "required_subjects": list(self.required_subjects),
            "avoid_terms": list(self.avoid_terms),
            "style_rules": self.style_rules.to_dict(),
            "query_budget": self.query_budget,
            "intents": [intent.to_dict() for intent in self.intents],
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ShotPlan":
        style = data.get("style_rules", {})
        return cls(
            topic=str(data.get("topic", "")),
            domain_id=str(data.get("domain_id", "")),
            primary_subject=str(data.get("primary_subject", "")),
            supporting_subjects=tuple(str(item) for item in data.get("supporting_subjects", [])),
            subject_persistence_target=float(data.get("subject_persistence_target", 0.85)),
            allowed_substitutions=tuple(str(item) for item in data.get("allowed_substitutions", [])),
            forbidden_substitutions=tuple(str(item) for item in data.get("forbidden_substitutions", [])),
            visual_identity=tuple(str(item) for item in data.get("visual_identity", [])),
            required_subjects=tuple(str(item) for item in data.get("required_subjects", [])),
            avoid_terms=tuple(str(item) for item in data.get("avoid_terms", [])),
            style_rules=DocumentaryStyleRules(
                shot_variety=tuple(str(item) for item in style.get("shot_variety", [])),
                explainer_limits=dict(style.get("explainer_limits", {})),
                framing_diversity=tuple(str(item) for item in style.get("framing_diversity", [])),
                visual_rhythm=tuple(str(item) for item in style.get("visual_rhythm", [])),
            ),
            query_budget={str(k): int(v) for k, v in data.get("query_budget", {}).items()},
            intents=tuple(ShotIntent.from_dict(item) for item in data.get("intents", [])),
            diagnostics=dict(data.get("diagnostics", {})),
        )

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


@dataclass(frozen=True)
class DomainKnowledge:
    id: str
    label: str
    trigger_terms: tuple[str, ...]
    primary_subject: str
    required_entities: tuple[str, ...]
    named_entities: tuple[str, ...]
    visual_identity: tuple[str, ...]
    avoid_terms: tuple[str, ...]
    exact_queries: tuple[str, ...]
    mechanism_queries: tuple[str, ...]
    context_queries: tuple[str, ...]
    fallback_queries: tuple[str, ...]
    scene_focus_rules: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DomainKnowledge":
        return cls(
            id=str(data["id"]),
            label=str(data.get("label", data["id"])),
            trigger_terms=tuple(str(item) for item in data.get("trigger_terms", [])),
            primary_subject=str(data.get("primary_subject", "")),
            required_entities=tuple(str(item) for item in data.get("required_entities", [])),
            named_entities=tuple(str(item) for item in data.get("named_entities", [])),
            visual_identity=tuple(str(item) for item in data.get("visual_identity", [])),
            avoid_terms=tuple(str(item) for item in data.get("avoid_terms", [])),
            exact_queries=tuple(str(item) for item in data.get("exact_queries", [])),
            mechanism_queries=tuple(str(item) for item in data.get("mechanism_queries", [])),
            context_queries=tuple(str(item) for item in data.get("context_queries", [])),
            fallback_queries=tuple(str(item) for item in data.get("fallback_queries", [])),
            scene_focus_rules=tuple(
                dict(item) for item in data.get("scene_focus_rules", [])
            ),
        )


class KnowledgePackStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).parent / "knowledge" / "documentary_domains.json"

    def load(self) -> tuple[DomainKnowledge, ...]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return tuple(DomainKnowledge.from_dict(item) for item in payload.get("domains", []))


class VisualDirector:
    """Build deterministic documentary shot plans from script segments."""

    DEFAULT_QUERY_BUDGET = {
        QueryTier.EXACT.value: 3,
        QueryTier.ENTITY.value: 3,
        QueryTier.MECHANISM.value: 2,
        QueryTier.CONTEXT.value: 2,
        QueryTier.FALLBACK.value: 1,
    }

    def __init__(
        self,
        knowledge_store: KnowledgePackStore | None = None,
        *,
        query_budget: Mapping[str, int] | None = None,
    ) -> None:
        self.knowledge_store = knowledge_store or KnowledgePackStore()
        self.query_budget = dict(self.DEFAULT_QUERY_BUDGET)
        if query_budget:
            self.query_budget.update({str(k): int(v) for k, v in query_budget.items()})

    def plan(
        self,
        *,
        topic: str,
        segments: list[Mapping[str, Any]],
        editorial_canon: EditorialCanon | None = None,
        scene_entity_plan: SceneEntityPlan | None = None,
    ) -> ShotPlan:
        if editorial_canon and scene_entity_plan is None:
            scene_entity_plan = SceneEntityPlanner().plan(
                editorial_canon=editorial_canon,
                segments=segments,
            )
        knowledge = self._match_domain(topic, segments, editorial_canon=editorial_canon)
        style_rules = DocumentaryStyleRules(
            shot_variety=("wide", "close", "action", "detail", "mechanism", "payoff"),
            explainer_limits={
                "max_explainer_cards_per_short": 2,
                "prefer_real_footage_until_tier": QueryTier.MECHANISM.value,
                "black_cards_are_final_fallback": True,
            },
            framing_diversity=("avoid_same_opening_clip", "alternate_wide_and_close", "avoid_adjacent_same_provider_id"),
            visual_rhythm=("hook_exact_subject", "establish_subject", "show_mechanism", "show_proof", "payoff_subject"),
        )
        intents = [
            self._intent_for_segment(
                index,
                segment,
                topic,
                knowledge,
                len(segments),
                editorial_canon,
                scene_entity_plan,
            )
            for index, segment in enumerate(segments)
        ]
        intents = self._diversify_adjacent_queries(intents)
        primary_subject = editorial_canon.primary_subject if editorial_canon else (knowledge.primary_subject if knowledge else _clean_query(topic))
        supporting_subjects = (
            _dedupe_terms([
                *editorial_canon.secondary_subjects,
                *editorial_canon.supporting_entities,
                *editorial_canon.location_entities,
            ])
            if editorial_canon
            else (knowledge.named_entities if knowledge else ())
        )
        allowed_substitutions = (
            _dedupe_terms([
                *editorial_canon.secondary_subjects,
                *editorial_canon.supporting_entities,
                *editorial_canon.comparison_entities,
                *editorial_canon.location_entities,
            ])
            if editorial_canon
            else (knowledge.required_entities if knowledge else ())
        )
        forbidden_substitutions = (
            _dedupe_terms([
                *editorial_canon.forbidden_primary_subjects,
                *editorial_canon.avoid_terms,
                *(knowledge.avoid_terms if knowledge else ()),
            ])
            if editorial_canon
            else (knowledge.avoid_terms if knowledge else ("generic people", "unrelated landscape"))
        )
        visual_identity = (
            editorial_canon.visual_identity
            if editorial_canon and editorial_canon.visual_identity
            else (knowledge.visual_identity if knowledge else (_clean_query(topic),))
        )
        required_subjects = (
            _dedupe_terms([primary_subject, *editorial_canon.secondary_subjects])
            if editorial_canon
            else (knowledge.required_entities if knowledge else (_clean_query(topic),))
        )
        avoid_terms = (
            _dedupe_terms([*editorial_canon.avoid_terms, *(knowledge.avoid_terms if knowledge else ())])
            if editorial_canon
            else (knowledge.avoid_terms if knowledge else ("generic lifestyle", "unrelated people"))
        )
        return ShotPlan(
            topic=topic,
            domain_id=knowledge.id if knowledge else "generic",
            primary_subject=primary_subject,
            supporting_subjects=supporting_subjects,
            subject_persistence_target=0.85,
            allowed_substitutions=allowed_substitutions,
            forbidden_substitutions=forbidden_substitutions,
            visual_identity=visual_identity,
            required_subjects=required_subjects,
            avoid_terms=avoid_terms,
            style_rules=style_rules,
            query_budget=dict(self.query_budget),
            intents=tuple(intents),
            diagnostics={
                "planner": "visual_director",
                "knowledge_pack": self.knowledge_store.path.name,
                "matched_domain": knowledge.id if knowledge else "generic",
                "editorial_canon": editorial_canon.to_dict() if editorial_canon else None,
                "scene_entity_plan": scene_entity_plan.to_report() if scene_entity_plan else None,
                "primary_subject_locked": bool(editorial_canon),
            },
        )

    def _match_domain(
        self,
        topic: str,
        segments: list[Mapping[str, Any]],
        *,
        editorial_canon: EditorialCanon | None = None,
    ) -> DomainKnowledge | None:
        text = _normalize(" ".join([
            topic,
            " ".join(str(segment.get("narration", "")) for segment in segments),
            " ".join(str(segment.get("broll", "")) for segment in segments),
        ]))
        best: tuple[int, DomainKnowledge] | None = None
        canon_subject = _normalize(editorial_canon.primary_subject) if editorial_canon else ""
        for domain in self.knowledge_store.load():
            if canon_subject and _normalize(domain.primary_subject) != canon_subject:
                continue
            score = sum(1 for term in domain.trigger_terms if _normalize(term) in text)
            if score and (best is None or score > best[0]):
                best = (score, domain)
        return best[1] if best else None

    def _intent_for_segment(
        self,
        index: int,
        segment: Mapping[str, Any],
        topic: str,
        knowledge: DomainKnowledge | None,
        total_segments: int,
        editorial_canon: EditorialCanon | None = None,
        scene_entity_plan: SceneEntityPlan | None = None,
    ) -> ShotIntent:
        narration = str(segment.get("narration", ""))
        broll = str(segment.get("broll", "") or topic)
        raw_queries = segment.get("broll_queries") or []
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        visual_goal = _visual_goal(index, narration, total_segments)
        documentary_role = _documentary_role_for_index(index, visual_goal, total_segments, editorial_canon)
        shot_type = _shot_type_for_index(index, visual_goal)
        primary_subject = editorial_canon.primary_subject if editorial_canon else (knowledge.primary_subject if knowledge else _clean_query(broll or topic))
        scene_entity = scene_entity_plan.entity_for_index(index) if scene_entity_plan else None
        action = _action_phrase(narration, broll)
        scene_importance = _scene_importance(index, narration, total_segments)
        all_scene_entities = scene_entity_plan.entities if scene_entity_plan else ()
        exact_queries, exact_rejections = self._tier_queries(QueryTier.EXACT, narration, knowledge, broll, action, shot_type, raw_queries, editorial_canon, documentary_role, scene_entity, all_scene_entities)
        entity_queries, entity_rejections = self._tier_queries(QueryTier.ENTITY, narration, knowledge, broll, action, shot_type, raw_queries, editorial_canon, documentary_role, scene_entity, all_scene_entities)
        mechanism_queries, mechanism_rejections = self._tier_queries(QueryTier.MECHANISM, narration, knowledge, broll, action, shot_type, raw_queries, editorial_canon, documentary_role, scene_entity, all_scene_entities)
        context_queries, context_rejections = self._tier_queries(QueryTier.CONTEXT, narration, knowledge, broll, action, shot_type, raw_queries, editorial_canon, documentary_role, scene_entity, all_scene_entities)
        fallback_queries, fallback_rejections = self._tier_queries(QueryTier.FALLBACK, narration, knowledge, broll, action, shot_type, raw_queries, editorial_canon, documentary_role, scene_entity, all_scene_entities)
        query_tiers = tuple(
            TieredQueries(tier, tuple(queries[:self.query_budget[tier.value]]), self.query_budget[tier.value])
            for tier, queries in (
                (QueryTier.EXACT, exact_queries),
                (QueryTier.ENTITY, entity_queries),
                (QueryTier.MECHANISM, mechanism_queries),
                (QueryTier.CONTEXT, context_queries),
                (QueryTier.FALLBACK, fallback_queries),
            )
        )
        return ShotIntent(
            scene_index=index,
            scene_importance=scene_importance,
            visual_goal=visual_goal,
            media_mode=_media_mode(visual_goal, scene_importance),
            visual_role=_visual_role(index, visual_goal, total_segments),
            documentary_role=documentary_role,
            primary_subject=primary_subject,
            scene_entity=scene_entity,
            required_entities=_required_entities(primary_subject, knowledge, editorial_canon, scene_entity),
            action=action,
            environment=_environment(narration, broll, topic),
            shot_type=shot_type,
            preferred_sources=_preferred_sources(knowledge, narration, topic),
            negative_terms=_negative_terms(knowledge, editorial_canon, scene_entity),
            query_tiers=query_tiers,
            minimum_confidence="high" if scene_importance in {"hook", "main_reveal"} else "medium",
            allow_explainer_card=visual_goal in {VisualGoal.EXPLAIN, VisualGoal.PROVE},
            diagnostics={
                "knowledge_domain": knowledge.id if knowledge else "generic",
                "bounded_query_count": sum(len(tier.queries) for tier in query_tiers),
                "editorial_canon_primary_subject": primary_subject if editorial_canon else "",
                "documentary_role": documentary_role,
                "scene_entity": scene_entity.to_dict() if scene_entity else None,
                "query_isolation_rejections": [
                    *exact_rejections,
                    *entity_rejections,
                    *mechanism_rejections,
                    *context_rejections,
                    *fallback_rejections,
                ],
                "fallback_chain": [
                    scene_entity.canonical_entity,
                    *scene_entity.aliases,
                    *scene_entity.optional_terms,
                    "generic documentary",
                ] if scene_entity else [],
            },
        )

    def _tier_queries(
        self,
        tier: QueryTier,
        narration: str,
        knowledge: DomainKnowledge | None,
        broll: str,
        action: str,
        shot_type: str,
        raw_queries: list[Any],
        editorial_canon: EditorialCanon | None = None,
        documentary_role: str = "",
        scene_entity: SceneEntity | None = None,
        all_scene_entities: tuple[SceneEntity, ...] = (),
    ) -> tuple[list[str], tuple[dict[str, Any], ...]]:
        domain_queries: tuple[str, ...] = ()
        if knowledge:
            domain_queries = {
                QueryTier.EXACT: knowledge.exact_queries,
                QueryTier.ENTITY: knowledge.named_entities,
                QueryTier.MECHANISM: knowledge.mechanism_queries,
                QueryTier.CONTEXT: knowledge.context_queries,
                QueryTier.FALLBACK: knowledge.fallback_queries,
            }[tier]
        if tier == QueryTier.EXACT:
            canon_query = _canon_scene_query(editorial_canon, documentary_role, narration, broll, action, shot_type, scene_entity)
            scene_query = _scene_specific_query(knowledge, narration, broll, action, shot_type)
            narration_query = _join_terms(_narration_visual_terms(narration), shot_type)
            base = [
                *_scene_entity_exact_queries(scene_entity, documentary_role, action, shot_type),
                canon_query,
                scene_query,
                narration_query,
                _join_terms(_sanitize_query_for_domain(broll, knowledge), shot_type),
                *[_sanitize_query_for_domain(str(query), knowledge) for query in raw_queries],
            ]
            if knowledge:
                if _weak_scene_query(scene_query, knowledge):
                    return _isolate_query_list([*domain_queries, *base], scene_entity, all_scene_entities)
                return _isolate_query_list([*base, *domain_queries], scene_entity, all_scene_entities)
            return _isolate_query_list(base, scene_entity, all_scene_entities)
        if tier == QueryTier.ENTITY:
            entity_queries = _scene_entity_alias_queries(scene_entity, action, shot_type)
            entity_queries.extend(_canon_entity_queries(editorial_canon, action, shot_type, scene_entity))
            if knowledge:
                entity_queries.extend(
                    _join_terms(entity, _sanitize_query_for_domain(action or shot_type, knowledge))
                    for entity in domain_queries
                )
            return _isolate_query_list(entity_queries, scene_entity, all_scene_entities)
        if tier == QueryTier.MECHANISM:
            return _isolate_query_list([
                *_scene_entity_broader_queries(scene_entity, "mechanism"),
                *domain_queries,
                _join_terms(broll, "mechanism"),
                _join_terms(broll, "diagram"),
            ], scene_entity, all_scene_entities)
        if tier == QueryTier.CONTEXT:
            return _isolate_query_list([
                *_scene_entity_broader_queries(scene_entity, "documentary"),
                *domain_queries,
                _join_terms(broll, "documentary"),
            ], scene_entity, all_scene_entities)
        return _isolate_query_list([
            *_scene_entity_broader_queries(scene_entity, "wide shot"),
            *domain_queries,
            broll,
        ], scene_entity, all_scene_entities)

    def _diversify_adjacent_queries(self, intents: list[ShotIntent]) -> list[ShotIntent]:
        diversified: list[ShotIntent] = []
        previous_first = ""
        for intent in intents:
            first_query = intent.search_queries[0] if intent.search_queries else ""
            if first_query and _normalize(first_query) == previous_first:
                replacement = _first_distinct_query(intent.search_queries, previous_first)
                if replacement:
                    intent = _promote_query(intent, replacement)
                    first_query = intent.search_queries[0] if intent.search_queries else ""
            previous_first = _normalize(first_query)
            diversified.append(intent)
        return diversified


def _visual_goal(index: int, narration: str, total_segments: int) -> VisualGoal:
    text = _normalize(narration)
    if index == total_segments - 1 or any(term in text for term in ("subscribe", "follow", "more like this")):
        return VisualGoal.TRANSITION
    if index == 0:
        return VisualGoal.REVEAL
    if any(term in text for term in ("because", "how", "process", "mechanism", "turns", "creates", "controlled")):
        return VisualGoal.EXPLAIN
    if any(term in text for term in ("actually", "proof", "still", "you can see", "ninety", "percent")):
        return VisualGoal.PROVE
    if any(term in text for term in ("but", "meanwhile", "instead", "compare")):
        return VisualGoal.COMPARE
    if any(term in text for term in ("massive", "tiny", "secret", "hidden", "wild")):
        return VisualGoal.EMPHASIZE
    return VisualGoal.SHOW


def _documentary_role_for_index(
    index: int,
    goal: VisualGoal,
    total_segments: int,
    editorial_canon: EditorialCanon | None,
) -> str:
    if editorial_canon and index < len(editorial_canon.expected_scene_roles):
        return editorial_canon.expected_scene_roles[index]
    return _visual_role(index, goal, total_segments)


def _required_entities(
    primary_subject: str,
    knowledge: DomainKnowledge | None,
    editorial_canon: EditorialCanon | None,
    scene_entity: SceneEntity | None = None,
) -> tuple[str, ...]:
    if scene_entity:
        return _dedupe_terms([
            scene_entity.canonical_entity,
            *scene_entity.required_terms,
            *scene_entity.aliases,
        ])
    if editorial_canon:
        return _dedupe_terms([
            primary_subject,
            *editorial_canon.secondary_subjects,
            *editorial_canon.supporting_entities,
        ])
    return knowledge.required_entities if knowledge else (primary_subject,)


def _negative_terms(
    knowledge: DomainKnowledge | None,
    editorial_canon: EditorialCanon | None,
    scene_entity: SceneEntity | None = None,
) -> tuple[str, ...]:
    if editorial_canon:
        return _dedupe_terms([
            *(scene_entity.forbidden_terms if scene_entity else ()),
            *editorial_canon.forbidden_primary_subjects,
            *editorial_canon.avoid_terms,
            *(knowledge.avoid_terms if knowledge else ()),
        ])
    return knowledge.avoid_terms if knowledge else ("generic people", "unrelated landscape")


def _canon_entity_queries(
    editorial_canon: EditorialCanon | None,
    action: str,
    shot_type: str,
    scene_entity: SceneEntity | None = None,
) -> list[str]:
    if not editorial_canon:
        return []
    if scene_entity:
        return [
            _join_terms(scene_entity.canonical_entity, action or shot_type),
            *[_join_terms(alias, action or shot_type) for alias in scene_entity.aliases],
        ]
    entities = [
        editorial_canon.primary_subject,
        *editorial_canon.secondary_subjects,
        *editorial_canon.supporting_entities[:5],
        *editorial_canon.location_entities[:3],
    ]
    return [
        _join_terms(entity, action or shot_type)
        for entity in _dedupe_terms(entities)
    ]


def _canon_scene_query(
    editorial_canon: EditorialCanon | None,
    role: str,
    narration: str,
    broll: str,
    action: str,
    shot_type: str,
    scene_entity: SceneEntity | None = None,
) -> str:
    if not editorial_canon:
        return ""
    if scene_entity:
        return _scene_entity_role_query(scene_entity, role, narration, broll, action, shot_type)
    subject = editorial_canon.primary_subject
    role_norm = _normalize(role)
    mode = editorial_canon.documentary_mode
    if role_norm == "map":
        if mode == DocumentaryMode.PLACE:
            return _join_terms(subject, "world map")
        if mode == DocumentaryMode.MULTI_SUBJECT:
            return "world wonders map"
        return _join_terms(subject, "map")
    if role_norm in {"landscape", "overview"}:
        identity = editorial_canon.visual_identity[0] if editorial_canon.visual_identity else subject
        return _join_terms(identity, "wide documentary")
    if role_norm == "comparison" and editorial_canon.comparison_entities:
        return _join_terms(subject, editorial_canon.comparison_entities[0], "comparison")
    if role_norm in {"wildlife", "close up", "macro"}:
        return _join_terms(subject, "close up", action or shot_type)
    if role_norm in {"evidence", "timeline"}:
        if editorial_canon.secondary_subjects:
            return _join_terms(editorial_canon.secondary_subjects[0], "archive documentary")
        return _join_terms(subject, "archive documentary")
    if role_norm in {"process", "explanation"}:
        narration_terms = _narration_visual_terms(narration)
        return _join_terms(subject, narration_terms or broll, "diagram" if "explanation" in role_norm else shot_type)
    return _join_terms(subject, action or broll, shot_type)


def _scene_entity_exact_queries(
    scene_entity: SceneEntity | None,
    role: str,
    action: str,
    shot_type: str,
) -> list[str]:
    if not scene_entity:
        return []
    return _dedupe_queries([
        _scene_entity_role_query(scene_entity, role, "", "", action, shot_type),
        _join_terms(scene_entity.canonical_entity, action or shot_type),
        _join_terms(scene_entity.canonical_entity, "documentary"),
    ])


def _scene_entity_alias_queries(
    scene_entity: SceneEntity | None,
    action: str,
    shot_type: str,
) -> list[str]:
    if not scene_entity:
        return []
    return _dedupe_queries([
        *[_join_terms(alias, action or shot_type) for alias in scene_entity.aliases],
        *[_join_terms(term, action or shot_type) for term in scene_entity.required_terms],
    ])


def _scene_entity_broader_queries(
    scene_entity: SceneEntity | None,
    descriptor: str,
) -> list[str]:
    if not scene_entity:
        return []
    return _dedupe_queries([
        *[_join_terms(scene_entity.canonical_entity, term) for term in scene_entity.optional_terms],
        _join_terms(scene_entity.canonical_entity, descriptor),
    ])


def _scene_entity_role_query(
    scene_entity: SceneEntity,
    role: str,
    narration: str,
    broll: str,
    action: str,
    shot_type: str,
) -> str:
    entity = scene_entity.canonical_entity
    role_norm = _normalize(role)
    if scene_entity.entity_type == "map":
        return _join_terms(entity, "documentary map")
    if scene_entity.entity_type == "landmark":
        if role_norm == "map":
            return _join_terms(entity, "aerial wide documentary")
        if role_norm in {"close up", "macro", "evidence"}:
            return _join_terms(entity, "architectural detail")
        return _join_terms(entity, action or "wide documentary")
    if scene_entity.entity_type == "species":
        if role_norm in {"close up", "macro", "evidence"}:
            return _join_terms(entity, "close up")
        return _join_terms(entity, action or shot_type or "documentary")
    if role_norm == "map":
        return _join_terms(entity, "map")
    narration_terms = _narration_visual_terms(narration)
    return _join_terms(entity, action or narration_terms or broll or shot_type)


def _isolate_query_list(
    queries: list[Any],
    scene_entity: SceneEntity | None,
    all_scene_entities: tuple[SceneEntity, ...],
) -> tuple[list[str], tuple[dict[str, Any], ...]]:
    accepted, rejected = isolated_query_candidates(
        scene_entity=scene_entity,
        queries=queries,
        all_entities=all_scene_entities,
    )
    return list(accepted), rejected


def _media_mode(goal: VisualGoal, scene_importance: str) -> MediaMode:
    if scene_importance == "cta":
        return MediaMode.CTA
    if goal == VisualGoal.PROVE:
        return MediaMode.PROVE
    if goal == VisualGoal.EXPLAIN:
        return MediaMode.EXPLAIN
    if goal == VisualGoal.COMPARE:
        return MediaMode.COMPARE
    if goal == VisualGoal.REVEAL:
        return MediaMode.REVEAL
    if goal == VisualGoal.TRANSITION:
        return MediaMode.TRANSITION
    return MediaMode.SHOW


def _media_mode_from_value(value: Any) -> MediaMode:
    try:
        return MediaMode(str(value))
    except ValueError:
        if str(value) == VisualGoal.PROVE.value:
            return MediaMode.PROVE
        if str(value) == VisualGoal.EXPLAIN.value:
            return MediaMode.EXPLAIN
        if str(value) == VisualGoal.COMPARE.value:
            return MediaMode.COMPARE
        if str(value) == VisualGoal.REVEAL.value:
            return MediaMode.REVEAL
        if str(value) == VisualGoal.TRANSITION.value:
            return MediaMode.TRANSITION
        return MediaMode.SHOW


def _scene_importance(index: int, narration: str, total_segments: int) -> str:
    text = _normalize(narration)
    if index == total_segments - 1 or any(term in text for term in ("subscribe", "follow")):
        return "cta"
    if index == 0:
        return "hook"
    if index == 1:
        return "main_reveal"
    return "supporting"


def _visual_role(index: int, goal: VisualGoal, total_segments: int) -> str:
    if index == 0:
        return "hook_exact_subject"
    if index == total_segments - 1:
        return "closing_payoff"
    if goal == VisualGoal.EXPLAIN:
        return "mechanism"
    if goal == VisualGoal.PROVE:
        return "proof"
    if goal == VisualGoal.COMPARE:
        return "contrast"
    return "story_beat"


def _shot_type_for_index(index: int, goal: VisualGoal) -> str:
    if goal == VisualGoal.EXPLAIN:
        return "diagram" if index % 3 == 0 else "detail"
    if index == 0:
        return "wide"
    return ("close", "action", "wide", "detail")[index % 4]


def _preferred_sources(knowledge: DomainKnowledge | None, narration: str, topic: str) -> tuple[str, ...]:
    text = _normalize(f"{topic} {narration} {knowledge.id if knowledge else ''}")
    if any(term in text for term in ("aurora", "lightning", "space", "solar", "satellite")):
        return ("nasa", "esa", "noaa", "wikimedia", "pexels", "pixabay")
    if any(term in text for term in ("volcano", "ocean", "weather", "undersea")):
        return ("noaa", "wikimedia", "pexels", "pixabay", "nasa")
    if any(term in text for term in ("roman", "aqueduct", "history")):
        return ("wikimedia", "pixabay", "pexels")
    return ("pexels", "pixabay", "wikimedia")


def _action_phrase(narration: str, broll: str) -> str:
    text = _normalize(f"{broll} {narration}")
    for phrase in (
        "opening jar", "changing color", "squeezing through", "waggle dance",
        "communicating", "lava flow", "eruption", "cooling lava", "water channel",
        "arches", "cable laying", "lightning strike", "storm", "camouflage",
        "hunting", "swimming", "flowing", "carried water",
    ):
        if phrase in text:
            return phrase
    words = [word for word in _tokens(broll) if word not in {"close", "wide", "shot", "detail"}]
    return " ".join(words[:3])


def _environment(narration: str, broll: str, topic: str) -> str:
    text = _normalize(f"{topic} {broll} {narration}")
    for term in ("underwater", "hive", "ancient Rome", "volcano", "storm", "space", "ocean floor", "reef"):
        if _normalize(term) in text:
            return term
    return ""


def _clean_query(value: Any) -> str:
    return " ".join(str(value or "").replace("-", " ").split())


def _subject_query(knowledge: DomainKnowledge | None, broll: str) -> str:
    if not knowledge:
        return broll
    normalized_subject = _normalize(knowledge.primary_subject)
    normalized_broll = _normalize(broll)
    subject_tokens = set(normalized_subject.split())
    broll_tokens = set(normalized_broll.split())
    if normalized_subject in normalized_broll or subject_tokens & broll_tokens:
        return _sanitize_query_for_domain(broll, knowledge)
    return knowledge.primary_subject


def _scene_specific_query(
    knowledge: DomainKnowledge | None,
    narration: str,
    broll: str,
    action: str,
    shot_type: str,
) -> str:
    broll_query = _sanitize_query_for_domain(broll, knowledge)
    narrative_terms = _narration_visual_terms(narration)
    broll_is_weak = _weak_scene_query(broll_query, knowledge) or _weak_scene_query(broll, knowledge)
    if broll_query and not broll_is_weak:
        return _join_terms(broll_query, shot_type)
    if knowledge and broll_is_weak:
        return ""
    if narrative_terms:
        return _join_terms(narrative_terms, shot_type)
    return _join_terms(_subject_query(knowledge, broll), action, shot_type)


def _narration_visual_terms(narration: str) -> str:
    tokens = [
        token
        for token in _tokens(narration)
        if token not in {
            "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
            "had", "has", "have", "in", "into", "is", "it", "its", "like", "of",
            "on", "or", "our", "so", "that", "the", "their", "then", "this", "to",
            "was", "were", "with", "without", "would", "you", "your",
        }
    ]
    priority = [
        "sun", "solar", "wind", "magnetosphere", "charged", "particles", "atmosphere",
        "earth", "jupiter", "asteroid", "asteroids", "moon", "mars", "planet",
        "collision", "galaxy", "protoplanetary", "debris",
    ]
    selected = [token for token in tokens if token in priority]
    if not selected:
        selected = tokens[:4]
    return _clean_query(" ".join(selected[:5]))


def _weak_scene_query(query: str, knowledge: DomainKnowledge | None) -> bool:
    normalized = _normalize(query)
    if not normalized:
        return True
    weak_terms = {
        "texture", "detail", "generic", "scenic", "landscape", "coastline", "smoke",
        "flower", "flowers", "garden", "person", "people", "looking",
    }
    tokens = set(normalized.split())
    meaningful_tokens = tokens - {"a", "an", "and", "in", "of", "on", "the", "to", "with"}
    if len(meaningful_tokens) <= 1:
        return True
    if tokens & weak_terms:
        if not knowledge:
            return True
        required = set(_normalize(" ".join(knowledge.required_entities)).split())
        if not (tokens & required):
            return True
    if knowledge and knowledge.id == "bee_communication" and "flower" in tokens:
        return True
    if knowledge and knowledge.id == "roman_aqueducts" and {"stone", "texture"} & tokens and "aqueduct" not in tokens:
        return True
    if knowledge and knowledge.id == "volcanic_land" and {"coastline", "ocean", "smoke"} & tokens and not ({"lava", "volcano", "volcanic"} & tokens):
        return True
    return False


def _sanitize_query_for_domain(query: str, knowledge: DomainKnowledge | None) -> str:
    cleaned = _clean_query(query)
    if not knowledge:
        return cleaned
    remove_terms = {
        "alien",
        "looking",
        "secret",
        "secrets",
        "beautiful",
        "scenic",
    }
    if knowledge.id == "bee_communication":
        remove_terms.update({"flower", "flowers", "garden"})
    if knowledge.id == "roman_aqueducts":
        remove_terms.update({"bricks", "texture", "forest", "bicycle"})
    if knowledge.id == "volcanic_land":
        remove_terms.update({"scuba", "diver", "bubbles", "sunset"})
    tokens = [token for token in cleaned.split() if token.lower() not in remove_terms]
    return _clean_query(" ".join(tokens))


def _join_terms(*values: Any) -> str:
    return _clean_query(" ".join(str(value or "") for value in values if str(value or "").strip()))


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _tokens(value: Any) -> list[str]:
    return [token for token in _normalize(value).split() if token]


def _dedupe_queries(queries: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        cleaned = _clean_query(query)
        key = _normalize(cleaned)
        if key and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return ordered


def _dedupe_terms(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = _clean_query(value)
        key = _normalize(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return tuple(ordered)


def _first_distinct_query(queries: tuple[str, ...], previous_key: str) -> str:
    for query in queries[1:]:
        if _normalize(query) != previous_key:
            return query
    return ""


def _promote_query(intent: ShotIntent, query: str) -> ShotIntent:
    promoted_tiers: list[TieredQueries] = []
    promoted = False
    for index, tier in enumerate(intent.query_tiers):
        queries = [existing for existing in tier.queries if _normalize(existing) != _normalize(query)]
        if not promoted and index == 0:
            queries.insert(0, query)
            promoted = True
        promoted_tiers.append(replace(tier, queries=tuple(queries)))
    return replace(
        intent,
        query_tiers=tuple(promoted_tiers),
        diagnostics={**intent.diagnostics, "adjacent_query_diversified": promoted},
    )
