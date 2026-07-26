#!/usr/bin/env python3
"""
NewsSphere — Fully automated news-to-YouTube video pipeline.
Orchestrates all agents in sequence with proper error handling.
Total monthly cost: $0.00
"""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import Config
from agents.news_agent import NewsAgent
from agents.script_agent import ScriptAgent
from agents.voice_agent import VoiceAgent
from agents.video_agent import VideoAgent
from agents.thumbnail_agent import ThumbnailAgent
from agents.caption_agent import CaptionAgent
from agents.seo_agent import SEOAgent
from agents.publisher_agent import PublisherAgent
from utils.progress import StepSpinner
from utils.telegram_notify import TelegramNotifier
from utils.file_manager import FileManager
from utils.rate_limiter import RateLimiter

logger = logging.getLogger("NewsSphere")

BANNER = r"""
 _   _                    ____        _
| \ | | _____      _____ / ___| _ __ | |__   ___ _ __ ___
|  \| |/ _ \ \ /\ / / __| \___ \| '_ \| '_ \ / _ \ '__/ _ \
| |\  |  __/\ V  V /\__ \  ___) | |_) | | | |  __/ | |  __/
|_| \_|\___| \_/\_/ |___/ |____/| .__/|_| |_|\___|_|  \___|
                                |_|
  Automated News → YouTube Pipeline  |  $0.00/month
"""


def _display_stories(stories: list[dict]) -> None:
    """Print fetched stories to the terminal in a formatted table."""
    print("\n" + "=" * 72)
    print("  TOP NEWS STORIES")
    print("=" * 72)
    for i, story in enumerate(stories, 1):
        score = story.get("score", 0)
        headline = story.get("headline", "No headline")[:65]
        keywords = ", ".join(story.get("keywords_matched", []))
        print(f"\n  [{i}] (Score: {score:.1f}) {headline}")
        print(f"      Keywords: {keywords}")
        print(f"      URL: {story.get('url', 'N/A')[:70]}")
    print("\n" + "=" * 72)


async def _select_story(stories: list[dict], timeout_seconds: int = 60) -> dict:
    """
    Let the user pick a story or auto-select #1 after timeout.
    Works in both interactive and non-interactive modes.
    """
    if not stories:
        raise ValueError("No stories available to select.")

    _display_stories(stories)
    print(f"\n  Enter story number (1-{len(stories)}) or wait {timeout_seconds}s to auto-select #1: ", end="", flush=True)

    loop = asyncio.get_running_loop()

    async def _read_input() -> str:
        return await loop.run_in_executor(None, sys.stdin.readline)

    try:
        raw = await asyncio.wait_for(_read_input(), timeout=timeout_seconds)
        choice = raw.strip()
        if choice.isdigit() and 1 <= int(choice) <= len(stories):
            selected = stories[int(choice) - 1]
            logger.info(f"User selected story #{choice}: {selected['headline'][:60]}")
            return selected
    except (asyncio.TimeoutError, EOFError):
        pass

    logger.info(f"Auto-selecting top story: {stories[0]['headline'][:60]}")
    print(f"\n  ⏰ Auto-selected story #1.")
    return stories[0]


