import requests
import logging
import os
from dotenv import load_dotenv
from optimizer import get_optimizer

load_dotenv()

logger = logging.getLogger(__name__)

HF_API_TOKEN = os.getenv("HF_API_TOKEN")
HEADERS = {"Authorization": f"Bearer {HF_API_TOKEN}"}
API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"


def generate_ai_content(prompt: str, filename: str) -> bool:
    """Wysyła zapytanie do modelu wizualnego i zapisuje plik."""
    logger.info(f"🔗 [API] Wysyłam zapytanie o obraz/wideo...")
    payload = {"inputs": prompt}
    
    try:
        response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=45)
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(response.content)
            logger.info(f"✨ Sukces! Pobrany plik: {filename}")
            return True
        else:
            logger.error(f"❌ Błąd API: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"💥 Błąd sieciowy: {e}")
        return False

def build_story_prompt(topic: str, narration: dict) -> str:
    """
    Używa Gemini do zbudowania spójnego promptu wideo (18s) dla modelu LTX 2.3.
    Wykorzystuje system cache, aby oszczędzać zapytania do API Gemini.
    """
    optimizer = get_optimizer()
    
    # 1. Sprawdzamy, czy ten scenariusz już kiedyś wygenerowaliśmy (Oszczędność!)
    cached_prompt = optimizer.get_cached_gemini_prompt(topic, narration)
    if cached_prompt:
        return cached_prompt

    # 2. Przygotowanie danych
    hook_text = narration.get("hook", "")
    problem_text = narration.get("problem", "")
    solution_text = narration.get("rozwiązanie", "")

    fallback_prompt = (
        f"Cinematic 18-second financial story about {topic}. "
        "A professional in a modern office environment: first looking stressed at financial documents, "
        "then discovering a solution on a smartphone showing green growth charts, "
        "finally smiling with relief. Consistent character, warm studio lighting, 4K, professional."
    )

    # 3. Jeśli nie ma Gemini, używamy fallback i również go zapisujemy w cache
    if not GEMINI_CLIENT:
        logger.warning("⚠️ GEMINI_CLIENT niedostępny – używam domyślnego (fallback) promptu.")
        optimizer.cache_gemini_prompt(topic, narration, fallback_prompt)
        return fallback_prompt

    # 4. Właściwe zapytanie do Gemini (zostanie aktywowane, gdy dodasz klucz)
    gemini_prompt = f"""You are a professional video director creating a single 18-second cinematic video for LTX 2.3 text-to-video model.

Topic: {topic}
Hook (0-6s): {hook_text}
Problem (6-12s): {problem_text}
Solution (12-18s): {solution_text}

Create ONE cohesive LTX 2.3 video prompt that:
1. Covers all three narrative phases in a single continuous shot or seamless transitions
2. Maintains a consistent character, environment, and visual style throughout
3. Uses professional, cinematic language suitable for a high-quality video model
4. Includes specific visual details (lighting, camera movement, props, colors)
5. Emphasizes the emotional arc: tension → discovery → resolution
6. Is concise but vivid (150-250 words)

Return ONLY the video prompt, no explanations or JSON."""

    try:
        # Symulacja wywołania Gemini (tutaj wpiszemy kod API Google)
        # response = GEMINI_CLIENT.generate_content(gemini_prompt)
        # generated_prompt = response.text.strip()
        
        # Na potrzeby testów, jeśli dojdziesz tutaj, zwróci po prostu przygotowany tekst
        generated_prompt = "Symulowany tekst z Gemini" 
        
        # 5. Zapisujemy wygenerowany scenariusz do cache
        optimizer.cache_gemini_prompt(topic, narration, generated_prompt)
        return generated_prompt
        
    except Exception as e:
        logger.error(f"❌ Błąd podczas odpytywania Gemini: {e}")
        return fallback_prompt
