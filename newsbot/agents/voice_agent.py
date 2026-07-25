import asyncio
import logging
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from tqdm import tqdm

from config import Config

logger = logging.getLogger(__name__)


class VoiceAgent:
    def __init__(self, config: Config):
        from kokoro_onnx import Kokoro

        self.config = config
        self.sample_rate = 24000

        model_dir = Path("assets") / "models"

        model_path = model_dir / "kokoro-v1.0.onnx"
        voices_path = model_dir / "voices-v1.0.bin"

        if not model_path.exists():
            raise FileNotFoundError(
                f"Kokoro model not found:\n{model_path}\n\n"
                "Download kokoro-v1.9.onnx and place it inside assets/models/"
            )

        if not voices_path.exists():
            raise FileNotFoundError(
                f"Voices file not found:\n{voices_path}\n\n"
                "Download voices-v1.0.bin and place it inside assets/models/"
            )

        logger.info("Loading Kokoro TTS model...")

        self.kokoro = Kokoro(
            str(model_path),
            str(voices_path),
        )

        logger.info("Kokoro model loaded successfully.")

    async def generate_voiceover(
    self,
    script: dict,
    output_path: Path,
) -> dict:
        """
        Generate voiceover from script.
        """

        text = script.get("full_script", "").strip()

        if not text:
            raise ValueError("Script is empty.")

        chunks = self._split_into_chunks(text)

        logger.info(
            "Generating voice (%d chunks)...",
            len(chunks),
        )

        logger.info(f"Voice: {self.config.KOKORO_VOICE}")
        logger.info(f"Speed: {self.config.KOKORO_SPEED}")

        audio_arrays = []

        for i, chunk in enumerate(chunks):

            logger.info(f"Generating chunk {i + 1}/{len(chunks)}")

            def generate():
                result = self.kokoro.create(
                    chunk,
                    voice=self.config.KOKORO_VOICE,
                    speed=self.config.KOKORO_SPEED,
                )

                if isinstance(result, tuple):
                    return result[0]

                return result

            audio = await asyncio.to_thread(generate)

            logger.info(f"Finished chunk {i + 1}")

            audio_arrays.append(
                np.asarray(audio, dtype=np.float32)
            )

        final_audio = self._concatenate_audio(audio_arrays)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        await asyncio.to_thread(
            sf.write,
            str(output_path),
            final_audio,
            self.sample_rate,
        )

        duration = len(final_audio) / self.sample_rate

        logger.info(
            "Voice generation complete (%.2f sec)",
            duration,
        )

        return {
            "audio_path": output_path,
            "duration_seconds": duration,
            "sample_rate": self.sample_rate,
        }

    def _split_into_chunks(
        self,
        text: str,
        max_chars: int = 500,
    ) -> list[str]:
        """
        Split text into chunks suitable for Kokoro.
        """

        sentences = re.split(
            r"(?<=[.!?])\s+",
            text,
        )

        chunks = []
        current = ""

        for sentence in sentences:

            if len(current) + len(sentence) + 1 <= max_chars:
                if current:
                    current += " " + sentence
                else:
                    current = sentence
            else:
                if current:
                    chunks.append(current)

                current = sentence

        if current:
            chunks.append(current)

        return chunks

    def _concatenate_audio(
        self,
        audio_arrays: list[np.ndarray],
    ) -> np.ndarray:
        """
        Concatenate chunks with 150ms silence.
        """

        if not audio_arrays:
            return np.array([], dtype=np.float32)

        silence = np.zeros(
            int(self.sample_rate * 0.15),
            dtype=np.float32,
        )

        merged = []

        for i, audio in enumerate(audio_arrays):

            merged.append(audio)

            if i != len(audio_arrays) - 1:
                merged.append(silence)

        return np.concatenate(merged)