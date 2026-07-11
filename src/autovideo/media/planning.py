"""Capability-driven query and source planning for media search.

This module is deterministic and provider-agnostic. It decides what visual
capabilities a scene needs and which registered providers can satisfy them.
Candidate scoring and final asset selection remain in ``selection.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .selection import VisualIntent


class SceneType(str, Enum):
    """Deterministic primary visual category for one script segment."""

    WILDLIFE = "wildlife"
    LANDSCAPE = "landscape"
    OCEAN = "ocean"
    WEATHER = "weather"
    SATELLITE = "satellite"
    ASTRONOMY = "astronomy"
    HISTORY = "history"
    TECHNOLOGY = "technology"
    CITY = "city"
    ARCHITECTURE = "architecture"
    MACRO = "macro"
    CLOSE_UP = "close_up"
    MAP = "map"
    DIAGRAM = "diagram"
    ARCHIVE = "archive"
    GEOLOGY = "geology"
    VOLCANO = "volcano"
    ABSTRACT_SCIENCE = "abstract_science"
    HUMAN = "human"
    LIFESTYLE = "lifestyle"


@dataclass(frozen=True)
class CapabilityRequirement:
    """One visual capability needed by a scene."""

    capability: str
    required: bool = False
    weight: float = 1.0


@dataclass(frozen=True)
class QueryPlan:
    """Provider-agnostic search intent for one scene."""

    primary_query: str
    alternate_queries: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()
    shot_type: str = ""
    visual_style: str = "stock_video"
    scene_type: SceneType = SceneType.LANDSCAPE
    capability_requirements: tuple[CapabilityRequirement, ...] = ()

    @property
    def all_queries(self) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for query in (self.primary_query, *self.alternate_queries):
            cleaned = " ".join(str(query).split())
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                ordered.append(cleaned)
        return tuple(ordered)


@dataclass(frozen=True)
class ProviderCapability:
    """Capabilities advertised by a provider adapter."""

    provider_id: str
    capabilities: tuple[str, ...]
    media_types: tuple[str, ...] = ("video",)
    base_priority: int = 100
    requires_api_key: bool = False
    enabled: bool = True
    domains: tuple[str, ...] = ()
    licensing: str = ""
    preferred_scene_types: tuple[str, ...] = ()
    confidence: float = 0.5


@dataclass(frozen=True)
class ProviderSearchPlan:
    """Queries to run against one provider."""

    provider_id: str
    queries: tuple[str, ...]
    matched_capabilities: tuple[str, ...]
    score: float
    is_fallback: bool = False


@dataclass(frozen=True)
class SearchStrategy:
    """Ordered provider plans for one scene."""

    query_plan: QueryPlan
    provider_plans: tuple[ProviderSearchPlan, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_order(self) -> tuple[str, ...]:
        return tuple(plan.provider_id for plan in self.provider_plans)


class ProviderCapabilityRegistry:
    """Registry of providers and their visual capabilities."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderCapability] = {}

    def register(self, capability: ProviderCapability) -> None:
        self._providers[capability.provider_id] = capability

    def get(self, provider_id: str) -> ProviderCapability | None:
        return self._providers.get(provider_id)

    def all(self) -> tuple[ProviderCapability, ...]:
        return tuple(sorted(self._providers.values(), key=lambda item: item.base_priority))

    def rank(self, requirements: Iterable[CapabilityRequirement]) -> tuple[tuple[ProviderCapability, float, tuple[str, ...]], ...]:
        reqs = tuple(requirements)
        ranked: list[tuple[ProviderCapability, float, tuple[str, ...]]] = []
        for provider in self.all():
            if not provider.enabled:
                continue
            caps = set(provider.capabilities)
            matched = tuple(req.capability for req in reqs if req.capability in caps)
            score = sum(req.weight for req in reqs if req.capability in caps)
            missing_required = [req.capability for req in reqs if req.required and req.capability not in caps]
            score -= len(missing_required) * 20.0
            if "generic_stock_video" in caps:
                score += 0.15
            score += max(0.0, min(provider.confidence, 1.0)) * 0.25
            ranked.append((provider, score, matched))
        return tuple(sorted(ranked, key=lambda item: (-item[1], item[0].base_priority, item[0].provider_id)))


