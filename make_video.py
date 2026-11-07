"""
Cinematic Documentary Pipeline v5.2 (Voice-Synced, AI-Image Edition, black-frame proof)

What changed vs v4:
- Every narration LINE gets its own AI-generated image, and the scene for that image
  lasts exactly as long as that line's voice clip. Picture can no longer drift from voice.
- No more random Wikimedia search results (that caused F-16 jets / Zoom calls / playbills).
- Storytelling script: hook, short punchy lines, questions, varied pauses.
- Slower, deeper voice + variable pauses (short / medium / long / dramatic).
- Subtitles break into 5-word chunks and are timed per line.
- Background music is now actually audible (old mix made it ~0.02 volume).

Same env vars as before: TOPIC, LANGUAGE (hi/en), SCENES, VOICE, RESUME, GEMINI_API_KEY, GEMINI_MODEL
New optional env vars:
  STYLE              - visual style line added to EVERY image prompt (keeps the whole video consistent)
  POLLINATIONS_TOKEN - optional free key from auth.pollinations.ai (removes the 15s-per-image wait + watermark)
  POLL_MODEL         - optional image model name (leave empty for default)
  IMG_GAP / IMG_TRIES / REPAIR_ROUNDS - patience settings (defaults 20s / 8 / 4)
"""

import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageOps, ImageStat

FPS = 25
W, H = 1920, 1080
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# ==================== Language & Voice Setup ====================
LANGUAGE = os.environ.get("LANGUAGE", "hi").strip().lower()  # "hi" or "en"

if LANGUAGE == "en":
    DEFAULT_VOICE = "en-US-ChristopherNeural"
    DEFAULT_FONT = "DejaVuSans-Bold"
    DEFAULT_RATE = "+12%"
else:
    DEFAULT_VOICE = "hi-IN-MadhurNeural"  # deeper male storyteller voice. Old one: hi-IN-SwaraNeural
    DEFAULT_FONT = "NotoSansDevanagari-Bold"
    DEFAULT_RATE = "+14%"

VOICE_RATE = os.environ.get("VOICE_RATE", DEFAULT_RATE).strip()   # speed. Try +8% (calmer) to +20% (very fast)
VOICE_PITCH = os.environ.get("VOICE_PITCH", "+0Hz").strip()
VOICE = os.environ.get("VOICE", DEFAULT_VOICE)

# Pause (seconds) added after a line's voice. Gemini picks one per line.
PAUSES = {"short": 0.12, "medium": 0.3, "long": 0.55, "dramatic": 0.9}
STYLE = os.environ.get(
    "STYLE",
    "cinematic historical illustration, painterly, dramatic lighting, rich warm earthy tones, "
    "highly detailed, wide shot, no text, no letters, no watermark, no modern objects",
).strip()

POLLINATIONS_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "").strip()
POLL_MODEL = os.environ.get("POLL_MODEL", "").strip()
# Patience settings (anonymous tier is slow and throttles hard; waiting is fine, black frames are not)
IMG_GAP = float(os.environ.get("IMG_GAP", "3" if POLLINATIONS_TOKEN else "20"))  # seconds between requests
IMG_TRIES = int(os.environ.get("IMG_TRIES", "8"))        # attempts per image per round
REPAIR_ROUNDS = int(os.environ.get("REPAIR_ROUNDS", "4"))  # extra rounds for images that still failed

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

# ==================== Gemini Script Engine ====================
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
                    if "flash" in nl and "lite" not in nl:
                        return 0  # better storytelling than lite
                    if "lite" in nl:
                        return 1
                    return 2

                usable.sort(key=sort_priority)
                return usable
    except Exception:
        pass
    return ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"]


def extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a: b + 1])
        raise


def gemini_json(prompt: str, label: str):
    global _good_model
    key = gemini_key()
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.8,
            "maxOutputTokens": 16384,
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
                r = requests.post(url, headers=headers, json=body, timeout=120)
                if r.status_code == 200:
                    cands = r.json().get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
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


def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()


