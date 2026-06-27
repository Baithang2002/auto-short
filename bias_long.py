#!/usr/bin/env python3
"""
bias_long.py — Automated Long-Form Landscape Video Generator for The Bias Files

Compiles 10-12 minute landscape YouTube videos (1920x1080) from a topic.
Handles script writing, TTS, stock B-roll downloads, Pillow text/citation cards,
Matplotlib charts, sound effects, Jamendo music mixing, and burned captions.
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
import subprocess
import shutil
import argparse
import datetime as dt
import uuid
import re
from pathlib import Path

# Load shared config and utilities from auto_short.py
import auto_short
import sfx
import charts
import text_cards
import thumbnail_gen

# Set resolution globals for landscape
auto_short.WIDTH = 1920
auto_short.HEIGHT = 1080
auto_short.VOICE = "en-US-EmmaNeural"  # Female warm storyteller default
auto_short.DEFAULT_MUSIC_VOLUME = 0.10  # Ducked music volume

# Output paths
SCRIPT_DIR = Path(__file__).parent
OUT_DIR = SCRIPT_DIR / "output"
LONG_OUT_DIR = OUT_DIR / "long"

# Override highlight words to focus on money/bias
auto_short.CAPTION_HIGHLIGHT_WORDS = {
    "loss", "lost", "broke", "bankrupt", "million", "billion", "trillion",
    "dollar", "dollars", "money", "rich", "poor", "fortune", "wealth",
    "wrong", "bias", "brain", "study", "research", "proven", "actually",
    "real", "smart", "dumb", "secretly", "twice", "doubled", "kahneman",
    "nobel", "experiment", "scientists", "psychologists",
    "never", "always", "every", "single", "exactly",
}

# Episode 1 hardcoded pre-verified script & shotlist (30 scenes)
EPISODE_1_SCENES = [
    {
        "idx": 0,
        "voice": "If you found a hundred dollars on the street today, you'd feel happy.",
        "visual_type": "stock_video",
        "stock_search": ["hundred dollar bill street ground", "money sidewalk"],
        "sfx": "pop",
        "phase": "PHASE 1 — HOOK"
    },
    {
        "idx": 1,
        "voice": "If you lost a hundred dollars tomorrow, you'd feel devastated.",
        "visual_type": "stock_video",
        "stock_search": ["empty wallet open hand", "person looking through wallet worried"],
        "sfx": "whoosh",
        "phase": "PHASE 1 — HOOK"
    },
    {
        "idx": 2,
        "voice": "Mathematically, those events are equal. To your brain, they're not even close.",
        "visual_type": "vs_card",
        "left_text": "Losing $100",
        "right_text": "Winning $100",
        "sfx": "heartbeat",
        "phase": "PHASE 1 — HOOK"
    },
    {
        "idx": 3,
        "voice": "In 1979, two psychologists ran an experiment that would eventually win one of them a Nobel Prize.",
        "visual_type": "citation",
        "study_name": "Prospect Theory: An Analysis of Decision under Risk",
        "authors": "Daniel Kahneman & Amos Tversky",
        "journal": "Econometrica",
        "year": 1979,
        "sfx": "paper_flip",
        "phase": "PHASE 1 — HOOK"
    },
    {
        "idx": 4,
        "voice": "They asked people a simple question: how much would you need to potentially win in a coin flip to make a hundred dollar loss feel worth the risk? The average answer wasn't a hundred dollars. It wasn't a hundred and fifty. It was around two hundred. Sometimes higher. Losing a hundred dollars hurts roughly twice as much as gaining a hundred feels good.",
        "visual_type": "chart",
        "chart_type": "loss_gain",
        "sfx": "coin_drop",
        "phase": "PHASE 1 — HOOK"
    },
    {
        "idx": 5,
        "voice": "This is loss aversion. And it isn't a personality flaw or a sign that you're bad with money. It's a hardwired neurological response running quietly behind every financial decision you make. In the next ten minutes I'm going to show you exactly how this single bias makes you hold losing stocks too long, refuse to negotiate raises you'd easily win, and keep paying for subscriptions you stopped using six months ago. I'll show you the brain-scan study that proved it. And the three-word interrupt the wealthiest investors in the world use to override it. But here's what makes loss aversion so dangerous. It doesn't feel like a bias when it's happening. It feels like wisdom. It feels like caution. It feels like the smart, responsible voice in your head telling you to wait, to hold, to not take the risk. And by the time you realize that voice was wrong, the money is already gone. Let me show you how it works.",
        "visual_type": "title_card",
        "title": "Why Losing $100 Hurts More Than Winning $200",
        "subtitle": "Episode 1 — Loss Aversion",
        "sfx": "whoosh",
        "phase": "PHASE 1 — HOOK"
    },
    {
        "idx": 6,
        "voice": "To understand why your brain treats losses and gains so differently, we have to go back to 1970s Israel. A young military psychologist named Daniel Kahneman was teaching a class on decision-making at Hebrew University. He invited a guest lecturer, a cognitive psychologist named Amos Tversky, to give a talk. Tversky's presentation was on how people learn from rare events. Kahneman thought it was wrong.",
        "visual_type": "stock_video",
        "stock_search": ["Jerusalem old city", "vintage classroom"],
        "sfx": "typing",
        "phase": "PHASE 2 — SETUP"
    },
    {
        "idx": 7,
        "voice": "What followed was one of the most productive arguments in the history of science. The two men started meeting daily, often for hours, arguing through how human beings actually make choices under uncertainty. Not how economists assumed they did. How they really did.",
        "visual_type": "stock_video",
        "stock_search": ["blackboard equation math", "coffee cups desk"],
        "sfx": "click",
        "phase": "PHASE 2 — SETUP"
    },
    {
        "idx": 8,
        "voice": "For decades, mainstream economics had a clean, elegant model of the human decision-maker. The model assumed people calculated probabilities, weighted outcomes, and chose whatever maximized their expected value. A fifty percent chance to win a hundred dollars was treated as the mirror image of a fifty percent chance to lose a hundred dollars. Symmetric. Rational. Predictable.",
        "visual_type": "citation",
        "study_name": "Standard Expected Utility Theory",
        "authors": "John von Neumann & Oskar Morgenstern",
        "journal": "Theory of Games and Economic Behavior",
        "year": 1944,
        "sfx": "paper_flip",
        "phase": "PHASE 2 — SETUP"
    },
    {
        "idx": 9,
        "voice": "Kahneman and Tversky started running experiments. And what they found broke that model completely. They presented thousands of people with gambles. Would you take a fifty-fifty coin flip where you could win a hundred dollars or lose a hundred? Almost everyone said no. So they raised the upside. Win a hundred and ten? Still no. Win a hundred and fifty? Most people still refused. Win two hundred? Now, finally, people would take the bet. The asymmetry was staggering. People weren't weighing gains and losses equally. They were treating losses as roughly twice as painful as equivalent gains were pleasurable.",
        "visual_type": "stock_video",
        "stock_search": ["coin flip slow motion", "gamble roulette dice"],
        "sfx": "cash",
        "phase": "PHASE 2 — SETUP"
    },
    {
        "idx": 10,
        "voice": "In 1979, they published their findings in the journal Econometrica under a name that would become legendary in behavioral science: Prospect Theory. The ratio they discovered, that loss aversion runs roughly two to one, has been replicated in hundreds of studies since, across cultures, income levels, and decision contexts. In 2002, Kahneman received the Nobel Prize in Economics for this work. Tversky had died six years earlier and Nobel Prizes aren't awarded posthumously, which Kahneman called the great injustice of his career. That two-to-one ratio is now considered one of the most robust findings in all of psychology. And it's quietly destroying your finances right now.",
        "visual_type": "citation",
        "study_name": "Prospect Theory: An Analysis of Decision under Risk",
        "authors": "Kahneman & Tversky",
        "journal": "Econometrica + Nobel Prize",
        "year": 2002,
        "sfx": "pop",
        "phase": "PHASE 2 — SETUP"
    },
    {
        "idx": 11,
        "voice": "Loss aversion isn't an abstract academic idea. It's the invisible force behind some of the most common money mistakes people make. Start with stocks. If you've ever owned a stock that dropped thirty percent and you held it, telling yourself it would come back, that's loss aversion. Selling means accepting the loss is real. Holding lets you pretend it isn't.",
        "visual_type": "stock_video",
        "stock_search": ["red stock chart falling", "stock exchange screen"],
        "sfx": "heartbeat",
        "phase": "PHASE 3 — CORE CONTENT (Section A)"
    },
    {
        "idx": 12,
        "voice": "Terrance Odean, an economist at Berkeley, analyzed the trading records of ten thousand brokerage accounts in the 1990s. He found that investors were significantly more likely to sell their winning stocks than their losing ones. They were systematically holding losers and dumping winners. The exact opposite of what's profitable. Behavioral economists call this the disposition effect.",
        "visual_type": "citation",
        "study_name": "Are Investors Reluctant to Realize Their Losses?",
        "authors": "Terrance Odean",
        "journal": "Journal of Finance",
        "year": 1998,
        "sfx": "paper_flip",
        "phase": "PHASE 3 — CORE CONTENT (Section A)"
    },
    {
        "idx": 13,
        "voice": "And in Odean's data, the winners that investors sold went on to outperform the losers they kept holding, by an average of three point four percentage points over the next year.",
        "visual_type": "chart",
        "chart_type": "winners_losers",
        "sfx": "click",
        "phase": "PHASE 3 — CORE CONTENT (Section A)"
    },
    {
        "idx": 14,
        "voice": "Now think about subscriptions. The gym membership you haven't used in eight months. The streaming service playing to an empty room. The cloud storage charging you twelve dollars every month for files you forgot you uploaded. Canceling them feels like admitting you wasted the money you've already spent. So you don't cancel. And the bleeding continues.",
        "visual_type": "stock_video",
        "stock_search": ["empty gym treadmills", "phone streaming app screen"],
        "sfx": "whoosh",
        "phase": "PHASE 3 — CORE CONTENT (Section A)"
    },
    {
        "idx": 15,
        "voice": "Or think about salary negotiation. You're offered a job at seventy thousand. You know peers are making eighty thousand. You should counter. But you don't, because the imagined pain of the offer being pulled away, losing what you already have, outweighs the imagined pleasure of getting the higher number. Loss aversion just cost you ten thousand dollars a year, compounding for the rest of your career. In every one of these moments, your brain is running the same ancient math. Losses count more than gains. So you cling, you avoid, you under-ask.",
        "visual_type": "vs_card",
        "left_text": "Accept $70k Offer",
        "right_text": "Negotiate $80k Salary",
        "sfx": "click",
        "phase": "PHASE 3 — CORE CONTENT (Section A)"
    },
    {
        "idx": 16,
        "voice": "Here's what makes it scarier. We now know exactly what's happening inside your brain when this fires. In 2007, a team led by Sabrina Tom at UCLA put subjects in an fMRI scanner and offered them gambles. They watched, in real time, which brain regions activated when subjects considered potential gains versus potential losses. The expectation going in was that losses would light up fear and pain regions, the amygdala, the insula. The places that process threat.",
        "visual_type": "citation",
        "study_name": "The Neural Basis of Loss Aversion in Decision-Making Under Risk",
        "authors": "Tom, Fox, Trepel & Poldrack",
        "journal": "Science",
        "year": 2007,
        "sfx": "pop",
        "phase": "PHASE 3 — CORE CONTENT (Section B)"
    },
    {
        "idx": 17,
        "voice": "What they actually found was stranger. Losses didn't activate separate pain circuits. They activated the same reward circuits as gains, the ventral striatum, the prefrontal cortex, just much more strongly in the opposite direction.",
        "visual_type": "stock_video",
        "stock_search": ["fMRI brain scan animation", "brain glowing neuron"],
        "sfx": "heartbeat",
        "phase": "PHASE 3 — CORE CONTENT (Section B)"
    },
    {
        "idx": 18,
        "voice": "The neural response to a potential loss was roughly twice as large as the response to an equivalent gain. The exact same two-to-one ratio Kahneman and Tversky had measured behaviorally thirty years earlier was now measurable, in real-time blood flow, inside the human brain. Loss aversion isn't a habit. It isn't a personality. It's literally how your nervous system is wired.",
        "visual_type": "text_card",
        "text": "2 : 1 NEURAL RESPONSE",
        "sfx": "click",
        "phase": "PHASE 3 — CORE CONTENT (Section B)"
    },
    {
        "idx": 19,
        "voice": "So at this point you might be thinking the answer is simple, learn about loss aversion, train yourself to override it, become more rational. There's just one problem with that plan.",
        "visual_type": "stock_video",
        "stock_search": ["person reading book desk", "library shelves"],
        "sfx": "whoosh",
        "phase": "PHASE 3 — CORE CONTENT (Section C)"
    },
    {
        "idx": 20,
        "voice": "In 2006, three researchers, Keith Chen, Venkat Lakshminarayanan, and Laurie Santos at Yale, taught a small group of capuchin monkeys how to use money. They gave the monkeys metal tokens and trained them to exchange the tokens for food. Then they ran the loss aversion experiment.",
        "visual_type": "citation",
        "study_name": "How Basic Are Behavioral Biases? Evidence from Capuchin Monkey Trading Behavior",
        "authors": "Chen, Lakshminarayanan & Santos",
        "journal": "Journal of Political Economy",
        "year": 2006,
        "sfx": "paper_flip",
        "phase": "PHASE 3 — CORE CONTENT (Section C)"
    },
    {
        "idx": 21,
        "voice": "Half the monkeys saw an experimenter who would start with one grape and sometimes add a second. The other half saw an experimenter who would start with two grapes and sometimes remove one. The expected payoff was identical. But the monkeys overwhelmingly avoided the experimenter who showed them losses. They were running the same loss-averse calculus as humans.",
        "visual_type": "stock_video",
        "stock_search": ["capuchin monkey", "monkey eating food"],
        "sfx": "coin_drop",
        "phase": "PHASE 3 — CORE CONTENT (Section C)"
    },
    {
        "idx": 22,
        "voice": "These monkeys had never read Kahneman. They'd never taken an economics class. They didn't have credit cards or mortgages or retirement accounts. And they still couldn't escape loss aversion. Which means this bias isn't a product of modern life, or financial illiteracy, or capitalism. It's older than our species. It's been wired into primate brains for tens of millions of years, because for most of evolutionary history, losing what you had was more likely to kill you than gaining something new was to help you. The brain that kept you alive in the savanna is the same brain managing your four-oh-one-k. And it's optimized for a problem you no longer have.",
        "visual_type": "stock_video",
        "stock_search": ["african savanna sunrise", "savanna landscape"],
        "sfx": "whoosh",
        "phase": "PHASE 3 — CORE CONTENT (Section C)"
    },
    {
        "idx": 23,
        "voice": "So if loss aversion is hardwired into a brain that's older than agriculture, what hope do you actually have of beating it?",
        "visual_type": "stock_video",
        "stock_search": ["person thinking hand on chin", "office window view"],
        "sfx": "heartbeat",
        "phase": "PHASE 4 — PAYOFF"
    },
    {
        "idx": 24,
        "voice": "The answer, from forty years of behavioral economics research, comes down to three words. Compared to what.",
        "visual_type": "text_card",
        "text": "COMPARED TO WHAT",
        "sfx": "pop",
        "phase": "PHASE 4 — PAYOFF"
    },
    {
        "idx": 25,
        "voice": "When loss aversion fires, it shows you the loss in isolation. The four thousand dollar stock that's now worth two thousand eight hundred. The gym membership you're paying for but not using. The salary you're afraid to negotiate up from. In each of those frames, the loss is the only thing on the screen. So your brain treats it as the only thing that matters. The interrupt is to force a comparison. That losing stock isn't really worth two thousand eight hundred dollars. It's worth whatever you could buy with two thousand eight hundred right now. If you sold it and put the money in an index fund that historically returns seven percent, what does it look like in ten years? Compared to what, staying invested in something that's been falling for nine months? Suddenly the calculation changes. That gym membership isn't forty dollars a month you're losing. It's four hundred and eighty dollars a year you could redirect to anything else. Compared to what, paying a company for nothing? The frame flips. Loss aversion needs an isolated loss to function. The moment you force a side-by-side, the spell breaks. Because now your brain is comparing two outcomes, both with potential losses, both with potential gains, and the two-to-one bias cancels out across both sides of the ledger.",
        "visual_type": "stock_video",
        "stock_search": ["decision matrix blackboard", "investment index fund chart"],
        "sfx": "click",
        "phase": "PHASE 4 — PAYOFF"
    },
    {
        "idx": 26,
        "voice": "The wealthiest investors have institutionalized this. Ray Dalio's firm requires every major decision to be presented with an explicit alternative. Warren Buffett's opportunity-cost framework, every dollar he holds is implicitly compared to every other place that dollar could go. These aren't superhuman thinkers. They're just running a routine that strips loss aversion of its trick.",
        "visual_type": "stock_video",
        "stock_search": ["business meeting boardroom", "financial chart whiteboard"],
        "sfx": "cash",
        "phase": "PHASE 4 — PAYOFF"
    },
    {
        "idx": 27,
        "voice": "So that's the Bias File on loss aversion. Discovered by Kahneman and Tversky in 1979. Measured in the brain by Tom and Poldrack in 2007. Observed in capuchin monkeys at Yale in 2006. Interrupted with three words: compared to what.",
        "visual_type": "text_card",
        "text": "THE BIAS FILES: LOSS AVERSION",
        "sfx": "whoosh",
        "phase": "PHASE 4 — PAYOFF"
    },
    {
        "idx": 28,
        "voice": "But here's the unsettling part I'll leave you with. Daniel Kahneman, the man who spent his career proving loss aversion exists, who won the Nobel Prize for the discovery, who literally wrote the book on how to think about it, has admitted in multiple interviews that knowing about these biases did almost nothing to help him avoid them in his own life. He still made the same mistakes. He still felt losses twice as hard. He still hesitated before risks he should have taken. Which means understanding this bias isn't enough. You need the interrupt. You need the comparison. You need the friction of forcing your brain to look at the alternative, every time, because the bias itself doesn't get weaker just because you can name it.",
        "visual_type": "stock_video",
        "stock_search": ["sad older man portrait", "worried older professor"],
        "sfx": "click",
        "phase": "PHASE 4 — PAYOFF"
    },
    {
        "idx": 29,
        "voice": "That's the Bias File for this week. We'll be back Saturday with the story of a man who won three hundred and fifteen million dollars in the lottery and lost almost all of it within seven years, and the bias that made it inevitable. Subscribe to The Bias Files for one new money trap your brain falls for every week.",
        "visual_type": "stock_video",
        "stock_search": ["lottery ticket hand close up", "dollar bills falling"],
        "sfx": "coin_drop",
        "phase": "PHASE 4 — PAYOFF"
    }
]

# Helper functions
def is_image(filepath: str | Path) -> bool:
    return Path(filepath).suffix.lower() in auto_short.IMAGE_EXTENSIONS

def run_ff(args: list[str], cwd: str | Path | None = None) -> str:
    return auto_short.run_ff(args, cwd)

def mix_audio_with_sfx(voice_path: Path, sfx_name: str, out_path: Path, sfx_delay_ms: int = 150) -> Path:
    """Overlay a synthesized SFX over the voice track."""
    try:
        sfx_path = sfx.get_sfx(sfx_name)
    except Exception as e:
        print(f"      [Warning] SFX '{sfx_name}' could not be generated: {e}. Using dry voice.")
        shutil.copyfile(voice_path, out_path)
        return out_path

    # Build filter complex to delay sfx and mix with voice
    filter_complex = (
        f"[1:a]adelay={sfx_delay_ms}|{sfx_delay_ms}[delayed_sfx];"
        f"[0:a][delayed_sfx]amix=inputs=2:duration=first:normalize=0[mixed]"
    )
    
    run_ff([
        "ffmpeg", "-y",
        "-i", str(voice_path),
        "-i", str(sfx_path),
        "-filter_complex", filter_complex,
        "-map", "[mixed]",
        "-c:a", "pcm_s16le", "-ar", "44100",
        str(out_path)
    ])
    return out_path

def image_to_clip_landscape(image_path: Path, duration: float, idx: int, animate: bool = False) -> Path:
    """Convert still image to landscape video segment with optional subtle Ken Burns zoom."""
    out_path = LONG_OUT_DIR / "temp" / f"img_clip_{idx}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = int(duration * auto_short.FPS)

    if animate:
        # Slow zoom from 1.0 to 1.15 over the course of the scene
        vf = (
            f"loop=loop={total_frames}:size=1:start=0,"
            f"scale=3840:2160,"
            f"zoompan=z='min(zoom+0.0005,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={auto_short.WIDTH}x{auto_short.HEIGHT}:fps={auto_short.FPS},"
            f"setsar=1"
        )
        run_ff([
            "ffmpeg", "-y",
            "-i", str(image_path),
            "-vf", vf,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(out_path),
        ])
    else:
        vf = f"scale={auto_short.WIDTH}:{auto_short.HEIGHT},setsar=1"
        run_ff([
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(out_path),
        ])
    return out_path

def build_segment(idx: int, broll_path: Path, voice_path: Path, duration: float, sfx_name: str | None = None, visual_type: str = "stock_video") -> Path:
    """Build landscape MP4 clip combining B-roll/graphics + voice + SFX."""
    out_path = LONG_OUT_DIR / "temp" / f"seg_{idx}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Mix SFX if specified
    active_voice = voice_path
    if sfx_name:
        mixed_voice_path = LONG_OUT_DIR / "temp" / f"voice_sfx_{idx}.wav"
        active_voice = mix_audio_with_sfx(voice_path, sfx_name, mixed_voice_path)

    # 2. Build visual source
    if is_image(broll_path):
        # Ken Burns zoom on cards/titles, static for citation/charts
        animate_kb = visual_type in ["text_card", "title_card"]
        visual_clip = image_to_clip_landscape(broll_path, duration, idx, animate=animate_kb)
        run_ff([
            "ffmpeg", "-y",
            "-i", str(visual_clip),
            "-i", str(active_voice),
            "-t", f"{duration:.3f}",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-shortest",
            str(out_path),
        ])
        return out_path

    # 3. Video mode: Scale, Crop to landscape, and loop if needed
    vf = f"scale={auto_short.WIDTH}:{auto_short.HEIGHT}:force_original_aspect_ratio=increase,crop={auto_short.WIDTH}:{auto_short.HEIGHT},setsar=1,fps={auto_short.FPS}"
    run_ff([
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(broll_path),
        "-i", str(active_voice),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-shortest",
        str(out_path),
    ])
    return out_path

def generate_script_general(topic: str) -> dict:
    """Gemini-powered general long-form video planner."""
    # Ensure dependencies and keys
    auto_short.check_deps()

    prompt = f"""