class QueryPlanner:
    """Build a query plan from visual intent."""

    def plan(self, intent: VisualIntent) -> QueryPlan:
        text = _norm(" ".join([intent.topic, intent.narration, intent.primary_subject, " ".join(intent.queries)]))
        scene_type = classify_scene_type(intent)
        requirements = self._requirements(intent, text, scene_type)
        primary = self._primary_query(intent, text)
        alternates = self._alternates(intent, text, primary)
        style = self._visual_style(text, requirements)
        return QueryPlan(
            primary_query=primary,
            alternate_queries=alternates,
            negative_terms=self._negative_terms(text),
            shot_type=intent.shot_type,
            visual_style=style,
            scene_type=scene_type,
            capability_requirements=requirements,
        )

    def _requirements(
        self,
        intent: VisualIntent,
        text: str,
        scene_type: SceneType,
    ) -> tuple[CapabilityRequirement, ...]:
        requirements: list[CapabilityRequirement] = []
        explanatory = _has(text, DIAGRAM_TERMS) or (_has(text, OCEAN_TERMS) and _has(text, EXPLANATORY_SCIENCE_TERMS))
        ocean_explanatory = _has(text, OCEAN_TERMS) and explanatory
        space_context = scene_type == SceneType.ASTRONOMY and not ocean_explanatory

        if space_context:
            requirements.extend([
                CapabilityRequirement("astronomy", required=True, weight=4.0),
                CapabilityRequirement("space_video", weight=3.0),
            ])
        if scene_type in {SceneType.OCEAN, SceneType.SATELLITE}:
            if explanatory:
                requirements.append(CapabilityRequirement("ocean_video", weight=2.4))
                requirements.append(CapabilityRequirement("scientific_media", weight=1.4))
                if scene_type == SceneType.SATELLITE and not _has(text, EARTH_ROTATION_TERMS):
                    requirements.append(CapabilityRequirement("ocean_satellite", weight=3.2))
            else:
                requirements.extend([
                    CapabilityRequirement("nature_video", weight=1.5),
                    CapabilityRequirement("ocean_video", weight=3.0),
                    CapabilityRequirement("scientific_media", weight=2.0),
                ])
        if scene_type == SceneType.WEATHER:
            requirements.append(CapabilityRequirement("weather_satellite", weight=2.0))
            requirements.append(CapabilityRequirement("scientific_media", weight=1.2))
        if scene_type == SceneType.WILDLIFE:
            requirements.extend([
                CapabilityRequirement("wildlife_video", required=True, weight=3.2),
                CapabilityRequirement("nature_video", weight=2.0),
                CapabilityRequirement("wildlife_images", weight=2.4),
            ])
        if scene_type in {SceneType.GEOLOGY, SceneType.VOLCANO}:
            requirements.extend([
                CapabilityRequirement("geology", required=True, weight=3.8),
                CapabilityRequirement("scientific_media", weight=2.4),
                CapabilityRequirement("volcano_images", weight=3.2 if scene_type == SceneType.VOLCANO else 1.2),
                CapabilityRequirement("volcano_video", weight=2.5 if scene_type == SceneType.VOLCANO else 0.8),
                CapabilityRequirement("archive_footage", weight=1.5),
            ])
        if scene_type == SceneType.CITY:
            requirements.append(CapabilityRequirement("city_video", required=True, weight=2.5))
        if scene_type == SceneType.ARCHITECTURE:
            requirements.append(CapabilityRequirement("architecture_video", weight=2.0))
        if scene_type in {SceneType.HISTORY, SceneType.ARCHIVE}:
            requirements.extend([
                CapabilityRequirement("history_video", weight=2.4),
                CapabilityRequirement("history_images", weight=1.8),
                CapabilityRequirement("archive_footage", weight=1.6),
                CapabilityRequirement("commons_media", weight=1.4),
            ])
        if scene_type == SceneType.TECHNOLOGY:
            requirements.append(CapabilityRequirement("technology", required=True, weight=2.7))
        if scene_type in {SceneType.DIAGRAM, SceneType.MAP} or explanatory:
            requirements.extend([
                CapabilityRequirement("diagrams", weight=2.6),
                CapabilityRequirement("illustrations", weight=1.8),
            ])
        if scene_type == SceneType.ABSTRACT_SCIENCE:
            requirements.append(CapabilityRequirement("abstract_concepts", weight=1.4))
            requirements.append(CapabilityRequirement("scientific_media", weight=1.2))
        if scene_type == SceneType.MACRO:
            requirements.append(CapabilityRequirement("macro_video", weight=2.0))
        if scene_type == SceneType.CLOSE_UP:
            requirements.append(CapabilityRequirement("close_up_video", weight=1.8))
        if scene_type == SceneType.HUMAN:
            requirements.append(CapabilityRequirement("human_video", weight=1.5))
        if scene_type == SceneType.LIFESTYLE:
            requirements.append(CapabilityRequirement("lifestyle_video", weight=1.5))

        if not requirements:
            requirements.append(CapabilityRequirement("generic_stock_video", required=True, weight=1.0))

        return _dedupe_requirements(requirements)

    def _primary_query(self, intent: VisualIntent, text: str) -> str:
        subject = _clean_query(intent.primary_subject)
        base = _clean_query(intent.queries[0] if intent.queries else subject or intent.topic)
        if _has(text, {"volcano", "volcanoes", "volcanic", "lava", "magma", "eruption", "erupting"}):
            return _clean_query(f"{base} volcano lava geology archive")
        if _has(text, GEOLOGY_TERMS):
            return _clean_query(f"{base} geology earth science archive")
        if _has(text, OCEAN_TERMS) and _has(text, EXPLANATORY_SCIENCE_TERMS):
            if _has(text, EARTH_ROTATION_TERMS):
                return "coriolis effect ocean current diagram"
            return _clean_query(f"{base} ocean current diagram")
        if _has(text, SPACE_TERMS):
            return _clean_query(f"{base} NASA astronomy")
        if _has(text, OCEAN_TERMS):
            return _clean_query(f"{base} ocean current underwater")
        if _has(text, WILDLIFE_TERMS):
            return _clean_query(f"{base} wildlife nature")
        if _has(text, TECH_TERMS):
            return _clean_query(f"{base} technology close up")
        if _has(text, HISTORY_TERMS):
            return _clean_query(f"{base} ancient history ruins")
        if _has(text, ABSTRACT_TERMS):
            concrete = subject or _first_concrete_query(intent)
            return _clean_query(f"{concrete} visual explanation")
        return base

    def _alternates(self, intent: VisualIntent, text: str, primary: str) -> tuple[str, ...]:
        alternates = [_clean_query(query) for query in intent.queries]
        if intent.primary_subject:
            alternates.append(_clean_query(f"{intent.primary_subject} {intent.shot_type}".strip()))
        if _has(text, {"volcano", "volcanoes", "volcanic", "lava", "magma", "eruption", "erupting"}):
            alternates.extend(["volcano eruption lava flow", "volcanic island formation", "kilauea lava flow", "USGS volcano lava"])
        elif _has(text, GEOLOGY_TERMS):
            alternates.extend(["geology rock formation", "earth science landscape", "geological process diagram"])
        elif _has(text, OCEAN_TERMS) and _has(text, EXPLANATORY_SCIENCE_TERMS):
            if _has(text, EARTH_ROTATION_TERMS):
                alternates.extend(["coriolis effect water flow", "ocean current swirl diagram", "rotating water flow animation"])
            alternates.extend(["ocean current map", "global ocean circulation diagram", "water flow arrows ocean"])
        elif _has(text, SPACE_TERMS):
            alternates.extend(["aurora borealis timelapse", "earth atmosphere from space", "solar wind animation"])
        elif _has(text, OCEAN_TERMS):
            alternates.extend(["ocean current aerial", "waves moving ocean", "underwater ocean flow"])
        if _has(text, ABSTRACT_TERMS):
            alternates.append(_clean_query(f"{intent.primary_subject} illustration"))
        return tuple(query for query in _dedupe_queries(alternates) if query.lower() != primary.lower())[:5]

    def _negative_terms(self, text: str) -> tuple[str, ...]:
        terms = ["people", "person", "man", "woman", "crowd"]
        if _has(text, WILDLIFE_TERMS):
            terms.extend(["zoo", "cage", "pet", "dog", "cat"])
        return tuple(terms)

    def _visual_style(self, text: str, requirements: tuple[CapabilityRequirement, ...]) -> str:
        caps = {req.capability for req in requirements}
        if _has(text, SPACE_TERMS) and not (_has(text, OCEAN_TERMS) and {"diagrams", "illustrations"} & caps):
            return "real_celestial_media"
        if {"diagrams", "illustrations"} & caps:
            return "explanatory_visual"
        return "stock_video"


