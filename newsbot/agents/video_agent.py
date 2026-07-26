import logging
import asyncio
import aiohttp
import re
from pathlib import Path
import os
import concurrent.futures
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
    
    async def fetch_broll(self, keywords: List[str], temp_dir: Path) -> List[Path]:
        """
        For each keyword:
        1. GET https://api.pexels.com/videos/search?query={keyword}&per_page=3&min_duration=5&max_duration=30&orientation=landscape
        2. From results, pick videos with resolution >= 1920x1080 (or best available)
        3. Download the HD video file to temp_dir/{keyword_slug}_{index}.mp4
        4. Use aiohttp for async downloads with progress logging
        5. Add 0.5s delay between API requests to respect 200/hour limit
        6. Return list of downloaded video paths
        7. If a keyword returns no results, log warning and skip
        8. Handle network errors gracefully
        Max downloads: config.MAX_BROLL_PER_KEYWORD per keyword
        """
        temp_dir.mkdir(parents=True, exist_ok=True)
        downloaded_paths = []

        async with aiohttp.ClientSession(headers=self.pexels_headers) as session:
            for keyword in keywords:
                keyword_slug = re.sub(r'[^a-z0-9]+', '_', keyword.lower()).strip('_')
                if not keyword_slug:
                    continue

                url = f"https://api.pexels.com/videos/search?query={keyword}&per_page=15&min_duration=5&max_duration=30&orientation=landscape"
                
                try:
                    logger.info(f"Searching Pexels for b-roll: '{keyword}'")
                    async with session.get(url) as response:
                        if response.status != 200:
                            logger.error(f"Pexels API error for '{keyword}': {response.status}")
                            continue
                        
                        data = await response.json()
                except Exception as e:
                    logger.error(f"Network error fetching b-roll metadata for '{keyword}': {e}")
                    continue
                
                videos = data.get("videos", [])
                if not videos:
                    logger.warning(f"No b-roll found for keyword '{keyword}'")
                    continue
                
                download_count = 0
                for video in videos:
                    if download_count >= self.config.MAX_BROLL_PER_KEYWORD:
                        break
                    
                    video_files = video.get("video_files", [])
                    if not video_files:
                        continue
                    
                    # Sort by resolution to get highest first
                    video_files = sorted(
                        video_files, 
                        key=lambda x: (x.get("width", 0) * x.get("height", 0)), 
                        reverse=True
                    )
                    
                    # Pick best file (at least 1920x1080 if available, else highest)
                    best_file = video_files[0]
                    for vf in video_files:
                        if vf.get("width", 0) >= 1920 and vf.get("height", 0) >= 1080:
                            best_file = vf
                            break
                    
                    download_link = best_file.get("link")
                    if not download_link:
                        continue
                    
                    dest_path = temp_dir / f"{keyword_slug}_{download_count}.mp4"
                    
                    try:
                        logger.info(f"Downloading b-roll {dest_path.name}")
                        async with session.get(download_link) as dl_resp:
                            if dl_resp.status == 200:
                                with open(dest_path, 'wb') as f:
                                    while True:
                                        chunk = await dl_resp.content.read(8192)
                                        if not chunk:
                                            break
                                        f.write(chunk)
                                downloaded_paths.append(dest_path)
                                download_count += 1
                            else:
                                logger.error(f"Failed to download video file: {dl_resp.status}")
                    except Exception as e:
                        logger.error(f"Error downloading video {download_link}: {e}")
                
                # Rate limit delay
                await asyncio.sleep(0.5)

        return downloaded_paths

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
            intro_path = self.config.ASSETS_DIR / "intro.mp4"
            if intro_path.exists():
                intro_clip = VideoFileClip(str(intro_path))
                clips_to_close.append(intro_clip)
                final_clips.append(intro_clip)
                # Need to resize?
                if intro_clip.w != 1920 or intro_clip.h != 1080:
                    intro_clip = intro_clip.resized(new_size=(1920, 1080))
            
            # 4. Script segments
            segments = script.get("segments", [])
            # Fake logic for segment duration: distribute evenly if not specified
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
                    clip = ColorClip(size=(1920, 1080), color=(0,0,0), duration=dur)
                    clips_to_close.append(clip)
                
                # Loop/trim and resize
                def _loop_clip_to_duration(self, clip, target_duration: float):
                 """Loop or trim a clip to exactly target_duration (MoviePy 2.x)."""

                # Trim if clip is longer
                if clip.duration >= target_duration:
                    return clip.subclipped(0, target_duration)

                # Otherwise repeat the clip until long enough
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
    
            # 5. Outro
            outro_path = self.config.ASSETS_DIR / "outro.mp4"
            outro_clip_obj = None
            if outro_path.exists():
                outro_clip_obj = VideoFileClip(str(outro_path))
                clips_to_close.append(outro_clip_obj)
                if outro_clip_obj.w != 1920 or outro_clip_obj.h != 1080:
                    outro_clip_obj = outro_clip_obj.resized(newsize=(1920, 1080))
            
            # Combine segments
            if segment_clips:
                main_video = concatenate_videoclips(segment_clips, method="compose")
                # 7. Set voiceover audio
                main_video = main_video.with_audio(voiceover_clip)
                final_clips.append(main_video)
            
            if outro_clip_obj:
                final_clips.append(outro_clip_obj)
            
            # 6. Concatenate all
            full_video = concatenate_videoclips(final_clips, method="compose")
            
            # 8. Add background music if provided
            if music_path and music_path.exists():
                bg_music = AudioFileClip(str(music_path))
                clips_to_close.append(bg_music)
                # Loop music to match full video length
                if hasattr(afx, "audio_loop"):
                    bg_music = bg_music.fx(afx.audio_loop, duration=full_video.duration)
                else:
                    bg_music = bg_music.with_duration(full_video.duration)
                
                bg_music = bg_music.volumex(self.config.MUSIC_VOLUME)
                
                mixed_audio = CompositeAudioClip([full_video.audio, bg_music])
                full_video = full_video.with_audio(mixed_audio)
            
            # 9. Burn captions
            if captions_srt.exists():
                full_video = self._burn_captions(full_video, captions_srt)
            
            # 10. Export
            output_path.parent.mkdir(parents=True, exist_ok=True)
            full_video.write_videofile(
                str(output_path),
                fps=self.config.VIDEO_FPS,
                codec="libx264",
                audio_codec="aac",
                preset="fast",
                threads=4,
                logger=None
            )
            
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

    def _create_lower_third(self, text: str, duration: float, video_size: Tuple[int, int]) -> 'CompositeVideoClip':
        """Create a lower-third text overlay."""
        w, h = video_size
        bar_height = int(h * 0.15)
        bar_y = h - bar_height - 50
        
        # Background bar
        bg = ColorClip(size=(w, bar_height), color=(20, 20, 20)).with_opacity(0.8)
        bg = bg.with_duration(duration).with_position((0, bar_y))
        
        # Text
        try:
            txt = TextClip(text, fontsize=60, color='white', font='Arial-Bold', align='West')
        except Exception:
            txt = TextClip(text, fontsize=60, color='white')
            
        txt = txt.with_duration(duration).with_position((50, bar_y + (bar_height - txt.h) // 2))
        
        return CompositeVideoClip([bg, txt], size=video_size).with_duration(duration)

    def _loop_clip_to_duration(self, clip, target_duration: float):
        """Loop a video clip to fill target_duration."""
        if clip.duration >= target_duration:
            return clip.subclip(0, target_duration)
        else:
            if hasattr(vfx, "loop"):
                return clip.fx(vfx.loop, duration=target_duration)
            else:
                # fallback loop
                n_loops = int(target_duration / clip.duration) + 1
                clips = [clip] * n_loops
                concat = concatenate_videoclips(clips)
                return concat.subclip(0, target_duration)

    def _burn_captions(self, video, srt_path: Path):
        """Parse SRT file and burn captions onto video."""
        captions = self._parse_srt(srt_path)
        if not captions:
            return video
            
        w, h = video.w, video.h
        
        text_clips = []
        for cap in captions:
            start_t = self._srt_time_to_seconds(cap["start"])
            end_t = self._srt_time_to_seconds(cap["end"])
            duration = end_t - start_t
            
            if duration <= 0:
                continue
                
            try:
                txt = TextClip(
                    cap["text"], 
                    fontsize=48, 
                    color='white',
                    stroke_color='black',
                    stroke_width=2,
                    method='caption',
                    size=(int(w * 0.8), None),
                    align='center'
                )
            except Exception:
                # Fallback if specific kwargs fail
                txt = TextClip(cap["text"], fontsize=48, color='white')
                
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