You are scripting a detailed 10-minute landscape documentary video for THE BIAS FILES — a YouTube channel about money psychology, cognitive biases, and finance traps.

TOPIC: "{topic}"

Return a STRICT JSON object representing the video plan. Do not include markdown backticks or preamble.
The JSON must fit this format:
{{
  "title": "ep title",
  "description": "description",
  "music_mood": "mysterious | dramatic | urgent | curious",
  "hashtags": ["#tag1", "#tag2"],
  "scenes": [
    {{
      "voice": "3-4 sentences of plain spoken narration (about 40-60 words). Specific, documentary tone.",
      "visual_type": "stock_video | text_card | citation | chart | vs_card | title_card",
      "stock_search": ["pexels video keyword1", "pexels video keyword2"],
      "text_overlay": "Main card text or comparison label if visual_type is card or vs_card",
      "citation_details": {{
        "study_name": "Study Title",
        "authors": "Author List",
        "journal": "Journal Name",
        "year": 2024
      }},
      "chart_details": {{
        "chart_type": "loss_gain | comparison | bar",
        "labels": ["Bar1", "Bar2"],
        "values": [10, 20],
        "colors": ["#E63946", "#D4AF37"]
      }},
      "sfx": "whoosh | pop | cash | click | heartbeat | coin_drop | paper_flip | typing",
      "phase": "PHASE 1 — HOOK | PHASE 2 — SETUP | PHASE 3 — CORE CONTENT | PHASE 4 — PAYOFF"
    }}
  ]
}}

