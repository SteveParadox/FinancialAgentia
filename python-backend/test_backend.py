"""
Robust test script for streaming LLM backend (/query).
Supports Ollama local/cloud, OpenAI, Anthropic, or plain streaming responses.
"""

import httpx
import json
import sys

# Backend URL (adjust if your backend runs elsewhere)
BACKEND_URL = "http://localhost:8000/query"

def run_backend_smoke(prompt="Hello, can you tell me about financial markets?"):
    """
    Send a test query to the backend and print the streamed response robustly.
    """
    payload = {"prompt": prompt} 
    headers = {"Content-Type": "application/json"}

    try:
        print(f"Sending request to {BACKEND_URL} with prompt: '{prompt}'")
        response = httpx.post(BACKEND_URL, json=payload, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"Error: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            return False

        print("Streaming response:")
        print("-" * 50)

        for line in response.iter_lines():
            if not line:
                continue
            line = line.strip()

            # Handle SSE prefix
            if line.startswith("data: "):
                line = line[6:]

            # Check for termination
            if line == "[DONE]":
                break

            # Try to parse JSON, otherwise fallback to raw text
            try:
                event = json.loads(line)
                token = event.get("token") or event.get("text") or line
                print(token, end="", flush=True)
            except json.JSONDecodeError:
                print(line, end="", flush=True)

        print("\n" + "-" * 50)
        print("Test completed successfully!")
        return True

    except httpx.HTTPError as e:
        print(f"Request failed: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        prompt = "Test the backend with a simple query."

    success = run_backend_smoke(prompt)
    sys.exit(0 if success else 1)