async def run_pipeline() -> None:
    """
    Full pipeline with proper error handling and logging.

    Steps:
     1. Load config
     2. NewsAgent: fetch top 5 stories
     3. Select story (manual or auto)
     4. ScriptAgent: write script
     5. Parallel: VoiceAgent + ThumbnailAgent
     6. CaptionAgent: generate captions
     7. VideoAgent: fetch b-roll + assemble video
     8. CaptionAgent: cut Shorts clip
     9. SEOAgent: generate metadata
    10. TelegramNotifier: send approval request
    11. Wait for approval (2-hour timeout)
    12. PublisherAgent: upload
    13. Cleanup + cost summary
    """
    pipeline_start = time.time()

    print(BANNER)
    logger.info("Pipeline starting...")

    # ── 1. Load config ──────────────────────────────────────────────────
    logger.info("Step 1/13: Loading configuration...")
    config = Config.from_env()

    file_mgr = FileManager({
        "output_dir": str(config.OUTPUT_DIR),
        "assets_dir": str(config.ASSETS_DIR),
    })
    file_mgr.ensure_assets()

    notifier = TelegramNotifier({
        "telegram_token": config.TELEGRAM_BOT_TOKEN,
        "telegram_chat_id": config.TELEGRAM_CHAT_ID,
    })

    rate_limiter = RateLimiter()
    temp_dir = file_mgr.get_temp_dir()

    # ── 2. Fetch news ───────────────────────────────────────────────────
    logger.info("Step 2/13: Fetching trending news stories...")
    news_agent = NewsAgent(config)
    stories = await news_agent.fetch_stories(num_stories=5)

    if not stories:
        logger.error("No stories found! Check your niche keywords or internet connection.")
        return

    logger.info(f"Found {len(stories)} stories.")

    # ── 3. Select story ─────────────────────────────────────────────────
    logger.info("Step 3/13: Story selection...")
    selected_story = await _select_story(stories, timeout_seconds=60)
    headline = selected_story.get("headline", "untitled")

    # ── 4. Write script ─────────────────────────────────────────────────
    logger.info("Step 4/13: Writing video script with Gemini 2.5 Flash...")
    script_agent = ScriptAgent(config)
    async with StepSpinner(4, 13, "Writing video script (Gemini)", pipeline_start):
        script = await script_agent.write_script(selected_story)
    logger.info(f"Script generated — {len(script.get('segments', []))} segments.")

    # Persist for crash recovery
    import json
    (temp_dir / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False))
    (temp_dir / "story.json").write_text(json.dumps(selected_story, indent=2, ensure_ascii=False))
    (temp_dir / "headline.txt").write_text(headline)

    # ── 5. Parallel: Voiceover + Thumbnail ──────────────────────────────
    logger.info("Step 5/13: Generating voiceover + thumbnail in parallel...")
    voice_agent = VoiceAgent(config)
    thumb_agent = ThumbnailAgent(config)
    voiceover_path = file_mgr.generate_output_path(headline, "voiceover", "wav")
    thumbnail_path = file_mgr.generate_output_path(headline, "thumbnail", "jpg")

    async with StepSpinner(5, 13, "Voiceover + thumbnail (parallel)", pipeline_start):
        voice_result, thumbnail_result = await asyncio.gather(
            voice_agent.generate_voiceover(script, voiceover_path),
            thumb_agent.create_thumbnail(script, selected_story, thumbnail_path),
        )

    audio_path = voice_result["audio_path"]
    audio_duration = voice_result["duration_seconds"]
    logger.info(f"Voiceover: {audio_duration:.1f}s | Thumbnail: {thumbnail_path.name}")
    file_mgr.log_file_size(audio_path)
    file_mgr.log_file_size(thumbnail_path)

    # ── 6. Generate captions ────────────────────────────────────────────
    logger.info("Step 6/13: Generating captions with Whisper...")
    caption_agent = CaptionAgent(config)
    captions_srt_path = temp_dir / "captions.srt"
    async with StepSpinner(6, 13, "Generating captions (Whisper)", pipeline_start):
        caption_result = await caption_agent.generate_captions(audio_path, captions_srt_path)

    logger.info(f"Captions: {caption_result['segment_count']} segments, "
                f"{caption_result['word_count']} words.")

    # ── 7. Fetch b-roll + assemble video ────────────────────────────────
    logger.info("Step 7/13: Fetching b-roll from Pexels + assembling video...")
    # ── 7. Assemble video (b-roll fetch has its own bars already) ──────
    video_agent = VideoAgent(config)
    broll_keywords = script.get("broll_keywords", [])
    broll_paths = await video_agent.fetch_broll(broll_keywords)  # now returns URLs, not file paths

    music_path = file_mgr.get_music_file()
    if music_path:
        logger.info(f"Background music: {music_path.name}")
    else:
        logger.info("No background music found in assets/music/ — skipping.")

    main_video_path = file_mgr.generate_output_path(headline, "main", "mp4")
    async with StepSpinner(7, 13, "Assembling final video (MoviePy)", pipeline_start):
        await video_agent.assemble_video(
            voiceover_path=audio_path,
            broll_paths=broll_paths,
            script=script,
            captions_srt=captions_srt_path,
            output_path=main_video_path,
            music_path=music_path,
        )
        
    logger.info(f"Downloaded {len(broll_paths)} b-roll clips.")

    music_path = file_mgr.get_music_file()
    if music_path:
        logger.info(f"Background music: {music_path.name}")
    else:
        logger.info("No background music found in assets/music/ — skipping.")

    main_video_path = file_mgr.generate_output_path(headline, "main", "mp4")
    await video_agent.assemble_video(
        voiceover_path=audio_path,
        broll_paths=broll_paths,
        script=script,
        captions_srt=captions_srt_path,
        output_path=main_video_path,
        music_path=music_path,
    )
    logger.info(f"Main video assembled: {main_video_path.name}")
    file_mgr.log_file_size(main_video_path)

    # ── 8. Cut Shorts clip ──────────────────────────────────────────────
    logger.info("Step 8/13: Cutting YouTube Shorts clip (1080x1920)...")
    shorts_path = file_mgr.generate_output_path(headline, "shorts", "mp4")
    async with StepSpinner(8, 13, "Cutting YouTube Shorts clip (1080x1920)", pipeline_start):
        await caption_agent.cut_shorts_clip(main_video_path, script, shorts_path)
    logger.info(f"Shorts clip: {shorts_path.name}")
    file_mgr.log_file_size(shorts_path)

    # ── 9. Generate SEO metadata ────────────────────────────────────────
    logger.info("Step 9/13: Generating SEO metadata with Gemini 2.5 Flash...")
    seo_agent = SEOAgent(config)
    async with StepSpinner(9, 13, "Generating SEO metadata (Gemini)", pipeline_start):
        metadata = await seo_agent.generate_metadata(script, selected_story)
    logger.info(f"SEO metadata ready — Title: \"{metadata.get('title', 'N/A')[:50]}...\"")
    logger.info(f"Tags: {len(metadata.get('tags', []))} | Chapters: {len(metadata.get('chapters', []))}")

    # ── 10. Send approval request ───────────────────────────────────────
    logger.info("Step 10/13: Sending approval request via Telegram...")
    await notifier.send_approval_request(
        script=script,
        metadata=metadata,
        thumbnail_path=thumbnail_path,
        video_preview_path=None,
    )

    # ── 11. Wait for approval ───────────────────────────────────────────
    logger.info("Step 11/13: Waiting for human approval (2-hour timeout)...")
    approved = await notifier.wait_for_approval(timeout_minutes=120)

    if not approved:
        logger.warning("❌ Video was REJECTED or approval timed out.")
        logger.info("Output files are preserved in the output/ folder for review.")
        _print_cost_summary(pipeline_start)
        file_mgr.cleanup_temp()
        return

    logger.info("✅ Video APPROVED! Proceeding to upload.")

    # ── 12. Upload to YouTube ───────────────────────────────────────────
    logger.info("Step 12/13: Uploading to YouTube...")
    publisher = PublisherAgent(config)
    publish_time = await publisher.get_optimal_publish_time()
    logger.info(f"Scheduled publish time: {publish_time.isoformat()}")

    try:
        async with StepSpinner(12, 13, "Uploading to YouTube", pipeline_start):
            upload_result = await publisher.upload_video(
                video_path=main_video_path,
                thumbnail_path=thumbnail_path,
                metadata=metadata,
                publish_at=publish_time,
                shorts_path=shorts_path,
            )

        video_id = upload_result.get("video_id", "unknown")
        shorts_id = upload_result.get("shorts_id")
        video_url = upload_result.get("video_url", "")
        shorts_url = upload_result.get("shorts_url", "")

        logger.info(f"🎉 Main video uploaded: {video_url}")
        if shorts_id:
            logger.info(f"🎉 Shorts uploaded: {shorts_url}")

        await notifier.send_success_notification(video_id, shorts_id)

        print("\n" + "=" * 72)
        print("  🎉  UPLOAD SUCCESSFUL!")
        print("=" * 72)
        print(f"  📺 Video:  {video_url}")
        if shorts_url:
            print(f"  📱 Shorts: {shorts_url}")
        print(f"  📅 Scheduled: {publish_time.strftime('%Y-%m-%d %H:%M UTC')}")
        print("=" * 72 + "\n")

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        logger.info("Your video files are preserved in the output/ folder. "
                     "You can upload them manually.")

    # ── 13. Cleanup + cost summary ──────────────────────────────────────
    logger.info("Step 13/13: Cleaning up temporary files...")
    file_mgr.cleanup_temp()
    _print_cost_summary(pipeline_start)


def _print_cost_summary(start_time: float) -> None:
    """Print pipeline duration and cost summary."""
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print("\n" + "=" * 72)
    print("  📊  PIPELINE SUMMARY")
    print("=" * 72)
    print(f"  ⏱  Duration:    {minutes}m {seconds}s")
    print(f"  💰 Total cost:  $0.00")
    print(f"  📡 APIs used:   Google News RSS (free)")
    print(f"                  Gemini 2.5 Flash (free tier)")
    print(f"                  Pexels Video API (free tier)")
    print(f"                  YouTube Data API v3 (free tier)")
    print(f"  🎤 TTS:         Kokoro (local, free)")
    print(f"  📝 Captions:    Whisper (local, free)")
    print(f"  🎬 Editing:     MoviePy + FFmpeg (open-source)")
    print("=" * 72 + "\n")

    logger.info(f"Pipeline completed in {minutes}m {seconds}s. Total API cost: $0.00")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
        sys.exit(0)
    except Exception as exc:
        logger.exception(f"Pipeline failed with unexpected error: {exc}")
        sys.exit(1)
