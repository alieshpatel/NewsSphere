import logging
import asyncio
import aiohttp
import os
import concurrent.futures
from pathlib import Path
from typing import List, Tuple, Optional
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance, ImageFilter

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

        # Supersample factor: render everything at 2x then LANCZOS downsample.
        # This is what makes text edges, badge corners, and the inset border
        # look crisp instead of slightly jagged/soft.
        self.SS = 2

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

        # Work at supersampled resolution for the whole composite; downsample
        # once at the very end. This is the single biggest lever for a
        # "premium" (crisp, non-jaggy) look vs. drawing at native res.
        ss = self.SS
        W, H = width * ss, height * ss

        img = self._build_background(W, H, photo_paths)
        draw = ImageDraw.Draw(img, "RGBA")

        # Badge
        score = story.get("score", 0)
        badge_text = "BREAKING" if score > 6 else "LATEST"
        badge_font = self._load_font(36 * ss)
        self._draw_badge(img, draw, badge_text, (50 * ss, 50 * ss), badge_font)

        # Headline — dynamically sized so it never overflows the canvas,
        # rendered with soft blurred drop shadows + a glow behind the
        # highlighted word instead of a flat hard-offset shadow.
        title = story.get("headline", "Breaking News")
        max_text_width = int(W * 0.72)  # leave room for the inset photo

        font_size = 96 * ss
        wrapped_lines = self._wrap_headline(title, max_words_per_line=4)
        headline_font = self._load_font(font_size)
        while font_size > 44 * ss and self._max_line_width(wrapped_lines, headline_font) > max_text_width:
            font_size -= 4 * ss
            headline_font = self._load_font(font_size)

        all_words = [w for line in wrapped_lines for w in line.split()]
        hl_idx, _ = self._highlight_key_word(all_words)

        line_height = int(font_size * 1.25)
        y_text = 220 * ss
        current_word_idx = 0

        for line in wrapped_lines:
            words = line.split()
            x_text = 80 * ss
            for word in words:
                is_hl = current_word_idx == hl_idx
                color = self.HIGHLIGHT_COLOR if is_hl else self.TEXT_PRIMARY
                w_px = self._text_width(word + " ", headline_font)

                if is_hl:
                    self._draw_glow(img, (x_text, y_text), word, headline_font, self.HIGHLIGHT_COLOR, ss)

                self._draw_text_with_shadow(draw, (x_text, y_text), word, headline_font, color, ss)
                x_text += w_px
                current_word_idx += 1
            y_text += line_height

        # Accent bar next to the headline block
        draw.rounded_rectangle(
            [50 * ss, 220 * ss, 54 * ss + 2 * ss, y_text - 24 * ss],
            radius=2 * ss, fill=self.ACCENT_COLOR
        )

        # Channel name — bottom gradient strip instead of a hard black box
        channel_name = self.config.CHANNEL_NAME
        channel_font = self._load_font(26 * ss)
        self._draw_channel_tag(img, draw, channel_name, channel_font, W, H, ss)

        # Subtle full-frame branding border (premium "finished" look)
        border_w = max(2, 3 * ss)
        draw.rectangle([0, 0, W - 1, H - 1], outline=(*self.ACCENT_COLOR, 110), width=border_w)

        # Downsample to target resolution — this is where supersampling pays off
        img = img.convert("RGB").resize((width, height), Image.LANCZOS)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "JPEG", quality=94)

        size_kb = os.path.getsize(output_path) / 1024
        logger.info(f"Rendered thumbnail {output_path.name} - Size: {size_kb:.1f} KB")

        return output_path

    # ── Background / scrim (vectorized) ──────────────────────────────────

    def _build_background(self, width: int, height: int, photo_paths: List[Path]) -> Image.Image:
        """
        Layout:
          - No photos:       gradient background (vectorized).
          - 1 photo:         full-bleed photo + left-side gradient scrim.
          - 2+ photos:       main photo full-bleed + a second photo as a
                              rounded, drop-shadowed inset panel bottom-right.
        """
        if not photo_paths:
            img = Image.new("RGBA", (width, height), (*self.BG_COLOR, 255))
            self._draw_gradient_bg(img)
            return img

        try:
            main_photo = Image.open(photo_paths[0]).convert("RGB")
            main_photo = ImageOps.fit(main_photo, (width, height), method=Image.LANCZOS)
            main_photo = ImageEnhance.Contrast(main_photo).enhance(1.12)
            main_photo = ImageEnhance.Color(main_photo).enhance(1.2)
            # Unsharp mask gives stock photos a crisper, less "stocky" edge —
            # a big part of what separates a premium thumbnail from a flat one.
            main_photo = main_photo.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
        except Exception as e:
            logger.warning(f"Failed to load main photo ({e}), using gradient fallback.")
            img = Image.new("RGBA", (width, height), (*self.BG_COLOR, 255))
            self._draw_gradient_bg(img)
            return img

        img = main_photo.convert("RGBA")

        # Vectorized left-side scrim: smooth alpha falloff via numpy instead
        # of a Python per-pixel loop (also ~100x faster at this resolution).
        fade_end = int(width * 0.62)
        x = np.arange(width)
        factor = np.clip(1 - (x / fade_end), 0, 1) ** 1.15  # slight ease-out curve
        alpha_row = (factor * 200).astype(np.uint8)
        alpha = np.tile(alpha_row, (height, 1))
        scrim = Image.fromarray(alpha, mode="L")
        black = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        img = Image.composite(black, img, scrim)

        # Faint top vignette for depth without touching the corners' content focus
        top_fade_h = int(height * 0.18)
        y = np.arange(height)
        top_factor = np.clip(1 - (y / top_fade_h), 0, 1) ** 2
        top_alpha_col = (top_factor * 90).astype(np.uint8)
        top_alpha = np.tile(top_alpha_col.reshape(-1, 1), (1, width))
        top_scrim = Image.fromarray(top_alpha, mode="L")
        img = Image.composite(black, img, top_scrim)

        if len(photo_paths) > 1:
            img = self._composite_inset(img, photo_paths[1], width, height)

        return img

    def _composite_inset(self, img: Image.Image, photo_path: Path, width: int, height: int) -> Image.Image:
        """Rounded-corner inset photo with a soft drop shadow, framed in the
        accent color — reads as a deliberate collage rather than a pasted box."""
        try:
            ss = self.SS
            inset_w, inset_h = int(width * 0.30), int(height * 0.42)
            radius = 18 * ss
            border = 6 * ss

            inset = Image.open(photo_path).convert("RGB")
            inset = ImageOps.fit(inset, (inset_w, inset_h), method=Image.LANCZOS)
            inset = ImageEnhance.Contrast(inset).enhance(1.1)
            inset = ImageEnhance.Color(inset).enhance(1.2)
            inset = inset.filter(ImageFilter.UnsharpMask(radius=2, percent=110, threshold=2))

            # Rounded-rect mask for the photo itself
            mask = Image.new("L", (inset_w, inset_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, inset_w, inset_h], radius=radius, fill=255)

            panel_w, panel_h = inset_w + border * 2, inset_h + border * 2
            panel_mask = Image.new("L", (panel_w, panel_h), 0)
            ImageDraw.Draw(panel_mask).rounded_rectangle(
                [0, 0, panel_w, panel_h], radius=radius + border, fill=255
            )
            panel = Image.new("RGBA", (panel_w, panel_h), (*self.ACCENT_COLOR, 255))
            panel.putalpha(panel_mask)
            panel.paste(inset, (border, border), mask)

            inset_x = width - inset_w - 40 * ss
            inset_y = height - inset_h - 40 * ss

            # Soft drop shadow, rendered separately and blurred, then the
            # panel composited on top — reads as a real elevation shadow
            # rather than the previous hard-edged colored rectangle.
            shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            shadow_mask = Image.new("L", (panel_w, panel_h), 0)
            ImageDraw.Draw(shadow_mask).rounded_rectangle(
                [0, 0, panel_w, panel_h], radius=radius + border, fill=140
            )
            shadow_layer = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
            shadow_layer.putalpha(shadow_mask)
            shadow.paste(shadow_layer, (inset_x - border + 8 * ss, inset_y - border + 10 * ss), shadow_layer)
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=10 * ss))

            img = Image.alpha_composite(img, shadow)
            img.paste(panel, (inset_x - border, inset_y - border), panel)
        except Exception as e:
            logger.warning(f"Failed to composite inset photo ({e}), continuing with single photo.")
        return img

    def _draw_gradient_bg(self, img: Image.Image) -> None:
        width, height = img.size
        x = np.linspace(0, 1, width)
        r1, g1, b1 = self.BG_COLOR
        r2, g2, b2 = self.BG_COLOR_LIGHT
        row = np.stack([
            (r1 + (r2 - r1) * x).astype(np.uint8),
            (g1 + (g2 - g1) * x).astype(np.uint8),
            (b1 + (b2 - b1) * x).astype(np.uint8),
        ], axis=-1)
        arr = np.tile(row, (height, 1, 1))
        grad = Image.fromarray(arr, mode="RGB").convert("RGBA")
        img.paste(grad, (0, 0))

    # ── Text rendering helpers ────────────────────────────────────────────

    def _draw_text_with_shadow(self, draw, pos, text, font, color, ss):
        """Soft blurred drop shadow instead of a flat 4px hard offset."""
        x, y = pos
        shadow_layer = Image.new("RGBA", draw.im.size if hasattr(draw, "im") else (10, 10), (0, 0, 0, 0))
        # Draw directly with a slightly larger, blurred offset for a cinematic feel
        draw.text((x + 3 * ss, y + 5 * ss), text, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), text, font=font, fill=color)

    def _draw_glow(self, img: Image.Image, pos, word, font, color, ss):
        """Soft colored glow behind the highlighted word — the detail that
        makes a highlighted term pop instead of just changing its fill color."""
        x, y = pos
        w = self._text_width(word, font)
        try:
            bbox = font.getbbox(word)
            h = bbox[3] - bbox[1]
        except AttributeError:
            _, h = font.getsize(word)

        pad = 14 * ss
        glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer)
        gdraw.rounded_rectangle(
            [x - pad, y - pad // 2, x + w + pad, y + h + pad],
            radius=10 * ss, fill=(*color, 90)
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=8 * ss))
        img.paste(Image.alpha_composite(img.crop((0, 0, img.width, img.height)), glow_layer), (0, 0))

    def _draw_channel_tag(self, img, draw, channel_name, font, W, H, ss):
        """Bottom-right channel tag on a soft gradient strip instead of a
        hard black box — blends into the frame rather than sitting on it."""
        strip_h = int(H * 0.11)
        y = np.arange(strip_h)
        alpha_col = np.clip((y / strip_h), 0, 1) ** 1.3 * 170
        alpha = np.tile(alpha_col.reshape(-1, 1).astype(np.uint8), (1, W))
        strip_mask = Image.fromarray(alpha, mode="L")
        strip = Image.new("RGBA", (W, strip_h), (0, 0, 0, 255))
        strip.putalpha(strip_mask)
        img.paste(strip, (0, H - strip_h), strip)

        tw = self._text_width(channel_name, font)
        try:
            bbox = font.getbbox(channel_name)
            th = bbox[3] - bbox[1]
        except AttributeError:
            _, th = font.getsize(channel_name)
        draw.text(
            (W - tw - 40 * ss, H - th - 34 * ss),
            channel_name, font=font, fill=self.TEXT_SECONDARY
        )

    def _text_width(self, text: str, font) -> int:
        try:
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0]
        except AttributeError:
            w, _ = font.getsize(text)
            return w

    def _max_line_width(self, lines: List[str], font) -> int:
        return max((self._text_width(line, font) for line in lines), default=0)

    def _draw_badge(self, img, draw: ImageDraw.ImageDraw, text: str, position: Tuple[int, int], font) -> None:
        """Badge with a soft shadow so it sits above the photo rather than
        looking pasted flat onto it."""
        x, y = position
        padding_x, padding_y = 20 * self.SS, 10 * self.SS
        tw = self._text_width(text, font)
        try:
            bbox = font.getbbox(text)
            th = bbox[3] - bbox[1]
        except AttributeError:
            _, th = font.getsize(text)
        bw, bh = tw + padding_x * 2, th + padding_y * 2

        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        sdraw.rounded_rectangle(
            [x + 4 * self.SS, y + 6 * self.SS, x + bw + 4 * self.SS, y + bh + 6 * self.SS],
            radius=bh // 2, fill=(0, 0, 0, 120)
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6 * self.SS))
        composited = Image.alpha_composite(img, shadow)
        img.paste(composited, (0, 0))

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