class SourcePlanner:
    """Turn a query plan into provider-specific search order."""

    def __init__(self, registry: ProviderCapabilityRegistry) -> None:
        self.registry = registry

    def plan(self, query_plan: QueryPlan) -> SearchStrategy:
        ranked = self.registry.rank(query_plan.capability_requirements)
        provider_plans = []
        for provider, score, matched in ranked:
            adjusted_score = score
            if query_plan.visual_style == "stock_video" and "video" not in provider.media_types:
                documentary_image_match = {
                    "history_images",
                    "wildlife_images",
                    "volcano_images",
                    "scientific_media",
                    "commons_media",
                } & set(matched)
                adjusted_score -= 1.5 if documentary_image_match else 25.0
            if query_plan.visual_style == "explanatory_visual" and "image" in provider.media_types:
                adjusted_score += 10.0
            nasa_supported = {"astronomy", "space_video", "ocean_satellite", "weather_satellite"} & set(matched)
            nasa_supported = nasa_supported or (
                query_plan.scene_type in {SceneType.GEOLOGY, SceneType.VOLCANO}
                and {"scientific_media", "archive_footage"} & set(matched)
            )
            if provider.provider_id == "nasa" and not nasa_supported:
                adjusted_score -= 25.0
            if "stock" not in provider.domains and _provider_matches_scene_domain(provider, query_plan.scene_type):
                adjusted_score += 4.5
            queries = self._queries_for_provider(provider.provider_id, query_plan)
            provider_plans.append(
                ProviderSearchPlan(
                    provider_id=provider.provider_id,
                    queries=queries,
                    matched_capabilities=matched,
                    score=round(adjusted_score, 3),
                    is_fallback=adjusted_score <= 0,
                )
            )
        provider_plans.sort(key=lambda plan: (-plan.score, self.registry.get(plan.provider_id).base_priority if self.registry.get(plan.provider_id) else 100, plan.provider_id))
        provider_diagnostics = []
        for plan in provider_plans:
            provider_capability = self.registry.get(plan.provider_id) or ProviderCapability(plan.provider_id, ())
            provider_diagnostics.append(
                {
                    "provider": plan.provider_id,
                    "queries": list(plan.queries),
                    "matched_capabilities": list(plan.matched_capabilities),
                    "score": plan.score,
                    "fallback": plan.is_fallback,
                    "media_types": list(provider_capability.media_types),
                    "domains": list(provider_capability.domains),
                    "licensing": provider_capability.licensing,
                    "provider_confidence": provider_capability.confidence,
                }
            )
        diagnostics = {
            "query_plan": {
                "primary_query": query_plan.primary_query,
                "alternate_queries": list(query_plan.alternate_queries),
                "negative_terms": list(query_plan.negative_terms),
                "shot_type": query_plan.shot_type,
                "visual_style": query_plan.visual_style,
                "scene_type": query_plan.scene_type.value,
                "capability_requirements": [
                    {"capability": req.capability, "required": req.required, "weight": req.weight}
                    for req in query_plan.capability_requirements
                ],
            },
            "search_strategy": provider_diagnostics,
        }
        return SearchStrategy(query_plan=query_plan, provider_plans=tuple(provider_plans), diagnostics=diagnostics)

    def _queries_for_provider(self, provider_id: str, query_plan: QueryPlan) -> tuple[str, ...]:
        queries = list(query_plan.all_queries)
        if provider_id in {"nasa", "esa", "noaa", "wikimedia", "usgs", "smithsonian", "nps", "usfws", "loc", "europeana", "flickr_commons", "internet_archive"}:
            queries = [_strip_stock_words(query) for query in queries]
        elif provider_id == "gemini_image":
            queries = [f"{query} cinematic explanatory image" for query in queries]
        return tuple(_dedupe_queries(queries))[:5]


