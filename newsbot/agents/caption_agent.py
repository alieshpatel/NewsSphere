import logging
import asyncio
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from config import Config

logger = logging.getLogger(__name__)

class CaptionAgent:
    def __init__(self, config: Config):
        import whisper
        logger.info("Loading Whisper 'base' model (first run downloads ~74MB)...")
        self.whisper_model = whisper.load_model("base")
        self.config = config
        self.executor = ThreadPoolExecutor(max_workers=2)

    async def generate_captions(self, audio_path: Path, output_srt: Path) -> dict:
        """
        Run whisper transcription, extract word-level segments, group into SRT blocks, and write to SRT.
        """
        try:
            logger.info(f"Transcribing audio file: {audio_path}")

            def transcribe():
                return self.whisper_model.transcribe(str(audio_path), word_timestamps=True)

            result = await asyncio.get_event_loop().run_in_executor(self.executor, transcribe)

            all_words = []
            for segment in result.get("segments", []):
                for word_info in segment.get("words", []):
                    all_words.append({
                        "word": word_info["word"].strip(),
                        "start": word_info["start"],
                        "end": word_info["end"]
                    })

            grouped_segments = self._group_words_into_segments(all_words)
            self._write_srt(grouped_segments, output_srt)

            segment_count = len(grouped_segments)
            word_count = len(all_words)
            duration = all_words[-1]["end"] if all_words else 0.0

            logger.info(f"Created {segment_count} caption segments from {word_count} words.")

            return {
                "srt_path": output_srt,
                "word_count": word_count,
                "segment_count": segment_count,
                "duration": float(duration)
            }
        except Exception as e:
            logger.error(f"Error generating captions: {e}")
            raise

    async def cut_shorts_clip(self, video_path: Path, script: dict, output_path: Path) -> Path:
        """
        Find the best 60-second clip for YouTube Shorts and crop/resize it to 1080x1920.
        """
        try:
            logger.info(f"Cutting shorts clip from {video_path}")

            start_time = 0.0
            end_time = float(self.config.SHORTS_DURATION_SECONDS)

            segments = script.get("segments", [])
            if segments:
                hook = segments[0]
                hook_duration = hook.get("duration", self.config.SHORTS_DURATION_SECONDS)
                end_time = min(float(hook_duration), float(self.config.SHORTS_DURATION_SECONDS))

            def process_video():
                try:
                    import moviepy as mp
                except ImportError:
                    import moviepy.editor as mp

                video = mp.VideoFileClip(str(video_path))

                actual_end = min(end_time, video.duration)
                clip = video.subclipped(start_time, actual_end)

                w, h = clip.size
                target_w = self.config.SHORTS_WIDTH   # 1080
                target_h = self.config.SHORTS_HEIGHT  # 1920

                # First, scale so the clip covers the target 9:16 frame
                # (matches or exceeds both target dimensions), then crop
                # any overflow — avoids distorted or letterboxed output.
                scale = max(target_w / w, target_h / h)
                new_w, new_h = int(w * scale), int(h * scale)
                clip = clip.resized(new_size=(new_w, new_h))

                # Center-crop to exactly target_w x target_h
                cw, ch = clip.size
                x_center = cw / 2
                y_center = ch / 2
                clip = clip.cropped(
                    x_center=x_center,
                    y_center=y_center,
                    width=target_w,
                    height=target_h,
                )

                clip.write_videofile(
                    str(output_path),
                    codec="libx264",
                    audio_codec="aac",
                    fps=self.config.VIDEO_FPS,
                    logger=None
                )

                clip.close()
                video.close()

            await asyncio.get_event_loop().run_in_executor(self.executor, process_video)

            logger.info(f"Shorts clip successfully exported to {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"Error cutting shorts clip: {e}")
            raise

    def _write_srt(self, segments: list[dict], output_path: Path) -> None:
        """Write SRT subtitle file."""
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                for seg in segments:
                    idx = seg["index"]
                    start_str = self._format_timestamp(seg["start"])
                    end_str = self._format_timestamp(seg["end"])
                    text = seg["text"]

                    f.write(f"{idx}\n")
                    f.write(f"{start_str} --> {end_str}\n")
                    f.write(f"{text}\n\n")
        except Exception as e:
            logger.error(f"Failed to write SRT file to {output_path}: {e}")
            raise

    def _format_timestamp(self, seconds: float) -> str:
        """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))

        if millis == 1000:
            millis = 0
            secs += 1
            if secs == 60:
                secs = 0
                minutes += 1
                if minutes == 60:
                    minutes = 0
                    hours += 1

        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _group_words_into_segments(self, words: list[dict]) -> list[dict]:
        """Group individual word timestamps into caption segments."""
        segments = []
        current_segment_words = []
        segment_index = 1

        def commit_segment():
            nonlocal current_segment_words, segment_index
            if not current_segment_words:
                return

            start_time = current_segment_words[0]["start"]
            end_time = current_segment_words[-1]["end"]
            text = " ".join([w["word"] for w in current_segment_words])

            segments.append({
                "index": segment_index,
                "start": start_time,
                "end": end_time,
                "text": text
            })
            segment_index += 1
            current_segment_words.clear()

        for word_info in words:
            word = word_info["word"]
            current_segment_words.append(word_info)

            if len(current_segment_words) >= 6 or re.search(r'[.!?,\n]', word):
                commit_segment()

        commit_segment()

        return segments