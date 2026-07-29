import logging
import asyncio
import aiohttp
import re
import shutil
import subprocess
from pathlib import Path
import os
import concurrent.futures
from utils.progress import ProgressBar
from typing import List, Optional, Dict, Tuple

try:
    # MoviePy v2 standard imports
    from moviepy import (
        VideoFileClip, AudioFileClip, TextClip,
        CompositeVideoClip, concatenate_videoclips, ColorClip,
        CompositeAudioClip, vfx, afx
    )
except ImportError:
    # Fallback for MoviePy 1.x
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, TextClip,
        CompositeVideoClip, concatenate_videoclips, ColorClip,
        CompositeAudioClip, vfx, afx
    )

from config import Config

logger = logging.getLogger(__name__)


class VideoAgent:
    def __init__(self, config: Config):
        self.config = config
        self.pexels_headers = {"Authorization": config.PEXELS_API_KEY}
        # Detect once per-process instead of once per render.
        self._nvenc_available = self._check_nvenc_available()

    # ── B-roll fetch (concurrent, local download, capped at 1080p) ────────

    async def fetch_broll(self, keywords: List[str], temp_dir: Path) -> List[Path]:
        """
        Fetch b-roll for all keywords CONCURRENTLY instead of sequentially.
        A semaphore caps how many keyword-searches run in parallel, so we
        respect Pexels' rate limit while still cutting total wall-clock time
        dramatically versus doing one keyword at a time.
        """
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Tune this: higher = faster, but more simultaneous connections/requests.
        MAX_CONCURRENT_KEYWORDS = 5
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_KEYWORDS)

        overall = ProgressBar(
            total=len(keywords) * self.config.MAX_BROLL_PER_KEYWORD,
            label="B-roll clips",
            unit="clips",
        )

        async with aiohttp.ClientSession(headers=self.pexels_headers) as session:

            async def _process_keyword(keyword: str) -> List[Path]:
                async with semaphore:
                    return await self._fetch_broll_for_keyword(
                        session, keyword, temp_dir, overall
                    )

            results = await asyncio.gather(
                *[_process_keyword(kw) for kw in keywords],
                return_exceptions=True,
            )

        downloaded_paths: List[Path] = []
        for keyword, result in zip(keywords, results):
            if isinstance(result, Exception):
                logger.error(f"B-roll fetch failed entirely for '{keyword}': {result}")
                continue
            downloaded_paths.extend(result)

        overall.finish(f"{len(downloaded_paths)} b-roll clips downloaded")
        return downloaded_paths

    async def _fetch_broll_for_keyword(
        self,
        session: aiohttp.ClientSession,
        keyword: str,
        temp_dir: Path,
        overall: "ProgressBar",
    ) -> List[Path]:
        """Search + download b-roll for a single keyword. Runs concurrently across keywords."""
        keyword_slug = re.sub(r'[^a-z0-9]+', '_', keyword.lower()).strip('_')
        if not keyword_slug:
            return []

        url = (
            f"https://api.pexels.com/videos/search"
            f"?query={keyword}&per_page=15&min_duration=5&max_duration=30&orientation=landscape"
        )

        try:
            logger.info(f"Searching Pexels for b-roll: '{keyword}'")
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Pexels API error for '{keyword}': {response.status}")
                    return []
                data = await response.json()
        except Exception as e:
            logger.error(f"Network error fetching b-roll metadata for '{keyword}': {e}")
            return []

        videos = data.get("videos", [])
        if not videos:
            logger.warning(f"No b-roll found for keyword '{keyword}'")
            return []

        downloaded: List[Path] = []
        download_count = 0

        for video in videos:
            if download_count >= self.config.MAX_BROLL_PER_KEYWORD:
                break

            video_files = video.get("video_files", [])
            if not video_files:
                continue

            best_file = self._select_best_broll_file(video_files)
            if not best_file:
                continue

            download_link = best_file.get("link")
            if not download_link:
                continue

            dest_path = temp_dir / f"{keyword_slug}_{download_count}.mp4"

            try:
                async with session.get(download_link) as dl_resp:
                    if dl_resp.status == 200:
                        with open(dest_path, 'wb') as f:
                            while True:
                                chunk = await dl_resp.content.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                        downloaded.append(dest_path)
                        download_count += 1
                        overall.update(1)
                    else:
                        logger.error(f"Failed to download video file: {dl_resp.status}")
            except Exception as e:
                logger.error(f"Error downloading video {download_link}: {e}")

        return downloaded

    def _select_best_broll_file(self, video_files: List[dict]) -> Optional[dict]:
        """
        Pick the best b-roll source file: prefer files at or under 1080p,
        and among those prefer an exact 1920x1080 (16:9) match so
        _force_1080p can skip its resize+crop pass entirely.
        """
        candidates = [
            vf for vf in video_files
            if vf.get("width", 0) <= 1920 and vf.get("height", 0) <= 1080
        ]
        if not candidates:
            # Nothing at/under 1080p — fall back to the smallest available file.
            candidates = sorted(video_files, key=lambda x: x.get("width", 0) * x.get("height", 0))
            return candidates[0] if candidates else None

        # Prefer exact 1920x1080 first, then largest-area among the rest —
        # avoids a resize+crop pass later in _force_1080p.
        def score(vf):
            w, h = vf.get("width", 0), vf.get("height", 0)
            exact = 1 if (w, h) == (1920, 1080) else 0
            return (exact, w * h)

        candidates.sort(key=score, reverse=True)
        return candidates[0]

    # ── Encoding helpers ─────────────────────────────────────────────────

    def _check_nvenc_available(self) -> bool:
        """Check once whether ffmpeg has h264_nvenc support (NVIDIA GPU encode)."""
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        try:
            result = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=10,
            )
            available = "h264_nvenc" in result.stdout
        except Exception as e:
            logger.debug(f"Could not probe ffmpeg encoders (assuming no nvenc): {e}")
            available = False

        logger.info(f"h264_nvenc GPU encoding {'available' if available else 'not available'}")
        return available

    def _write_video_kwargs(self) -> Dict:
        """
        Build the write_videofile kwargs: GPU-accelerated nvenc when available,
        otherwise a fast CPU libx264 fallback. Progress bar logger is only
        enabled when NEWSSPHERE_DEBUG is set, since it writes to stdout every
        frame and is pure overhead on unattended/automated runs.
        """
        common = {
            "fps": self.config.VIDEO_FPS,
            "audio_codec": "aac",
            "threads": os.cpu_count(),
            "logger": "bar" if os.environ.get("NEWSSPHERE_DEBUG") else None,
        }

        if self._nvenc_available:
            return {
                **common,
                "codec": "h264_nvenc",
                "preset": "p4",  # nvenc presets: p1 (fastest) .. p7 (slowest/best quality)
            }

        return {
            **common,
            "codec": "libx264",
            "preset": "ultrafast",
            "ffmpeg_params": ["-crf", "20"],
        }

    # ── Video assembly ──────────────────────────────────────────────────

    def _assemble_video_sync(
        self,
        voiceover_path: Path,
        broll_paths: List[Path],
        script: Dict,
        captions_srt: Path,
        output_path: Path,
        music_path: Optional[Path] = None
    ) -> Path:
        clips_to_close = []
        try:
            # 1. Load voiceover
            voiceover_clip = AudioFileClip(str(voiceover_path))
            clips_to_close.append(voiceover_clip)
            total_duration = voiceover_clip.duration

            final_clips = []

            # 3. Intro
            intro_duration = 0.0
            intro_path = self.config.ASSETS_DIR / "intro.mp4"
            if intro_path.exists():
                intro_clip = VideoFileClip(str(intro_path))
                clips_to_close.append(intro_clip)
                intro_clip = self._force_1080p(intro_clip)
                intro_duration = intro_clip.duration
                final_clips.append(intro_clip)

            # 4. Script segments
            segments = script.get("segments", [])
            segment_duration = total_duration / len(segments) if segments else 0

            broll_index = 0
            segment_clips = []

            for i, segment in enumerate(segments):
                seg_name = segment.get("name", f"Segment {i+1}")
                dur = segment.get("duration", segment_duration)

                if broll_paths:
                    broll = broll_paths[broll_index % len(broll_paths)]
                    broll_index += 1
                    clip = VideoFileClip(str(broll))
                    clips_to_close.append(clip)
                else:
                    clip = ColorClip(size=(1920, 1080), color=(0, 0, 0), duration=dur)
                    clips_to_close.append(clip)

                # Loop/trim to the segment's target duration, then force exact 1080p
                clip = self._loop_clip_to_duration(clip, dur)
                clip = self._force_1080p(clip)

                segment_clips.append(clip)

            # 5. Outro
            outro_path = self.config.ASSETS_DIR / "outro.mp4"
            outro_clip_obj = None
            if outro_path.exists():
                outro_clip_obj = VideoFileClip(str(outro_path))
                clips_to_close.append(outro_clip_obj)
                outro_clip_obj = self._force_1080p(outro_clip_obj)

            # Combine segments.
            # "chain" instead of "compose": every clip here has already been
            # forced to exactly 1920x1080 above, so there's no need to pay
            # for compose's per-frame canvas recompute/blit — chain just
            # concatenates the same-sized clips directly.
            if segment_clips:
                main_video = concatenate_videoclips(segment_clips, method="chain")
                main_video = main_video.with_audio(voiceover_clip)
                final_clips.append(main_video)

            if outro_clip_obj:
                final_clips.append(outro_clip_obj)

            full_video = concatenate_videoclips(final_clips, method="chain")

            # Safety net: never allow the composited canvas to exceed 1080p
            if full_video.w != 1920 or full_video.h != 1080:
                logger.warning(f"full_video was {full_video.w}x{full_video.h}, forcing 1920x1080")
                full_video = self._force_1080p(full_video)

            # 8. Add background music if provided
            if music_path and music_path.exists():
                bg_music = AudioFileClip(str(music_path))
                clips_to_close.append(bg_music)

                # Loop music to match full video length
                bg_music = bg_music.with_effects([afx.AudioLoop(duration=full_video.duration)])

                # Scale volume
                bg_music = bg_music.with_effects([afx.MultiplyVolume(self.config.MUSIC_VOLUME)])

                mixed_audio = CompositeAudioClip([full_video.audio, bg_music])
                full_video = full_video.with_audio(mixed_audio)

            # 9. Burn captions
            if captions_srt.exists():
                full_video = self._burn_captions(full_video, captions_srt, offset=intro_duration)

            # 10. Export — GPU (nvenc) when available, else fast libx264 fallback.
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_kwargs = self._write_video_kwargs()
            try:
                full_video.write_videofile(str(output_path), **write_kwargs)
            except Exception as e:
                if write_kwargs.get("codec") == "h264_nvenc":
                    logger.warning(f"nvenc encode failed ({e}), falling back to libx264")
                    self._nvenc_available = False
                    fallback_kwargs = self._write_video_kwargs()
                    full_video.write_videofile(str(output_path), **fallback_kwargs)
                else:
                    raise

            # 11. Log output size
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"Rendered video {output_path.name} - Size: {size_mb:.2f} MB")

            return output_path

        finally:
            for c in clips_to_close:
                try:
                    if hasattr(c, "close"):
                        c.close()
                except Exception as e:
                    logger.debug(f"Error closing clip: {e}")

    async def assemble_video(
        self,
        voiceover_path: Path,
        broll_paths: List[Path],
        script: Dict,
        captions_srt: Path,
        output_path: Path,
        music_path: Optional[Path] = None
    ) -> Path:
        """
        Full video assembly using MoviePy v2:
        Run in thread executor since MoviePy is CPU-bound.
        """
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                self._assemble_video_sync,
                voiceover_path,
                broll_paths,
                script,
                captions_srt,
                output_path,
                music_path
            )

    # ── Sizing helper ────────────────────────────────────────────────────

    def _force_1080p(self, clip):
        """
        Always force a clip to exactly 1920x1080, regardless of source
        resolution — resize to cover, then center-crop any overflow.
        Prevents any oversized (e.g. 4K) source from ballooning the
        final composite canvas and causing OOM during rendering.
        """
        target_w, target_h = 1920, 1080
        if clip.w == target_w and clip.h == target_h:
            return clip

        scale = max(target_w / clip.w, target_h / clip.h)
        new_w, new_h = int(clip.w * scale), int(clip.h * scale)
        clip = clip.resized(new_size=(new_w, new_h))

        if clip.w != target_w or clip.h != target_h:
            x_center = clip.w / 2
            y_center = clip.h / 2
            clip = clip.cropped(
                x_center=x_center, y_center=y_center,
                width=target_w, height=target_h,
            )
        return clip

    # ── Text overlays ────────────────────────────────────────────────────

    def _create_lower_third(self, text: str, duration: float, video_size: Tuple[int, int]) -> 'CompositeVideoClip':
        """Create a lower-third text overlay."""
        w, h = video_size
        bar_height = int(h * 0.15)
        bar_y = h - bar_height - 50

        bg = ColorClip(size=(w, bar_height), color=(20, 20, 20)).with_opacity(0.8)
        bg = bg.with_duration(duration).with_position((0, bar_y))

        font_path = r"C:\Windows\Fonts\arialbd.ttf"
        try:
            txt = TextClip(font=font_path, text=text, font_size=60, color='white', horizontal_align='left')
        except Exception:
            txt = TextClip(font=font_path, text=text, font_size=60, color='white')

        txt = txt.with_duration(duration).with_position((50, bar_y + (bar_height - txt.h) // 2))

        return CompositeVideoClip([bg, txt], size=video_size).with_duration(duration)

    def _loop_clip_to_duration(self, clip, target_duration: float):
        """Loop or trim a clip to exactly target_duration (MoviePy 2.x)."""
        if clip.duration >= target_duration:
            return clip.subclipped(0, target_duration)

        loops = []
        remaining = target_duration
        while remaining > 0:
            if remaining >= clip.duration:
                loops.append(clip)
                remaining -= clip.duration
            else:
                loops.append(clip.subclipped(0, remaining))
                remaining = 0

        return concatenate_videoclips(loops, method="compose")

    def _burn_captions(self, video, srt_path: Path, offset: float = 0.0):
        """Parse SRT file and burn captions onto video, shifted by `offset`
        seconds so they line up with where the voiceover actually starts
        in the final timeline (e.g. after an intro clip)."""
        captions = self._parse_srt(srt_path)
        if not captions:
            return video

        w, h = video.w, video.h
        font_path = r"C:\Windows\Fonts\arialbd.ttf"

        text_clips = []
        for cap in captions:
            start_t = self._srt_time_to_seconds(cap["start"]) + offset
            end_t = self._srt_time_to_seconds(cap["end"]) + offset
            duration = end_t - start_t

            if duration <= 0:
                continue

            try:
                txt = TextClip(
                    font=font_path,
                    text=cap["text"],
                    font_size=48,
                    color='white',
                    stroke_color='black',
                    stroke_width=2,
                    method='caption',
                    size=(int(w * 0.8), None),
                    text_align='center',
                )
            except Exception as e:
                logger.warning(f"TextClip styled render failed ({e}), falling back to plain text")
                txt = TextClip(font=font_path, text=cap["text"], font_size=48, color='white')

            txt = txt.with_duration(duration).with_position(('center', h * 0.85)).with_start(start_t)
            text_clips.append(txt)

        return CompositeVideoClip([video] + text_clips)

    def _parse_srt(self, srt_path: Path) -> List[Dict]:
        """Parse an SRT file into list of {index, start, end, text} dicts."""
        content = srt_path.read_text(encoding="utf-8")
        blocks = content.strip().split("\n\n")
        captions = []

        for block in blocks:
            lines = block.split("\n")
            if len(lines) >= 3:
                index = lines[0].strip()
                times = lines[1].split(" --> ")
                if len(times) == 2:
                    text = "\n".join(lines[2:]).strip()
                    captions.append({
                        "index": index,
                        "start": times[0].strip(),
                        "end": times[1].strip(),
                        "text": text
                    })
        return captions

    def _srt_time_to_seconds(self, time_str: str) -> float:
        """Convert SRT timestamp 'HH:MM:SS,mmm' to float seconds."""
        time_str = time_str.replace(',', '.')
        parts = time_str.split(':')
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        return 0.0