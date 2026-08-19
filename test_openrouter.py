import os
import requests
import json
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

def test_openrouter():
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"')
    if not api_key:
        print("Error: OPENROUTER_API_KEY is not set in your .env file.")
        return

    print("Sending a simple text prompt to OpenRouter (openai/gpt-4o)...")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": "Hello! If you receive this, please reply with 'I am working perfectly!'"
            }
        ]
    }
    
    resp = requests.post(url, headers=headers, json=payload)
    
    print("\n--- OPENROUTER RAW RESPONSE ---")
    print(f"Status Code: {resp.status_code}")
    
    try:
        data = resp.json()
        print(json.dumps(data, indent=2))
        
        if resp.ok:
            print("\n✅ SUCCESS: OpenRouter is working and you have enough credits!")
        else:
            print("\n❌ ERROR: OpenRouter request failed.")
            if resp.status_code == 402:
                print("   Reason: Insufficient Credits. Please top up at https://openrouter.ai/settings/credits")
    except Exception as e:
        print(f"Failed to parse JSON. Raw text: {resp.text}")

if __name__ == "__main__":
    test_openrouter()
