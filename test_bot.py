"""
Test suite for bot performance improvements.
Tests ai_models.py, optimizer.py, and bot.py functionality.
"""

import pytest
import asyncio
import os
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock

# Import modules to test
from ai_models import (
    _check_ffmpeg_installed,
    _exponential_backoff,
    _generate_single_scene,
    generate_ai_video,
    DEFAULT_NEGATIVE_PROMPT
)
from optimizer import CostOptimizer
from bot import build_story_prompts, get_sheet_headers


class TestAIModels:
    """Test AI video generation functionality."""
    
    def test_exponential_backoff_increases(self):
        """Test that backoff time increases exponentially."""
        attempt_0 = _exponential_backoff(0, base_wait=30)
        attempt_1 = _exponential_backoff(1, base_wait=30)
        attempt_2 = _exponential_backoff(2, base_wait=30)
        
        # Each should be roughly 2x the previous (accounting for jitter)
        assert attempt_0 >= 27 and attempt_0 <= 33  # 30s ± jitter
        assert attempt_1 >= 54 and attempt_1 <= 66  # 60s ± jitter
        assert attempt_2 >= 108 and attempt_2 <= 132  # 120s ± jitter
    
    def test_exponential_backoff_max_wait(self):
        """Test that backoff respects max_wait cap."""
        attempt_10 = _exponential_backoff(10, base_wait=30, max_wait=600)
        
        # Should never exceed max_wait
        assert attempt_10 <= 600
    
    def test_build_story_prompts_splits_by_newline(self):
        """Test prompt splitting by newline."""
        prompt = "Scene 1\nScene 2\nScene 3"
        result = build_story_prompts(prompt)
        
        assert len(result) == 3
        assert "Scene 1" in result[0]
        assert "Scene 2" in result[1]
        assert "Scene 3" in result[2]
    
    def test_build_story_prompts_splits_by_pipe(self):
        """Test prompt splitting by pipe."""
        prompt = "Hook | Problem | Solution"
        result = build_story_prompts(prompt)
        
        assert len(result) == 3
        assert "Hook" in result[0]
        assert "Problem" in result[1]
        assert "Solution" in result[2]
    
    def test_build_story_prompts_fallback(self):
        """Test fallback when prompt has no separators."""
        prompt = "A single continuous prompt"
        result = build_story_prompts(prompt)
        
        assert len(result) == 3
        assert all(prompt in scene for scene in result)
        assert "dynamic opening hook scene" in result[0]
        assert "core topic explanation" in result[1]
        assert "final conclusion" in result[2]


