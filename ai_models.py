import time
import logging
import os
import shutil
import subprocess
import random
from gradio_client import Client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_NEGATIVE_PROMPT = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, "
    "watermark, logo, duplicate, deformed hands, extra fingers, artifacts"
)

def _generate_single_scene(client: Client, prompt: str, duration: int, seed: int, output_path: str) -> bool:
    max_retries = 10
    wait_time_seconds = 600  
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Generowanie sceny -> {output_path} (Próba {attempt + 1}/{max_retries})...")
            
            # Nowa, zaktualizowana struktura zapytań dla API LTX-Video-ZeroGPU-Optimized
            result = client.predict(
                prompt=prompt,
                negative_prompt=DEFAULT_NEGATIVE_PROMPT,
                input_image_filepath=None,
                input_video_filepath=None,
                height_ui=1280,
                width_ui=704,
                mode="text-to-video",
                duration_ui=duration,
                ui_frames_to_use=9,
                seed_ui=seed,
                randomize_seed=False,
                ui_guidance_scale=2,
                improve_texture_flag=True,
                slow_motion_flag=False,
                api_name="/text_to_video"
            )
            
            # API zwraca teraz tuplę: (slownik_z_wideo, seed)
            if result and len(result) > 0:
                video_data = result[0]
                
                # Wyciągnięcie ścieżki w zależności od tego, jak dokładnie Gradio ją pakuje
                video_filepath = video_data.get('video') if isinstance(video_data, dict) else video_data
                
                if video_filepath and os.path.exists(video_filepath):
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    shutil.copy(video_filepath, output_path)
                    return True
                else:
                    logger.error(f"API zwróciło pustą lub nieprawidłową ścieżkę: {video_filepath}")
            else:
                logger.error("Brak odpowiedzi od API (pusty wynik).")
                
        except Exception as e:
            error_msg = str(e).lower()
            if any(err in error_msg for err in ["queue full", "gpu busy", "capacity", "rate limit", "time limit"]):
                logger.warning(f"Serwer przeciążony. Odczekam 10 minut. Szczegóły: {e}")
                time.sleep(wait_time_seconds)
            else:
                logger.error(f"Nieoczekiwany błąd API: {e}")
                time.sleep(60)
                
    return False

def generate_ai_video(prompts: list[str], output_cache_path: str = "cache/final_output.mp4") -> tuple[str, int]:
    if not prompts:
        logger.error("Otrzymano pustą listę promptów.")
        return None, None

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        logger.warning("Brak HF_TOKEN w zmiennych środowiskowych. Wywołania ZeroGPU mogą być limitowane.")

    client = Client("DeepRat/LTX-Video-ZeroGPU-Optimized", token=hf_token)
    cache_dir = os.path.dirname(output_cache_path) or "cache"
    os.makedirs(cache_dir, exist_ok=True)
    
    temp_files = []
    master_seed = random.randint(0, 2147483647)
    logger.info(f"Wygenerowano seed dla produkcji: {master_seed}")
    
    for i, prompt in enumerate(prompts):
        scene_path = os.path.join(cache_dir, f"scene_{i+1}.mp4")
        logger.info(f"--- Start generowania sceny {i+1}/{len(prompts)} ---")
        
        # LTX domyślnie generuje krótkie ujęcia, podajemy 6, ale API najpewniej przytnie to wg. swoich limitów.
        success = _generate_single_scene(client, prompt, duration=6, seed=master_seed, output_path=scene_path)
        
        if not success:
            logger.critical(f"Nie udało się wygenerować sceny {i+1}.")
            return None, master_seed
        
        temp_files.append(scene_path)

    logger.info("Wszystkie sceny wygenerowane. Rozpoczynam łączenie wideo przez FFmpeg...")
    concat_list_path = os.path.join(cache_dir, "concat_list.txt")
    
    with open(concat_list_path, "w") as f:
        for temp_file in temp_files:
            abs_path = os.path.abspath(temp_file)
            f.write(f"file '{abs_path}'\n")
            
    try:
        command = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy", output_cache_path
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info(f"Sukces! Plik zapisany w: {output_cache_path}")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Błąd FFmpeg: {e}")
        return None, master_seed
        
    finally:
        if os.path.exists(concat_list_path):
            os.remove(concat_list_path)
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                
    return output_cache_path, master_seed
