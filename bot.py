import time
import shutil
import logging
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
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


async def process_single_task(sheet, optimizer, i: int, row: dict, naglowki: list, indeks_status: int, indeks_seed: int) -> bool:
    """
    Process a single task asynchronously.
    
    Returns True if task was successfully processed, False otherwise.
    """
    try:
        kolumna_status_nazwa = naglowki[indeks_status]
        wartosc_status = str(row.get(kolumna_status_nazwa, "")).strip()

        if wartosc_status != "Do zrobienia" and wartosc_status != "":
            return False  # Skip non-pending tasks

        task_id = row.get("id", f"task_{i}")
        prompt_tekst = str(row.get("prompt", "")).strip()

        if not prompt_tekst or prompt_tekst.startswith("http"):
            logger.warning(f"⚠️ Pominięto zadanie ID: {task_id} (błędny lub pusty prompt)")
            sheet.update_cell(i + 2, indeks_status + 1, "Błąd - Pusty lub URL")
            return False

        logger.info(f"\n🚀 Przetwarzanie ID: {task_id}")
        output_path = STORAGE_DIR / f"video_{task_id}.mp4"

        # 1. Check cache FIRST before generating
        cached_video = optimizer.get_cached_video(
            prompt=prompt_tekst, duration=4, height=512, width=512
        )
        if cached_video:
            logger.info(f"✅ Użycie cache dla zadania {task_id}")
            shutil.copy(cached_video, output_path)
            sheet.update_cell(i + 2, indeks_status + 1, "Gotowe")
            if indeks_seed is not None:
                sheet.update_cell(i + 2, indeks_seed + 1, "cached")
            return True

        # 2. Build story structure (3 scenes)
        sceny_prompts = build_story_prompts(prompt_tekst)
        logger.info("✅ Wygenerowano strukturę storyboardu (3 sceny).")

        # 3. Reserve task in sheet
        sheet.update_cell(i + 2, indeks_status + 1, "Generowanie...")

        # 4. Get HF token
        hf_token = os.environ.get("HF_TOKEN")
        if not hf_token:
            logger.warning("⚠️ Brak HF_TOKEN w zmiennych środowiskowych!")
            sheet.update_cell(i + 2, indeks_status + 1, "Błąd - Brak tokena")
            return False

        temp_cache_file = f"cache/temp_render_{task_id}.mp4"

        # 5. Generate video asynchronously
        try:
            logger.info(f"🎬 Wysyłam zadanie {task_id} do ZeroGPU...")
            final_video_cache, used_seed = await generate_ai_video(
                sceny_prompts, output_cache_path=temp_cache_file
            )

            if final_video_cache and Path(final_video_cache).exists():
                shutil.move(final_video_cache, output_path)
                logger.info(f"✅ Sukces! Pełny materiał dla ID {task_id} zapisany w: {output_path}")

                # Cache the video for future use
                optimizer.cache_video(
                    prompt=prompt_tekst, duration=4, height=512, width=512,
                    video_url_or_path=str(output_path), ttl_hours=168
                )

                sheet.update_cell(i + 2, indeks_status + 1, "Gotowe")

                if indeks_seed is not None and used_seed:
                    sheet.update_cell(i + 2, indeks_seed + 1, str(used_seed))
                    logger.info(f"💾 Seed {used_seed} zalogowany w arkuszu.")
                
                return True
            else:
                logger.error(f"❌ Generowanie wideo nie powiodło się dla zadania {task_id}")
                sheet.update_cell(i + 2, indeks_status + 1, "Błąd - Generacja")
                return False

        except Exception as e:
            logger.error(f"💥 Błąd podczas generowania wideo dla {task_id}: {e}")
            sheet.update_cell(i + 2, indeks_status + 1, f"Błąd - {str(e)[:30]}")
            return False

    except Exception as e:
        logger.error(f"💥 Nieoczekiwany błąd w process_single_task: {e}")
        return False


async def process_tasks_async(sheet, optimizer):
    """
    Async task processor - allows processing tasks efficiently.
    """
    logger.info("🧐 Sprawdzam zadania w arkuszu...")

    # Occasional cache cleanup
    if int(time.time()) % 10 == 0:
        deleted = optimizer.cleanup_expired_cache()
        if deleted > 0:
            logger.info(f"🧹 Wyczyszczono {deleted} wygasłych wpisów cache")

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
        logger.info("📊 Brak zadań w arkuszu.")
        return

    # Process tasks sequentially (safer for API limits, but can be made parallel if needed)
    tasks_processed = 0
    for i, row in enumerate(data):
        success = await process_single_task(sheet, optimizer, i, row, naglowki, indeks_status, indeks_seed)
        if success:
            tasks_processed += 1

    # Log metrics
    metrics = optimizer.get_metrics()
    logger.info(
        f"📊 Statystyki: Oszczędzono ${metrics['cost_saved_usd']:.2f} | "
        f"Hit rate: {metrics['hit_rate_percent']:.1f}% | "
        f"Zadań przetworzonych: {tasks_processed}"
    )


def run_bot_loop():
    """
    Main bot loop running async task processor.
    """
    try:
        logger.info("🤖 Inicjalizacja automatyzacji bota wideo...")
        arkusz = connect_to_sheet()
        optymalizator = get_optimizer()
        logger.info("✅ Bot uruchomiony i gotowy do pracy.")

        poll_interval = 300  # 5 minutes
        while True:
            try:
                # Run async task processor
                asyncio.run(process_tasks_async(arkusz, optymalizator))
            except Exception as e:
                logger.error(f"❌ Błąd w cyklu przetwarzania: {e}")
            
            logger.info(f"😴 Następna weryfikacja za {poll_interval}s...")
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        logger.info("🛑 Zatrzymano bota na żądanie użytkownika.")
    except Exception as e:
        logger.critical(f"💥 Krytyczna awaria głównej pętli bota: {e}")
    finally:
        optymalizator.close()
        logger.info("🔌 Połączenia zamknięte. Bot zatrzymany.")


if __name__ == "__main__":
    run_bot_loop()
