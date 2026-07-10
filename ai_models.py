import time
import logging
import os
import shutil
import subprocess
import random
import asyncio
from gradio_client import Client
from typing import Tuple, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_NEGATIVE_PROMPT = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, "
    "watermark, logo, duplicate, deformed hands, extra fingers, artifacts"
)

def _check_ffmpeg_installed() -> bool:
    """Verify FFmpeg is installed and accessible."""
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("❌ FFmpeg not found. Install it with: apt-get install ffmpeg (Linux) or brew install ffmpeg (Mac)")
        return False

def _exponential_backoff(attempt: int, base_wait: int = 30, max_wait: int = 600) -> int:
    """Calculate exponential backoff with jitter (30s → 1m → 2m → ...) capped at 10 min."""
    wait_time = min(base_wait * (2 ** attempt), max_wait)
    jitter = random.uniform(0, wait_time * 0.1)
    return int(wait_time + jitter)

async def _generate_single_scene(client: Client, prompt: str, duration: int, seed: int, output_path: str) -> Tuple[bool, Optional[int]]:
    """Generate a single video scene with exponential backoff retry logic."""
    max_retries = 5  # Reduced from 10 to 5 attempts
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Generowanie sceny -> {output_path} (Próba {attempt + 1}/{max_retries})...")
            
            # Updated API structure for LTX-Video-ZeroGPU-Optimized
            result = client.predict(
                prompt=prompt,
                negative_prompt=DEFAULT_NEGATIVE_PROMPT,
                input_image_filepath=None,
                input_video_filepath=None,
                height_ui=704,
                width_ui=512,
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
            
            # API returns tuple: (video_dict, seed)
            if result and len(result) > 0:
                video_data = result[0]
                video_filepath = video_data.get('video') if isinstance(video_data, dict) else video_data
                
                if video_filepath and os.path.exists(video_filepath):
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    shutil.copy(video_filepath, output_path)
                    return True, seed
                else:
                    logger.error(f"API zwróciło pustą lub nieprawidłową ścieżkę: {video_filepath}")
            else:
                logger.error("Brak odpowiedzi od API (pusty wynik).")
                
        except Exception as e:
            error_msg = str(e).lower()
            if any(err in error_msg for err in ["queue full", "gpu busy", "capacity", "rate limit", "time limit"]):
                wait_time = _exponential_backoff(attempt)
                logger.warning(f"Serwer przeciążony. Odczekam {wait_time}s. Szczegóły: {e}")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Nieoczekiwany błąd API: {e}")
                await asyncio.sleep(5)
                
    return False, None

async def generate_ai_video(prompts: list[str], output_cache_path: str = "cache/final_output.mp4") -> Tuple[Optional[str], Optional[int]]:
    """Generate AI video with parallel scene generation."""
    if not prompts:
        logger.error("Otrzymano pustą listę promptów.")
        return None, None

    # Preflight checks
    if not _check_ffmpeg_installed():
        return None, None

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        logger.warning("Brak HF_TOKEN w zmiennych środowiskowych. Wywołania ZeroGPU mogą być limitowane.")

    client = Client("DeepRat/LTX-Video-ZeroGPU-Optimized", token=hf_token)
    cache_dir = os.path.dirname(output_cache_path) or "cache"
    os.makedirs(cache_dir, exist_ok=True)
    
    logger.info("Rozpoczynanie generowania produkcji z unikalnymi seedami dla każdej sceny.")
    
    # Generate seeds for all scenes
    scene_seeds = [random.randint(0, 2147483647) for _ in prompts]
    
    # Create tasks for parallel execution
    tasks = []
    for i, (prompt, seed) in enumerate(zip(prompts, scene_seeds)):
        scene_path = os.path.join(cache_dir, f"scene_{i+1}.mp4")
        logger.info(f"--- Start generowania sceny {i+1}/{len(prompts)} ---")
        tasks.append(_generate_single_scene(client, prompt, duration=2, seed=seed, output_path=scene_path))
    
    # Run all scene generations in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    temp_files = []
    last_seed = None
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.critical(f"Nie udało się wygenerować sceny {i+1}: {result}")
            return None, last_seed
        
        success, seed = result
        if not success:
            logger.critical(f"Nie udało się wygenerować sceny {i+1}.")
            return None, last_seed
        
        last_seed = seed
        scene_path = os.path.join(cache_dir, f"scene_{i+1}.mp4")
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
        # Restore stderr/stdout for debugging
        subprocess.run(command, check=True)
        logger.info(f"Sukces! Plik zapisany w: {output_cache_path}")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Błąd FFmpeg: {e}")
        return None, last_seed
        
    finally:
        if os.path.exists(concat_list_path):
            os.remove(concat_list_path)
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                
    return output_cache_path, last_seed
