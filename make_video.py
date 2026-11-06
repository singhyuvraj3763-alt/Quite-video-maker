"""
Hindi Documentary Automation Pipeline
- Energetic narration pacing (+12% rate)
- Dynamic timed Hindi subtitles (Captions)
- Distinct visual per scene with Ken Burns motion
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
DEFAULT_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.0-flash",
    "gemini-3.7-flash"
]

VOICE = os.environ.get("VOICE", "hi-IN-SwaraNeural")
VOICE_RATE = "+12%"  # Energetic, engaging pace

STYLE = (
    "cinematic documentary photography, archival realism, muted warm tones, "
    "dramatic lighting, 8k, photorealistic, 16:9, clean composition, no text"
)

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
        fatal("GEMINI_API_KEY is missing in repo secrets.")
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
                    log(f"[{label}] network retry with {model}: {e!r}")
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

        time.sleep(20)

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
        f"You are creating a fast-paced, engaging Hindi YouTube documentary.\nTopic: {topic}\n"
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
            + (f"Previous scene text:\n{prev}\n" if prev else "")
            + f"Write chapter {i + 1}/{k}: {ch['title']} ({m} scenes).\n"
            "Rules:\n"
            "- narration: spoken Hindi in Devanagari, engaging documentary tone, 35-45 words per scene.\n"
            "- image_prompt: specific, highly distinct visual description in English describing the exact moment.\n"
            'Return JSON only: {"scenes": [{"narration": "", "image_prompt": ""}]}'
        )
        data = gemini_json(prompt, make_check_scenes(m), label=f"chapter {i + 1}/{k}")
        for s in data["scenes"][:m]:
            scenes.append({
                "chapter": ch["title"],
                "narration": clean_text(s["narration"]),
                "image_prompt": clean_text(s["image_prompt"]),
            })
        prev = scenes[-1]["narration"]
        time.sleep(1)

    return {
        "topic": topic,
        "title": clean_text(outline["title"]),
        "description": clean_text(outline.get("description", "")),
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
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    
    # Clean and simplify prompt
    clean_p = quote(f"{prompt[:200]}, {STYLE}", safe="")
    
    # 1. Try Pollinations Flux with seed
    p_url = f"https://image.pollinations.ai/prompt/{clean_p}"
    params = {"width": 1280, "height": 720, "seed": seed, "nologo": "true", "model": "flux"}
    if key:
        params["key"] = key

    try:
        r = requests.get(p_url, params=params, headers=headers, timeout=35)
        if r.status_code == 200 and len(r.content) > 8000:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            fit_cover(img).save(path, quality=92)
            return True
    except Exception:
        pass

    # 2. Unsplash / Picsum Direct Fallback (Guarantees every scene has a unique photo)
    fallback_urls = [
        f"https://picsum.photos/1920/1080?random={seed}",
        f"https://loremflickr.com/1920/1080/documentary,history?lock={seed}"
    ]
    for fb in fallback_urls:
        try:
            r = requests.get(fb, timeout=15)
            if r.status_code == 200 and len(r.content) > 8000:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                fit_cover(img).save(path, quality=92)
                return True
        except Exception:
            continue

    return False

def generate_images(scenes: list) -> list:
    n = len(scenes)
    paths = []
    for i, sc in enumerate(scenes):
        img = WORK / f"img_{i:03d}.jpg"
        if not (img.exists() and img.stat().st_size > 5000):
            log(f"Generating Image {i + 1}/{n}...")
            ok = fetch_image(sc["image_prompt"], img, seed=2000 + (i * 13))
            if not ok:
                # Emergency slate
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=#1b263b:s={W}x{H}:d=1", "-frames:v", "1", str(img)]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        paths.append(img)
        time.sleep(0.5)
    return paths

# ==================== Voiceover Engine ====================
async def _edge(text: str, path: Path) -> None:
    import edge_tts
    # SwaraNeural is dynamic and punchy, paced with VOICE_RATE
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
    # gTTS fallback
    from gtts import gTTS
    gTTS(text, lang="hi").save(str(path))

# ==================== Subtitles & Captions ====================
def format_srt_time(sec: float) -> str:
    millis = int((sec % 1) * 1000)
    s = int(sec)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"

def create_subtitles(scenes: list, durs: list, srt_path: Path):
    cur_time = 0.0
    lines = []
    for idx, (sc, dur) in enumerate(zip(scenes, durs), start=1):
        start = cur_time
        end = cur_time + dur
        cur_time = end

        # Split Hindi text into readable two-line chunks if long
        words = sc["narration"].split()
        if len(words) > 10:
            mid = len(words) // 2
            text = " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
        else:
            text = sc["narration"]

        lines.append(f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n")

    srt_path.write_text("\n".join(lines), encoding="utf-8")

# ==================== Compositing & Motion ====================
def motion(i: int, frames: int) -> str:
    f = frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    styles = [
        (f"1+0.12*on/{f}", cx, cy),
        (f"1.12-0.12*on/{f}", cx, cy),
        ("1.08", f"(iw-iw/zoom)*on/{f}", cy),
        ("1.08", f"(iw-iw/zoom)*(1-on/{f})", cy),
    ]
    z, x, y = styles[i % 4]
    
    # 1) Crop bottom 45px to eliminate any watermark logo
    # 2) Scale to high res and apply zoompan motion
    return (
        f"crop=iw:ih-45:0:0,"
        f"scale=3840:2160:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={f}:s={W}x{H}:fps={FPS},format=yuv420p"
    )

def render_clip(img: Path, aud: Path, clip: Path, i: int, dur: float) -> None:
    tmp = clip.with_name(clip.stem + ".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(img), "-i", str(aud),
        "-vf", motion(i, int(dur * FPS)), "-af", "apad",
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
    n = len(scenes)

    log("=== Step 1/3: Generating Distinct Visuals ===")
    imgs = generate_images(scenes)

    log("=== Step 2/3: Generating Voiceovers ===")
    auds, durs = [], []
    for i, sc in enumerate(scenes):
        aud = WORK / f"aud_{i:03d}.mp3"
        if not (aud.exists() and aud.stat().st_size > 1000 and audio_duration(aud) > 0.8):
            log(f"Recording Voice {i + 1}/{n}...")
            speak(sc["narration"], aud)
        auds.append(aud)
        durs.append(audio_duration(aud) + 0.3)

    log("=== Step 3/3: Rendering Clips with Motion & Subtitles ===")
    clips, t = [], 0.0
    for i in range(n):
        clip = WORK / f"clip_{i:03d}.mp4"
        if not (clip.exists() and clip.stat().st_size > 1000):
            log(f"Rendering Clip {i + 1}/{n}...")
            render_clip(imgs[i], auds[i], clip, i, durs[i])
        t += durs[i]
        clips.append(clip)

    listfile = WORK / "list.txt"
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))

    raw_concat = WORK / "concat_raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", str(raw_concat)],
        check=True
    )

    # Subtitles track
    srt_path = WORK / "subtitles.srt"
    create_subtitles(scenes, durs, srt_path)

    # Final Burn-in: Add centered documentary-style subtitles
    final = OUT / "final.mp4"
    subtitle_filter = (
        f"subtitles='{srt_path.resolve()}':force_style="
        "'FontName=Noto Sans Devanagari,FontSize=20,PrimaryColour=&H00FFFFFF,"
        "BackColour=&H80000000,BorderStyle=4,Outline=0,Shadow=0,MarginV=45,Alignment=2'"
    )

    log("Burning in Hindi subtitles...")
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
    topic = os.environ.get("TOPIC", "The Mahindra story in Hindi").strip()
    n = int(os.environ.get("SCENES", "24") or 24)

    # Clean old cache when starting a full run
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
            log("Script saved.")
            return
    else:
        p = Path("script.json")
        if not p.exists():
            fatal("script.json not found.")
        data = json.loads(p.read_text(encoding="utf-8"))

    build_video(data)

if __name__ == "__main__":
    main()
          
