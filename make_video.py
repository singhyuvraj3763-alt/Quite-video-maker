"""
Professional Cinematic Documentary Automation Pipeline (Wikipedia Infobox & Entity Matcher)
- Direct Wikipedia Article Image Extraction: Pulls official portraits and photos for modern figures and topics.
- Multi-Beat Dynamic Beats: Changes visual context every 2-3 seconds to match narration beats.
- Language-Aware Font Engine: Isolates Latin vs Devanagari fonts to prevent '▯▯▯' box bugs.
- Zero-Dark-Screen Guarantee: Advanced multi-tone gradient fallbacks.
"""

import asyncio
import io
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
import requests
from PIL import Image, ImageEnhance, ImageOps

FPS = 25
W, H = 1920, 1080
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# ==================== Language & Voice Setup ====================
LANGUAGE = os.environ.get("LANGUAGE", "hi").strip().lower()  # "hi" or "en"

if LANGUAGE == "en":
    DEFAULT_VOICE = "en-US-ChristopherNeural"
    DEFAULT_FONT = "DejaVuSans-Bold"
    VOICE_RATE = "+8%"
    VOICE_PITCH = "-2Hz"
else:
    DEFAULT_VOICE = "hi-IN-SwaraNeural"
    DEFAULT_FONT = "NotoSansDevanagari-Bold"
    VOICE_RATE = "+10%"
    VOICE_PITCH = "-1Hz"

VOICE = os.environ.get("VOICE", DEFAULT_VOICE)

WORK = Path("work")
OUT = Path("out")

BAD_WORDS = ("image", "tts", "live", "audio", "embed", "robotics", "computer", "vision")
_used_urls = set()

def fatal(msg: str) -> None:
    print("\nERROR: " + msg, flush=True)
    sys.exit(2)

def log(msg: str) -> None:
    print(msg, flush=True)

# ==================== Language-Aware Font Discovery ====================
def get_font_path(lang: str) -> tuple:
    if lang == "en":
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c, Path(c).stem
        for p in Path("/usr/share/fonts").rglob("*.ttf"):
            if "dejavu" in p.name.lower() and "bold" in p.name.lower():
                return str(p), p.stem
    else:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c, Path(c).stem
    return "", DEFAULT_FONT

FONT_FILE, FONT_NAME = get_font_path(LANGUAGE)
log(f"Selected Font for {LANGUAGE.upper()}: {FONT_FILE or FONT_NAME}")

# ==================== Dynamic Gemini Script Engine ====================
_good_model = None

def gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        fatal("GEMINI_API_KEY is missing in repo secrets.")
    return key

