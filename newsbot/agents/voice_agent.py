import logging
import asyncio
import re
import numpy as np
import soundfile as sf
from pathlib import Path
from tqdm import tqdm

from config import Config

logger = logging.getLogger(__name__)

class VoiceAgent:
    def __init__(self, config: Config):
        from kokoro_onnx import Kokoro
        self.kokoro = Kokoro("kokoro-v1.9.onnx", "voices-v1.0.bin")
        self.config = config
        self.sample_rate = 24000
    
    async def generate_voiceover(self, script: dict, output_path: Path) -> dict:
        """
        Extract full_script text, split into chunks, generate audio, show progress,
        concatenate arrays, and save as WAV.
        """
        try:
            text = script.get("full_script", "")
            if not text:
                raise ValueError("Script dictionary is missing 'full_script' key or it is empty.")
            
            chunks = self._split_into_chunks(text, max_chars=500)
            audio_arrays = []
            
            logger.info(f"Generating voiceover in {len(chunks)} chunks.")
            
            for chunk in tqdm(chunks, desc="Generating Audio"):
                def synthesize():
                    return self.kokoro.create(
                        chunk,
                        voice=self.config.KOKORO_VOICE,
                        speed=self.config.KOKORO_SPEED
                    )
                
                result = await asyncio.to_thread(synthesize)
                
                if isinstance(result, tuple) and len(result) >= 2:
                    audio_chunk = result[0]
                else:
                    audio_chunk = result
                    
                audio_arrays.append(np.array(audio_chunk, dtype=np.float32))
                
            if not audio_arrays:
                raise ValueError("No audio generated from the script.")
                
            combined_audio = self._concatenate_audio(audio_arrays)
            
            await asyncio.to_thread(sf.write, str(output_path), combined_audio, self.sample_rate)
            
            file_size = output_path.stat().st_size
            duration = len(combined_audio) / self.sample_rate
            
            logger.info(f"Audio saved to {output_path} (Size: {file_size} bytes, Duration: {duration:.2f}s)")
            
            return {
                "audio_path": output_path,
                "duration_seconds": float(duration),
                "sample_rate": self.sample_rate
            }
        except Exception as e:
            logger.error(f"Failed to generate voiceover: {e}")
            raise

    def _split_into_chunks(self, text: str, max_chars: int = 500) -> list[str]:
        """Split text at sentence boundaries (., !, ?) ensuring no chunk exceeds max_chars.
        If a single sentence exceeds max_chars, split at comma or space.
        Never split mid-word."""
        sentences = re.split(r'(?<=[.!?]) +', text.strip())
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            if not sentence:
                continue
                
            if len(current_chunk) + len(sentence) + 1 <= max_chars:
                if current_chunk:
                    current_chunk += " " + sentence
                else:
                    current_chunk = sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = ""
                
                if len(sentence) > max_chars:
                    parts = re.split(r'(?<=,) +', sentence)
                    for part in parts:
                        if len(current_chunk) + len(part) + 1 <= max_chars:
                            if current_chunk:
                                current_chunk += " " + part
                            else:
                                current_chunk = part
                        else:
                            if current_chunk:
                                chunks.append(current_chunk)
                            current_chunk = ""
                            if len(part) > max_chars:
                                words = part.split(' ')
                                for word in words:
                                    if len(current_chunk) + len(word) + 1 <= max_chars:
                                        if current_chunk:
                                            current_chunk += " " + word
                                        else:
                                            current_chunk = word
                                    else:
                                        if current_chunk:
                                            chunks.append(current_chunk)
                                        current_chunk = word
                            else:
                                current_chunk = part
                else:
                    current_chunk = sentence
                    
        if current_chunk:
            chunks.append(current_chunk)
            
        return chunks

    def _concatenate_audio(self, audio_arrays: list[np.ndarray]) -> np.ndarray:
        """Concatenate numpy arrays with a small silence gap (0.15s) between chunks
        for natural pacing."""
        if not audio_arrays:
            return np.array([])
            
        silence_duration = 0.15
        silence_samples = int(self.sample_rate * silence_duration)
        silence = np.zeros(silence_samples, dtype=np.float32)
        
        combined = []
        for i, arr in enumerate(audio_arrays):
            combined.append(arr.flatten())
            if i < len(audio_arrays) - 1:
                combined.append(silence)
                
        return np.concatenate(combined)