def make_script(topic: str, n_scenes: int) -> dict:
    log(f"Generating storytelling script for: '{topic}' ({n_scenes} scenes x 3 lines)")

    if LANGUAGE == "en":
        lang_rules = (
            "LANGUAGE: English. Write 'line' as spoken narration, like a gripping documentary storyteller.\n"
        )
        callout_rule = "plain English letters/numbers only, 2-4 words, NO emojis (e.g. 'June 1576', 'The Pass')"
    else:
        lang_rules = (
            "LANGUAGE: Hindi in Devanagari for every 'line'. Write the way a Hindi kissago (storyteller) SPEAKS: "
            "simple everyday words, NOT textbook or heavily Sanskritised Hindi. Use '।' and commas for natural "
            "breathing. Never mix Latin letters into 'line'.\n"
        )
        callout_rule = "short Hindi badge in Devanagari, 2-4 words, NO emojis (e.g. 'जून 1576', 'हल्दीघाटी')"

    prompt = (
        "You are a master documentary storyteller writing a high-retention YouTube video.\n"
        f"Topic: {topic}\n"
        f"{lang_rules}\n"
        f"Create exactly {n_scenes} chronological scenes. Each scene has EXACTLY 3 beats.\n"
        "A beat = one narration 'line' + the 'image' that will be shown while that exact line is spoken.\n\n"
        "STORYTELLING RULES:\n"
        "- Scene 1, beat 1 is a gripping HOOK: a startling question or fact. No greetings, no 'welcome'.\n"
        "- Each 'line' is 8-14 words, ONE idea. At tense moments use short punchy lines (3-6 words).\n"
        "- Tell it as a story with people, choices, danger and emotion, not a list of facts. Build tension, then reveal.\n"
        "- Speak to the viewer now and then (e.g. 'Imagine...', 'But here is the twist...'). Ask a question roughly every 5 scenes.\n"
        "- Stay factual. Do NOT invent dates, numbers, or quotes you are not sure about; say 'around' or leave them out.\n"
        "- Final scene ends with a memorable one-line reflection.\n"
        "- 'pause' controls the silence AFTER that line: 'short' (normal), 'medium' (end of a thought), "
        "'long' (let it sink in), 'dramatic' (right before a big reveal). Use 'dramatic' only 3-5 times in the whole video.\n\n"
        "IMAGE RULES (always in ENGLISH, 15-30 words):\n"
        "- Show EXACTLY what the line says: subject, setting, action, mood, correct era/place.\n"
        "- Name the specific people/places. Every time a main character appears, repeat the SAME physical description "
        "so he/she looks the same across scenes.\n"
        "- No text, signs, or lettering in the image. No modern objects unless the topic is modern.\n"
        "- Vary shot types: wide landscape, close-up, over-the-shoulder, aerial, silhouette.\n\n"
        f"callout (once per scene): {callout_rule}\n\n"
        "Return pure JSON only:\n"
        "{\n"
        '  "title": "...", "description": "2-3 sentence summary",\n'
        '  "scenes": [\n'
        '    {"callout": "...", "beats": [\n'
        '      {"line": "...", "image": "...", "pause": "short"},\n'
        '      {"line": "...", "image": "...", "pause": "short"},\n'
        '      {"line": "...", "image": "...", "pause": "medium"}\n'
        "    ]}\n"
        "  ]\n"
        "}"
    )
    data = gemini_json(prompt, label="full_script")

    scenes = []
    for s in data.get("scenes", [])[:n_scenes]:
        beats = []
        for b in s.get("beats", []):
            if not isinstance(b, dict):
                continue
            line = clean_text(b.get("line", ""))
            if len(line) < 3:
                continue
            pause = str(b.get("pause", "short")).strip().lower()
            beats.append({
                "line": line,
                "image": clean_text(b.get("image", "")) or topic,
                "pause": pause if pause in PAUSES else "short",
            })
        if beats:
            scenes.append({"callout": clean_text(s.get("callout", "")), "beats": beats})

    if not scenes:
        fatal("Gemini returned no usable scenes.")

    # Make sure every scene ends with at least a medium pause, and the hook gets room to land.
    for sc in scenes:
        if PAUSES[sc["beats"][-1]["pause"]] < PAUSES["medium"]:
            sc["beats"][-1]["pause"] = "medium"
    if PAUSES[scenes[0]["beats"][0]["pause"]] < PAUSES["long"]:
        scenes[0]["beats"][0]["pause"] = "long"

    log(f"Script ready: {len(scenes)} scenes, {sum(len(s['beats']) for s in scenes)} lines.")
    return {
        "topic": topic,
        "title": clean_text(data.get("title", topic)),
        "description": clean_text(data.get("description", "")),
        "scenes": scenes,
    }


# ==================== Image Engine (AI-generated, matched to each line) ====================
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
    """Last-resort background. Deliberately warm and bright, never black."""
    random.seed(seed)
    c1 = (random.randint(90, 120), random.randint(70, 95), random.randint(50, 70))
    c2 = (random.randint(150, 190), random.randint(115, 145), random.randint(80, 100))
    strip = Image.new("RGB", (1, H))
    px = strip.load()
    for y in range(H):
        f = y / H
        px[0, y] = tuple(int(c1[i] * (1 - f) + c2[i] * f) for i in range(3))
    strip.resize((W, H)).save(path, quality=92)


