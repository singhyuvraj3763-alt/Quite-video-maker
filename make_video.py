"""
Professional Hindi Documentary Automation Pipeline
- 100% Dynamic Model Discovery (Queries Google AI Studio directly; prioritizes Lite/8B/Flash)
- Zero hardcoded model lists to eliminate HTTP 404s completely
- Single-pass structured script generation (eliminates timeouts)
- Paced image generation with multi-endpoint fallback (eliminates IP bans)
- Milestone Callout Badges with Devanagari font support
- Fast, readable 1-2 line synchronized Hindi subtitles
- Energetic SwaraNeural voiceover (+10% rate)
- Bottom logo crop filter to eliminate watermarks
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

# ==================== Dynamic Gemini Engine ====================
_good_model = None

def gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        fatal("GEMINI_API_KEY is missing in repo secrets.")
    return key

def discover_all_active_models(key: str) -> list:
    """Discovers every model currently enabled on your key, prioritizing lite and flash."""
    url = f"{GEMINI_BASE}/models?pageSize=150"
    try:
        r = requests.get(url, headers={"x-goog-api-key": key}, timeout=30)
        if r.status_code == 200:
            usable = []
            for m in r.json().get("models", []):
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and not any(b in name for b in BAD_WORDS):
                    usable.append(name)

            if usable:
                # Rank priority: Lite/8B first (highest quota, fastest), then Flash, then others
                def rank_model(n: str) -> int:
                    nl = n.lower()
                    if "lite" in nl or "8b" in nl:
                        return 0
                    if "flash" in nl:
                        return 1
                    return 2

                usable.sort(key=rank_model)
                log(f"Auto-discovered {len(usable)} active models on your key: {', '.join(usable[:6])}")
                return usable
        else:
            log(f"Failed to query model list (HTTP {r.status_code}): {r.text[:120]}")
    except Exception as e:
        log(f"Model auto-discovery connection failed: {e!r}")
    
    fatal("Unable to retrieve available models from Google AI Studio. Check your GEMINI_API_KEY.")

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

def gemini_json(prompt: str, label: str):
    global _good_model
    key = gemini_key()
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7,
            "maxOutputTokens": 8192,
        },
    }

    env_model = os.environ.get("GEMINI_MODEL", "").strip()
    discovered = discover_all_active_models(key)

    # Front-load user env model or previously verified working model
    candidates = ([env_model] if env_model else []) + ([_good_model] if _good_model else []) + discovered
    models = [m for i, m in enumerate(candidates) if m and m not in candidates[:i]]

    for attempt in range(1, 5):
        for model in models:
            url = f"{GEMINI_BASE}/models/{model}:generateContent"
            try:
                log(f"[{label}] Querying model: {model} (Attempt {attempt})...")
                r = requests.post(url, headers=headers, json=body, timeout=45)

                if r.status_code == 200:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                    data = extract_json(text)
                    if isinstance(data, dict) and data.get("scenes"):
                        _good_model = model
                        return data

                if r.status_code in (429, 503):
                    log(f"[{label}] {model} throttled/busy ({r.status_code}). Switching to next model...")
                    time.sleep(5)
                elif r.status_code == 404:
                    continue
                else:
                    log(f"[{label}] {model} returned HTTP {r.status_code}.")

            except Exception as e:
                log(f"[{label}] {model} error: {e!r}")
                continue

        time.sleep(8 * attempt)

    fatal(f"All discovered models failed or timed out for '{label}'.")

# ==================== Script Processing ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def make_script(topic: str, n_scenes: int) -> dict:
    log(f"Generating documentary script for: '{topic}' ({n_scenes} scenes)")
    prompt = (
        f"You are writing a premium, fast-paced YouTube documentary script in Hindi.\n"
        f"Topic: {topic}\n"
        f"Create exactly {n_scenes} scenes in chronological order covering the complete story.\n"
        "Rules:\n"
        "- title: engaging Hindi documentary title.\n"
        "- description: 2-3 sentence Hindi summary.\n"
        "- narration: natural spoken Hindi in Devanagari script, exactly 25-35 words per scene.\n"
        "- image_prompt: detailed visual description in English of the exact moment/location/artifacts. No text or logos.\n"
        "- callout: 2-4 word milestone badge in English/Hindi for dates, rulers, battles, or places (e.g. '1459 • Mehrangarh Fort', '1544 • Battle of Sammel', 'Rao Maldeo'). Keep empty \"\" if no milestone.\n"
        "Return pure JSON:\n"
        '{\n'
        '  "title": "Hindi Title",\n'
        '  "description": "Hindi summary",\n'
        '  "scenes": [\n'
        '    {"narration": "...", "image_prompt": "...", "callout": "..."}\n'
        '  ]\n'
        '}'
    )
    data = gemini_json(prompt, label="full_script")
    scenes = []
    for s in data.get("scenes", [])[:n_scenes]:
        scenes.append({
            "narration": clean_text(s.get("narration", "")),
            "image_prompt": clean_text(s.get("image_prompt", "")),
            "callout": clean_text(s.get("callout", ""))
        })

    log(f"Script ready: {len(scenes)} scenes assembled.")
    return {
        "topic": topic,
        "title": clean_text(data.get("title", topic)),
        "description": clean_text(data.get("description", "")),
        "scenes": scenes,
    }

# ==================== Visuals Generation ====================
def fit_cover(img: Image.Image) -> Image.Image:
    w, h = img.size
    target = W / H
    if w / h > target:
        nw = int(h * target)
        left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    else:
        nh = int(w / target)
        top = (h - nh) // 2
        img = img.crop((0, top, w, top + nh))
    return img.resize((W, H), Image.LANCZOS)

def fetch_image(prompt: str, path: Path, seed: int) -> bool:
    key = os.environ.get("POLLINATIONS_API_KEY", "").strip()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    words = [w for w in re.findall(r"\b\w+\b", prompt) if len(w) > 2][:10]
    subject = " ".join(words)
    clean_p = quote(f"{subject}, historical documentary, cinematic lighting, realistic, 16:9", safe="")

    urls = [
        f"https://image.pollinations.ai/prompt/{clean_p}?width=1280&height=720&seed={seed}&nologo=true",
        f"https://image.pollinations.ai/prompt/{quote(subject)}?width=1280&height=720&seed={seed+5}&nologo=true",
        f"https://image.pollinations.ai/prompt/{clean_p}?width=1024&height=576&seed={seed+10}&nologo=true&model=turbo"
    ]

    for url in urls:
        for attempt in range(2):
            try:
                r = requests.get(url, headers=headers, timeout=18)
                if r.status_code == 200 and len(r.content) > 5000:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    fit_cover(img).save(path, quality=90)
                    return True
            except Exception:
                time.sleep(2)
                continue

    return False

def generate_images(scenes: list) -> list:
    n = len(scenes)
    paths = []
    for i, sc in enumerate(scenes):
        img = WORK / f"img_{i:03d}.jpg"
        if not (img.exists() and img.stat().st_size > 5000):
            log(f"Generating Image {i + 1}/{n}: {sc['image_prompt'][:45]}...")
            ok = fetch_image(sc["image_prompt"], img, seed=5000 + (i * 37))
            if not ok:
                log(f"::warning:: Image {i + 1} timed out from provider. Generating solid dark slate.")
                cmd = [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"color=c=#101820:s={W}x{H}:d=1",
                    "-frames:v", "1", str(img)
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Pacing delay to avoid runner IP rate limits
            time.sleep(3.0)

        paths.append(img)
    return paths

# ==================== Voiceover Engine ====================
async def _edge(text: str, path: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=VOICE_RATE).save(str(path))

def audio_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0

def speak(text: str, path: Path) -> None:
    text = clean_text(text)
    for attempt in range(3):
        try:
            asyncio.run(_edge(text, path))
            if path.exists() and path.stat().st_size > 1000 and audio_duration(path) > 0.8:
                return
        except Exception as e:
            log(f"Edge-TTS attempt {attempt + 1} failed: {e!r}")
            time.sleep(2)
    from gtts import gTTS
    gTTS(text, lang="hi").save(str(path))

# ==================== Dynamic Subtitles ====================
def format_srt_time(sec: float) -> str:
    millis = int((sec % 1) * 1000)
    s = int(sec)
    m, s = divmod(s, 60)
    h, m = divmod(m,
    