def default_provider_capability_registry(
    *,
    local_enabled: bool = True,
    pexels_enabled: bool = True,
    pixabay_enabled: bool = True,
    nasa_enabled: bool = True,
    gemini_image_enabled: bool = True,
    mixkit_enabled: bool = False,
    coverr_enabled: bool = False,
    videvo_enabled: bool = False,
    wikimedia_enabled: bool = False,
    noaa_enabled: bool = False,
    esa_enabled: bool = False,
    usgs_enabled: bool = False,
    smithsonian_enabled: bool = False,
    nps_enabled: bool = False,
    usfws_enabled: bool = False,
    loc_enabled: bool = False,
    europeana_enabled: bool = False,
    flickr_commons_enabled: bool = False,
    internet_archive_enabled: bool = False,
) -> ProviderCapabilityRegistry:
    """Current provider capability registration.

    Future providers should plug in by registering capabilities here or through
    provider adapters without changing QueryPlanner/SourcePlanner logic.
    """

    registry = ProviderCapabilityRegistry()
    registry.register(ProviderCapability(
        "local",
        ("generic_stock_video", "wildlife_video", "nature_video", "city_video", "technology", "abstract_concepts"),
        base_priority=0,
        enabled=local_enabled,
        domains=("operator_provided",),
        licensing="operator-provided",
        confidence=0.85,
    ))
    registry.register(ProviderCapability(
        "pexels",
        ("generic_stock_video", "wildlife_video", "nature_video", "city_video", "technology", "abstract_concepts", "ocean_video", "macro_video", "close_up_video", "human_video", "lifestyle_video"),
        base_priority=45,
        requires_api_key=True,
        enabled=pexels_enabled,
        domains=("stock", "wildlife", "nature", "technology", "city"),
        licensing="Pexels License",
        confidence=0.55,
    ))
    registry.register(ProviderCapability(
        "mixkit",
        ("generic_stock_video", "wildlife_video", "nature_video", "city_video", "technology", "abstract_concepts", "ocean_video", "lifestyle_video"),
        base_priority=50,
        enabled=mixkit_enabled,
        domains=("stock", "city", "technology", "lifestyle"),
        licensing="Mixkit License",
        confidence=0.45,
    ))
    registry.register(ProviderCapability(
        "pixabay",
        ("generic_stock_video", "wildlife_video", "nature_video", "city_video", "technology", "abstract_concepts", "ocean_video", "history_video", "history_images", "archive_footage"),
        base_priority=48,
        requires_api_key=True,
        enabled=pixabay_enabled,
        domains=("stock", "wildlife", "nature", "history"),
        licensing="Pixabay Content License",
        confidence=0.5,
    ))
    registry.register(ProviderCapability(
        "coverr",
        ("generic_stock_video", "city_video", "technology", "architecture_video", "lifestyle_video", "human_video"),
        base_priority=55,
        enabled=coverr_enabled,
        domains=("stock", "technology", "city"),
        licensing="Coverr License",
        confidence=0.45,
    ))
    registry.register(ProviderCapability(
        "videvo",
        ("generic_stock_video", "city_video", "technology", "architecture_video", "lifestyle_video", "human_video", "history_video"),
        base_priority=58,
        requires_api_key=True,
        enabled=videvo_enabled,
        domains=("stock", "archive", "history"),
        licensing="Videvo license varies by clip",
        confidence=0.45,
    ))
    registry.register(ProviderCapability(
        "noaa",
        ("ocean_video", "ocean_satellite", "weather_satellite", "scientific_media", "archive_footage", "diagrams", "illustrations"),
        media_types=("video", "image"),
        base_priority=6,
        enabled=noaa_enabled,
        domains=("ocean", "weather", "climate", "earth_science"),
        licensing="NOAA public domain / usage varies",
        preferred_scene_types=("ocean", "weather", "satellite", "diagram"),
        confidence=0.8,
    ))
    registry.register(ProviderCapability(
        "nasa",
        ("space_video", "astronomy", "archive_footage", "weather_satellite", "ocean_satellite", "scientific_media", "diagrams", "illustrations", "geology", "volcano_images", "volcano_video"),
        media_types=("video", "image"),
        base_priority=8,
        enabled=nasa_enabled,
        domains=("space", "astronomy", "earth_science", "satellite", "volcano"),
        licensing="NASA media usage guidelines",
        preferred_scene_types=("astronomy", "satellite", "weather", "diagram"),
        confidence=0.82,
    ))
    registry.register(ProviderCapability(
        "esa",
        ("space_video", "astronomy", "archive_footage", "scientific_media"),
        base_priority=32,
        enabled=esa_enabled,
        domains=("space", "astronomy", "earth_observation"),
        licensing="ESA media usage guidelines",
        confidence=0.7,
    ))
    registry.register(ProviderCapability(
        "usgs",
        ("scientific_media", "archive_footage", "diagrams", "illustrations", "geology", "volcano_video", "volcano_images", "history_images"),
        media_types=("video", "image"),
        base_priority=5,
        enabled=usgs_enabled,
        domains=("geology", "volcano", "earth_science", "maps"),
        licensing="USGS public domain / usage varies",
        preferred_scene_types=("diagram", "landscape", "archive"),
        confidence=0.82,
    ))
    registry.register(ProviderCapability(
        "usfws",
        ("wildlife_video", "nature_video", "archive_footage", "wildlife_images"),
        media_types=("video", "image"),
        base_priority=7,
        enabled=usfws_enabled,
        domains=("wildlife", "nature", "biology"),
        licensing="USFWS public domain / usage varies",
        preferred_scene_types=("wildlife", "macro"),
        confidence=0.78,
    ))
    registry.register(ProviderCapability(
        "loc",
        ("history_images", "archive_footage", "commons_media", "architecture_video", "illustrations"),
        media_types=("image",),
        base_priority=9,
        enabled=loc_enabled,
        domains=("history", "archive", "architecture", "maps"),
        licensing="Library of Congress rights vary by item",
        preferred_scene_types=("history", "architecture", "map", "archive"),
        confidence=0.72,
    ))
    registry.register(ProviderCapability(
        "smithsonian",
        ("history_images", "wildlife_images", "scientific_media", "archive_footage", "illustrations"),
        media_types=("image",),
        base_priority=11,
        enabled=smithsonian_enabled,
        domains=("history", "science", "wildlife", "museum"),
        licensing="Smithsonian Open Access / rights vary",
        confidence=0.7,
    ))
    registry.register(ProviderCapability(
        "nps",
        ("nature_video", "history_images", "archive_footage", "landscape_images", "geology"),
        media_types=("video", "image"),
        base_priority=13,
        enabled=nps_enabled,
        domains=("nature", "history", "geology", "landscape"),
        licensing="National Park Service public domain / usage varies",
        confidence=0.68,
    ))
    registry.register(ProviderCapability(
        "wikimedia",
        ("history_video", "history_images", "archive_footage", "architecture_video", "diagrams", "illustrations", "commons_media", "wildlife_images", "scientific_media", "geology", "volcano_images"),
        media_types=("video", "image"),
        base_priority=14,
        enabled=wikimedia_enabled,
        domains=("history", "science", "wildlife", "geology", "commons"),
        licensing="Creative Commons / public domain varies by item",
        preferred_scene_types=("history", "architecture", "diagram", "wildlife"),
        confidence=0.65,
    ))
    registry.register(ProviderCapability(
        "europeana",
        ("history_images", "archive_footage", "commons_media", "illustrations"),
        media_types=("image",),
        base_priority=18,
        requires_api_key=True,
        enabled=europeana_enabled,
        domains=("history", "archive", "museum"),
        licensing="Europeana rights vary by item",
        confidence=0.6,
    ))
    registry.register(ProviderCapability(
        "flickr_commons",
        ("history_images", "wildlife_images", "archive_footage", "commons_media"),
        media_types=("image",),
        base_priority=22,
        requires_api_key=True,
        enabled=flickr_commons_enabled,
        domains=("history", "wildlife", "archive"),
        licensing="Flickr Commons license varies by item",
        confidence=0.58,
    ))
    registry.register(ProviderCapability(
        "internet_archive",
        ("archive_footage", "history_video", "history_images", "commons_media", "scientific_media"),
        media_types=("video", "image"),
        base_priority=17,
        enabled=internet_archive_enabled,
        domains=("archive", "history", "public_domain", "commons"),
        licensing="Internet Archive rights vary by item",
        preferred_scene_types=("archive", "history"),
        confidence=0.62,
    ))
    registry.register(ProviderCapability(
        "gemini_image",
        ("diagrams", "illustrations", "abstract_concepts", "animation", "generic_image"),
        media_types=("image",),
        base_priority=90,
        enabled=gemini_image_enabled,
    ))
    return registry