class TestOptimizer:
    """Test cache optimization functionality."""
    
    def test_optimizer_initialization(self):
        """Test that optimizer initializes properly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            optimizer = CostOptimizer(cache_db_path=db_path)
            
            assert optimizer.cache_db_path == db_path
            assert os.path.exists(db_path)
            optimizer.close()
    
    def test_cache_video_and_retrieval(self):
        """Test caching and retrieving videos."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            optimizer = CostOptimizer(cache_db_path=db_path)
            
            # Create a fake video file
            video_path = os.path.join(tmpdir, "test_video.mp4")
            Path(video_path).touch()
            
            # Cache it
            optimizer.cache_video(
                prompt="test prompt",
                duration=4,
                height=512,
                width=512,
                video_url_or_path=video_path
            )
            
            # Retrieve it
            cached = optimizer.get_cached_video(
                prompt="test prompt",
                duration=4,
                height=512,
                width=512
            )
            
            assert cached == video_path
            assert optimizer.metrics["cache_hits"] == 1
            optimizer.close()
    
    def test_cache_expiration(self):
        """Test that expired cache entries are cleaned up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            optimizer = CostOptimizer(cache_db_path=db_path)
            
            video_path = os.path.join(tmpdir, "test_video.mp4")
            Path(video_path).touch()
            
            # Cache with 1-second TTL
            optimizer.cache_video(
                prompt="test",
                duration=4,
                height=512,
                width=512,
                video_url_or_path=video_path,
                ttl_hours=0  # Immediately expire
            )
            
            # Wait a moment
            import time
            time.sleep(0.1)
            
            # Should not retrieve expired cache
            cached = optimizer.get_cached_video(
                prompt="test",
                duration=4,
                height=512,
                width=512
            )
            
            assert cached is None
            optimizer.close()
    
    def test_metrics_calculation(self):
        """Test metrics are calculated correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            optimizer = CostOptimizer(cache_db_path=db_path)
            
            # Simulate some cache activity
            optimizer.metrics["cache_hits"] = 10
            optimizer.metrics["cache_misses"] = 40
            optimizer.metrics["cost_saved_usd"] = 2.0
            
            metrics = optimizer.get_metrics()
            
            assert metrics["cache_hits"] == 10
            assert metrics["cache_misses"] == 40
            assert metrics["hit_rate_percent"] == 20.0  # 10 / 50
            assert metrics["cost_saved_usd"] == 2.0
            optimizer.close()
    
    def test_thread_safety(self):
        """Test that optimizer is thread-safe."""
        import threading
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            optimizer = CostOptimizer(cache_db_path=db_path)
            
            results = []
            
            def cache_and_retrieve():
                video_path = os.path.join(tmpdir, f"video_{threading.current_thread().name}.mp4")
                Path(video_path).touch()
                
                optimizer.cache_video(
                    prompt=f"test_{threading.current_thread().name}",
                    duration=4,
                    height=512,
                    width=512,
                    video_url_or_path=video_path
                )
                
                cached = optimizer.get_cached_video(
                    prompt=f"test_{threading.current_thread().name}",
                    duration=4,
                    height=512,
                    width=512
                )
                
                results.append(cached is not None)
            
            threads = [threading.Thread(target=cache_and_retrieve, name=f"thread-{i}") for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            assert all(results)
            optimizer.close()


class TestSheetIntegration:
    """Test Google Sheets integration."""
    
    def test_get_sheet_headers_caching(self):
        """Test that sheet headers are cached."""
        mock_sheet = Mock()
        mock_sheet.row_values.return_value = ["id", "prompt", "status"]
        
        # First call should fetch from API
        headers1 = get_sheet_headers(mock_sheet)
        assert mock_sheet.row_values.call_count == 1
        
        # Second call should use cache
        headers2 = get_sheet_headers(mock_sheet)
        assert mock_sheet.row_values.call_count == 1  # No additional call
        
        # Force refresh should fetch again
        headers3 = get_sheet_headers(mock_sheet, force_refresh=True)
        assert mock_sheet.row_values.call_count == 2
    
    def test_get_sheet_headers_cache_expiration(self):
        """Test that sheet headers cache expires."""
        mock_sheet = Mock()
        mock_sheet.row_values.return_value = ["id", "prompt", "status"]
        
        # Import to reset cache
        import bot
        bot._headers_cache_time = 0  # Force expired cache
        
        get_sheet_headers(mock_sheet)
        assert mock_sheet.row_values.call_count >= 1


class TestAsyncGeneration:
    """Test async video generation (requires mock API)."""
    
    @pytest.mark.asyncio
    async def test_generate_ai_video_mock(self):
        """Test video generation with mocked API."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock scene files
            scene1 = os.path.join(tmpdir, "scene_1.mp4")
            scene2 = os.path.join(tmpdir, "scene_2.mp4")
            scene3 = os.path.join(tmpdir, "scene_3.mp4")
            for scene in [scene1, scene2, scene3]:
                Path(scene).touch()
            
            output_path = os.path.join(tmpdir, "output.mp4")
            
            # Mock the client and subprocess
            with patch('ai_models.Client') as mock_client_class:
                mock_client = AsyncMock()
                mock_client_class.return_value = mock_client
                
                # Mock successful scene generation
                async def mock_predict(*args, **kwargs):
                    # Simulate creating scene file
                    idx = mock_predict.call_count
                    scene_file = os.path.join(tmpdir, f"scene_{idx}.mp4")
                    return (scene_file, 12345)
                
                mock_predict.call_count = 0
                mock_client.predict = AsyncMock(side_effect=mock_predict)
                
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = Mock(returncode=0)
                    
                    prompts = ["test prompt 1", "test prompt 2", "test prompt 3"]
                    result_path, seed = await generate_ai_video(
                        prompts,
                        output_cache_path=output_path
                    )
                    
                    # Should have called predict 3 times (3 scenes)
                    assert mock_client.predict.call_count == 3
                    # Should have called ffmpeg
                    assert mock_run.call_count >= 1


class TestErrorHandling:
    """Test error handling and edge cases."""
    
    def test_exponential_backoff_with_zero_attempt(self):
        """Test backoff calculation at attempt 0."""
        backoff = _exponential_backoff(0, base_wait=10)
        assert backoff >= 9 and backoff <= 11  # 10 ± jitter
    
    def test_build_story_prompts_empty_string(self):
        """Test handling of empty prompts."""
        result = build_story_prompts("")
        assert len(result) == 3
        assert all("" in scene for scene in result)
    
    def test_optimizer_metrics_zero_division(self):
        """Test metrics calculation with no cache activity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            optimizer = CostOptimizer(cache_db_path=db_path)
            
            metrics = optimizer.get_metrics()
            
            # Should handle division by zero gracefully
            assert metrics["hit_rate_percent"] == 0.0
            optimizer.close()


# Performance benchmarks
class TestPerformance:
    """Test performance improvements."""
    
    def test_exponential_backoff_performance(self):
        """Benchmark exponential backoff calculation."""
        import time
        
        start = time.time()
        for i in range(10000):
            _exponential_backoff(i % 10)
        elapsed = time.time() - start
        
        # Should complete 10k iterations in < 100ms
        assert elapsed < 0.1
    
    def test_optimizer_database_query_performance(self):
        """Benchmark database query speed."""
        import time
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            optimizer = CostOptimizer(cache_db_path=db_path)
            
            video_path = os.path.join(tmpdir, "test_video.mp4")
            Path(video_path).touch()
            
            # Cache 100 entries
            for i in range(100):
                optimizer.cache_video(
                    prompt=f"prompt_{i}",
                    duration=4,
                    height=512,
                    width=512,
                    video_url_or_path=video_path
                )
            
            # Benchmark retrieval
            start = time.time()
            for i in range(100):
                optimizer.get_cached_video(
                    prompt=f"prompt_{i}",
                    duration=4,
                    height=512,
                    width=512
                )
            elapsed = time.time() - start
            
            # Should retrieve 100 entries in < 50ms
            assert elapsed < 0.05
            optimizer.close()


if __name__ == "__main__":
    # Run tests with: pytest test_bot.py -v
    pytest.main([__file__, "-v", "--tb=short"])
