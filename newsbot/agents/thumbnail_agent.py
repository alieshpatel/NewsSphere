import logging
import asyncio
import aiohttp
import os
import concurrent.futures
from pathlib import Path
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config import Config

logger = logging.getLogger(__name__)

class ThumbnailAgent:
    def __init__(self, config: Config):
        self.config = config
        # Thumbnail color palette for news channel
        self.BG_COLOR = (15, 15, 30)           # Deep navy
        self.BG_COLOR_LIGHT = (25, 30, 60)     # Slightly lighter navy for gradient
        self.ACCENT_COLOR = (220, 50, 50)      # Breaking news red
        self.TEXT_PRIMARY = (255, 255, 255)    # White
        self.TEXT_SECONDARY = (180, 190, 230)  # Light blue-white
        self.HIGHLIGHT_COLOR = (255, 200, 0)   # Amber for key words
        
        self.font_path = self.config.ASSETS_DIR / 'fonts' / 'Montserrat-Bold.ttf'

    def _create_thumbnail_sync(self, script: dict, story: dict, output_path: Path) -> Path:
        width = self.config.THUMBNAIL_WIDTH
        height = self.config.THUMBNAIL_HEIGHT
        
        img = Image.new("RGB", (width, height), self.BG_COLOR)
        
        # 1. Gradient background
        self._draw_gradient_bg(img)
        
        draw = ImageDraw.Draw(img)
        
        # 2. Red 'BREAKING' or 'LATEST' badge
        score = story.get("score", 0)
        badge_text = "BREAKING" if score > 6 else "LATEST"
        
        badge_font = self._load_font(36)
        self._draw_badge(draw, badge_text, (50, 50), badge_font)
        
        # 3. Main headline text
        title = story.get("title", "Breaking News")
        wrapped_lines = self._wrap_headline(title, max_words_per_line=4)
        
        headline_font = self._load_font(100)
        y_text = 200
        
        # Find highlight word across all lines
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
                
                # Check getbbox for size
                try:
                    bbox = headline_font.getbbox(word + " ")
                    w = bbox[2] - bbox[0]
                    h = bbox[3] - bbox[1]
                except AttributeError:
                    w, h = headline_font.getsize(word + " ")
                    
                draw.text((x_text, y_text), word, font=headline_font, fill=color)
                x_text += w
                current_word_idx += 1
                
            y_text += int(100 * 1.2) # line height
            
        # 4. Thin red accent line
        draw.rectangle([50, 200, 54, y_text - 20], fill=self.ACCENT_COLOR)
        
        # 5. Channel name
        channel_name = self.config.CHANNEL_NAME
        channel_font = self._load_font(24)
        
        try:
            bbox = channel_font.getbbox(channel_name)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            tw, th = channel_font.getsize(channel_name)
            
        draw.text((width - tw - 40, height - th - 30), channel_name, font=channel_font, fill=self.TEXT_SECONDARY)
        
        # 6. Vignette
        img = self._draw_vignette(img)
        
        # Save
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "JPEG", quality=90)
        
        size_kb = os.path.getsize(output_path) / 1024
        logger.info(f"Rendered thumbnail {output_path.name} - Size: {size_kb:.1f} KB")
        
        return output_path

    async def create_thumbnail(self, script: dict, story: dict, output_path: Path) -> Path:
        """
        Create a 1280x720 YouTube thumbnail using Pillow.
        Run in thread executor.
        """
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                self._create_thumbnail_sync,
                script,
                story,
                output_path
            )

    def _draw_gradient_bg(self, img: Image.Image) -> None:
        """Draw a horizontal gradient from BG_COLOR (left) to BG_COLOR_LIGHT (right)."""
        width, height = img.size
        pixels = img.load()
        
        r1, g1, b1 = self.BG_COLOR
        r2, g2, b2 = self.BG_COLOR_LIGHT
        
        for x in range(width):
            # Calculate interpolation factor
            factor = x / max(1, width - 1)
            
            r = int(r1 + (r2 - r1) * factor)
            g = int(g1 + (g2 - g1) * factor)
            b = int(b1 + (b2 - b1) * factor)
            
            for y in range(height):
                pixels[x, y] = (r, g, b)

    def _draw_badge(self, draw: ImageDraw.ImageDraw, text: str, position: Tuple[int, int], font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> None:
        """Draw a pill-shaped badge with ACCENT_COLOR background and white text."""
        x, y = position
        padding_x = 20
        padding_y = 10
        
        try:
            bbox = font.getbbox(text)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            tw, th = font.getsize(text)
            
        bw = tw + padding_x * 2
        bh = th + padding_y * 2
        
        # Draw pill shape
        draw.rounded_rectangle([x, y, x + bw, y + bh], radius=bh//2, fill=self.ACCENT_COLOR)
        draw.text((x + padding_x, y + padding_y - 2), text, font=font, fill=self.TEXT_PRIMARY)

    def _wrap_headline(self, headline: str, max_words_per_line: int = 3) -> List[str]:
        """Extract the most impactful 4-6 words from headline and split across 2 lines."""
        stop_words = {"the", "a", "an", "is", "are", "in", "on", "at", "to", "for", "with", "by", "of", "and"}
        words = [w for w in headline.split() if w.lower() not in stop_words]
        
        # Take up to 6 words
        words = words[:6]
        
        lines = []
        for i in range(0, len(words), max_words_per_line):
            lines.append(" ".join(words[i:i+max_words_per_line]))
            if len(lines) == 2:
                break
                
        return lines

    def _highlight_key_word(self, words: List[str]) -> Tuple[int, str]:
        """Pick the most impactful word to highlight in amber."""
        if not words:
            return 0, ""
            
        best_idx = 0
        best_score = -1
        
        for i, word in enumerate(words):
            score = 0
            clean_word = word.strip(".,!?\"'")
            
            # numbers/stats get highest score
            if any(c.isdigit() for c in clean_word):
                score = 10
            # uppercase/proper nouns
            elif clean_word.istitle() or clean_word.isupper():
                score = 5
            # length
            elif len(clean_word) > 5:
                score = 2
                
            if score > best_score:
                best_score = score
                best_idx = i
                
        return best_idx, words[best_idx]

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Try to load Montserrat-Bold at given size. Fallback to default PIL font."""
        try:
            if self.font_path.exists():
                return ImageFont.truetype(str(self.font_path), size)
            else:
                # Try standard download logic if absent, but synchronous download in ThreadPool is tricky
                # Just log and fallback since we cannot easily do async download here
                logger.warning(f"Font not found at {self.font_path}, falling back to default.")
                return ImageFont.load_default()
        except Exception as e:
            logger.warning(f"Error loading font {self.font_path}: {e}")
            return ImageFont.load_default()

    def _draw_vignette(self, img: Image.Image) -> Image.Image:
        """Apply a subtle vignette effect."""
        width, height = img.size
        
        # Create alpha mask for vignette
        mask = Image.new('L', (width, height), 0)
        draw = ImageDraw.Draw(mask)
        
        # Draw ellipse for the bright center
        # The larger the bounding box, the softer the vignette
        margin_x = int(width * 0.2)
        margin_y = int(height * 0.2)
        draw.ellipse((margin_x, margin_y, width - margin_x, height - margin_y), fill=255)
        
        # Blur the mask heavily
        mask = mask.filter(ImageFilter.GaussianBlur(radius=min(width, height) * 0.25))
        
        # Create a black image
        black = Image.new("RGB", (width, height), (0, 0, 0))
        
        # Composite the original image with the black image using the mask
        return Image.composite(img, black, mask)