SPACE_TERMS = {
    "aurora", "borealis", "northern", "lights", "space", "astronomy", "saturn", "venus",
    "mars", "jupiter", "galaxy", "nebula", "cosmos", "universe", "solar", "sun",
    "atmosphere", "magnetosphere", "star", "stars", "orbit", "satellite",
}
OCEAN_TERMS = {
    "ocean", "current", "currents", "sea", "marine", "underwater", "atlantic", "pacific",
    "gulf", "stream", "waves", "tide", "water",
}
WEATHER_TERMS = {
    "weather",
    "storm",
    "thunderstorm",
    "thunder",
    "lightning",
    "hurricane",
    "cloud",
    "clouds",
    "rain",
    "monsoon",
    "cyclone",
    "atmosphere",
    "climate",
}
WILDLIFE_TERMS = {
    "animal", "wildlife", "fox", "bear", "wolf", "lion", "tiger", "bird", "eagle", "whale",
    "dolphin", "shark", "octopus", "fish", "turtle", "bee", "bees", "honeybee", "honeybees",
}
CITY_TERMS = {"city", "urban", "street", "traffic", "building", "skyscraper"}
HISTORY_TERMS = {"ancient", "roman", "empire", "history", "castle", "ruins", "road", "archaeology"}
TECH_TERMS = {"technology", "qr", "code", "computer", "phone", "robot", "chip", "screen", "data"}
DIAGRAM_TERMS = {"diagram", "infographic", "map", "chart", "explain", "explains"}
EXPLANATORY_SCIENCE_TERMS = {
    "animation", "belt", "circulation", "conveyor", "coriolis", "data", "diagram",
    "effect", "flow", "flows", "global", "gyre", "gyres", "map", "satellite",
    "swirl", "swirls", "temperature",
}
EARTH_ROTATION_TERMS = {"coriolis", "rotate", "rotating", "rotation", "spinning"}
ABSTRACT_TERMS = {"brain", "memory", "embarrassing", "psychology", "invisible", "dark", "concept", "works"}
HUMAN_TERMS = {"person", "people", "human", "man", "woman", "teacher", "student"}
LIFESTYLE_TERMS = {"office", "home", "family", "coffee", "lifestyle", "daily"}
MACRO_TERMS = {"macro", "microscopic", "closeup", "detail"}
ARCHITECTURE_TERMS = {"architecture", "aqueduct", "bridge", "building", "structure", "temple"}
GEOLOGY_TERMS = {
    "basalt", "geology", "geological", "lava", "magma", "tectonic",
    "volcanic", "volcano", "volcanoes", "eruption", "erupting", "kilauea", "iceland",
}