def get_live_models(key: str) -> list:
    url = f"{GEMINI_BASE}/models?pageSize=100"
    try:
        r = requests.get(url, headers={"x-goog-api-key": key}, timeout=25)
        if r.status_code == 200:
            usable = []
            for m in r.json().get("models", []):
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and not any(b in name for b in BAD_WORDS):
                    usable.append(name)
            if usable:
                def sort_priority(n: str) -> int:
                    nl = n.lower()
                    if "lite" in nl or "8b" in nl:
                        return 0
                    if "flash" in nl:
                        return 1
                    return 2
                usable.sort(key=sort_priority)
                return usable
    except Exception:
        pass
    return ["gemini-2.0-flash-lite", "gemini-1.5-flash"]

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
    live_models = get_live_models(key)
    candidates = ([env_model] if env_model else []) + ([_good_model] if _good_model else []) + live_models
    models = [m for i, m in enumerate(candidates) if m and m not in candidates[:i]]

    for attempt in range(1, 5):
        for model in models:
            url = f"{GEMINI_BASE}/models/{model}:generateContent"
            try:
                r = requests.post(url, headers=headers, json=body, timeout=50)
                if r.status_code == 200:
                    resp_json = r.json()
                    candidates_list = resp_json.get("candidates", [])
                    if candidates_list:
                        parts = candidates_list[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                        if text:
                            data = extract_json(text)
                            if isinstance(data, dict) and data.get("scenes"):
                                _good_model = model
                                return data
                if r.status_code in (429, 503):
                    time.sleep(4)
                elif r.status_code == 404:
                    continue
            except Exception:
                continue
        time.sleep(8 * attempt)

    sys.exit(f"Failed to generate script across all discovered models for '{label}'.")

# ==================== Script Processing with Precision Entity Keywords ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def make_script(topic: str, n_scenes: int) -> dict:
    log(f"Generating precision-matched documentary script for: '{topic}' ({n_scenes} scenes)")
    
    if LANGUAGE == "en":
        lang_rules = (
            "- title: Engaging English documentary title.\n"
            "- description: 2-3 sentence English summary.\n"
            "- narration: Deep documentary storytelling (26-32 words per scene). SCENE 1 MUST START WITH A QUESTION.\n"
            "- callout: Clean 2-4 word milestone badge using plain English letters and numbers, NO EMOJIS.\n"
            "- beats: Exactly 3 specific, highly recognizable proper nouns, Wikipedia page subjects, landmarks, or corporate entities (e.g., 'Ratan Tata', 'Taj Mahal Palace Mumbai', 'Tata Nano', 'Bombay House') matching the micro-events of this scene.\n"
        )
    else:
        lang_rules = (
            "- title: Engaging Hindi documentary title.\n"
            "- description: 2-3 sentence Hindi summary.\n"
            "- narration: पेशेवर डॉक्यूमेंट्री शैली में गंभीर किस्सागोई (25-32 शब्द प्रति दृश्य)। पहला दृश्य सवाल से शुरू हो।\n"
            "- callout: बिना किसी इमोजी के साफ-सुथरा माइलस्टोन बैज।\n"
            "- beats: इस दृश्य के सटीक प्रसंग से जुड़े ठीक 3 विशिष्ट अंग्रेजी कीवर्ड या विकिपीडिया शीर्षक (जैसे: 'Ratan Tata', 'Taj Hotel Mumbai', 'Tata Nano', 'Bombay House')।\n"
        )

    prompt = (
        f"You are a master historical documentary filmmaker creating a high-retention video like IRON AND INK.\n"
        f"Exact Topic / Subject: {topic}\n"
        f"Language: {'English' / if LANGUAGE == 'en' else 'Hindi'}\n"
        f"Create exactly {n_scenes} chronological scenes covering this subject.\n"
        f"{lang_rules}\n"
        "Return pure JSON:\n"
        '{\n'
        '  "title": "Title",\n'
        '  "description": "Summary",\n'
        '  "scenes": [\n'
        '    {\n'
        '      "narration": "...",\n'
        '      "callout": "...",\n'
        '      "beats": ["specific entity 1", "specific entity 2", "specific entity 3"]\n'
        '    }\n'
        '  ]\n'
        '}'
    )
    data = gemini_json(prompt, label="full_script")
    scenes = []
    for s in data.get("scenes", [])[:n_scenes]:
        beats = s.get("beats", [])
        if not isinstance(beats, list) or len(beats) < 3:
            base_p = topic
            beats = [base_p, base_p, base_p]
        scenes.append({
            "narration": clean_text(s.get("narration", "")),
            "callout": clean_text(s.get("callout", "")),
            "beats": [clean_text(b) for b in beats[:3]]
        })

    log(f"Script generated successfully: {len(scenes)} precision scenes ready.")
    return {
        "topic": topic,
        "title": clean_text(data.get("title", topic)),
        "description": clean_text(data.get("description", "")),
        "scenes": scenes,
    }
    # ==================== Wikipedia Infobox & Entity-Matched Visual Engine ====================
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

def create_cinematic_gradient(path: Path, seed: int) -> None:
    random.seed(seed)
    base = Image.new("RGB", (W, H), (20, 18, 25))
    px = base.load()
    c1 = (random.randint(15, 35), random.randint(15, 30), random.randint(35, 60))
    c2 = (random.randint(40, 70), random.randint(25, 45), random.randint(20, 35))
    for y in range(H):
        factor = y / H
        r = int(c1[0] * (1 - factor) + c2[0] * factor)
        g = int(c1[1] * (1 - factor) + c2[1] * factor)
        b = int(c1[2] * (1 - factor) + c2[2] * factor)
        for x in range(W):
            noise = random.randint(-6, 6)
            px[x, y] = (max(0, min(255, r + noise)), max(0, min(255, g + noise)), max(0, min(255, b + noise)))
    base.save(path, quality=92)

def fetch_wiki_infobox_image(title: str) -> str:
    """Queries official Wikipedia REST API to pull the main article infobox photograph."""
    try:
        clean_title = title.replace(" ", "_")
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(clean_title)}"
        headers = {"User-Agent": "HistoricalDocumentaryPipeline/5.0 (Contact: github@actions.com)"}
        r = requests.get(url, headers=headers, timeout=6)
        if r.status_code == 200:
            data = r.json()
            if "originalimage" in data and data["originalimage"].get("source"):
                return data["originalimage"]["source"]
            elif "thumbnail" in data and data["thumbnail"].get("source"):
                return data["thumbnail"]["source"]
    except Exception:
        pass
    return ""

