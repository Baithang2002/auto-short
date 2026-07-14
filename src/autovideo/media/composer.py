"""Hybrid visual composition for documentary fallback scenes.

The composer creates a single renderer-compatible image asset from structured
ShotPlan context when provider media is unavailable. It keeps explainer cards as
the last fallback by producing richer diagram/map/motion-graphic visuals first.
"""

from __future__ import annotations

import hashlib
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont


class CompositionKind(str, Enum):
    """High-level visual form selected for a composed scene."""

    SCIENTIFIC_DIAGRAM = "scientific_diagram"
    MAP_DIAGRAM = "map_diagram"
    ENGINEERING_DIAGRAM = "engineering_diagram"
    BIOLOGY_DIAGRAM = "biology_diagram"
    MOTION_GRAPHIC = "motion_graphic"


@dataclass(frozen=True)
class HybridCompositionPlan:
    """Renderer-compatible composition plan for one script segment."""

    kind: CompositionKind
    scene_type: str
    title: str
    visual_goal: str = "show"
    components: tuple[str, ...] = field(default_factory=tuple)
    source_media_types: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the composition plan into metadata-safe JSON."""

        return {
            "kind": self.kind.value,
            "scene_type": self.scene_type,
            "title": self.title,
            "visual_goal": self.visual_goal,
            "components": list(self.components),
            "source_media_types": list(self.source_media_types),
        }


@dataclass(frozen=True)
class HybridCompositionResult:
    """A composed local asset and diagnostics for MediaAsset.metadata."""

    local_path: Path
    plan: HybridCompositionPlan
    metadata: dict[str, Any]


class HybridVisualComposer:
    """Compose documentary fallback visuals without changing renderer inputs."""

    def __init__(self, *, width: int = 1080, height: int = 1920) -> None:
        self.width = width
        self.height = height

    def plan(
        self,
        *,
        topic: str,
        narration: str,
        queries: list[str] | tuple[str, ...],
        shot_intent: Any | None = None,
        grammar_decision: Any | None = None,
    ) -> HybridCompositionPlan:
        """Build a deterministic composition plan from ShotPlan context."""

        text = _normalize(" ".join([topic, narration, " ".join(queries)]))
        visual_goal = _shot_intent_value(shot_intent, "visual_goal", "show")
        title = _title_from_queries(queries, topic)
        template = _grammar_decision_value(grammar_decision, "composition_template")
        if template in {
            "ship_collision_diagram", "wreck_depth_map", "wreck_decay_diagram",
            "archive_headline", "artifact_evidence", "ship_diagram",
        }:
            return HybridCompositionPlan(
                kind=CompositionKind.ENGINEERING_DIAGRAM,
                scene_type=f"historical_maritime_{template}",
                title=title,
                visual_goal=visual_goal,
                components=_maritime_components(template),
                source_media_types=("archive_image", "map", "diagram", "wreck_footage"),
            )
        if template == "space_weather_diagram":
            return HybridCompositionPlan(
                kind=CompositionKind.SCIENTIFIC_DIAGRAM,
                scene_type="space_weather",
                title=title,
                visual_goal=visual_goal,
                components=("sun", "charged particles", "earth magnetosphere", "upper atmosphere"),
                source_media_types=("nasa_imagery", "scientific_diagram", "archive_image"),
            )
        if template == "astronomy_scale_diagram":
            return HybridCompositionPlan(
                kind=CompositionKind.SCIENTIFIC_DIAGRAM,
                scene_type="astronomy_scale",
                title=title,
                visual_goal=visual_goal,
                components=("celestial body", "scale marker", "orbit path", "viewer reference"),
                source_media_types=("nasa_imagery", "scientific_diagram", "archive_image"),
            )
        if any(term in text for term in ("solar wind", "magnetosphere", "aurora", "charged particle")):
            return HybridCompositionPlan(
                kind=CompositionKind.SCIENTIFIC_DIAGRAM,
                scene_type="space_weather",
                title=title,
                visual_goal=visual_goal,
                components=("sun", "charged particles", "earth magnetosphere", "upper atmosphere"),
                source_media_types=("scientific_diagram", "motion_graphic", "archive_image"),
            )
        if any(term in text for term in ("volcano", "volcanic", "lava", "magma")):
            return HybridCompositionPlan(
                kind=CompositionKind.SCIENTIFIC_DIAGRAM,
                scene_type="volcanic_land",
                title=title,
                visual_goal=visual_goal,
                components=("magma chamber", "lava flow", "cooling rock", "new land"),
                source_media_types=("scientific_diagram", "archive_image", "motion_graphic"),
            )
        if any(term in text for term in ("roman", "aqueduct", "aqua claudia", "pont du gard")):
            return HybridCompositionPlan(
                kind=CompositionKind.ENGINEERING_DIAGRAM,
                scene_type="roman_aqueduct",
                title=title,
                visual_goal=visual_goal,
                components=("source spring", "gravity channel", "stone arches", "city supply"),
                source_media_types=("archive_image", "map", "engineering_diagram"),
            )
        if any(term in text for term in ("bee", "bees", "honeybee", "waggle", "hive")):
            return HybridCompositionPlan(
                kind=CompositionKind.BIOLOGY_DIAGRAM,
                scene_type="bee_communication",
                title=title,
                visual_goal=visual_goal,
                components=("hive", "waggle path", "sun angle", "food direction"),
                source_media_types=("archive_image", "scientific_diagram", "motion_graphic"),
            )
        if any(term in text for term in ("map", "ocean current", "route", "empire", "river")):
            return HybridCompositionPlan(
                kind=CompositionKind.MAP_DIAGRAM,
                scene_type="map_explainer",
                title=title,
                visual_goal=visual_goal,
                components=("map base", "direction arrows", "labels"),
                source_media_types=("map", "diagram", "motion_graphic"),
            )
        return HybridCompositionPlan(
            kind=CompositionKind.MOTION_GRAPHIC,
            scene_type="documentary_explainer",
            title=title,
            visual_goal=visual_goal,
            components=("subject label", "evidence marker", "motion lines"),
            source_media_types=("motion_graphic", "diagram"),
        )

    def compose(
        self,
        *,
        topic: str,
        narration: str,
        queries: list[str] | tuple[str, ...],
        output_dir: Path,
        idx: int,
        shot_intent: Any | None = None,
        grammar_decision: Any | None = None,
    ) -> HybridCompositionResult:
        """Create a portrait-safe composed visual and return metadata."""

        output_dir.mkdir(parents=True, exist_ok=True)
        plan = self.plan(
            topic=topic,
            narration=narration,
            queries=queries,
            shot_intent=shot_intent,
            grammar_decision=grammar_decision,
        )
        out_path = output_dir / f"hybrid_visual_{idx}.png"
        image = Image.new("RGB", (self.width, self.height), _palette(plan.scene_type)[0])
        draw = ImageDraw.Draw(image)
        fonts = _fonts()
        _draw_background(draw, self.width, self.height, plan.scene_type)
        _draw_scene(draw, self.width, self.height, plan, narration, fonts)
        image.save(out_path)
        metadata = {
            "provider": "hybrid_composer",
            "provider_id": out_path.name,
            "query": queries[0] if queries else topic,
            "confidence": "fallback",
            "confidence_level": "MEDIUM",
            "composition_confidence": _grammar_decision_value(
                grammar_decision,
                "composition_confidence",
                "medium",
            ),
            "portrait_score": 10.0,
            "relevance_score": 7.8,
            "fallback_level": "hybrid_composition",
            "selection_reason": "composed documentary visual after provider scoring rejected candidates",
            "rejection_reason": "no provider candidate passed scoring",
            "warnings": ["provider candidates rejected", "hybrid visual composition used"],
            "rejection_reasons": ["no candidate passed scoring"],
            "candidate_count": 0,
            "score_breakdown": {},
            "hybrid_composition": plan.to_dict(),
            "visual_grammar": (
                grammar_decision.to_dict()
                if hasattr(grammar_decision, "to_dict")
                else {}
            ),
        }
        return HybridCompositionResult(out_path, plan, metadata)


def _draw_scene(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    narration: str,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    _draw_title(draw, width, plan.title, fonts["title"])
    if plan.scene_type == "space_weather":
        _draw_space_weather(draw, width, height, plan, fonts)
    elif plan.scene_type == "volcanic_land":
        _draw_volcano(draw, width, height, plan, fonts)
    elif plan.scene_type == "roman_aqueduct":
        _draw_aqueduct(draw, width, height, plan, fonts)
    elif plan.scene_type == "bee_communication":
        _draw_bees(draw, width, height, plan, fonts)
    elif plan.scene_type == "map_explainer":
        _draw_map(draw, width, height, plan, fonts)
    elif plan.scene_type.startswith("historical_maritime_"):
        _draw_historical_maritime(draw, width, height, plan, fonts)
    elif plan.scene_type == "astronomy_scale":
        _draw_astronomy_scale(draw, width, height, plan, fonts)
    else:
        _draw_generic_motion_graphic(draw, width, height, plan, narration, fonts)
    _draw_goal_badge(draw, width, height, plan.visual_goal, fonts["small"])


def _draw_title(
    draw: ImageDraw.ImageDraw,
    width: int,
    title: str,
    font: ImageFont.ImageFont,
) -> None:
    lines = textwrap.wrap(title.upper(), width=18)[:2]
    y = 108
    for line in lines:
        text_width = draw.textlength(line, font=font)
        draw.text(((width - text_width) / 2, y), line, fill="#F8FBFF", font=font)
        y += 74


def _draw_space_weather(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    palette = _palette(plan.scene_type)
    sun = (170, 650)
    earth = (820, 1050)
    draw.ellipse([sun[0] - 145, sun[1] - 145, sun[0] + 145, sun[1] + 145], fill="#FFB648")
    for radius in (190, 250, 310):
        draw.ellipse([sun[0] - radius, sun[1] - radius, sun[0] + radius, sun[1] + radius], outline="#FFDD7A", width=3)
    for offset in range(-230, 260, 90):
        draw.line([sun[0] + 160, sun[1] + offset, earth[0] - 155, earth[1] + offset // 3], fill=palette[2], width=9)
        draw.polygon([
            (earth[0] - 170, earth[1] + offset // 3),
            (earth[0] - 210, earth[1] + offset // 3 - 18),
            (earth[0] - 210, earth[1] + offset // 3 + 18),
        ], fill=palette[2])
    draw.ellipse([earth[0] - 126, earth[1] - 126, earth[0] + 126, earth[1] + 126], fill="#2D8CFF")
    draw.arc([earth[0] - 245, earth[1] - 320, earth[0] + 245, earth[1] + 320], 88, 272, fill="#89F7FE", width=8)
    draw.arc([earth[0] - 330, earth[1] - 430, earth[0] + 330, earth[1] + 430], 88, 272, fill="#66E0A3", width=5)
    _label(draw, "SOLAR WIND", 285, 470, fonts["label"])
    _label(draw, "MAGNETOSPHERE", 520, 1370, fonts["label"])
    _label(draw, "AURORA ZONE", 555, 1530, fonts["label"])


def _draw_volcano(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    draw.polygon([(155, 1380), (520, 540), (930, 1380)], fill="#344148")
    draw.polygon([(285, 1380), (520, 610), (805, 1380)], fill="#5B4237")
    draw.polygon([(455, 1380), (520, 650), (610, 1380)], fill="#FF6B35")
    draw.rectangle([0, 1380, width, height], fill="#162A32")
    draw.polygon([(595, 1380), (1010, 1380), (1010, 1485), (655, 1485)], fill="#E85D32")
    for x in range(640, 1000, 80):
        draw.line([x, 1410, x + 70, 1478], fill="#FFD166", width=6)
    draw.ellipse([450, 470, 590, 610], fill="#FFCB47")
    _label(draw, "MAGMA CHAMBER", 105, 1490, fonts["label"])
    _label(draw, "LAVA FLOW", 650, 1290, fonts["label"])
    _label(draw, "NEW LAND", 700, 1540, fonts["label"])


def _draw_aqueduct(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    y_base = 1320
    for x in range(120, 920, 180):
        draw.rectangle([x, 760, x + 70, y_base], fill="#B9A57D")
        draw.arc([x - 10, 760, x + 190, 1120], 180, 360, fill="#D8C493", width=28)
    draw.rectangle([90, 700, 990, 785], fill="#D8C493")
    draw.line([130, 725, 950, 745], fill="#4FC3F7", width=16)
    draw.line([130, 725, 950, 745], fill="#A7E8FF", width=5)
    draw.polygon([(70, 1450), (1010, 1190), (1010, height), (70, height)], fill="#263B2E")
    _label(draw, "GRAVITY CHANNEL", 250, 610, fonts["label"])
    _label(draw, "STONE ARCHES", 260, 1370, fonts["label"])
    _label(draw, "CITY WATER SUPPLY", 540, 820, fonts["label"])


def _draw_bees(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    for row in range(7):
        for col in range(5):
            cx = 210 + col * 150 + (row % 2) * 75
            cy = 610 + row * 112
            _hex(draw, cx, cy, 58, "#DAA520", "#F5D76E")
    path = [(520, 920), (460, 820), (520, 720), (580, 820), (520, 920), (470, 1020), (520, 1120), (570, 1020), (520, 920)]
    draw.line(path, fill="#111111", width=12)
    for cx, cy in ((520, 920), (460, 820), (580, 820), (470, 1020), (570, 1020)):
        _bee(draw, cx, cy)
    draw.line([840, 490, 710, 790], fill="#FFD166", width=8)
    draw.ellipse([780, 390, 910, 520], fill="#FFD166")
    _label(draw, "WAGGLE PATH", 330, 1240, fonts["label"])
    _label(draw, "SUN ANGLE", 700, 540, fonts["label"])
    _label(draw, "FOOD DIRECTION", 560, 1390, fonts["label"])


def _draw_map(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    draw.rounded_rectangle([120, 440, 960, 1460], radius=36, fill="#D9E8DE")
    draw.polygon([(220, 620), (360, 540), (500, 640), (430, 820), (250, 800)], fill="#8BAA7C")
    draw.polygon([(590, 620), (820, 540), (860, 790), (680, 860), (560, 760)], fill="#8BAA7C")
    for points in (
        [(225, 1150), (390, 1020), (610, 1040), (810, 900)],
        [(290, 1320), (520, 1260), (760, 1330)],
    ):
        draw.line(points, fill="#1668A7", width=18)
        draw.polygon([(points[-1][0], points[-1][1]), (points[-1][0] - 45, points[-1][1] - 15), (points[-1][0] - 20, points[-1][1] + 40)], fill="#1668A7")
    _label(draw, "REAL-WORLD PATH", 220, 1500, fonts["label"])
    _label(draw, "DIRECTION", 600, 890, fonts["label"])


def _draw_historical_maritime(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    template = plan.scene_type.replace("historical_maritime_", "")
    if template == "ship_collision_diagram":
        _draw_ship(draw, 215, 1020, 520, "#B8C7D9")
        draw.polygon([(755, 700), (930, 1180), (635, 1180)], fill="#D9F3FF", outline="#83CDEB")
        draw.line([720, 980, 560, 1040], fill="#EF476F", width=12)
        _label(draw, "ICEBERG IMPACT", 500, 1320, fonts["label"])
        _label(draw, "HULL DAMAGE", 235, 1165, fonts["label"])
        return
    if template == "wreck_depth_map":
        draw.line([540, 410, 540, 1490], fill="#4FC3F7", width=10)
        for y, label in ((530, "SURFACE"), (860, "SEARCH ZONE"), (1220, "WRECK DEPTH")):
            draw.line([430, y, 650, y], fill="#A7E8FF", width=6)
            _label(draw, label, 675, y - 25, fonts["label"])
        _draw_ship(draw, 300, 1420, 430, "#7F8C8D")
        return
    if template == "wreck_decay_diagram":
        _draw_ship(draw, 210, 970, 610, "#7F8C8D")
        for x in range(320, 760, 95):
            draw.ellipse([x, 1150, x + 44, 1194], fill="#D46A2C")
            draw.line([x + 20, 1190, x + 50, 1270], fill="#D46A2C", width=6)
        _label(draw, "RUSTICLES", 250, 1330, fonts["label"])
        _label(draw, "METAL DECAY", 600, 1215, fonts["label"])
        return
    if template == "archive_headline":
        draw.rounded_rectangle([145, 520, 935, 1370], radius=18, fill="#E8DEC8")
        draw.text((205, 610), "ARCHIVE", fill="#1C1C1C", font=fonts["title"])
        draw.line([205, 730, 875, 730], fill="#1C1C1C", width=5)
        _draw_ship(draw, 245, 930, 590, "#455A64")
        _label(draw, "NEWSPAPER CLAIM", 255, 1435, fonts["label"])
        return
    if template == "artifact_evidence":
        draw.ellipse([360, 900, 520, 990], fill="#6D5F4F")
        draw.ellipse([540, 925, 700, 1015], fill="#6D5F4F")
        draw.line([220, 1180, 860, 1180], fill="#A47E50", width=8)
        draw.scatter = None
        _label(draw, "DEBRIS FIELD", 330, 1225, fonts["label"])
        _label(draw, "HUMAN EVIDENCE", 295, 1380, fonts["label"])
        return
    _draw_ship(draw, 220, 1030, 650, "#B8C7D9")
    _label(draw, "OCEAN LINER", 330, 1330, fonts["label"])


def _draw_astronomy_scale(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    draw.ellipse([410, 650, 670, 910], fill="#3A7BD5")
    draw.ellipse([715, 520, 940, 745], fill="#D8A657")
    draw.arc([225, 500, 950, 1225], 20, 330, fill="#89F7FE", width=7)
    draw.ellipse([265, 1080, 330, 1145], fill="#D9D9D9")
    _label(draw, "SCALE COMPARISON", 250, 1270, fonts["label"])
    _label(draw, "ORBIT PATH", 610, 1075, fonts["label"])


def _draw_generic_motion_graphic(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    plan: HybridCompositionPlan,
    narration: str,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> None:
    digest = hashlib.sha1(f"{plan.title}:{narration}".encode("utf-8")).digest()
    colors = ("#4FC3F7", "#66E0A3", "#FFD166", "#EF476F")
    center = (width // 2, 890)
    for index, radius in enumerate((310, 230, 150)):
        color = colors[digest[index] % len(colors)]
        draw.ellipse([center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius], outline=color, width=10)
    for index, component in enumerate(plan.components):
        y = 1240 + index * 95
        draw.rounded_rectangle([165, y, 915, y + 64], radius=14, fill="#203642")
        draw.text((205, y + 12), component.upper(), fill="#F8FBFF", font=fonts["label"])


def _draw_goal_badge(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    goal: str,
    font: ImageFont.ImageFont,
) -> None:
    label = str(goal or "show").upper()
    text_width = draw.textlength(label, font=font)
    x0 = width - text_width - 125
    y0 = height - 185
    draw.rounded_rectangle([x0, y0, width - 72, y0 + 58], radius=18, fill="#1C2D36")
    draw.text((x0 + 28, y0 + 13), label, fill="#A7E8FF", font=font)


def _draw_background(draw: ImageDraw.ImageDraw, width: int, height: int, scene_type: str) -> None:
    bg, accent, _ = _palette(scene_type)
    for y in range(0, height, 80):
        shade = 1 + y / height
        color = _mix(bg, accent, min(0.16, shade * 0.06))
        draw.rectangle([0, y, width, y + 80], fill=color)
    for x in range(-200, width, 180):
        draw.line([x, 0, x + 520, height], fill=_mix(bg, "#FFFFFF", 0.06), width=2)


def _label(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
) -> None:
    padding = 18
    text_width = draw.textlength(text, font=font)
    draw.rounded_rectangle(
        [x - padding, y - padding, x + text_width + padding, y + 50 + padding],
        radius=16,
        fill="#101820",
    )
    draw.text((x, y), text, fill="#F8FBFF", font=font)


def _hex(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: int, fill: str, outline: str) -> None:
    points = []
    for x_mul, y_mul in ((0, -1), (0.86, -0.5), (0.86, 0.5), (0, 1), (-0.86, 0.5), (-0.86, -0.5)):
        points.append((cx + x_mul * radius, cy + y_mul * radius))
    draw.polygon(points, fill=fill, outline=outline)


def _bee(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    draw.ellipse([cx - 30, cy - 18, cx + 30, cy + 18], fill="#F7C948", outline="#111111", width=3)
    draw.line([cx - 10, cy - 18, cx - 10, cy + 18], fill="#111111", width=5)
    draw.line([cx + 10, cy - 18, cx + 10, cy + 18], fill="#111111", width=5)
    draw.ellipse([cx - 26, cy - 52, cx + 2, cy - 16], fill="#D6F3FF", outline="#5EA6C8", width=2)
    draw.ellipse([cx - 2, cy - 52, cx + 26, cy - 16], fill="#D6F3FF", outline="#5EA6C8", width=2)


def _palette(scene_type: str) -> tuple[str, str, str]:
    if scene_type.startswith("historical_maritime_"):
        return ("#081826", "#18354A", "#77C5D5")
    return {
        "space_weather": ("#071521", "#12344D", "#89F7FE"),
        "astronomy_scale": ("#071521", "#12344D", "#89F7FE"),
        "volcanic_land": ("#171A1C", "#4A2D2A", "#FF6B35"),
        "roman_aqueduct": ("#152521", "#46513A", "#D8C493"),
        "bee_communication": ("#18170F", "#5D4A14", "#F5D76E"),
        "map_explainer": ("#10242B", "#284E4D", "#4FC3F7"),
    }.get(scene_type, ("#101820", "#203642", "#66E0A3"))


def _draw_ship(draw: ImageDraw.ImageDraw, x: int, y: int, length: int, fill: str) -> None:
    height = max(80, length // 5)
    draw.polygon(
        [(x, y), (x + length, y), (x + length - 80, y + height), (x + 80, y + height)],
        fill=fill,
        outline="#E6EEF5",
    )
    draw.rectangle([x + 120, y - 70, x + length - 160, y], fill=fill, outline="#E6EEF5")
    for offset in range(175, max(176, length - 180), 90):
        draw.ellipse([x + offset, y - 45, x + offset + 34, y - 11], fill="#0B1C2A")


def _mix(left: str, right: str, ratio: float) -> str:
    l_rgb = tuple(int(left[index:index + 2], 16) for index in (1, 3, 5))
    r_rgb = tuple(int(right[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(int(l_rgb[index] * (1 - ratio) + r_rgb[index] * ratio) for index in range(3))
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def _fonts() -> dict[str, ImageFont.ImageFont]:
    paths = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def font(size: int, bold: bool = True) -> ImageFont.ImageFont:
        candidates = paths[0:1] + paths[2:3] if bold else paths[1:2] + paths[3:4]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    return {"title": font(66), "label": font(36), "small": font(28, bold=False)}


def _title_from_queries(queries: list[str] | tuple[str, ...], topic: str) -> str:
    for query in queries:
        cleaned = " ".join(str(query or "").replace("-", " ").split())
        if cleaned:
            return cleaned[:48]
    return str(topic or "Documentary visual")[:48]


def _shot_intent_value(shot_intent: Any | None, name: str, default: str) -> str:
    value = getattr(shot_intent, name, default)
    return getattr(value, "value", value) or default


def _grammar_decision_value(grammar_decision: Any | None, name: str, default: str = "") -> str:
    value = getattr(grammar_decision, name, default)
    return getattr(value, "value", value) or default


def _maritime_components(template: str) -> tuple[str, ...]:
    return {
        "ship_collision_diagram": ("ocean liner", "iceberg", "hull damage", "impact path"),
        "wreck_depth_map": ("surface", "search zone", "wreck depth", "seafloor"),
        "wreck_decay_diagram": ("steel hull", "rusticles", "bacteria", "decay timeline"),
        "archive_headline": ("newspaper headline", "archive photo", "claim", "public memory"),
        "artifact_evidence": ("debris field", "artifact", "human evidence", "wreck site"),
        "ship_diagram": ("ocean liner", "deck", "hull", "scale"),
    }.get(template, ("archive image", "map", "diagram"))


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())
