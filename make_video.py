import os
import sys
import json
import time
import shutil
import asyncio
import subprocess
import requests
import edge_tts

# ----------------- STEP 1: SCRIPT GENERATOR -----------------
def generate_script(topic, num_scenes=24):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is missing!")
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=api_key)

    print(f"Generating documentary script for: '{topic}' ({num_scenes} scenes)...")
    
    prompt = f"""
    Write an engaging, factual documentary video script in Hindi about: {topic}.
    Return strictly valid JSON without markdown formatting with this schema:
    {{
      "title": "Documentary Title in Hindi",
      "scenes": [
        {{
          "scene_number": 1,
          "narration": "Hindi narration for scene 1",
          "visual_prompt": "Specific visual description in English for image generation"
        }}
      ]
    }}
    Ensure exactly {num_scenes} scenes.
    """

    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt,
            )
            raw = response.text.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            return raw.strip()
        except Exception as e:
            print(f"Gemini attempt {attempt + 1} failed: {e}")
            time.sleep((attempt + 1) * 4)

    raise RuntimeError("Failed to generate script from Gemini API.")

# ----------------- STEP 2: VOICEOVER GENERATOR -----------------
VOICE = "hi-IN-SwaraNeural"

async def generate_voiceovers(scenes):
    os.makedirs("audio", exist_ok=True)
    print(f"Generating voiceovers for {len(scenes)} scenes...")

    for scene in scenes:
        scene_num = scene["scene_number"]
        narration = scene["narration"]
        audio_path = f"audio/scene_{scene_num}.mp3"

        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            continue

        print(f"Recording narration for Scene {scene_num}...")
        communicate = edge_tts.Communicate(narration, VOICE)
        await communicate.save(audio_path)

    print("All voiceovers ready!")

# ----------------- STEP 3: VISUALS GENERATOR (NO WATERMARK) -----------------
def generate_visuals(scenes):
    os.makedirs("images", exist_ok=True)
    print(f"Generating visuals for {len(scenes)} scenes...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for scene in scenes:
        scene_num = scene["scene_number"]
        v_prompt = scene.get("visual_prompt", "documentary photograph cinematic lighting")
        img_path = f"images/scene_{scene_num}.jpg"

        if os.path.exists(img_path) and os.path.getsize(img_path) > 5000:
            continue

        print(f"Generating visual for Scene {scene_num}...")
        
        # Clean query: strip non-alphanumeric chars
        clean_words = "".join([c for c in v_prompt if c.isalnum() or c.isspace()])
        keywords = " ".join(clean_words.split()[:8])
        query = requests.utils.quote(f"{keywords}, 8k documentary cinematography, sharp focus, historical realism")
        
        # Pollinations with nologo=true (removes watermark) and seed for uniqueness
        seed = 2000 + scene_num
        ai_url = f"[https://image.pollinations.ai/prompt/](https://image.pollinations.ai/prompt/){query}?width=1280&height=720&seed={seed}&nologo=true&model=flux"

        downloaded = False
        for _ in range(3):
            try:
                res = requests.get(ai_url, headers=headers, timeout=25)
                if res.status_code == 200 and len(res.content) > 8000:
                    with open(img_path, "wb") as f_out:
                        f_out.write(res.content)
                    downloaded = True
                    break
            except Exception:
                time.sleep(2)

        # Fail-safe: If AI generation hits timeout, generate a clean cinematic slate
        if not downloaded:
            prev_img = f"images/scene_{scene_num - 1}.jpg"
            if os.path.exists(prev_img) and os.path.getsize(prev_img) > 5000:
                shutil.copy(prev_img, img_path)
            else:
                # Generate a high-contrast cinematic slate so screen is never black
                cmd = [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", "color=c=#141e30:s=1280x720:d=1",
                    "-frames:v", "1", img_path
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("All visuals ready without watermarks!")

# ----------------- STEP 4: VIDEO COMPOSITING & STITCHING -----------------
def render_full_video(scenes):
    os.makedirs("temp_clips", exist_ok=True)
    clip_list_path = "clips.txt"
    valid_clips = []

    print(f"Rendering {len(scenes)} clips into final documentary...")

    for scene in scenes:
        scene_num = scene["scene_number"]
        audio_file = f"audio/scene_{scene_num}.mp3"
        image_file = f"images/scene_{scene_num}.jpg"
        clip_file = f"temp_clips/clip_{scene_num}.mp4"

        if not os.path.exists(audio_file) or not os.path.exists(image_file):
            continue

        print(f"Encoding Scene {scene_num}...")

        # Probe duration of narration
        probe_cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_file
        ]
        duration_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            duration = float(duration_res.stdout.strip())
        except Exception:
            duration = 10.0

        # Encode clip matching exact audio length with scale and pad protection
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_file,
            "-i", audio_file,
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-shortest", clip_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        valid_clips.append(f"file '{clip_file}'")

    if not valid_clips:
        print("Error: No clips rendered.")
        sys.exit(1)

    with open(clip_list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(valid_clips))

    final_output = "final_documentary.mp4"
    print("Stitching clips into final_documentary.mp4...")
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", clip_list_path,
        "-c", "copy",
        final_output
    ]
    subprocess.run(concat_cmd, check=True)
    print(f"Finished! Output ready: {final_output}")

# ----------------- MAIN PIPELINE ENTRYPOINT -----------------
if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "The Mahindra story in Hindi"
    num_scenes = int(sys.argv[2]) if len(sys.argv) > 2 else 24

    # 1. Wipe previous caches so old topics never mix with new topics
    for folder in ["audio", "images", "temp_clips"]:
        if os.path.exists(folder):
            shutil.rmtree(folder)
    if os.path.exists("final_documentary.mp4"):
        os.remove("final_documentary.mp4")

    # 2. Step 1: Script
    script_raw = generate_script(topic, num_scenes=num_scenes)
    data = json.loads(script_raw)
    with open("script.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    scenes = data.get("scenes", [])

    # 3. Step 2: Audio
    asyncio.run(generate_voiceovers(scenes))

    # 4. Step 3: Visuals
    generate_visuals(scenes)

    # 5. Step 4: Stitch Final MP4
    render_full_video(scenes)
