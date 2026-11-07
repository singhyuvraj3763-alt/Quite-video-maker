"""
Professional Cinematic Documentary Automation Pipeline (Synchronized Multi-Beat Visual Edition)
- Perfectly Synced Multi-Beat Visuals: Changes images every 2-3 seconds to match narration beats.
- Strict Keyword-to-Image Mapping: Eliminates mismatched imagery by tying visuals directly to spoken words.
- Language-Aware Font Engine: Isolates Latin vs Devanagari fonts to prevent '▯▯▯' box bugs.
- Harmonic Documentary Ambient Scoring and Edge-TTS Deep Narration Pacing.
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

# ==================== Script Processing with Multi-Beat Sub-Prompts ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def make_script(topic: str, n_scenes: int) -> dict:
    log(f"Generating synchronized documentary script for: '{topic}' ({n_scenes} scenes)")
    
    if LANGUAGE == "en":
        lang_rules = (
            "- title: Engaging English documentary title.\n"
            "- description: 2-3 sentence English summary.\n"
            "- narration: Deep documentary storytelling (26-32 words per scene). SCENE 1 MUST START WITH A QUESTION.\n"
            "- callout: Clean 2-4 word milestone badge using plain English letters and numbers, NO EMOJIS.\n"
            "- beats: Exactly 3 distinct, highly descriptive English image prompts (6-9 words each) that match the exact micro-events spoken during the beginning, middle, and end of this scene's narration.\n"
        )
    else:
        lang_rules = (
            "- title: Engaging Hindi documentary title.\n"
            "- description: 2-3 sentence Hindi summary.\n"
            "- narration: पेशेवर डॉक्यूमेंट्री शैली में गंभीर किस्सागोई (25-32 शब्द प्रति दृश्य)। पहला दृश्य सवाल से शुरू हो।\n"
            "- callout: बिना किसी इमोजी के साफ-सुथरा माइलस्टोन बैज।\n"
            "- beats: दृश्य के शुरुआती, मध्य और अंतिम भाग में बोले जाने वाले शब्दों से सटीक मेल खाने वाले ठीक 3 अंग्रेजी इमेज प्रॉम्प्ट (प्रत्येक 6-9 शब्द)।\n"
        )

    prompt = (
        f"You are a master historical documentary filmmaker creating a high-retention video like IRON AND INK.\n"
        f"Exact Topic / Subject: {topic}\n"
        f"Language: {'English' if LANGUAGE == 'en' else 'Hindi'}\n"
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
        '      "beats": ["prompt for start", "prompt for middle", "prompt for end"]\n'
        '    }\n'
        '  ]\n'
        '}'
    )
    data = gemini_json(prompt, label="full_script")
    scenes = []
    for s in data.get("scenes", [])[:n_scenes]:
        beats = s.get("beats", [])
        if not isinstance(beats, list) or len(beats) < 3:
            base_p = s.get("image_prompt", topic)
            beats = [base_p, base_p, base_p]
        scenes.append({
            "narration": clean_text(s.get("narration", "")),
            "callout": clean_text(s.get("callout", "")),
            "beats": [clean_text(b) for b in beats[:3]]
        })

    log(f"Script generated successfully: {len(scenes)} synchronized scenes ready.")
    return {
        "topic": topic,
        "title": clean_text(data.get("title", topic)),
        "description": clean_text(data.get("description", "")),
        "scenes": scenes,
    }
    # ==================== Hugging Face FLUX Multi-Beat Visual Engine ====================
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

def fetch_image_hf(prompt_text: str, path: Path, seed: int, topic: str, prev_images: list) -> bool:
    hf_key = os.environ.get("HF_TOKEN", "").strip()
    full_prompt = f"{prompt_text or topic}, classical historical oil painting, museum masterpiece, cinematic lighting, 8k"

    if hf_key:
        api_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
        headers = {"Authorization": f"Bearer {hf_key}"}
        payload = {
            "inputs": full_prompt,
            "options": {"wait_for_model": True, "seed": seed}
        }
        
        for attempt in range(3):
            try:
                time.sleep(3.5) # Safe rate-limit delay
                r = requests.post(api_url, headers=headers, json=payload, timeout=40)
                if r.status_code == 200 and len(r.content) > 15000:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    fit_cover(img).save(path, quality=94)
                    return True
                elif r.status_code == 503:
                    time.sleep(5 * (attempt + 1))
            except Exception:
                time.sleep(2)

    if prev_images:
        try:
            src_img = Image.open(random.choice(prev_images)).convert("RGB")
            src_img = ImageOps.mirror(src_img)
            w, h = src_img.size
            cropped = src_img.crop((int(w * 0.08), int(h * 0.08), int(w * 0.92), int(h * 0.92))).resize((W, H), Image.LANCZOS)
            cropped.save(path, quality=92)
            return True
        except Exception:
            pass

    create_vintage_canvas(path, seed)
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
            log(f"  Generating Scene {scene_idx + 1} Beat {b_idx + 1}/3: '{beat_prompt[:30]}...'")
            fetch_image_hf(beat_prompt, b_img, seed=9000 + (scene_idx * 100) + b_idx, topic=topic, prev_images=valid_prev)
            if b_img.exists() and b_img.stat().st_size > 10000:
                valid_prev.append(b_img)
        
        # Render individual 2.5s sub-clip with motion
        sub_tmp = WORK / f"sub_{scene_idx}_{b_idx}.mp4"
        frames = int(beat_dur * FPS)
        cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        z = f"1+0.08*on/{frames}" if b_idx % 2 == 0 else f"1.08-0.08*on/{frames}"
        
        vf = (
            f"crop=iw:ih-45:0:0,"
            f"scale=3840:2160:flags=lanczos,"
            f"zoompan=z='{z}':x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p"
        )
        
        # Add callout badge ONLY on the first beat of the scene
        if b_idx == 0 and scene_data.get("callout"):
            callout = scene_data["callout"]
            normalized = callout.replace("•", "-").replace(":", " - ").replace("'", "").replace('"', "")
            if LANGUAGE == "en":
                clean_callout = re.sub(r"[^a-zA-Z0-9\s\-\,\.\(\)]", "", normalized).strip()
            else:
                clean_callout = re.sub(r"[^\w\s\d\-\,\.\(\)\u0900-\u097F]", "", normalized).strip()
            clean_callout = re.sub(r"\s+", " ", clean_callout)
            
            if len(clean_callout) > 1:
                badge_dur = min(3.0, beat_dur * 0.8)
                box_w = max(240, min(880, len(clean_callout) * 14 + 55))
                font_opt = f"fontfile='{FONT_FILE}'" if FONT_FILE else f"font='{DEFAULT_FONT}'"
                vf += (
                    f",drawbox=x=80:y=80:w={box_w}:h=60:color=black@0.85:t=fill:enable='between(t,0.2,{badge_dur})'"
                    f",drawbox=x=80:y=80:w=6:h=60:color=#E63946:t=fill:enable='between(t,0.2,{badge_dur})'"
                    f",drawtext=text='{clean_callout}':fontcolor=white:fontsize=23:x=105:y=98:"
                    f"{font_opt}:enable='between(t,0.2,{badge_dur})'"
                )

        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(b_img),
            "-vf", vf, "-t", f"{beat_dur:.3f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(sub_tmp)
        ], check=True)
        beat_clips.append(sub_tmp)

    # Concat the 3 beats together for this scene
    beat_list = WORK / f"beats_{scene_idx}.txt"
    beat_list.write_text("".join(f"file '{bc.resolve()}'\n" for bc in beat_clips))
    
    scene_video_only = WORK / f"scene_video_{scene_idx}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(beat_list), "-c", "copy", str(scene_video_only)
    ], check=True)

    # Merge with scene audio
    tmp_clip = clip_path.with_name(clip_path.stem + ".tmp.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(scene_video_only), "-i", str(aud_path),
        "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        "-shortest", str(tmp_clip)
    ], check=True)
    os.replace(tmp_clip, clip_path)

def build_video(data: dict) -> None:
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    scenes = data["scenes"]
    topic = data.get("topic", "History")
    n = len(scenes)

    log("=== Step 1/2: Recording Documentary Narration ===")
    auds, durs = [], []
    for i, sc in enumerate(scenes):
        aud = WORK / f"aud_{i:03d}.mp3"
        if not (aud.exists() and aud.stat().st_size > 1000 and audio_duration(aud) > 0.8):
            log(f"Recording Scene {i + 1}/{n}...")
            speak(sc["narration"], aud)
        auds.append(aud)
        durs.append(audio_duration(aud) + 0.4)

    log("=== Step 2/2: Generating Multi-Beat Synchronized Visuals & Rendering ===")
    clips = []
    valid_prev = []
    for i in range(n):
        clip = WORK / f"clip_{i:03d}.mp4"
        if not (clip.exists() and clip.stat().st_size > 1000):
            log(f"Processing Scene {i + 1}/{n} (Multi-Beat Sync)...")
            render_multi_beat_clip(scenes[i], auds[i], clip, i, durs[i], topic, valid_prev)
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
    
