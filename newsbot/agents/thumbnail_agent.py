import logging
import asyncio
import aiohttp
import os
import concurrent.futures
from pathlib import Path
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance

import google.generativeai as genai

from config import Config

logger = logging.getLogger(__name__)

GEMINI_MODEL_NAME = "gemini-2.5-flash"


class ThumbnailAgent:
    def __init__(self, config: Config):
        self.config = config
        self.BG_COLOR = (15, 15, 30)
        self.BG_COLOR_LIGHT = (25, 30, 60)
        self.ACCENT_COLOR = (220, 50, 50)
        self.TEXT_PRIMARY = (255, 255, 255)
        self.TEXT_SECONDARY = (180, 190, 230)
        self.HIGHLIGHT_COLOR = (255, 200, 0)

        self.font_path = self.config.ASSETS_DIR / 'fonts' / 'Montserrat-Bold.ttf'

        genai.configure(api_key=self.config.GEMINI_API_KEY)
        self._gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)

        self.pexels_headers = {"Authorization": self.config.PEXELS_API_KEY}

    # ── Public entrypoint ────────────────────────────────────────────────

    async def create_thumbnail(self, script: dict, story: dict, output_path: Path) -> Path:
        keywords = await self._get_photo_keywords(story, script)
        logger.info(f"Thumbnail photo keywords: {keywords}")

        photo_paths = await self._fetch_background_photos(keywords, output_path.parent, max_photos=2)
        if not photo_paths:
            logger.warning("No thumbnail photos found for any keyword — using gradient fallback.")

        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool,
                self._create_thumbnail_sync,
                script,
                story,
                output_path,
                photo_paths,
            )

        for p in photo_paths:
            try:
                if p.exists():
                    p.unlink()
            except Exception as e:
                logger.debug(f"Could not remove temp bg photo {p}: {e}")

        return result

    # ── Gemini keyword generation ────────────────────────────────────────

    async def _get_photo_keywords(self, story: dict, script: dict) -> List[str]:
        headline = story.get("headline", "")
        summary = script.get("full_script", "")[:500]
        existing_tags = story.get("keywords_matched", [])

        prompt = (
            "You are choosing stock photo search terms for a news video thumbnail.\n"
            f"Headline: {headline}\n"
            f"Context: {summary}\n"
            f"Existing tags: {', '.join(existing_tags)}\n\n"
            "Return 3-5 short, concrete, visual search phrases (2-4 words each) "
            "that would find striking, relevant stock PHOTOS on Pexels — "
            "favor tangible objects/scenes over abstract concepts. "
            "Reply with ONLY a comma-separated list, nothing else."
        )

        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                None, lambda: self._gemini_model.generate_content(prompt)
            )
            raw = (response.text or "").strip()
            keywords = [k.strip() for k in raw.split(",") if k.strip()]
            if keywords:
                logger.info(f"Gemini returned keywords: {keywords}")
                return keywords[:5]
            logger.warning("Gemini returned an empty keyword list, using fallback.")
        except Exception as e:
            logger.warning(f"Gemini keyword generation failed ({e}), using fallback.")

        if existing_tags:
            return existing_tags[:5]
        return [w for w in headline.split() if len(w) > 4][:5] or ["technology news"]

    # ── Pexels photo fetch (multiple) ─────────────────────────────────────

    async def _fetch_background_photos(
        self, keywords: List[str], dest_dir: Path, max_photos: int = 2
    ) -> List[Path]:
        """Fetch up to max_photos distinct photos, trying successive keywords."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        found: List[Path] = []

        async with aiohttp.ClientSession(headers=self.pexels_headers) as session:
            for keyword in keywords:
                if len(found) >= max_photos:
                    break

                url = f"https://api.pexels.com/v1/search?query={keyword}&per_page=5&orientation=landscape"
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            logger.warning(f"Pexels photo search failed for '{keyword}': {response.status}")
                            continue
                        data = await response.json()
                except Exception as e:
                    logger.warning(f"Network error searching photos for '{keyword}': {e}")
                    continue

                photos = data.get("photos", [])
                if not photos:
                    logger.warning(f"No photos found for '{keyword}'")
                    continue

                photo = photos[0]
                src = photo.get("src", {})
                download_url = src.get("large2x") or src.get("original") or src.get("large")
                if not download_url:
                    continue

                dest_path = dest_dir / f"thumb_bg_{keyword.replace(' ', '_')}.jpg"
                try:
                    async with session.get(download_url) as dl_resp:
                        if dl_resp.status != 200:
                            continue
                        with open(dest_path, "wb") as f:
                            while True:
                                chunk = await dl_resp.content.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                    logger.info(f"Fetched thumbnail photo for '{keyword}' -> {dest_path.name}")
                    found.append(dest_path)
                except Exception as e:
                    logger.warning(f"Failed downloading photo for '{keyword}': {e}")
                    continue

        return found

    # ── Sync rendering ────────────────────────────────────────────────────

    def _create_thumbnail_sync(
        self,
        script: dict,
        story: dict,
        output_path: Path,
        photo_paths: List[Path],
    ) -> Path:
        width = self.config.THUMBNAIL_WIDTH
        height = self.config.THUMBNAIL_HEIGHT

        img = self._build_background(width, height, photo_paths)
        draw = ImageDraw.Draw(img)

        # Badge
        score = story.get("score", 0)
        badge_text = "BREAKING" if score > 6 else "LATEST"
        badge_font = self._load_font(36)
        self._draw_badge(draw, badge_text, (50, 50), badge_font)

        # Headline (left-aligned, over the scrim so it stays readable)
        title = story.get("headline", "Breaking News")
        wrapped_lines = self._wrap_headline(title, max_words_per_line=4)

        headline_font = self._load_font(96)
        y_text = 220

        all_words = []
        for line in wrapped_lines:
            all_words.extend(line.split())
        hl_idx, hl_word = self._highlight_key_word(all_words)

        current_word_idx = 0
        for line in wrapped_lines:
            words = line.split()
            x_text = 80
            for word in words:
                color = self.HIGHLIGHT_COLOR if current_word_idx == hl_idx else self.TEXT_PRIMARY
                try:
                    bbox = headline_font.getbbox(word + " ")
                    w = bbox[2] - bbox[0]
                except AttributeError:
                    w, _ = headline_font.getsize(word + " ")

                draw.text((x_text + 4, y_text + 4), word, font=headline_font, fill=(0, 0, 0))
                draw.text((x_text, y_text), word, font=headline_font, fill=color)
                x_text += w
                current_word_idx += 1

            y_text += int(96 * 1.25)

        draw.rectangle([50, 220, 54, y_text - 24], fill=self.ACCENT_COLOR)

        # Channel name
        channel_name = self.config.CHANNEL_NAME
        channel_font = self._load_font(26)
        try:
            bbox = channel_font.getbbox(channel_name)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            tw, th = channel_font.getsize(channel_name)
        draw.rectangle(
            [width - tw - 60, height - th - 46, width - 20, height - 20],
            fill=(0, 0, 0),
        )
        draw.text((width - tw - 40, height - th - 34), channel_name, font=channel_font, fill=self.TEXT_SECONDARY)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "JPEG", quality=92)

        size_kb = os.path.getsize(output_path) / 1024
        logger.info(f"Rendered thumbnail {output_path.name} - Size: {size_kb:.1f} KB")

        return output_path

    def _build_background(self, width: int, height: int, photo_paths: List[Path]) -> Image.Image:
        """
        Layout:
          - No photos:       gradient background (original fallback).
          - 1 photo:         full-bleed photo + left-side text scrim only.
          - 2+ photos:       main photo full-bleed + a second photo as a
                              bordered inset panel bottom-right, giving a
                              collage look instead of a single flat image.
        Corners are left untouched — no full-frame vignette.
        """
        if not photo_paths:
            img = Image.new("RGB", (width, height), self.BG_COLOR)
            self._draw_gradient_bg(img)
            return img

        try:
            main_photo = Image.open(photo_paths[0]).convert("RGB")
            main_photo = ImageOps.fit(main_photo, (width, height), method=Image.LANCZOS)
            main_photo = ImageEnhance.Contrast(main_photo).enhance(1.12)
            main_photo = ImageEnhance.Color(main_photo).enhance(1.2)
        except Exception as e:
            logger.warning(f"Failed to load main photo ({e}), using gradient fallback.")
            img = Image.new("RGB", (width, height), self.BG_COLOR)
            self._draw_gradient_bg(img)
            return img

        # Left-side scrim only (text legibility), fades out by mid-canvas —
        # right two-thirds of the photo stays fully visible.
        scrim = Image.new("L", (width, height), 0)
        scrim_draw = ImageDraw.Draw(scrim)
        fade_end = int(width * 0.62)
        for x in range(width):
            if x < fade_end:
                factor = 1 - (x / fade_end)
                alpha = int(200 * factor)
            else:
                alpha = 0
            scrim_draw.line([(x, 0), (x, height)], fill=alpha)
        black = Image.new("RGB", (width, height), (0, 0, 0))
        img = Image.composite(black, main_photo, scrim)

        # Second photo as an inset panel — creates the multi-photo collage look
        if len(photo_paths) > 1:
            try:
                inset = Image.open(photo_paths[1]).convert("RGB")
                inset_w, inset_h = int(width * 0.30), int(height * 0.42)
                inset = ImageOps.fit(inset, (inset_w, inset_h), method=Image.LANCZOS)
                inset = ImageEnhance.Contrast(inset).enhance(1.1)
                inset = ImageEnhance.Color(inset).enhance(1.2)

                inset_x = width - inset_w - 40
                inset_y = height - inset_h - 40

                border = 6
                bordered = Image.new(
                    "RGB",
                    (inset_w + border * 2, inset_h + border * 2),
                    self.ACCENT_COLOR,
                )
                bordered.paste(inset, (border, border))
                img.paste(bordered, (inset_x - border, inset_y - border))
            except Exception as e:
                logger.warning(f"Failed to composite inset photo ({e}), continuing with single photo.")

        return img

    def _draw_gradient_bg(self, img: Image.Image) -> None:
        width, height = img.size
        pixels = img.load()
        r1, g1, b1 = self.BG_COLOR
        r2, g2, b2 = self.BG_COLOR_LIGHT
        for x in range(width):
            factor = x / max(1, width - 1)
            r = int(r1 + (r2 - r1) * factor)
            g = int(g1 + (g2 - g1) * factor)
            b = int(b1 + (b2 - b1) * factor)
            for y in range(height):
                pixels[x, y] = (r, g, b)

    def _draw_badge(self, draw: ImageDraw.ImageDraw, text: str, position: Tuple[int, int], font) -> None:
        x, y = position
        padding_x, padding_y = 20, 10
        try:
            bbox = font.getbbox(text)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = font.getsize(text)
        bw, bh = tw + padding_x * 2, th + padding_y * 2
        draw.rounded_rectangle([x, y, x + bw, y + bh], radius=bh // 2, fill=self.ACCENT_COLOR)
        draw.text((x + padding_x, y + padding_y - 2), text, font=font, fill=self.TEXT_PRIMARY)

    def _wrap_headline(self, headline: str, max_words_per_line: int = 3) -> List[str]:
        stop_words = {"the", "a", "an", "is", "are", "in", "on", "at", "to", "for", "with", "by", "of", "and"}
        words = [w for w in headline.split() if w.lower() not in stop_words][:6]
        lines = []
        for i in range(0, len(words), max_words_per_line):
            lines.append(" ".join(words[i:i + max_words_per_line]))
            if len(lines) == 2:
                break
        return lines

    def _highlight_key_word(self, words: List[str]) -> Tuple[int, str]:
        if not words:
            return 0, ""
        best_idx, best_score = 0, -1
        for i, word in enumerate(words):
            clean_word = word.strip(".,!?\"'")
            score = 10 if any(c.isdigit() for c in clean_word) else (
                5 if (clean_word.istitle() or clean_word.isupper()) else (
                    2 if len(clean_word) > 5 else 0
                )
            )
            if score > best_score:
                best_score, best_idx = score, i
        return best_idx, words[best_idx]

    def _load_font(self, size: int):
        try:
            if self.font_path.exists():
                return ImageFont.truetype(str(self.font_path), size)
            logger.warning(f"Font not found at {self.font_path}, falling back to default.")
            return ImageFont.load_default()
        except Exception as e:
            logger.warning(f"Error loading font {self.font_path}: {e}")
            return ImageFont.load_default()