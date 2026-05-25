import google.generativeai as genai
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

def list_gemini_models():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Erro: GEMINI_API_KEY não encontrada no .env")
        return
    
    genai.configure(api_key=api_key)
    print("Modelos disponíveis para generateContent:")
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"- {m.name}")


def list_deepseek_models():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("Erro: DEEPSEEK_API_KEY não encontrada no .env")
        return

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    print("Modelos DeepSeek disponíveis:")
    for m in client.models.list().data:
        print(f"- {m.id}")


def list_models():
    list_gemini_models()
    print()
    list_deepseek_models()


if __name__ == "__main__":
    list_models()
