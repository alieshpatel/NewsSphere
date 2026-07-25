import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class FileManager:
    def __init__(self, config: dict):
        self.config = config
        self.output_dir = Path(config.get('output_dir', 'output')).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.assets_dir = Path(config.get('assets_dir', 'assets')).resolve()
        self.temp_base_dir = self.output_dir / "tmp"

    def _slugify(self, text: str, max_length: int = 50) -> str:
        text = text.lower()
        text = re.sub(r'[^a-z0-9\s-]', '', text)
        text = re.sub(r'[\s-]+', '-', text).strip('-')
        return text[:max_length].strip('-')

    def generate_output_path(self, headline: str, suffix: str, ext: str) -> Path:
        date_str = datetime.now().strftime('%Y-%m-%d')
        slug = self._slugify(headline)
        filename = f"{date_str}_{slug}_{suffix}.{ext.lstrip('.')}"
        return self.output_dir / filename

    def get_temp_dir(self) -> Path:
        self.temp_base_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = self.temp_base_dir / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir

    def cleanup_temp(self) -> None:
        try:
            if self.temp_base_dir.exists() and self.temp_base_dir.is_dir():
                shutil.rmtree(self.temp_base_dir)
                logger.info(f"Cleaned up temporary directory: {self.temp_base_dir}")
        except Exception as e:
            logger.error(f"Failed to cleanup temp directory {self.temp_base_dir}: {e}")

    def ensure_assets(self) -> None:
        fonts_dir = self.assets_dir / "fonts"
        music_dir = self.assets_dir / "music"
        
        fonts_dir.mkdir(parents=True, exist_ok=True)
        music_dir.mkdir(parents=True, exist_ok=True)

        intro_path = self.assets_dir / "intro.mp4"
        outro_path = self.assets_dir / "outro.mp4"

        if not intro_path.exists():
            logger.warning("Intro video (intro.mp4) is missing in assets directory.")
        if not outro_path.exists():
            logger.warning("Outro video (outro.mp4) is missing in assets directory.")

    def get_music_file(self) -> Optional[Path]:
        music_dir = self.assets_dir / "music"
        if not music_dir.exists():
            return None
            
        for ext in ['*.mp3', '*.wav']:
            for file in music_dir.glob(ext):
                if file.is_file():
                    return file
        return None

    def log_file_size(self, path: Path) -> None:
        if not path.exists():
            logger.error(f"File not found for size logging: {path}")
            return
            
        try:
            size_bytes = path.stat().st_size
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if size_bytes < 1024.0:
                    logger.info(f"File size for {path.name}: {size_bytes:.2f} {unit}")
                    return
                size_bytes /= 1024.0
        except Exception as e:
            logger.error(f"Error getting file size for {path}: {e}")