Generate exactly 18 to 22 sequential scenes. Include:
- At least 3 citations of real academic studies or books.
- At least 1 vs_card comparison.
- At least 1 Matplotlib chart (loss_gain, comparison, or bar).
- Visual variety: mix stock_video (primary) with title_cards, text_cards, citation_cards, and charts.
- Sound effects at transition moments.
- STRICT ALIGNMENT: The "stock_search" keywords for a scene MUST directly match the key subject, person, object, or action described in that scene's "voice" narration. If you discuss a coin flip, the search term must be coin flip. If you discuss a capuchin monkey, the search term must be monkey. Never use unrelated visuals or broad abstract concepts. Keep each search query simple, concrete, and Pexels-friendly.
"""
    print("[1/5] generating long-form script structure with Gemini...")
    raw = auto_short.generate_script_raw(prompt)
    data = auto_short.parse_script_json(raw)
    
    # Normalize general script metadata
    data.setdefault("title", topic)
    data.setdefault("description", topic)
    data.setdefault("music_mood", "dramatic")
    data.setdefault("hashtags", ["#moneypsychology", "#cognitivebias", "#thebiasfiles"])
    
    return data

def generate_voiceovers(scenes: list[dict], episode_dir: Path) -> list[dict]:
    """Generates voice files for each scene and gathers duration info."""
    voice_items = []
    voice_dir = episode_dir / "audio" / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)

    print("[2/5] generating voiceovers with Edge-TTS...")
    for idx, scene in enumerate(scenes):
        voice_path = voice_dir / f"voice_{idx}.mp3"
        text = scene["voice"]
        print(f"      Scene {idx+1}/{len(scenes)}: generating Edge-TTS voiceover...")
        auto_short._edge_tts_with_retry(text, voice_path)
        
        duration = auto_short.media_duration(voice_path)
        voice_items.append({
            "idx": idx,
            "scene": scene,
            "voice_path": voice_path,
            "duration": duration
        })
    return voice_items

def generate_assets(scenes: list[dict], episode_dir: Path, local_media: list, used_set: set) -> list[Path]:
    """Creates/downloads all visuals (stock video, text cards, charts, citations)."""
    visual_paths = []
    assets_dir = episode_dir / "assets"
    stock_dir = assets_dir / "stock"
    cards_dir = assets_dir / "cards"
    charts_dir = assets_dir / "charts"

    stock_dir.mkdir(parents=True, exist_ok=True)
    cards_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    print("[3/5] gathering B-roll, cards, and charts...")
    for idx, scene in enumerate(scenes):
        vtype = scene.get("visual_type", "stock_video")
        voice_text = scene["voice"]
        
        print(f"      Scene {idx+1}/{len(scenes)}: type='{vtype}'")
        
        if vtype == "stock_video":
            queries = scene.get("stock_search", ["money cash"])
            broll = auto_short.fetch_broll(
                queries=queries,
                idx=idx,
                fallback="money finance",
                local_media=local_media,
                narration=voice_text,
                used_set=used_set,
                hybrid=bool(local_media),
                target_duration=5.0
            )
            # Copy to current episode assets dir
            episode_broll = stock_dir / f"broll_{idx}.mp4"
            shutil.copy2(str(broll), str(episode_broll))
            visual_paths.append(episode_broll)
            
        elif vtype == "title_card":
            out_p = cards_dir / f"title_{idx}.png"
            text_cards.create_title_card(
                title=scene.get("title", "Episode Title"),
                subtitle=scene.get("subtitle", "Episode 1"),
                output_path=out_p
            )
            visual_paths.append(out_p)
            
        elif vtype == "text_card":
            out_p = cards_dir / f"text_{idx}.png"
            text_cards.create_text_card(
                text=scene.get("text", scene.get("text_overlay", "THE BIAS FILES")),
                output_path=out_p
            )
            visual_paths.append(out_p)
            
        elif vtype == "vs_card":
            out_p = cards_dir / f"vs_{idx}.png"
            left = scene.get("left_text", "Loss")
            right = scene.get("right_text", "Gain")
            text_cards.create_vs_card(
                left_text=left,
                right_text=right,
                output_path=out_p
            )
            visual_paths.append(out_p)
            
        elif vtype == "citation":
            out_p = cards_dir / f"citation_{idx}.png"
            text_cards.create_citation_card(
                study_name=scene.get("study_name", "Academic Study"),
                authors=scene.get("authors", "Researchers"),
                journal=scene.get("journal", "Journal"),
                year=scene.get("year", 2026),
                output_path=out_p
            )
            visual_paths.append(out_p)
            
        elif vtype == "chart":
            out_p = charts_dir / f"chart_{idx}.png"
            ctype = scene.get("chart_type", "bar")
            
            if ctype == "loss_gain":
                charts.create_loss_gain_chart(out_p)
            elif ctype == "winners_losers" or ctype == "comparison":
                charts.create_comparison_chart(
                    label_a=scene.get("left_text", "Winners Sold"),
                    value_a=10.0,
                    label_b=scene.get("right_text", "Losers Held"),
                    value_b=13.4,
                    output_path=out_p
                )
            else:
                # Custom general bar chart from details
                details = scene.get("chart_details", {})
                labels = details.get("labels", ["A", "B"])
                values = details.get("values", [1, 2])
                c_colors = details.get("colors", ["#E63946", "#D4AF37"])
                c_title = details.get("title", "Chart title")
                charts.create_bar_chart(
                    labels=labels,
                    values=values,
                    colors=c_colors,
                    title=c_title,
                    output_path=out_p
                )
            visual_paths.append(out_p)
            
        else:
            # Fallback B-roll
            broll = auto_short.fetch_broll(
                queries=["money"],
                idx=idx,
                fallback="finance",
                target_duration=5.0
            )
            episode_broll = stock_dir / f"broll_{idx}.mp4"
            shutil.copy2(str(broll), str(episode_broll))
            visual_paths.append(episode_broll)

    return visual_paths

def generate_chapters(voice_items: list[dict], output_path: Path):
    """Generates chapters.txt with timestamps for each phase/scene."""
    cumulative = 0.0
    lines = []
    
    current_phase = None
    
    for idx, item in enumerate(voice_items):
        scene = item["scene"]
        phase = scene.get("phase", f"Section {idx+1}")
        
        # Format time code: MM:SS or HH:MM:SS
        td = dt.timedelta(seconds=int(cumulative))
        timestamp = str(td)
        if timestamp.startswith("0:"):
            timestamp = timestamp[2:]  # MM:SS
            if len(timestamp) == 4:
                timestamp = "0" + timestamp
        
        if phase != current_phase:
            lines.append(f"{timestamp} {phase}")
            current_phase = phase
            
        cumulative += item["duration"]
        
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"      [OK] chapters.txt written to: {output_path}")

def orchestrate_long(topic: str, use_script: bool = True):
    # Setup directories
    slug = auto_short.slugify(topic)
    episode_dir = LONG_OUT_DIR / f"ep_{slug}"
    episode_dir.mkdir(parents=True, exist_ok=True)
    
    # Create temp workspace
    temp_workspace = LONG_OUT_DIR / "temp"
    if temp_workspace.exists():
        shutil.rmtree(temp_workspace)
    temp_workspace.mkdir(parents=True, exist_ok=True)
    
    print(f"\n=== Orchestrating Landscape Video: {topic} ===")
    
    # Step 1: Script & Scene planning
    if use_script and "winning" in topic.lower() and "losing" in topic.lower():
        print("[1/5] using pre-verified Episode 1 script & scenes...")
        scenes = EPISODE_1_SCENES
        metadata_source = {
            "title": "Why Losing $100 Hurts More Than Winning $200",
            "description": "Daniel Kahneman and Amos Tversky's Nobel Prize-winning Prospect Theory proved that the pain of losing is psychologically twice as powerful as the pleasure of gaining. We explore how loss aversion affects your stock trades, salary negotiations, and everyday subscriptions—and the three-word interrupt Dalio and Buffett use to cancel it out.",
            "music_mood": "mysterious",
            "hashtags": ["#moneypsychology", "#cognitivebias", "#investing", "#behavioralfinance", "#thebiasfiles"]
        }
    else:
        # Full automation path
        script_plan = generate_script_general(topic)
        scenes = script_plan["scenes"]
        metadata_source = script_plan

    # Step 2: Voiceovers
    voice_items = generate_voiceovers(scenes, episode_dir)
    
    # Step 3: Visual Assets
    local_media = auto_short.get_local_media()
    used_set = set()
    visual_paths = generate_assets(scenes, episode_dir, local_media, used_set)
    
    # Step 4: Assemble scene segments
    print("[4/5] assembling segments...")
    seg_paths = []
    meta = []
    for idx, item in enumerate(voice_items):
        scene = item["scene"]
        voice_path = item["voice_path"]
        duration = item["duration"]
        vpath = visual_paths[idx]
        sfx_name = scene.get("sfx")
        vtype = scene.get("visual_type", "stock_video")
        
        print(f"      Assembling segment {idx+1}/{len(scenes)} (dur={duration:.1f}s, visual={vpath.name}, sfx={sfx_name})...")
        seg = build_segment(idx, vpath, voice_path, duration, sfx_name=sfx_name, visual_type=vtype)
        seg_paths.append(seg)
        meta.append((scene["voice"], duration))
        
    # Step 5: Concat, Caption and Music
    print("[5/5] stitching final video...")
    
    # Concat
    listfile = temp_workspace / "concat.txt"
    listfile.write_text("".join(f"file '{p.name}'\n" for p in seg_paths), encoding="utf-8")
    
    combined_mp4 = temp_workspace / "combined.mp4"
    run_ff([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "combined.mp4"
    ], cwd=temp_workspace)
    
    # Subtitles (Burned-in)
    # Redirect auto_short outputs
    auto_short.OUT_DIR = temp_workspace
    auto_short.build_ass(meta)
    captioned_mp4 = auto_short.burn_captions()
    
    # Music
    captioned_duration = auto_short.media_duration(captioned_mp4)
    final_output_path = episode_dir / "video.mp4"
    
    # Mix Music
    print("      ducking background music...")
    auto_short.OUT_DIR = episode_dir
    final_mp4, music_used = auto_short.add_background_music(
        captioned_mp4,
        captioned_duration,
        metadata_source["music_mood"],
        music_volume=auto_short.DEFAULT_MUSIC_VOLUME
    )
    
    # Clean up and rename final video
    if final_mp4.exists():
        if final_output_path.exists():
            final_output_path.unlink()
        final_mp4.rename(final_output_path)
        
    # Generate Thumbnail
    print("      generating YouTube thumbnail...")
    thumb_path = episode_dir / "thumbnail.jpg"
    thumbnail_gen.generate_thumbnail(
        title_text=metadata_source["title"],
        output_path=thumb_path,
        variant="A",
        loss_text="Losing $100",
        gain_text="Winning $200"
    )
    
    # Save Chapters
    generate_chapters(voice_items, episode_dir / "chapters.txt")
    
    # Write metadata json
    metadata = {
        "id": f"long-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug}",
        "title": metadata_source["title"],
        "description": metadata_source["description"],
        "hashtags": metadata_source["hashtags"],
        "music_mood": metadata_source["music_mood"],
        "music_path": music_used,
        "duration_sec": round(captioned_duration, 2),
        "video_path": str(final_output_path.resolve()),
        "thumbnail_path": str(thumb_path.resolve()),
        "created_at": dt.datetime.now().isoformat()
    }
    (episode_dir / "upload_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    
    # Clean up temp files
    shutil.rmtree(temp_workspace, ignore_errors=True)
    
    print(f"\n[OK] Video created successfully!")
    print(f"     Video: {final_output_path}")
    print(f"     Thumbnail: {thumb_path}")
    print(f"     Chapters: {episode_dir / 'chapters.txt'}")
    print(f"     Metadata: {episode_dir / 'upload_metadata.json'}\n")

def main():
    parser = argparse.ArgumentParser(description="Bias Files Long-Form Video Pipeline")
    parser.add_argument("topic", nargs="?", default="Why Losing $100 Hurts More Than Winning $200", help="Video topic")
    parser.add_argument("--generate", action="store_true", help="Force Gemini script generation instead of pre-verified")
    
    args = parser.parse_args()
    
    use_pre_verified = not args.generate
    orchestrate_long(args.topic, use_script=use_pre_verified)

if __name__ == "__main__":
    main()
