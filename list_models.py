import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

def list_gemini_models():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Erro: GEMINI_API_KEY não encontrada no .env")
        return

    try:
        from google import genai
    except ImportError:
        print("Erro: instale google-genai com `pip install -r requirements.txt`")
        return

    client = genai.Client(api_key=api_key)
    print("Modelos disponíveis para generateContent:")
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or getattr(model, "supported_generation_methods", [])
        if not actions or "generateContent" in actions:
            print(f"- {model.name}")


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
