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

    print("Modelos disponíveis para generateContent:")
    with genai.Client(api_key=api_key, http_options={"timeout": 30000}) as client:
        for model in client.models.list():
            actions = getattr(model, "supported_actions", None) or getattr(model, "supported_generation_methods", [])
            if "generateContent" in actions:
                print(f"- {model.name}")


def list_deepseek_models():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("Erro: DEEPSEEK_API_KEY não encontrada no .env")
        return

    print("Modelos DeepSeek disponíveis:")
    with OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=30, max_retries=0) as client:
        for m in client.models.list().data:
            print(f"- {m.id}")


def list_models():
    for name, list_provider_models in (("Gemini", list_gemini_models), ("DeepSeek", list_deepseek_models)):
        try:
            list_provider_models()
        except Exception as exc:
            print(f"Erro ao listar modelos {name}: {type(exc).__name__}")
        print()


if __name__ == "__main__":
    list_models()