def classify_scene_type(intent: VisualIntent) -> SceneType:
    """Classify a script segment into one primary visual scene type."""

    text = _norm(" ".join([intent.topic, intent.narration, intent.primary_subject, " ".join(intent.queries)]))
    if _has(text, {"diagram", "infographic", "chart"}):
        return SceneType.DIAGRAM
    if _has(text, {"map", "maps"}):
        return SceneType.MAP
    if _has(text, {"satellite", "data"}) and (_has(text, OCEAN_TERMS) or _has(text, WEATHER_TERMS)):
        return SceneType.SATELLITE
    if _has(text, SPACE_TERMS) and not _has(text, OCEAN_TERMS):
        return SceneType.ASTRONOMY
    if _has(text, {"volcano", "volcanoes", "volcanic", "lava", "magma", "eruption", "erupting"}):
        return SceneType.VOLCANO
    if _has(text, GEOLOGY_TERMS):
        return SceneType.GEOLOGY
    if _has(text, WILDLIFE_TERMS):
        return SceneType.WILDLIFE
    if _has(text, HISTORY_TERMS):
        return SceneType.HISTORY
    if _has(text, ARCHITECTURE_TERMS):
        return SceneType.ARCHITECTURE
    if _has(text, WEATHER_TERMS):
        return SceneType.WEATHER
    if _has(text, OCEAN_TERMS):
        return SceneType.OCEAN
    if _has(text, TECH_TERMS):
        return SceneType.TECHNOLOGY
    if _has(text, CITY_TERMS):
        return SceneType.CITY
    if _has(text, MACRO_TERMS) or intent.shot_type == "detail":
        return SceneType.MACRO
    if intent.shot_type == "close":
        return SceneType.CLOSE_UP
    if _has(text, ABSTRACT_TERMS):
        return SceneType.ABSTRACT_SCIENCE
    if _has(text, HUMAN_TERMS):
        return SceneType.HUMAN
    if _has(text, LIFESTYLE_TERMS):
        return SceneType.LIFESTYLE
    if _has(text, {"archive", "archival"}):
        return SceneType.ARCHIVE
    return SceneType.LANDSCAPE


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _has(text: str, terms: set[str]) -> bool:
    tokens = set(_norm(text).split())
    return bool(tokens & terms)


