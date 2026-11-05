import os
import sys
import json
import time
import asyncio
import urllib.parse
import urllib.request
import edge_tts

# ----------------- STEP 1: SCRIPT LOGIC -----------------
def generate_script(topic, num_scenes=24):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is missing!")
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=api_key)

    print(f"Generating script for topic: '{topic}' with {num_scenes} scenes...")
    
    prompt = f"""
    Create a detailed documentary-style video script in Hindi about: {topic}.
    Provide the output strictly as a valid JSON object with the following structure:
    {{
      "title": "Video title in Hindi",
      "scenes": [
        {{
          "scene_number": 1,
          "narration": "Narration text in Hindi for scene 1",
          "visual_prompt": "Cinematic visual description in English for image generation"
        }}
      ]
    }}
    Ensure there are exactly {num_scenes} scenes in the 'scenes' list. Do not include markdown code block formatting like ```json in the output, just raw JSON.
    """

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return text.strip()
        except Exception as e:
            print(f"Attempt {attempt + 1} failed due to server load: {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                print(f"Waiting {wait_time} seconds before retrying...")
                time.sleep(wait_time)
            else:
                raise e

# ----------------- STEP 2: AUDIO LOGIC -----------------
VOICE = "hi-IN-SwaraNeural"

async def generate_voiceovers():
    if not os.path.exists("script.json"):
        print("Error: script.json not found!")
        sys.exit(1)

    with open("script.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    scenes = data.get("scenes", [])
    print(f"Found {len(scenes)} scenes to process for voiceover...")

    os.makedirs("audio", exist_ok=True)

    for scene in scenes:
        scene_num = scene["scene_number"]
        narration = scene["narration"]
        audio_path = f"audio/scene_{scene_num}.mp3"

        if os.path.exists(audio_path):
            continue

        print(f"Generating audio for Scene {scene_num}...")
        communicate = edge_tts.Communicate(narration, VOICE)
        await communicate.save(audio_path)

    print("Voiceovers ready in 'audio/' folder!")

# ----------------- STEP 3: VISUALS LOGIC (Zero-Key Open Stock) -----------------
def download_wikimedia_image(query, target_path):
    headers = {"User-Agent": "DocumentaryBot/1.0 (contact: test@example.com)"}
    clean_query = urllib.parse.quote(query)
    search_url = (
        f"https://commons.wikimedia.org/w/api.php?action=query&generator=search"
        f"&gsrnamespace=6&gsrsearch={clean_query}&gsrlimit=3&prop=imageinfo"
        f"&iiprop=url|mime&format=json"
    )

    try:
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            pages = res_data.get("query", {}).get("pages", {})
            for _, page in pages.items():
                image_info = page.get("imageinfo", [{}])[0]
                img_url = image_info.get("url")
                mime = image_info.get("mime", "")
                if img_url and ("jpeg" in mime or "png" in mime or "jpg" in mime):
                    img_req = urllib.request.Request(img_url, headers=headers)
                    with urllib.request.urlopen(img_req, timeout=15) as img_res:
                        with open(target_path, "wb") as out_file:
                            out_file.write(img_res.read())
                    return True
    except Exception as e:
        print(f"Fetch failed for query '{query}': {e}")
    return False

def generate_visuals():
    if not os.path.exists("script.json"):
        print("Error: script.json not found!")
        sys.exit(1)

    with open("script.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    scenes = data.get("scenes", [])
    print(f"Found {len(scenes)} scenes to process for visuals...")
    os.makedirs("images", exist_ok=True)

    fallback_topics = ["Mahindra", "tractor", "factory", "vintage car", "industrial India", "business office"]

    for scene in scenes:
        scene_num = scene["scene_number"]
        prompt = scene.get("visual_prompt", "")
        img_path = f"images/scene_{scene_num}.jpg"

        if os.path.exists(img_path):
            continue

        print(f"Fetching visual for Scene {scene_num}...")
        
        # Use first 3 keywords from visual prompt, or fallback
        keywords = " ".join(prompt.replace(",", "").split()[:3])
        success = download_wikimedia_image(keywords, img_path)

        if not success:
            fallback = fallback_topics[scene_num % len(fallback_topics)]
            download_wikimedia_image(fallback, img_path)

    print("All scene visuals downloaded into 'images/' folder!")

# ----------------- MAIN DISPATCHER -----------------
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "script"
    topic = sys.argv[2] if len(sys.argv) > 2 else "The Mahindra story in Hindi"

    if mode == "script":
        script_json_str = generate_script(topic)
        try:
            parsed = json.loads(script_json_str)
        except Exception as e:
            print("Failed to parse JSON:", e)
            print("Raw text:", script_json_str)
            sys.exit(1)
            
        with open("script.json", "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        print("script.json successfully created and saved!")

    elif mode in ["audio", "video"]:
        asyncio.run(generate_voiceovers())
        generate_visuals()

    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