def fetch_image_smart(query_text: str, path: Path, seed: int, topic: str, prev_images: list) -> bool:
    wiki_headers = {
        "User-Agent": "HistoricalDocumentaryPipeline/5.0 (Contact: github@actions.com) PythonRequests/2.31"
    }
    
    # STEP 1: Try fetching the official Wikipedia article summary image for the specific beat or main topic
    for target in [query_text, topic]:
        if target:
            wiki_img_url = fetch_wiki_infobox_image(target)
            if wiki_img_url and wiki_img_url not in _used_urls:
                try:
                    img_resp = requests.get(wiki_img_url, headers=wiki_headers, timeout=8)
                    if img_resp.status_code == 200 and len(img_resp.content) > 10000:
                        img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
                        if img.width >= 300 and img.height >= 200:
                            _used_urls.add(wiki_img_url)
                            fit_cover(img).save(path, quality=94)
                            return True
                except Exception:
                    pass

    # STEP 2: Try Wikimedia Commons search with precise entity terms
    clean_q = re.sub(r"[^a-zA-Z0-9\s]", "", query_text).strip()
    search_queries = [
        clean_q,
        f"{clean_q} photography",
        f"{topic} corporate building",
        f"{topic} historical event",
        "Mumbai corporate architecture landmark"
    ]

    for sq in search_queries:
        if not sq:
            continue
        try:
            api_url = "https://commons.wikimedia.org/w/api.php"
            params = {
                "action": "query", "generator": "search", "gsrsearch": sq,
                "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo",
                "iiprop": "url|mime", "iiurlwidth": 1280, "format": "json"
            }
            r = requests.get(api_url, params=params, headers=wiki_headers, timeout=8)
            if r.status_code == 200:
                pages = r.json().get("query", {}).get("pages", {})
                items = list(pages.values())
                random.shuffle(items)
                for info in items:
                    imginfo = info.get("imageinfo", [{}])[0]
                    mime = imginfo.get("mime", "")
                    url = imginfo.get("thumburl") or imginfo.get("url")
                    if url and url not in _used_urls and ("jpeg" in mime or "png" in mime or "jpg" in mime):
                        img_resp = requests.get(url, headers=wiki_headers, timeout=8)
                        if img_resp.status_code == 200 and len(img_resp.content) > 15000:
                            img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
                            if img.width >= 450 and img.height >= 300:
                                _used_urls.add(url)
                                fit_cover(img).save(path, quality=94)
                                return True
        except Exception:
            continue

    if prev_images:
        try:
            src_img = Image.open(random.choice(prev_images)).convert("RGB")
            src_img = ImageOps.mirror(src_img)
            w, h = src_img.size
            cropped = src_img.crop((int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9))).resize((W, H), Image.LANCZOS)
            cropped.save(path, quality=92)
            return True
        except Exception:
            pass

    create_cinematic_gradient(path, seed)
    return False

# ==================== Voiceover Engine ====================
async def _edge(text: str, path: Path) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH)
    await communicate.save(str(path))

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
    for _ in range(3):
        try:
            asyncio.run(_edge(text, path))
            if path.exists() and path.stat().st_size > 1000 and audio_duration(path) > 0.8:
                return
        except Exception:
            time.sleep(2)
    from gtts import gTTS
    gTTS(text, lang=LANGUAGE).save(str(path))

# ==================== Dynamic Subtitles ====================
def format_srt_time(sec: float) -> str:
    millis = int((sec % 1) * 1000)
    s = int(sec)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"

def create_subtitles(scenes: list, durs: list, srt_path: Path):
    cur_time = 0.0
    lines = []
    sub_idx = 1
    for sc, dur in zip(scenes, durs):
        scene_start = cur_time
        scene_end = cur_time + dur
        cur_time = scene_end
        words = sc["narration"].split()
        chunk_size = 7
        chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]
        if not chunks:
            continue
        chunk_dur = dur / len(chunks)
        for i, chk in enumerate(chunks):
            c_start = scene_start + (i * chunk_dur)
            c_end = c_start + chunk_dur
            text = " ".join(chk)
            lines.append(f"{sub_idx}\n{format_srt_time(c_start)} --> {format_srt_time(c_end)}\n{text}\n")
            sub_idx += 1
    srt_path.write_text("\n".join(lines), encoding="utf-8")

# ==================== Multi-Beat Sub-Clip Rendering ====================
def render_multi_beat_clip(scene_data: dict, aud_path: Path, clip_path: Path, scene_idx: int, total_dur: float, topic: str, valid_prev: list) -> None:
    beats = scene_data.get("beats", [topic, topic, topic])
    n_beats = len(beats)
    beat_dur = total_dur / n_beats
    
    beat_clips = []
    for b_idx, beat_prompt in enumerate(beats):
        b_img = WORK / f"scene_{scene_idx:03d}_beat_{b_idx}.jpg"
        if not (b_img.exists() and b_img.stat().st_size > 10000):
            log(f"  Fetching Scene {scene_idx + 1} Beat {b_idx + 1}/3 for entity: '{beat_prompt}'")
            fetch_image_smart(beat_prompt, b_img, seed
                                   
