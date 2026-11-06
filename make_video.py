"""
Professional Bilingual (Hindi & English) Documentary Automation Pipeline
- Auto-discovers active Gemini models (prioritizing Flash-Lite & Flash)
- Seamless English & Hindi support via LANGUAGE environment variable ("hi" or "en")
- High-retention question hooks and curious storytelling intro
- Voice tuning: SwaraNeural with +14% rate and +1Hz pitch to eliminate trailing syllable lag
- High-res Wikimedia Commons archive search with deduplication
- Universal font auto-discovery supporting both Devanagari and Latin fonts for milestone badges
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

# ==================== Language & Voice Setup ====================
LANGUAGE = os.environ.get("LANGUAGE", "hi").strip().lower()  # "hi" or "en"

if LANGUAGE == "en":
    DEFAULT_VOICE = "en-US-ChristopherNeural"
    DEFAULT_FONT = "DejaVuSans-Bold"
    VOICE_RATE = "+10%"
    VOICE_PITCH = "+0Hz"
else:
    DEFAULT_VOICE = "hi-IN-SwaraNeural"
    DEFAULT_FONT = "NotoSansDevanagari-Bold"
    VOICE_RATE = "+14%"
    VOICE_PITCH = "+1Hz"

VOICE = os.environ.get("VOICE", DEFAULT_VOICE)

WORK = Path("work")
OUT = Path("out")

BAD_WORDS = ("image", "tts", "live", "audio", "embed", "robotics", "computer", "vision")

def fatal(msg: str) -> None:
    print("\nERROR: " + msg, flush=True)
    sys.exit(2)

def log(msg: str) -> None:
    print(msg, flush=True)

# ==================== Universal Font Discovery ====================
def get_font_path() -> tuple:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c, Path(c).stem
    for p in Path("/usr/share/fonts").rglob("*.ttf"):
        if "Bold" in p.name or "Sans" in p.name:
            return str(p), p.stem
    return "", DEFAULT_FONT

FONT_FILE, FONT_NAME = get_font_path()
if FONT_FILE:
    log(f"Font loaded: {FONT_FILE}")

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

# ==================== Script Processing with Hooks & Unique Images ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def make_script(topic: str, n_scenes: int) -> dict:
    log(f"Generating full script in {LANGUAGE.upper()} for: {topic} ({n_scenes} scenes)")
    
    if LANGUAGE == "en":
        lang_rules = (
            "- title: Engaging English documentary title.\n"
            "- description: 2-3 sentence English summary.\n"
            "- narration: Dynamic, conversational documentary storytelling (28-34 words per scene). SCENE 1 MUST START WITH A POWERFUL CURIOUS HOOK AND QUESTION. Speak directly to the audience with high suspense and curiosity.\n"
            "- callout: 2-4 word milestone badge starting with a relevant emoji (e.g., '🏰 1459 • Mehrangarh Fort', '⚔️ 1544 • Battle of Sammel'). Leave empty \"\" if no milestone.\n"
        )
    else:
        lang_rules = (
            "- title: Engaging Hindi documentary title.\n"
            "- description: 2-3 sentence Hindi summary.\n"
            "- narration: जीवंत, रोमांचक और सरल किस्सागोई (Storytelling) शैली में हिंदी नरेशन (25-32 शब्द प्रति दृश्य)। "
            "पहला दृश्य (Scene 1) एक जबरदस्त और जिज्ञासा से भरे सवाल से शुरू होना चाहिए ताकि दर्शक वीडियो छोड़कर न जाएँ। "
            "जहाँ हल्का ठहराव चाहिए वहाँ अल्पविराम (,) का प्रयोग करें ताकि आवाज़ में लैग न लगे。\n"
            "- callout: 2-4 word milestone badge starting with an engaging emoji (e.g., '🏰 1459 • मेहरानगढ़ दुर्ग', '⚔️ 1544 • सुमेल युद्ध'). Leave empty \"\" if no milestone.\n"
        )

    prompt = (
        f"You are a master historical storyteller creating a high-retention YouTube documentary.\n"
        f"Topic: {topic}\n"
        f"Language: {'English' if LANGUAGE == 'en' else 'Hindi'}\n"
        f"Create exactly {n_scenes} scenes in chronological order covering the complete story from start to finish.\n"
        "CRITICAL RULE FOR IMAGES: Every single scene MUST have a 100% UNIQUE 'image_prompt'. "
        "DO NOT repeat similar visual prompts across scenes. Progress chronologically: "
        "Early scenes = ancient origins & foundation; Middle scenes = major battles, rulers, and fortifications; "
        "Final scenes (Scene 18-24) = modern history, royal palaces, cenotaphs, Umaid Bhawan Palace, and cultural heritage.\n"
        "Guidelines:\n"
        "- Hook the listener immediately in Scene 1 with dramatic narrative tension and a question.\n"
        f"{lang_rules}"
        "- image_prompt: 3-5 specific English keywords focused on majestic architectural views, historical artifacts, vintage maps, royal durbars, weapons/armor, or landscapes. No text or logos.\n"
        "Return pure JSON:\n"
        '{\n'
        '  "title": "Title",\n'
        '  "description": "Summary",\n'
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

    log(f"Script generated successfully: {len(scenes)} scenes ready.")
    return {
        "topic": topic,
        "title": clean_text(data.get("title", topic)),
        "description": clean_text(data.get("description", "")),
        "scenes": scenes,
    }

# ==================== High-Res Visuals Engine ====================
_used_image_urls = set()

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

def search_wikimedia(query: str) -> list:
    api_url = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": "HistoricalDocBot/2.0"}
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "iiurlwidth": 1280,
        "format": "json"
    }
    found = []
    try:
        r = requests.get(api_url, params=params, headers=headers, timeout=12)
        if r.status_code == 200:
            pages = r.json().get("query", {}).get("pages", {})
            for pid, info in pages.items():
                imginfo = info.get("imageinfo", [{}])[0]
                mime = imginfo.get("mime", "")
                url = imginfo.get("thumburl") or imginfo.get("url")
                if url and ("jpeg" in mime or "png" in mime or "jpg" in mime):
                    found.append(url)
    except Exception:
        pass
    return found

def fetch_image(scene_info: dict, path: Path, seed: int, topic: str) -> bool:
    global _used_image_urls
    headers = {"User-Agent": "Mozilla/5.0"}
    prompt = scene_info.get("image_prompt", "")
    words = [w for w in re.findall(r"\b[A-Za-z]+\b", prompt) if len(w) > 2]
    specific_query = " ".join(words[:4]) if words else "Rajasthan history"
    topic_clean = " ".join([w for w in re.findall(r"\b[A-Za-z]+\b", topic) if len(w) > 2][:3])
    
    query_chain = [
        f"{specific_query} Rajasthan",
        specific_query,
        f"{topic_clean} history painting",
        "Mehrangarh Fort Jodhpur architecture",
        "Mandore gardens cenotaphs",
        "Thar Desert dunes Rajasthan landscape"
    ]

    for q in query_chain:
        urls = search_wikimedia(q)
        for u in urls:
            if u not in _used_image_urls:
                try:
                    r = requests.get(u, headers=headers, timeout=12)
                    if r.status_code == 200 and len(r.content) > 25000:
                        img = Image.open(io.BytesIO(r.content)).convert("RGB")
                        if img.width >= 800 and img.height >= 500:
                            fit_cover(img).save(path, quality=94)
                            _used_image_urls.add(u)
                            return True
                except Exception:
                    continue

    core_kw = " ".join(words[:5]) if words else "historical architecture Rajasthan"
    safe_prompt = quote(f"{core_kw}, cinematic documentary still, national geographic style, 8k, sharp focus")
    ai_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1280&height=720&seed={seed}&nologo=true&model=turbo"
    try:
        r = requests.get(ai_url, headers=headers, timeout=14)
        if r.status_code == 200 and len(r.content) > 12000:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            fit_cover(img).save(path, quality=92)
            return True
    except Exception:
        pass
    return False
def generate_images(scenes: list, topic: str) -> list:
    n = len(scenes)
    paths = []
    for i, sc in enumerate(scenes):
        img = WORK / f"img_{i:03d}.jpg"
        if not (img.exists() and img.stat().st_size > 5000):
            log(f"Fetching Distinct Visual {i + 1}/{n}: {sc['image_prompt'][:45]}...")
            ok = fetch_image(sc, img, seed=8000 + (i * 47), topic=topic)
            if not ok:
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=#141c26:s={W}x{H}:d=1", "-frames:v", "1", str(img)]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
        paths.append(img)
    return paths

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
    for attempt in range(3):
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

# ==================== Dynamic Cinematic FX & Callout Compositing ====================
def motion_and_callout_filter(i: int, frames: int, callout: str, dur: float) -> str:
    f = frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    
    fx_styles = [
        (f"1+0.14*on/{f}", cx, cy),
        (f"1.14-0.14*on/{f}", cx, cy),
        ("1.1", f"(iw-iw/zoom)*on/{f}", cy),
        ("1.1", f"(iw-iw/zoom)*(1-on/{f})", cy),
        (f"1+0.08*on/{f}", f"(ih-ih/zoom)*on/{f}", f"(ih-ih/zoom)*on/{f}"),
    ]
    z, x, y = fx_styles[i % len(fx_styles)]

    vf = (
        f"crop=iw:ih-45:0:0,"
        f"scale=3840:2160:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={f}:s={W}x{H}:fps={FPS},format=yuv420p"
    )

    if callout and len(callout.strip()) > 1:
        clean_callout = (
            callout.replace("\\", "")
            .replace("'", "")
            .replace(":", " - ")
            .replace('"', "")
            .strip()
        )
        badge_duration = min(4.5, dur - 0.5) if dur > 2 else dur
        box_w = max(260, min(850, len(clean_callout) * 17 + 60))
        font_opt = f"fontfile='{FONT_FILE}'" if FONT_FILE else f"font='{DEFAULT_FONT}'"

        vf += (
            f",drawbox=x=80:y=80:w={box_w}:h=60:color=black@0.80:t=fill:enable='between(t,0.5,{badge_duration})'"
            f",drawbox=x=80:y=80:w=6:h=60:color=#E63946:t=fill:enable='between(t,0.5,{badge_duration})'"
            f",drawtext=text='{clean_callout}':fontcolor=white:fontsize=22:x=105:y=98:"
            f"{font_opt}:enable='between(t,0.5,{badge_duration})'"
        )
    return vf

def render_clip(img: Path, aud: Path, clip: Path, i: int, dur: float, callout: str) -> None:
    tmp = clip.with_name(clip.stem + ".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(img), "-i", str(aud),
        "-vf", motion_and_callout_filter(i, int(dur * FPS), callout, dur), "-af", "apad",
        "-t", f"{dur:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(tmp)
    ]
    subprocess.run(cmd, check=True)
    os.replace(tmp, clip)

def build_video(data: dict) -> None:
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    scenes = data["scenes"]
    topic = data.get("topic", "History")
    n = len(scenes)

    log("=== Step 1/3: Generating Distinct Scene Visuals ===")
    imgs = generate_images(scenes, topic=topic)

    log("=== Step 2/3: Recording Narration ===")
    auds, durs = [], []
    for i, sc in enumerate(scenes):
        aud = WORK / f"aud_{i:03d}.mp3"
        if not (aud.exists() and aud.stat().st_size > 1000 and audio_duration(aud) > 0.8):
            log(f"Recording Scene {i + 1}/{n}...")
            speak(sc["narration"], aud)
        auds.append(aud)
        durs.append(audio_duration(aud) + 0.3)

    log("=== Step 3/3: Rendering Motion, Callout Badges & Subtitles ===")
    clips = []
    for i in range(n):
        clip = WORK / f"clip_{i:03d}.mp4"
        callout_text = scenes[i].get("callout", "")
        if not (clip.exists() and clip.stat().st_size > 1000):
            render_clip(imgs[i], auds[i], clip, i, durs[i], callout_text)
        clips.append(clip)

    listfile = WORK / "list.txt"
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))

    raw_concat = WORK / "concat_raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", str(raw_concat)],
        check=True
    )

    srt_path = WORK / "subtitles.srt"
    create_subtitles(scenes, durs, srt_path)

    final = OUT / "final.mp4"
    sub_font = "Noto Sans Devanagari" if LANGUAGE == "hi" else "DejaVu Sans"
    subtitle_filter = (
        f"subtitles='{srt_path.resolve()}':force_style="
        f"'FontName={sub_font},FontSize=22,PrimaryColour=&H0000FFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=55,Alignment=2'"
    )

    log("Burning in clean dynamic subtitles...")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_concat),
         "-vf", subtitle_filter, "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
         "-c:a", "copy", "-movflags", "+faststart", str(final)],
        check=True
    )
    log(f"COMPLETED! Final video saved to {final}")

# ==================== Main Runner ====================
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    topic = os.environ.get("TOPIC", "The history of Marwar").strip()
    n = int(os.environ.get("SCENES", "24") or 24)

    if mode == "full" and os.environ.get("RESUME") != "1":
        for p in [WORK, OUT]:
            if p.exists():
                import shutil
                shutil.rmtree(p)
        WORK.mkdir(exist_ok=True)
        OUT.mkdir(exist_ok=True)

    if mode in ("full", "script"):
        data = make_script(topic, n)
        Path("script.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if mode == "script":
            return
    else:
        p = Path("script.json")
        if not p.exists():
            fatal("script.json not found.")
        data = json.loads(p.read_text(encoding="utf-8"))

    build_video(data)

if __name__ == "__main__":
    main()
    
