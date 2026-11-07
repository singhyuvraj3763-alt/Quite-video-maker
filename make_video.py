"""
Professional Cinematic Documentary Automation Pipeline (IRON & INK Style - Hugging Face Edition)
- Hugging Face FLUX.1-schnell Image Generator API integration with strict safety timers
- Language-Specific Font Engine: Isolates Latin vs Devanagari fonts to prevent '▯▯▯' box bugs
- Zero-Dark-Screen Guarantee: Smart visual remix and textured vintage canvas fallbacks
- Harmonic Documentary Ambient Scoring and Edge-TTS Deep Narration Pacing
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
_used_prompts = set()

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

# ==================== Script Processing ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def make_script(topic: str, n_scenes: int) -> dict:
    log(f"Generating professional documentary script for: '{topic}' ({n_scenes} scenes)")
    
    if LANGUAGE == "en":
        lang_rules = (
            "- title: Engaging English documentary title.\n"
            "- description: 2-3 sentence English summary.\n"
            "- narration: Deep documentary storytelling (26-32 words per scene). SCENE 1 MUST START WITH A QUESTION.\n"
            "- callout: Clean 2-4 word milestone badge using plain English letters and numbers, NO EMOJIS (e.g., '753 BC - Founding of Rome').\n"
        )
    else:
        lang_rules = (
            "- title: Engaging Hindi documentary title.\n"
            "- description: 2-3 sentence Hindi summary.\n"
            "- narration: पेशेवर डॉक्यूमेंट्री शैली में गंभीर किस्सागोई (25-32 शब्द प्रति दृश्य)। पहला दृश्य सवाल से शुरू हो।\n"
            "- callout: बिना किसी इमोजी के साफ-सुथरा माइलस्टोन बैज (जैसे: '1459 - मेहरानगढ़ दुर्ग').\n"
        )

    prompt = (
        f"You are a master historical documentary filmmaker creating a high-retention video like IRON AND INK.\n"
        f"Exact Topic / Subject: {topic}\n"
        f"Language: {'English' if LANGUAGE == 'en' else 'Hindi'}\n"
        f"Create exactly {n_scenes} chronological scenes covering this subject.\n"
        f"{lang_rules}"
        "- image_prompt: 6-10 descriptive English words for a classical oil painting or historical scene (e.g., 'Roman legions marching across snowy Alps oil painting').\n"
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
    # ==================== Hugging Face FLUX Visuals Engine with Rate-Limit Timers ====================
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

def create_vintage_canvas(path: Path, seed: int) -> None:
    random.seed(seed)
    base = Image.new("RGB", (W, H), (44, 34, 24))
    px = base.load()
    for y in range(0, H, 4):
        for x in range(0, W, 4):
            n = random.randint(-12, 12)
            r = max(20, min(80, 52 + n))
            g = max(15, min(65, 40 + n))
            b = max(10, min(50, 28 + n))
            for dy in range(4):
                for dx in range(4):
                    if x + dx < W and y + dy < H:
                        px[x + dx, y + dy] = (r, g, b)
    base.save(path, quality=92)

def fetch_image_hf(scene_info: dict, path: Path, seed: int, topic: str, prev_images: list) -> bool:
    hf_key = os.environ.get("HF_TOKEN", "").strip()
    prompt = scene_info.get("image_prompt", "") or topic
    full_prompt = f"{prompt}, classical historical oil painting, museum masterpiece, cinematic museum lighting, 8k"

    if hf_key:
        api_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
        headers = {"Authorization": f"Bearer {hf_key}"}
        payload = {
            "inputs": full_prompt,
            "options": {"wait_for_model": True, "seed": seed}
        }
        
        for attempt in range(3):
            try:
                time.sleep(4.0)
                r = requests.post(api_url, headers=headers, json=payload, timeout=45)
                if r.status_code == 200 and len(r.content) > 15000:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    fit_cover(img).save(path, quality=94)
                    log("  [OK] Hugging Face FLUX visual generated successfully.")
                    return True
                elif r.status_code == 503:
                    log(f"  [Info] Model loading on HF, retrying in {5 * (attempt + 1)}s...")
                    time.sleep(5 * (attempt + 1))
            except Exception:
                time.sleep(3)

    if prev_images:
        try:
            src_img = Image.open(random.choice(prev_images)).convert("RGB")
            src_img = ImageOps.mirror(src_img)
            w, h = src_img.size
            cropped = src_img.crop((int(w * 0.08), int(h * 0.08), int(w * 0.92), int(h * 0.92))).resize((W, H), Image.LANCZOS)
            cropped = ImageEnhance.Color(cropped).enhance(0.85)
            cropped.save(path, quality=92)
            log("  [OK] Remixed existing scene visual (zero dark screen fallback).")
            return True
        except Exception:
            pass

    create_vintage_canvas(path, seed)
    return False

def generate_images(scenes: list, topic: str) -> list:
    n = len(scenes)
    paths = []
    valid_prev = []
    for i, sc in enumerate(scenes):
        img = WORK / f"img_{i:03d}.jpg"
        if not (img.exists() and img.stat().st_size > 10000):
            log(f"Generating Visual {i + 1}/{n}: '{sc['image_prompt'][:35]}...'")
            fetch_image_hf(sc, img, seed=8000 + (i * 47), topic=topic, prev_images=valid_prev)
        if img.exists() and img.stat().st_size > 10000:
            valid_prev.append(img)
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

# ==================== Cinematic Motion & Glyph-Safe Callout Badges ====================
def motion_and_callout_filter(i: int, frames: int, callout: str, dur: float) -> str:
    f = frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    
    fx_styles = [
        (f"1+0.12*on/{f}", cx, cy),
        (f"1.12-0.12*on/{f}", cx, cy),
        ("1.08", f"(iw-iw/zoom)*on/{f}", cy),
        ("1.08", f"(iw-iw/zoom)*(1-on/{f})", cy),
    ]
    z, x, y = fx_styles[i % len(fx_styles)]

    vf = (
        f"crop=iw:ih-45:0:0,"
        f"scale=3840:2160:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={f}:s={W}x{H}:fps={FPS},format=yuv420p"
    )

    if callout and len(callout.strip()) > 1:
        normalized = callout.replace("•", "-").replace(":", " - ").replace("'", "").replace('"', "")
        if LANGUAGE == "en":
            clean_callout = re.sub(r"[^a-zA-Z0-9\s\-\,\.\(\)]", "", normalized).strip()
        else:
            clean_callout = re.sub(r"[^\w\s\d\-\,\.\(\)\u0900-\u097F]", "", normalized).strip()
        clean_callout = re.sub(r"\s+", " ", clean_callout)

        if len(clean_callout) > 1:
            badge_duration = min(3.5, dur * 0.65) if dur > 1.5 else dur
            box_w = max(240, min(880, len(clean_callout) * 14 + 55))
            font_opt = f"fontfile='{FONT_FILE}'" if FONT_FILE else f"font='{DEFAULT_FONT}'"

            vf += (
                f",drawbox=x=80:y=80:w={box_w}:h=60:color=black@0.85:t=fill:enable='between(t,0.3,{badge_duration})'"
                f",drawbox=x=80:y=80:w=6:h=60:color=#E63946:t=fill:enable='between(t,0.3,{badge_duration})'"
                f",drawtext=text='{clean_callout}':fontcolor=white:fontsize=23:x=105:y=98:"
                f"{font_opt}:enable='between(t,0.3,{badge_duration})'"
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

    log("=== Step 1/3: Generating Hugging Face FLUX Visuals ===")
    imgs = generate_images(scenes, topic=topic)

    log("=== Step 2/3: Recording Documentary Narration ===")
    auds, durs = [], []
    for i, sc in enumerate(scenes):
        aud = WORK / f"aud_{i:03d}.mp3"
        if not (aud.exists() and aud.stat().st_size > 1000 and audio_duration(aud) > 0.8):
            log(f"Recording Scene {i + 1}/{n}...")
            speak(sc["narration"], aud)
        auds.append(aud)
        durs.append(audio_duration(aud) + 0.3)

    log("=== Step 3/3: Rendering Motion, Clean Badges & Subtitles ===")
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

    log("Synthesizing harmonic documentary background score...")
    bg_music = WORK / "bg_music.mp3"
    total_dur = sum(durs)
    expr = (
        "(sin(110*2*PI*t)*0.35 + sin(164.81*2*PI*t)*0.25 + "
        "sin(220*2*PI*t)*0.20 + sin(261.63*2*PI*t)*0.15) * (0.75 + 0.25*sin(0.25*2*PI*t))"
    )
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc='{expr}':s=44100:d={total_dur}",
        "-af", "lowpass=f=900,volume=0.14",
        str(bg_music)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    video_with_audio = WORK / "video_with_audio.mp4"
    if bg_music.exists() and bg_music.stat().st_size > 1000:
        log("Mixing background score with narration...")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(raw_concat), "-i", str(bg_music),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.14[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", str(video_with_audio)
        ], check=True)
    else:
        video_with_audio = raw_concat

    final = OUT / "final.mp4"
    sub_font = "Noto Sans Devanagari" if LANGUAGE == "hi" else "DejaVu Sans"
    subtitle_filter = (
        f"subtitles='{srt_path.resolve()}':force_style="
        f"'FontName={sub_font},FontSize=22,PrimaryColour=&H0000FFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=55,Alignment=2'"
    )

    log("Burning in clean documentary subtitles...")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_with_audio),
         "-vf", subtitle_filter, "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
         "-c:a", "copy", "-movflags", "+faststart", str(final)],
        check=True
    )
    log(f"COMPLETED! Final video saved to {final}")

# ==================== Main Runner ====================
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    topic = os.environ.get("TOPIC", "The Rise and Fall of the Roman Empire").strip()
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
    
