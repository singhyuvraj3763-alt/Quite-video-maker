"""
Professional Hindi Documentary Automation Pipeline
- Auto-discovers active Gemini models (prioritizing Flash-Lite & Flash)
- Zero-watermark archival & AI visual pipeline (Wikimedia Commons + Pollinations fallback)
- Fast single-pass structured script generation
- Milestone Callout Badges with Devanagari font support & safe FFmpeg escaping
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
                log(f"Discovered {len(usable)} active models on key. Priority: {', '.join(usable[:6])}")
                return usable
    except Exception as e:
        log(f"Live model discovery note: {e!r}")
    
    return ["gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-2.0-flash-exp"]

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
                log(f"[{label}] Querying model: {model} (Attempt {attempt})...")
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
                    log(f"[{label}] {model} throttled ({r.status_code}). Switching to next model...")
                    time.sleep(4)
                elif r.status_code == 404:
                    continue
                else:
                    log(f"[{label}] {model} returned HTTP {r.status_code}.")

            except Exception as e:
                log(f"[{label}] {model} connection error: {e!r}")
                continue

        time.sleep(8 * attempt)

    sys.exit(f"Failed to generate script across all discovered models for '{label}'.")

# ==================== Script Processing ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def make_script(topic: str, n_scenes: int) -> dict:
    log(f"Generating full script in one go for topic: {topic} ({n_scenes} scenes)")
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

    log(f"Script generated successfully: {len(scenes)} scenes ready.")
    return {
        "topic": topic,
        "title": clean_text(data.get("title", topic)),
        "description": clean_text(data.get("description", "")),
        "scenes": scenes,
    }

# ==================== High-Res Visuals Engine (Wikimedia + AI) ====================
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

def search_wikimedia_highres(query: str) -> str:
    """Finds genuine historical, high-resolution original images on Wikimedia Commons."""
    api_url = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": "HistoricalDocBot/2.0 (educational automated pipeline)"}
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrlimit": 5,
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "format": "json"
    }
    try:
        r = requests.get(api_url, params=params, headers=headers, timeout=10)
        if r.status_code == 200:
            pages = r.json().get("query", {}).get("pages", {})
            for pid, info in pages.items():
                imginfo = info.get("imageinfo", [{}])[0]
                mime = imginfo.get("mime", "")
                width = imginfo.get("width", 0)
                height = imginfo.get("height", 0)
                url = imginfo.get("url", "")
                
                # Check for high-resolution raster file (no SVGs or tiny icons)
                if ("jpeg" in mime or "png" in mime or "jpg" in mime) and width >= 1024 and height >= 600:
                    return url
    except Exception:
        pass
    return ""

def fetch_image(prompt: str, path: Path, seed: int) -> bool:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    stop_words = {"cinematic", "dramatic", "shot", "view", "scene", "historical", "lighting", "ultra", "realistic"}
    words = [w for w in re.findall(r"\b[A-Za-z]+\b", prompt) if len(w) > 3 and w.lower() not in stop_words]
    core_search = " ".join(words[:4]) if words else "Rajasthan history"

    # Step 1: Wikimedia Commons high-resolution public archive search
    wiki_url = search_wikimedia_highres(core_search)
    if wiki_url:
        try:
            r = requests.get(wiki_url, headers=headers, timeout=14)
            if r.status_code == 200 and len(r.content) > 25000:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                if img.width >= 800 and img.height >= 500:
                    fit_cover(img).save(path, quality=92)
                    return True
        except Exception:
            pass

    # Step 2: High-Quality Direct AI Generation
    clean_prompt = quote(f"{' '.join(words[:8])}, authentic documentary photography, 35mm archival realism, 16:9")
    ai_urls = [
        f"https://image.pollinations.ai/prompt/{clean_prompt}?width=1280&height=720&seed={seed}&nologo=true&model=turbo",
        f"https://image.pollinations.ai/prompt/{quote(' '.join(words[:6]))}?width=1280&height=720&seed={seed+9}&nologo=true"
    ]

    for url in ai_urls:
        try:
            r = requests.get(url, headers=headers, timeout=16)
            if r.status_code == 200 and len(r.content) > 8000:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                fit_cover(img).save(path, quality=92)
                return True
        except Exception:
            time.sleep(1.5)
            continue

    return False

def generate_images(scenes: list) -> list:
    n = len(scenes)
    paths = []
    for i, sc in enumerate(scenes):
        img = WORK / f"img_{i:03d}.jpg"
        if not (img.exists() and img.stat().st_size > 5000):
            log(f"Fetching Scene Visual {i + 1}/{n}: {sc['image_prompt'][:45]}...")
            ok = fetch_image(sc["image_prompt"], img, seed=7000 + (i * 31))
            
            if not ok:
                log(f"::warning:: Visual {i + 1} fallback engaged.")
                prev = WORK / f"img_{i-1:03d}.jpg"
                if prev.exists() and prev.stat().st_size > 5000:
                    import shutil
                    shutil.copy(prev, img)
                else:
                    cmd = [
                        "ffmpeg", "-y", "-f", "lavfi",
                        "-i", f"color=c=#101820:s={W}x{H}:d=1",
                        "-frames:v", "1", str(img)
                    ]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            time.sleep(2.5)

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

# ==================== Motion & Callout Video Compositing ====================
def motion_and_callout_filter(i: int, frames: int, callout: str, dur: float) -> str:
    f = frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    styles = [
        (f"1+0.12*on/{f}", cx, cy),
        (f"1.12-0.12*on/{f}", cx, cy),
        ("1.08", f"(iw-iw/zoom)*on/{f}", cy),
        ("1.08", f"(iw-iw/zoom)*(1-on/{f})", cy),
    ]
    z, x, y = styles[i % 4]

    # Watermark bottom 45px slice and Ken Burns motion
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
            .replace("%", "")
            .replace('"', "")
            .strip()
        )
        badge_duration = min(4.5, dur - 0.5) if dur > 2 else dur
        box_w = max(240, min(800, len(clean_callout) * 16 + 50))
        vf += (
            f",drawbox=x=80:y=80:w={box_w}:h=60:color=black@0.75:t=fill:enable='between(t,0.5,{badge_duration})'"
            f",drawbox=x=80:y=80:w=6:h=60:color=#E63946:t=fill:enable='between(t,0.5,{badge_duration})'"
            f",drawtext=text='{clean_callout}':fontcolor=white:fontsize=22:x=102:y=98:"
            f"font='Noto Sans Devanagari':enable='between(t,0.5,{badge_duration})'"
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
    n = len(scenes)

    log("=== Step 1/3: Generating Distinct Scene Visuals ===")
    imgs = generate_images(scenes)

    log("=== Step 2/3: Recording Hindi Narration ===")
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
            badge_log = f" [Badge: '{callout_text}']" if callout_text else ""
            log(f"Encoding Scene {i + 1}/{n}{badge_log}...")
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
    subtitle_filter = (
        f"subtitles='{srt_path.resolve()}':force_style="
        "'FontName=Noto Sans Devanagari,FontSize=22,PrimaryColour=&H0000FFFF,"
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
    
