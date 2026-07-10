import time
import shutil
import logging
import random
from dotenv import load_dotenv
from pathlib import Path
from optimizer import get_optimizer
from ai_models import generate_ai_video  # Zmienione na nową funkcję wideo
from sheets import connect_to_sheet

# Załadowanie zmiennych środowiskowych z pliku .env
load_dotenv()

# --- Poprawna, pojedyncza konfiguracja Logów ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# --- Inicjalizacja katalogów operacyjnych ---
STORAGE_DIR = Path("./generated_outputs")
STORAGE_DIR.mkdir(exist_ok=True)


def build_story_prompts(base_prompt: str) -> list[str]:
    """
    Rozbija bazowy prompt na 3 powiązane sceny (Hook / Problem / Rozwiązanie)
    dla zachowania dynamiki w formacie Shorts.
    """
    # Jeśli prompt ma już podział za pomocą nowych linii lub pionowych kresek, używamy go
    if "\n" in base_prompt:
        parts = [p.strip() for p in base_prompt.split("\n") if p.strip()]
    elif "|" in base_prompt:
        parts = [p.strip() for p in base_prompt.split("|") if p.strip()]
    else:
        parts = []

    if len(parts) >= 3:
        return parts[:3]

    # Fallback: jeśli prompt to jeden ciąg tekstowy, tworzymy logiczną sekwencję 3 ujęć
    return [
        f"{base_prompt}, dynamic opening hook scene, high visual impact, cinematic 9:16 vertical",
        f"{base_prompt}, core topic explanation, detailed macro view, cinematic 9:16 vertical",
        f"{base_prompt}, final conclusion, clean professional outro, cinematic 9:16 vertical",
    ]


def process_tasks(sheet, optimizer):
    logger.info("🧐 Sprawdzam zadania w arkuszu...")

    # Okazjonalne czyszczenie starych plików cache
    if int(time.time()) % 10 == 0:
        optimizer.cleanup_expired_cache()

    # Naprawiony i rozdzielony blok pobierania nagłówków
    naglowki = sheet.row_values(1)
    indeks_status = next(
        (i for i, h in enumerate(naglowki) if h.strip().lower() == "status"), None
    )
    indeks_seed = next(
        (i for i, h in enumerate(naglowki) if h.strip().lower() == "seed"), None
    )

    if indeks_status is None:
        nowa_kolumna_num = len(naglowki) + 1
        sheet.update_cell(1, nowa_kolumna_num, "status")
        naglowki = sheet.row_values(1)
        indeks_status = nowa_kolumna_num - 1

    data = sheet.get_all_records()
    if not data:
        return

    kolumna_status_nazwa = naglowki[indeks_status]

    for i, row in enumerate(data):
        wartosc_status = str(row.get(kolumna_status_nazwa, "")).strip()

        if wartosc_status == "Do zrobienia" or wartosc_status == "":
            # ... reszta Twojego kodu ..
            task_id = row.get("id", f"task_{i}")
            prompt_tekst = str(row.get("prompt", "")).strip()

            if not prompt_tekst or prompt_tekst.startswith("http"):
                logger.warning(
                    f"⚠️ Pominięto zadanie ID: {task_id} (błędny lub pusty prompt)"
                )
                sheet.update_cell(i + 2, indeks_status + 1, "Błąd - Pusty lub URL")
                continue

            logger.info(f"\n🚀 Przetwarzanie ID: {task_id}")

            # Zmiana wyjścia na format wideo MP4 (Shorts)
            output_path = STORAGE_DIR / f"video_{task_id}.mp4"

            # 1. Przygotowanie 3 scen (lokalnie, bez użycia API)
            sceny_prompts = build_story_prompts(prompt_tekst)
            logger.info("✅ Wygenerowano strukturę storyboardu (3 sceny).")

            # 2. Rezerwacja zadania w arkuszu, by uniknąć konfliktów
            sheet.update_cell(i + 2, indeks_status + 1, "Generowanie...")

            # 3. Wywołanie silnika LTX-Video z automatyczną rotacją kluczy Hugging Face
            import os

            # Pobieramy klucze ze zmiennych środowiskowych
            lista_kluczy = [
                os.environ.get("HF_TOKEN_1"),
                os.environ.get("HF_TOKEN_2"),
                os.environ.get("HF_TOKEN_3"),
            ]
            # Odrzucamy puste wartości
            lista_kluczy = [k.strip() for k in lista_kluczy if k and k.strip()]

            # Jeśli nie znaleziono dedykowanych tokenów, spróbujmy użyć domyślnego HF_TOKEN
            if not lista_kluczy and os.environ.get("HF_TOKEN"):
                lista_kluczy = [os.environ.get("HF_TOKEN").strip()]

            final_video_cache = None
            used_seed = None
            temp_cache_file = f"cache/temp_render_{task_id}.mp4"

            if not lista_kluczy:
                logger.warning("⚠️ Brak jakichkolwiek kluczy HF_TOKEN w zmiennych środowiskowych. Próba bez klucza...")
                try:
                    final_video_cache, used_seed = generate_ai_video(
                        sceny_prompts, output_cache_path=temp_cache_file
                    )
                except Exception as e:
                    logger.error(f"💥 Błąd podczas generowania wideo: {e}")
            else:
                for idx, klucz in enumerate(lista_kluczy):
                    logger.info(f"🔑 Próba generowania wideo z kluczem {idx + 1}/{len(lista_kluczy)}...")
                    os.environ["HF_TOKEN"] = klucz
                    try:
                        final_video_cache, used_seed = generate_ai_video(
                            sceny_prompts, output_cache_path=temp_cache_file
                        )
                        if final_video_cache and Path(final_video_cache).exists():
                            logger.info(f"✅ Sukces z kluczem {idx + 1}!")
                            break
                        else:
                            logger.warning(f"⚠️ Klucz {idx + 1} nie wygenerował wideo. Próbuję następny...")
                    except Exception as e:
                        logger.error(f"💥 Błąd na kluczu {idx + 1}: {e}")
                        continue

            # 4. Finalizacja i aktualizacja danych w bazie (Google Sheets)
            if final_video_cache and Path(final_video_cache).exists():
                shutil.move(final_video_cache, output_path)
                logger.info(
                    f"✅ Sukces! Pełny materiał dla ID {task_id} zapisany w: {output_path}"
                )

                # Zapis statusu sukcesu
                sheet.update_cell(i + 2, indeks_status + 1, "Gotowe")

                # Dynamiczny zapis użytego ziarna (seed), jeśli kolumna istnieje w arkuszu
                if indeks_seed is not None:
                    sheet.update_cell(i + 2, indeks_seed + 1, str(used_seed))
                    logger.info(
                        f"💾 Seed {used_seed} został poprawnie zalogowany w arkuszu."
                    )
            else:
                logger.error(
                    f"❌ Wszystkie klucze wyczerpane lub błąd generowania wideo dla zadania {task_id}!"
                )
                sheet.update_cell(i + 2, indeks_status + 1, "Błąd - Limit API")


if __name__ == "__main__":
    try:
        logger.info("🤖 Inicjalizacja automatyzacji bota wideo...")
        arkusz = connect_to_sheet()
        optymalizator = get_optimizer()

        while True:
            process_tasks(arkusz, optymalizator)
            logger.info("😴 Cykl wjechany czekamy...")
            time.sleep(300)

    except KeyboardInterrupt:
        logger.info("🛑 Zatrzymano bota na żądanie użytkownika.")
    except Exception as e:
        logger.critical(f"💥 Krytyczna awaria głównej pętli bota: {e}")
