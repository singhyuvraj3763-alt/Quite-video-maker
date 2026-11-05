import os
import sys
import json
import time
from google import genai
from google.genai import types

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY environment variable is missing!")
    sys.exit(1)

client = genai.Client(api_key=api_key)

def generate_script(topic, num_scenes=24):
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

    # Retry loop to automatically handle temporary 503 capacity spikes
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

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "script"
    topic = sys.argv[2] if len(sys.argv) > 2 else "The Mahindra story in Hindi"

    if mode == "script":
        script_json_str = generate_script(topic)
        
        try:
            parsed = json.loads(script_json_str)
        except Exception as e:
            print("Failed to parse JSON from Gemini response. Raw text was:")
            print(script_json_str)
            sys.exit(1)
            
        with open("script.json", "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
            
        print("script.json successfully created and saved!")
    else:
        print("Video rendering mode selected.")
