import time
import shutil
import logging
import random
import asyncio
import threading
from dotenv import load_dotenv
from pathlib import Path
from optimizer import get_optimizer
from ai_models import generate_ai_video
from sheets import connect_to_sheet

# Load environment variables
load_dotenv()

# --- Logging configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# --- Initialize operational directories ---
STORAGE_DIR = Path("./generated_outputs")
STORAGE_DIR.mkdir(exist_ok=True)

# --- Cache headers to avoid repeated lookups ---
_sheet_headers_cache = None
_headers_cache_time = 0
HEADERS_CACHE_TTL = 300  # 5 minutes

def get_sheet_headers(sheet, force_refresh: bool = False) -> list[str]:
    """Get sheet headers with caching to reduce API calls."""
    global _sheet_headers_cache, _headers_cache_time
    
    current_time = time.time()
    if force_refresh or _sheet_headers_cache is None or (current_time - _headers_cache_time) > HEADERS_CACHE_TTL:
        _sheet_headers_cache = sheet.row_values(1)
        _headers_cache_time = current_time
        logger.debug(f"🔄 Sheet headers refreshed from API")
    
    return _sheet_headers_cache

def build_story_prompts(base_prompt: str) -> list[str]:
    """
    Split base prompt into 3 related scenes (Hook / Problem / Solution)
    to maintain dynamics in Shorts format.
    """
    # If prompt already has line breaks or pipes, use them
    if "\n" in base_prompt:
        parts = [p.strip() for p in base_prompt.split("\n") if p.strip()]
    elif "|" in base_prompt:
        parts = [p.strip() for p in base_prompt.split("|") if p.strip()]
    else:
        parts = []

    if len(parts) >= 3:
        return parts[:3]

    # Fallback: create logical 3-shot sequence from single string
    return [
        f"{base_prompt}, dynamic opening hook scene, high visual impact, cinematic 9:16 vertical",
        f"{base_prompt}, core topic explanation, detailed macro view, cinematic 9:16 vertical",
        f"{base_prompt}, final conclusion, clean professional outro, cinematic 9:16 vertical",
    ]


async def process_tasks_async(sheet, optimizer):
    """Async task processor - non-blocking video generation."""
    logger.info("🧐 Sprawdzam zadania w arkuszu...")

    # Occasional cache cleanup
    if int(time.time()) % 10 == 0:
        deleted = optimizer.cleanup_expired_cache()

    # Get headers ONCE (moved outside loop)
    naglowki = get_sheet_headers(sheet)
    indeks_status = next(
        (i for i, h in enumerate(naglowki) if h.strip().lower() == "status"), None
    )
    indeks_seed = next(
        (i for i, h in enumerate(naglowki) if h.strip().lower() == "seed"), None
    )

    if indeks_status is None:
        nowa_kolumna_num = len(naglowki) + 1
        sheet.update_cell(1, nowa_kolumna_num, "status")
        naglowki = get_sheet_headers(sheet, force_refresh=True)
        indeks_status = nowa_kolumna_num - 1

    data = sheet.get_all_records()
    if not data:
        return

    kolumna_status_nazwa = naglowki[indeks_status]

    for i, row in enumerate(data):
        wartosc_status = str(row.get(kolumna_status_nazwa, "")).strip()

        if wartosc_status == "Do zrobienia" or wartosc_status == "":
            task_id = row.get("id", f"task_{i}")
            prompt_tekst = str(row.get("prompt", "")).strip()

            if not prompt_tekst or prompt_tekst.startswith("http"):
                logger.warning(
                    f"⚠️ Pominięto zadanie ID: {task_id} (błędny lub pusty prompt)"
                )
                sheet.update_cell(i + 2, indeks_status + 1, "Błąd - Pusty lub URL")
                continue

            logger.info(f"\n🚀 Przetwarzanie ID: {task_id}")
            output_path = STORAGE_DIR / f"video_{task_id}.mp4"

            # 1. Check cache FIRST before generating
            cached_video = optimizer.get_cached_video(
                prompt=prompt_tekst, duration=2, height=704, width=512
            )
            if cached_video:
                logger.info(f"✅ Using cached video for task {task_id}")
                shutil.copy(cached_video, output_path)
                sheet.update_cell(i + 2, indeks_status + 1, "Gotowe")
                continue

            # 2. Build story structure (3 scenes)
            sceny_prompts = build_story_prompts(prompt_tekst)
            logger.info("✅ Wygenerowano strukturę storyboardu (3 sceny).")

            # 3. Reserve task in sheet
            sheet.update_cell(i + 2, indeks_status + 1, "Generowanie...")

            # 4. Get HF tokens with fallback
            import os
            lista_kluczy = [
                os.environ.get("HF_TOKEN_1"),
                os.environ.get("HF_TOKEN_2"),
                os.environ.get("HF_TOKEN_3"),
            ]
            lista_kluczy = [k.strip() for k in lista_kluczy if k and k.strip()]

            if not lista_kluczy and os.environ.get("HF_TOKEN"):
                lista_kluczy = [os.environ.get("HF_TOKEN").strip()]

            final_video_cache = None
            used_seed = None
            temp_cache_file = f"cache/temp_render_{task_id}.mp4"

            if not lista_kluczy:
                logger.warning("⚠️ Brak jakichkolwiek kluczy HF_TOKEN w zmiennych środowiskowych. Próba bez klucza...")
                try:
                    final_video_cache, used_seed = await generate_ai_video(
                        sceny_prompts, output_cache_path=temp_cache_file
                    )
                except Exception as e:
                    logger.error(f"💥 Błąd podczas generowania wideo: {e}")
            else:
                for idx, klucz in enumerate(lista_kluczy):
                    logger.info(f"🔑 Próba generowania wideo z kluczem {idx + 1}/{len(lista_kluczy)}...")
                    os.environ["HF_TOKEN"] = klucz
                    try:
                        final_video_cache, used_seed = await generate_ai_video(
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

            # 5. Finalize and update sheet
            if final_video_cache and Path(final_video_cache).exists():
                shutil.move(final_video_cache, output_path)
                logger.info(
                    f"✅ Sukces! Pełny materiał dla ID {task_id} zapisany w: {output_path}"
                )

                # Cache the video for future use
                optimizer.cache_video(
                    prompt=prompt_tekst, duration=2, height=704, width=512,
                    video_url_or_path=str(output_path), ttl_hours=168
                )

                sheet.update_cell(i + 2, indeks_status + 1, "Gotowe")

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


def run_bot_loop():
    """Main bot loop running in background thread."""
    try:
        logger.info("🤖 Inicjalizacja automatyzacji bota wideo...")
        arkusz = connect_to_sheet()
        optymalizator = get_optimizer()

        while True:
            try:
                # Run async task processor
                asyncio.run(process_tasks_async(arkusz, optymalizator))
            except Exception as e:
                logger.error(f"❌ Błąd w cyklu przetwarzania: {e}")
            
            logger.info("😴 Cykl wjechany czekamy...")
            time.sleep(300)  # 5-minute interval

    except KeyboardInterrupt:
        logger.info("🛑 Zatrzymano bota na żądanie użytkownika.")
    except Exception as e:
        logger.critical(f"💥 Krytyczna awaria głównej pętli bota: {e}")


if __name__ == "__main__":
    run_bot_loop()