_seen_hashes = set()


def is_good_image(img: Image.Image) -> bool:
    """Reject black / near-black / flat frames (rate-limit or error placeholders)."""
    g = img.convert("L").resize((160, 90))
    st = ImageStat.Stat(g)
    return st.mean[0] > 28 and st.stddev[0] > 14


def file_is_good(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size < 10000:
            return False
        return is_good_image(Image.open(path).convert("RGB"))
    except Exception:
        return False


_last_img_request = 0.0


def _throttle() -> None:
    global _last_img_request
    wait = IMG_GAP - (time.time() - _last_img_request)
    if wait > 0:
        time.sleep(wait)
    _last_img_request = time.time()


def generate_image(visual: str, path: Path, seed: int) -> bool:
    """Try hard. Returns True only if a real, non-black, non-duplicate image was saved."""
    full_prompt = f"{visual}, {STYLE}"[:450]
    headers = {"User-Agent": "DocumentaryPipeline/5.2"}
    if POLLINATIONS_TOKEN:
        # current Pollinations API: needs the key, uses free hourly pollen balance
        url = f"https://gen.pollinations.ai/image/{quote(full_prompt, safe='')}"
        model = POLL_MODEL or "flux"
        headers["Authorization"] = f"Bearer {POLLINATIONS_TOKEN}"
    else:
        url = f"https://image.pollinations.ai/prompt/{quote(full_prompt, safe='')}"
        model = POLL_MODEL

    for attempt in range(IMG_TRIES):
        params = {"width": W, "height": H, "seed": seed + attempt * 101, "nologo": "true"}
        if model:
            params["model"] = model
        _throttle()
        status = None
        try:
            r = requests.get(url, params=params, headers=headers, timeout=180)
            status = r.status_code
            ctype = r.headers.get("content-type", "")
            if status == 200 and "image" in ctype and len(r.content) > 15000:
                digest = hashlib.md5(r.content).hexdigest()
                tmp = path.with_suffix(".raw")
                tmp.write_bytes(r.content)
                img = Image.open(tmp).convert("RGB")
                tmp.unlink(missing_ok=True)
                if digest in _seen_hashes:
                    log("    got a repeated image (likely a placeholder), retrying")
                elif not is_good_image(img):
                    log("    got a black/flat image, retrying")
                else:
                    _seen_hashes.add(digest)
                    fit_cover(img).save(path, quality=94)
                    return True
        except Exception as e:
            log(f"    request error: {type(e).__name__}: {e}")

        if status == 402 and not POLLINATIONS_TOKEN:
            fatal("Pollinations returned 402: anonymous image generation is no longer available. "
                  "Add your API key as secret POLLINATIONS_TOKEN and pass it in the workflow env.")
        if status in (401, 403) and POLLINATIONS_TOKEN:
            fatal(f"Pollinations rejected the API key (status {status}). Check the secret value "
                  "(use the sk_ key from enter.pollinations.ai).")

        if status == 402:
            wait = 300  # out of free pollen for now; the balance refills every hour
        else:
            base = 60 if status == 429 else 25
            wait = min(240, base * (attempt + 1))
        log(f"    attempt {attempt + 1}/{IMG_TRIES} failed (status {status}); waiting {wait}s")
        time.sleep(wait)
    return False


def borrow_neighbor(idx: int, paths: list, good: list) -> bool:
    """Give a failed image a mirrored/cropped copy of the nearest good image (never black)."""
    order = sorted((i for i in range(len(paths)) if good[i]), key=lambda i: abs(i - idx))
    for i in order:
        try:
            src = ImageOps.mirror(Image.open(paths[i]).convert("RGB"))
            w, h = src.size
            src.crop((int(w * 0.12), int(h * 0.12), int(w * 0.88), int(h * 0.88))).resize((W, H), Image.LANCZOS).save(paths[idx], quality=92)
            return True
        except Exception:
            continue
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
            capture_output=True, text=True,
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


def speak(text: str, path: Path) -> None:
    text = clean_text(text)
    if text and text[-1] not in ".?!।…":
        text += "।" if LANGUAGE == "hi" else "."
    for _ in range(3):
        try:
            asyncio.run(_edge(text, path))
            if path.exists() and path.stat().st_size > 1000 and audio_duration(path) > 0.4:
                return
        except Exception:
            time.sleep(2)
    from gtts import gTTS
    gTTS(text, lang=LANGUAGE).save(str(path))


# ==================== Subtitles (timed per line) ====================
def format_srt_time(sec: float) -> str:
    millis = int(round((sec - int(sec)) * 1000))
    if millis == 1000:
        sec += 1
        millis = 0
    s = int(sec)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def create_subtitles(timeline: list, srt_path: Path) -> None:
    """timeline items: (start_sec, speech_sec, text)"""
    out = []
    idx = 1
    for start, speech, text in timeline:
        words = text.split()
        chunks = [words[i:i + 5] for i in range(0, len(words), 5)]
        if len(chunks) > 1 and len(chunks[-1]) <= 2:  # avoid a tiny orphan chunk
            chunks[-2].extend(chunks[-1])
            chunks.pop()
        if not chunks:
            continue
        total_words = sum(len(c) for c in chunks)
        t = start
        for c in chunks:
            d = speech * len(c) / total_words
            out.append(f"{idx}\n{format_srt_time(t)} --> {format_srt_time(t + d)}\n{' '.join(c)}\n")
            idx += 1
            t += d
    srt_path.write_text("\n".join(out), encoding="utf-8")


# ==================== Beat clip: one image + its own voice line ====================
def render_beat_clip(img: Path, aud: Path, clip: Path, frames: int, dur: float, pad: float,
                     idx_in_video: int, callout: str) -> None:
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    z = f"1+0.08*on/{frames}" if idx_in_video % 2 == 0 else f"1.08-0.08*on/{frames}"

    vf = (
        # crop the bottom strip (hides any generator watermark), then fill the frame again
        "crop=iw:ih-70:0:0,"
        "scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,"
        f"zoompan=z='{z}':x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p"
    )

    if callout:
        normalized = callout.replace("•", "-").replace(":", " - ").replace("'", "").replace('"', "")
        if LANGUAGE == "en":
            clean_callout = re.sub(r"[^a-zA-Z0-9\s\-\,\.\(\)]", "", normalized).strip()
        else:
            clean_callout = re.sub(r"[^\w\s\d\-\,\.\(\)\u0900-\u097F]", "", normalized).strip()
        clean_callout = re.sub(r"\s+", " ", clean_callout)
        if len(clean_callout) > 1:
            badge_dur = min(3.0, dur * 0.8)
            box_w = max(240, min(880, len(clean_callout) * 14 + 55))
            font_opt = f"fontfile='{FONT_FILE}'" if FONT_FILE else f"font='{DEFAULT_FONT}'"
            vf += (
                f",drawbox=x=80:y=80:w={box_w}:h=60:color=black@0.85:t=fill:enable='between(t,0.2,{badge_dur:.2f})'"
                f",drawbox=x=80:y=80:w=6:h=60:color=#E63946:t=fill:enable='between(t,0.2,{badge_dur:.2f})'"
                f",drawtext=text='{clean_callout}':fontcolor=white:fontsize=23:x=105:y=98:"
                f"{font_opt}:enable='between(t,0.2,{badge_dur:.2f})'"
            )

    tmp = clip.with_name(clip.stem + ".tmp.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(img), "-i", str(aud),
        "-vf", vf,
        "-af", f"apad=pad_dur={pad:.3f},aresample=44100",
        "-ac", "2", "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k",
        str(tmp),
    ], check=True)
    os.replace(tmp, clip)


