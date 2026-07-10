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
        logger.debug("✅ FFmpeg found and available.")
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
    """
    Generate a single video scene using ZeroGPU LTX-Video Space.
    
    The official ZeroGPU LTX-Video Space expects:
    - prompt: str (required) - the video description
    - negative_prompt: str - what NOT to generate
    - duration: int (in seconds, 2-16 recommended for ZeroGPU)
    - seed: int (for reproducibility)
    """
    max_retries = 5
    
    for attempt in range(max_retries):
        try:
            logger.info(f"🎬 Generowanie sceny → {output_path} (Próba {attempt + 1}/{max_retries})...")
            
            # Official ZeroGPU LTX-Video API - simplified call structure
            # The Space handles all parameters internally
            result = client.predict(
                prompt=prompt,
                negative_prompt=DEFAULT_NEGATIVE_PROMPT,
                duration=duration,
                seed=seed,
                api_name="/generate"
            )
            
            # Handle the response from ZeroGPU Space
            # Result is typically (video_path, seed_used)
            if result:
                # Check if it's a tuple or list
                if isinstance(result, (list, tuple)) and len(result) > 0:
                    video_filepath = result[0]
                # If it's a dict with 'video' key
                elif isinstance(result, dict):
                    video_filepath = result.get('video')
                else:
                    video_filepath = result
                
                # Ensure it's a string path
                video_filepath = str(video_filepath) if video_filepath else None
                
                if video_filepath and os.path.exists(video_filepath):
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    shutil.copy(video_filepath, output_path)
                    logger.info(f"✅ Scena wygenerowana pomyślnie: {output_path}")
                    return True, seed
                else:
                    logger.warning(f"⚠️ API zwróciło nieprawidłową ścieżkę: {video_filepath}")
            else:
                logger.warning("⚠️ API zwróciło pustą odpowiedź, retry...")
                
        except Exception as e:
            error_msg = str(e).lower()
            # ZeroGPU specific queue/capacity errors
            if any(err in error_msg for err in [
                "queue full", "gpu busy", "capacity", "rate limit", 
                "time limit", "zerogpu", "queue", "space is busy", "429", "503"
            ]):
                wait_time = _exponential_backoff(attempt)
                logger.warning(f"⏳ ZeroGPU przeciążony (attempt {attempt + 1}/{max_retries}). Czekam {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"❌ Błąd API: {type(e).__name__}: {e}")
                await asyncio.sleep(5)
                
    logger.critical(f"❌ Nie udało się wygenerować sceny po {max_retries} próbach.")
    return False, None

async def generate_ai_video(prompts: list[str], output_cache_path: str = "cache/final_output.mp4") -> Tuple[Optional[str], Optional[int]]:
    """
    Generate AI video using official ZeroGPU LTX-Video Space with parallel scene generation.
    
    Args:
        prompts: List of prompts for each scene (typically 3-5 scenes)
        output_cache_path: Path where final MP4 will be saved
    
    Returns:
        Tuple of (output_file_path, last_seed_used) or (None, None) on failure
    """
    if not prompts:
        logger.error("❌ Otrzymano pustą listę promptów.")
        return None, None

    # Preflight checks
    if not _check_ffmpeg_installed():
        logger.error("❌ FFmpeg jest wymagany do łączenia scen. Przerwanie.")
        return None, None

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        logger.warning(
            "⚠️ Brak HF_TOKEN. ZeroGPU może działać wolniej lub zwrócić błąd limitu. "
            "Ustaw: export HF_TOKEN='hf_xxxxx'"
        )

    # Connect to OFFICIAL ZeroGPU LTX-Video Space
    try:
        logger.info("🔗 Łączę się z oficjalnym ZeroGPU LTX-Video Space...")
        client = Client(
            "ZeroGPU/LTX-Video",  # Official Space - NOT DeepRat's fork
            hf_token=hf_token
        )
        logger.info("✅ Pomyślnie połączono z ZeroGPU.")
    except Exception as e:
        logger.error(f"❌ Nie można połączyć się z ZeroGPU: {e}")
        return None, None

    cache_dir = os.path.dirname(output_cache_path) or "cache"
    os.makedirs(cache_dir, exist_ok=True)
    
    logger.info(f"🎥 Rozpoczynam generowanie {len(prompts)} scen równolegle...")
    
    # Generate unique seed for each scene (ensures variation)
    scene_seeds = [random.randint(0, 2147483647) for _ in prompts]
    
    # Create parallel async tasks for all scenes
    tasks = []
    for i, (prompt, seed) in enumerate(zip(prompts, scene_seeds)):
        scene_path = os.path.join(cache_dir, f"scene_{i+1}.mp4")
        logger.info(f"📍 Scena {i+1}/{len(prompts)}")
        tasks.append(_generate_single_scene(client, prompt, duration=4, seed=seed, output_path=scene_path))
    
    # Execute all scenes in parallel
    logger.info("⚡ Uruchamiam paralelne generowanie scen...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    temp_files = []
    last_seed = None
    failed_count = 0
    
    # Collect successful scenes
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"❌ Scena {i+1} - Wyjątek: {result}")
            failed_count += 1
            continue
        
        success, seed = result
        if success:
            last_seed = seed
            scene_path = os.path.join(cache_dir, f"scene_{i+1}.mp4")
            if os.path.exists(scene_path):
                temp_files.append(scene_path)
                logger.info(f"✅ Scena {i+1} wygenerowana pomyślnie.")
            else:
                logger.error(f"❌ Scena {i+1} - Plik nie znaleziony po generacji.")
                failed_count += 1
        else:
            logger.error(f"❌ Scena {i+1} - Generacja nie powiodła się.")
            failed_count += 1

    if not temp_files:
        logger.critical("❌ Żadna scena nie została wygenerowana! Przerwanie.")
        return None, last_seed

    logger.info(f"📊 Wynik: {len(temp_files)} scen wygenerowanych, {failed_count} błędów")

    # Concatenate successful scenes using FFmpeg
    logger.info(f"📹 Łączę {len(temp_files)} scen za pomocą FFmpeg...")
    concat_list_path = os.path.join(cache_dir, "concat_list.txt")
    
    with open(concat_list_path, "w") as f:
        for temp_file in sorted(temp_files):  # Sort to ensure correct order
            abs_path = os.path.abspath(temp_file)
            f.write(f"file '{abs_path}'\\n")
            
    try:
        command = [
            "ffmpeg", 
            "-y",              # Overwrite output file
            "-f", "concat",    # Use concat demuxer
            "-safe", "0",      # Allow absolute paths
            "-i", concat_list_path,
            "-c", "copy",      # Copy codec (no re-encoding = fast)
            output_cache_path
        ]
        logger.debug(f"🛠️ FFmpeg: {' '.join(command)}")
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        logger.info(f"✅ Wideo zapisane: {output_cache_path}")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Błąd FFmpeg: {e.stderr}")
        return None, last_seed
    except Exception as e:
        logger.error(f"❌ Błąd podczas łączenia: {e}")
        return None, last_seed
        
    finally:
        # Cleanup temporary files
        if os.path.exists(concat_list_path):
            os.remove(concat_list_path)
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
                
    return output_cache_path, last_seed
