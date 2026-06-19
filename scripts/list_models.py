import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import google.generativeai as genai

# Using key from environment or fallback to legacy key if desired
try:
    from main import GEMINI_API_KEY
except ImportError:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyBud1O2kwl6SxJMgNvFkNTpVz7AOvvKJo4")

genai.configure(api_key=GEMINI_API_KEY)

print("Available generative models:")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f"- {m.name}")
