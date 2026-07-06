import time
import shutil
import logging
import random
from dotenv import load_dotenv
from pathlib import Path
from optimizer import get_optimizer
from ai_models import generate_ai_video  # Zmienione na nową funkcję wideo
from sheets import connect_to_sheet

# --- Poprawna, pojedyncza konfiguracja Logów ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
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
        f"{base_prompt}, final conclusion, clean professional outro, cinematic 9:16 vertical"
    ]

def process_tasks(sheet, optimizer):
    logger.info("🧐 Sprawdzam zadania w arkuszu...")

    # Okazjonalne czyszczenie starych plików cache
    if int(time.time()) % 10 == 0: 
        optimizer.cleanup_expired_cache()      
        
    # Naprawiony i rozdzielony blok pobierania nagłówków
    naglowki = sheet.row_values(1)
    indeks_status = next((i for i, h in enumerate(naglowki) if h.strip().lower() == 'status'), None)
    indeks_seed = next((i for i, h in enumerate(naglowki) if h.strip().lower() == 'seed'), None)

    if indeks_status is None:
        nowa_kolumna_num = len(naglowki) + 1
        sheet.update_cell(1, nowa_kolumna_num, 'status')
        naglowki = sheet.row_values(1)
        indeks_status = nowa_kolumna_num - 1

    data = sheet.get_all_records()
    if not data:
        return

    kolumna_status_nazwa = naglowki[indeks_status]

    for i, row in enumerate(data):
        wartosc_status = str(row.get(kolumna_status_nazwa, '')).strip()

        if wartosc_status == 'Do zrobienia' or wartosc_status == '':
            task_id = row.get('id', f"task_{i}")
            prompt_tekst = str(row.get('prompt', '')).strip()

            if not prompt_tekst or prompt_tekst.startswith("http"):
                logger.warning(f"⚠️ Pominięto zadanie ID: {task_id} (błędny lub pusty prompt)")
                sheet.update_cell(i + 2, indeks_status + 1, 'Błąd - Pusty lub URL')
                continue

            logger.info(f"\n🚀 Przetwarzanie ID: {task_id}")
            
            # Zmiana wyjścia na format wideo MP4 (Shorts)
            output_path = STORAGE_DIR / f"video_{task_id}.mp4"
            
            # 1. Przygotowanie 3 scen
            sceny_prompts = build_story_prompts(prompt_tekst)
            logger.info(f"Wygenerowano strukturę storyboardu (3 sceny po 6s).")

            # 2. Rezerwacja zadania w arkuszu, by uniknąć konfliktów
            sheet.update_cell(i + 2, indeks_status + 1, 'Generowanie...')

            # 3. Wywołanie silnika LTX-Video z automatycznym kolejkowaniem i seedowaniem
            temp_cache_file = f"cache/temp_render_{task_id}.mp4"
            final_video_cache, used_seed = generate_ai_video(sceny_prompts, output_cache_path=temp_cache_file)

            # 4. Finalizacja i aktualizacja danych w bazie (Google Sheets)
            if final_video_cache and Path(final_video_cache).exists():
                shutil.move(final_video_cache, output_path)
                logger.info(f"✅ Sukces! Pełny materiał dla ID {task_id} zapisany w: {output_path}")
                
                # Zapis statusu sukcesu
                sheet.update_cell(i + 2, indeks_status + 1, 'Gotowe')
                
                # Dynamiczny zapis użytego ziarna (seed), jeśli kolumna istnieje w arkuszu
                if indeks_seed is not None:
                    sheet.update_cell(i + 2, indeks_seed + 1, str(used_seed))
                    logger.info(f"💾 Seed {used_seed} został poprawnie zalogowany w arkuszu.")
            else:
                logger.error(f"❌ Proces generowania wideo dla ID {task_id} zakończył się niepowodzeniem.")
                sheet.update_cell(i + 2, indeks_status + 1, 'Błąd generowania')

if __name__ == "__main__":
    try:
        logger.info("🤖 Inicjalizacja automatyzacji bota wideo...")
        arkusz = connect_to_sheet()
        optymalizator = get_optimizer()
        
        while True:
            process_tasks(arkusz, optymalizator)
            logger.info("😴 Cykl zakończony. Odpoczynek 60 sekund przed kolejnym sprawdzeniem...")
            time.sleep(60)
            
    except KeyboardInterrupt:
        logger.info("🛑 Zatrzymano bota na żądanie użytkownika.")
    except Exception as e:
        logger.critical(f"💥 Krytyczna awaria głównej pętli bota: {e}")