# ==================== Build ====================
def build_video(data: dict) -> None:
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    scenes = data["scenes"]
    total_lines = sum(len(s["beats"]) for s in scenes)

    log("=== Step 1/3: Recording narration (one clip per line) ===")
    jobs = []  # one per beat
    n = 0
    for si, sc in enumerate(scenes):
        for bi, b in enumerate(sc["beats"]):
            n += 1
            aud = WORK / f"aud_{si:03d}_{bi}.mp3"
            if not (aud.exists() and aud.stat().st_size > 1000 and audio_duration(aud) > 0.4):
                log(f"Recording line {n}/{total_lines}...")
                speak(b["line"], aud)
            speech = audio_duration(aud)
            pad = PAUSES.get(b.get("pause", "short"), 0.25)
            frames = max(1, round((speech + pad) * FPS))
            dur = frames / FPS
            jobs.append({
                "si": si, "bi": bi, "line": b["line"], "visual": b["image"], "aud": aud,
                "speech": speech, "pad": dur - speech, "frames": frames, "dur": dur,
                "callout": sc.get("callout", "") if bi == 0 else "",
            })

    log("=== Step 2/3: Generating one image per line (slow and patient) ===")
    if not POLLINATIONS_TOKEN:
        log(f"(anonymous tier: ~{int(IMG_GAP)}s per image, about {total_lines * int(IMG_GAP) // 60} min minimum)")
    img_paths = [WORK / f"img_{j['si']:03d}_{j['bi']}.jpg" for j in jobs]
    good = [False] * len(jobs)

    # Round 0 = main pass. Rounds 1..N = repair passes for anything that still failed.
    for rnd in range(REPAIR_ROUNDS + 1):
        pending = [k for k in range(len(jobs)) if not good[k]]
        if not pending:
            break
        if rnd > 0:
            log(f"--- Repair round {rnd}/{REPAIR_ROUNDS}: {len(pending)} image(s) left. Cooling down 3 min first ---")
            time.sleep(180)
        for k in pending:
            j = jobs[k]
            if file_is_good(img_paths[k]):  # valid image already cached from a previous run
                good[k] = True
                _seen_hashes.add(hashlib.md5(img_paths[k].read_bytes()).hexdigest())
                continue
            # a cached black image or stale clip must not survive
            img_paths[k].unlink(missing_ok=True)
            (WORK / f"clip_{j['si']:03d}_{j['bi']}.mp4").unlink(missing_ok=True)
            log(f"Image {k + 1}/{total_lines}: {j['visual'][:80]}")
            if generate_image(j["visual"], img_paths[k], seed=7000 + k):
                good[k] = True

    missing = [k for k in range(len(jobs)) if not good[k]]
    if missing:
        log(f"WARNING: {len(missing)} image(s) could not be generated; borrowing nearby images instead of black frames.")
        for k in missing:
            if not borrow_neighbor(k, img_paths, good):
                create_cinematic_gradient(img_paths[k], 7000 + k)
            (WORK / f"clip_{jobs[k]['si']:03d}_{jobs[k]['bi']}.mp4").unlink(missing_ok=True)

    log("Rendering clips...")
    clips, timeline = [], []
    t_cursor = 0.0
    for k, j in enumerate(jobs):
        clip = WORK / f"clip_{j['si']:03d}_{j['bi']}.mp4"
        if not (clip.exists() and clip.stat().st_size > 1000):
            render_beat_clip(img_paths[k], j["aud"], clip, j["frames"], j["dur"], j["pad"], k, j["callout"])
        clips.append(clip)
        timeline.append((t_cursor, j["speech"], j["line"]))
        t_cursor += j["dur"]

    log("=== Step 3/3: Final assembly ===")
    listfile = WORK / "list.txt"
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    raw_concat = WORK / "concat_raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", str(raw_concat)],
        check=True,
    )

    srt_path = WORK / "subtitles.srt"
    create_subtitles(timeline, srt_path)

    log("Synthesizing background score...")
    bg_music = WORK / "bg_music.mp3"
    total_dur = t_cursor
    expr = (
        "(sin(110*2*PI*t)*0.35 + sin(164.81*2*PI*t)*0.25 + "
        "sin(220*2*PI*t)*0.20 + sin(261.63*2*PI*t)*0.15) * (0.75 + 0.25*sin(0.25*2*PI*t))"
    )
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc='{expr}':s=44100:d={total_dur:.2f}",
        "-af", f"lowpass=f=900,afade=t=in:d=3,afade=t=out:st={max(0, total_dur - 4):.2f}:d=4",
        str(bg_music),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    video_with_audio = WORK / "video_with_audio.mp4"
    if bg_music.exists() and bg_music.stat().st_size > 1000:
        # music at ~10% of voice. amix halves both inputs, so volume=2 restores the voice level.
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(raw_concat), "-i", str(bg_music),
            "-filter_complex",
            "[1:a]volume=0.10[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(video_with_audio),
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
    log("Burning in subtitles...")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_with_audio),
         "-vf", subtitle_filter, "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
         "-c:a", "copy", "-movflags", "+faststart", str(final)],
        check=True,
    )

    (OUT / "title.txt").write_text(data.get("title", ""), encoding="utf-8")
    (OUT / "description.txt").write_text(data.get("description", ""), encoding="utf-8")
    log(f"COMPLETED! Final video saved to {final} ({total_dur:.0f}s)")


# ==================== Main Runner ====================
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    topic = os.environ.get("TOPIC", "The Rise and Fall of the Roman Empire").strip()
    n = int(os.environ.get("SCENES", "20") or 20)

    if mode == "full" and os.environ.get("RESUME") != "1":
        for p in [WORK, OUT]:
            if p.exists():
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