def _clean_query(query: str) -> str:
    return " ".join(str(query or "").replace("-", " ").split())


def _dedupe_queries(queries: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for query in queries:
        cleaned = _clean_query(query)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            deduped.append(cleaned)
    return deduped


def _dedupe_requirements(requirements: Iterable[CapabilityRequirement]) -> tuple[CapabilityRequirement, ...]:
    merged: dict[str, CapabilityRequirement] = {}
    for req in requirements:
        current = merged.get(req.capability)
        if current is None or req.weight > current.weight or req.required:
            merged[req.capability] = CapabilityRequirement(
                req.capability,
                required=req.required or (current.required if current else False),
                weight=max(req.weight, current.weight if current else req.weight),
            )
    return tuple(merged.values())


def _first_concrete_query(intent: VisualIntent) -> str:
    for query in intent.queries:
        cleaned = _clean_query(query)
        if cleaned:
            return cleaned
    return _clean_query(intent.topic)


def _strip_stock_words(query: str) -> str:
    words = [word for word in _clean_query(query).split() if word.lower() not in {"stock", "footage", "close", "up", "wide", "shot"}]
    return " ".join(words)


def _provider_matches_scene_domain(provider: ProviderCapability, scene_type: SceneType) -> bool:
    domains = set(provider.domains)
    if scene_type in {SceneType.HISTORY, SceneType.ARCHIVE, SceneType.ARCHITECTURE, SceneType.MAP}:
        return bool(domains & {"history", "archive", "architecture", "maps", "museum"})
    if scene_type == SceneType.WILDLIFE:
        return bool(domains & {"wildlife", "nature", "biology"})
    if scene_type in {SceneType.GEOLOGY, SceneType.VOLCANO}:
        return bool(domains & {"geology", "volcano", "earth_science", "landscape"})
    if scene_type in {SceneType.OCEAN, SceneType.WEATHER, SceneType.SATELLITE}:
        return bool(domains & {"ocean", "weather", "climate", "earth_science", "satellite"})
    if scene_type == SceneType.ASTRONOMY:
        return bool(domains & {"space", "astronomy"})
    return False
