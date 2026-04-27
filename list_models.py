import google.generativeai as genai
import os

genai.configure(api_key="AIzaSyBud1O2kwl6SxJMgNvFkNTpVz7AOvvKJo4")

print("Available generative models:")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f"- {m.name}")
