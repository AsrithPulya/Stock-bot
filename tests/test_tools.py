import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import os
from google import genai
from google.genai import types

def get_current_weather(location: str) -> str:
    """Gets the current weather in a given location."""
    return f"The weather in {location} is 72 degrees and sunny."

client = genai.Client()

def test_tool_calling():
    print("Testing tool calling...")
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='What is the weather in Paris?',
        config=types.GenerateContentConfig(
            tools=[get_current_weather],
            temperature=0,
        )
    )
    print("Response text:", response.text)
    print("Function calls:", response.function_calls)

if __name__ == "__main__":
    test_tool_calling()
