"""
Quiet Video Maker - Automated Documentary Pipeline
- Automatic milestone callout badges (no FFmpeg eval errors)
- Contextual visual generation matching the topic
- Dynamic 1-2 line synchronized Hindi subtitles
- Energetic SwaraNeural voiceover
- Watermark elimination & Ken Burns camera motion
"""

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
import requests
from PIL import Image

FPS = 25
W, H = 1920, 1080
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.0-flash",
    "gemini-3.7-flash"
]

VOICE = os.environ.get("VOICE", "hi-IN-SwaraNeural")
VOICE_RATE = "+10%"

WORK = Path("work")
OUT = Path("out")

BAD_WORDS = ("image", "tts", "live", "audio", "embed", "robotics", "computer", "vision")

def fatal(msg: str) -> None:
    print("\nERROR: " + msg, flush=True)
    sys.exit(2)

def log(msg: str) -> None:
    print(msg, flush=True)

# ==================== Gemini Script Engine ====================
_good_model = None
_discovered = []

def gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        fatal("GEMINI_API_KEY is missing.")
    return key

def list_models(key: str) -> list:
    try:
        url = f"{GEMINI_BASE}/models?pageSize=200"
        r = requests.get(url, headers={"x-goog-api-key": key}, timeout=60)
        names = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in name or any(b in name for b in BAD_WORDS):
                continue
            names.append(name)

        def version(n: str) -> float:
            nums = re.findall(r"\d+(?:\.\d+)?", n)
            return float(nums[0]) if nums else 0.0

        normal = sorted([n for n in names if "lite" not in n], key=version, reverse=True)
        lite = sorted([n for n in names if "lite" in n], key=version, reverse=True)
        return (normal + lite)[:6]
    except Exception as e:
        log(f"Could not list models: {e!r}")
        return []

def extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a : b + 1])
        raise

def gemini_json(prompt: str, validate, label: str, max_tokens: int = 16000, temperature: float = 0.8):
    global _good_model, _discovered
    key = gemini_key()
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    env_model = os.environ.get("GEMINI_MODEL", "").strip()
    for round_no in range(1, 5):
        candidates = [_good_model, env_model] + DEFAULT_MODELS + _discovered
        models, seen = [], set()
        for m in candidates:
            if m and m not in seen:
                seen.add(m)
                models.append(m)

        for model in models:
            url = f"{GEMINI_BASE}/models/{model}:generateContent"
            for attempt in range(1, 4):
                try:
                    r = requests.post(url, headers=headers, json=body, timeout=180)
                except Exception as e:
                    log(f"[{label}] retry with {model}: {e!r}")
                    time.sleep(5 * attempt)
                    continue

                if r.status_code == 200:
                    try:
                        parts = r.json()["candidates"][0]["content"]["parts"]
                        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                        data = extract_json(text)
                        problem = validate(data)
                        if problem is None:
                            _good_model = model
                            return data
                    except Exception:
                        time.sleep(3)
                        continue

                if r.status_code in (400, 403, 404):
                    break
                time.sleep(min(5 * (2 ** (attempt - 1)), 30))

        if round_no == 1 or not _discovered:
            found = list_models(key)
            _discovered = [m for m in found if m not in DEFAULT_MODELS]

        time.sleep(15)

    sys.exit(f"Gemini script generation timed out for: '{label}'.")

# ==================== Script Processing ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def check_outline(d) -> str:
    ch = d.get("chapters") if isinstance(d, dict) else None
    if not isinstance(ch, list) or not (3 <= len(ch) <= 10):
        return "need 3 to 10 chapters"
    if not all(isinstance(c, dict) and c.get("title") and c.get("summary") for c in ch):
        return "chapter missing title or summary"
    return None

def make_check_scenes(m: int):
    def check(d) -> str:
        sc = d.get("scenes") if isinstance(d, dict) else None
        if not isinstance(sc, list) or len(sc) < m:
            return f"need {m} scenes"
        for s in sc[:m]:
            if not isinstance(s, dict) or not s.get("narration") or not s.get("image_prompt"):
                return "scene missing narration or prompt"
        return None
    return check

def make_script(topic: str, n_scenes: int) -> dict:
    k = max(4, min(8, round(n_scenes / 3.5)))
    log(f"Planning outline for: {topic}")
    outline = gemini_json(
        f"You are writing a premium YouTube documentary in Hindi.\nTopic: {topic}\n"
        f"Divide into {k} chapters in chronological storytelling order.\n"
        'Return JSON: {"title": "Hindi Title", "description": "Hindi summary", '
        '"chapters": [{"title": "short English title", "summary": "brief summary"}]}',
        check_outline,
        label="outline",
    )
    chapters = outline["chapters"]
    k = len(chapters)
    counts = [n_scenes // k + (1 if i < (n_scenes % k) else 0) for i in range(k)]
    outline_text = "\n".join(f"{i + 1}. {c['title']}: {c['summary']}" for i, c in enumerate(chapters))
    scenes, prev = [], ""

    for i, (ch, m) in enumerate(zip(chapters, counts)):
        log(f"Writing chapter {i + 1}/{k}: {ch['title']} ({m} scenes)")
        prompt = (
            f"Topic: {topic}\nOutline:\n{outline_text}\n"
            + (f"Previous scene:\n{prev}\n" if prev else "")
            + f"Write chapter {i + 1}/{k}: {ch['title']} ({m} scenes).\n"
            "Rules:\n"
            "- narration: spoken Hindi in Devanagari, engaging tone, exactly 25-35 words per scene.\n"
            "- image_prompt: A direct, specific scene description in English matching the exact events (historical objects, people, vintage machinery, vintage architecture, cinematic dramatic lighting). NO text or logos.\n"
            "- callout: An informative 2-4 word milestone badge (Year, Date, Person, Location, or Key Turning Point) to display on-screen. If no milestone, keep it an empty string \"\".\n"
            'Return JSON only: {"scenes": [{"narration": "", "image_prompt": "", "callout": ""}]}'
        )
        data = gemini_json(prompt, make_check_scenes(m), label=f"chapter {i + 1}/{k}")
        for s in data["scenes"][:m]:
            scenes.append({
        
