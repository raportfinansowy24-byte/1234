import time
import shutil
import logging
import random
from pathlib import Path

# --- Importy z naszych nowych modułów ---
from optimizer import get_optimizer
from ai_models import generate_ai_content
from sheets import connect_to_sheet

# --- Konfiguracja Logów ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Inicjalizacja katalogów operacyjnych ---
STORAGE_DIR = Path("./generated_outputs")
STORAGE_DIR.mkdir(exist_ok=True)

def process_tasks(sheet, optimizer):
    logger.info("🧐 Sprawdzam zadania w arkuszu...")

    # Okazjonalne czyszczenie starych plików cache
    if int(time.time()) % 10 == 0: 
        optimizer.cleanup_expired_cache()

    naglowki = sheet.row_values(1)
    indeks_status = next((i for i, h in enumerate(naglowki) if h.strip().lower() == 'status'), None)

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
            output_filename = f"video_{task_id}.png"

            # Parametry wideo do obliczenia identyfikatora (hash)
            duration = float(row.get('duration', 5.0)) if row.get('duration') else 5.0
            resolution_raw = str(row.get('resolution', '1024x1024')).lower()
            width, height = 1024, 1024
            if 'x' in resolution_raw:
                try:
                    width, height = map(int, resolution_raw.split('x'))
                except ValueError:
                    pass

            # Próba pobrania gotowego pliku z cache
            cached_file = optimizer.get_cached_video(prompt_tekst, duration, height, width)

            if cached_file:
                shutil.copy(cached_file, output_filename)
                logger.info(f"♻️ [HIT] Skopiowano z cache: {cached_file}")
                sukces = True
            else:
                # Generacja nowego pliku za pomocą AI
                sukces = generate_ai_content(prompt_tekst, output_filename)
                if sukces:
                    persistent_cache_path = STORAGE_DIR / f"hash_{optimizer.hash_video_params(prompt_tekst, duration, height, width)}.png"
                    shutil.copy(output_filename, persistent_cache_path)
                    optimizer.cache_video(prompt_tekst, duration, height, width, str(persistent_cache_path))

            # Aktualizacja arkusza Google
            if sukces:
                sheet.update_cell(i + 2, indeks_status + 1, 'Gotowe')
            else:
                sheet.update_cell(i + 2, indeks_status + 1, 'Błąd API')

# =====================================================================
# GŁÓWNA PĘTLA
# =====================================================================
if __name__ == "__main__":
    # Inicjalizujemy usługi tylko raz podczas startu
    my_sheet = connect_to_sheet()
    my_optimizer = get_optimizer()

    logger.info("🟢 Bot uruchomiony i gotowy do pracy.")

while True:
        try:
            process_tasks(my_sheet, my_optimizer)
            metrics = my_optimizer.get_metrics()
            logger.info(f"📊 Statystyki: Zapisano ${metrics['cost_saved_usd']:.3f} | Hit rate: {metrics['hit_rate_percent']}%")
        except Exception as e:
            logger.error(f"Krytyczny błąd głównej pętli: {e}")

        logger.info("⏱️ Czekam losowy czas przed kolejną weryfikacją...")
        time.sleep(random.randint(60, 180)) # <--- I TU JEST JEGO WYKONANIE
