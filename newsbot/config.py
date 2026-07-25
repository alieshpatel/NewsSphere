import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from dotenv import load_dotenv

@dataclass
class Config:
    GEMINI_API_KEY: str
    PEXELS_API_KEY: str
    GNEWS_API_KEY: str
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    
    YOUTUBE_CLIENT_SECRET_PATH: str = './client_secret.json'
    NICHE_KEYWORDS: List[str] = field(default_factory=lambda: ["artificial intelligence", "technology", "AI news", "tech industry"])
    CHANNEL_NAME: str = "Tech Daily AI"
    CHANNEL_TAGLINE: str = "Your Daily AI & Tech News"
    
    VIDEO_DURATION_TARGET_SECONDS: int = 480
    SHORTS_DURATION_SECONDS: int = 60
    VIDEO_WIDTH: int = 1920
    VIDEO_HEIGHT: int = 1080
    SHORTS_WIDTH: int = 1080
    SHORTS_HEIGHT: int = 1920
    VIDEO_FPS: int = 30
    THUMBNAIL_WIDTH: int = 1280
    THUMBNAIL_HEIGHT: int = 720
    
    KOKORO_VOICE: str = 'af_heart'
    KOKORO_SPEED: float = 1.1
    MUSIC_VOLUME: float = 0.08
    MAX_BROLL_PER_KEYWORD: int = 3
    GEMINI_MODEL: str = 'gemini-2.5-flash'
    
    OUTPUT_DIR: Path = Path('./output')
    ASSETS_DIR: Path = Path('./assets')
    
    OPTIMAL_PUBLISH_HOUR: int = 8
    GEMINI_SCRIPT_TEMPERATURE: float = 0.7
    GEMINI_SEO_TEMPERATURE: float = 0.4

    @classmethod
    def from_env(cls) -> 'Config':
        load_dotenv()
        
        niche_keywords_env = os.environ.get("NICHE_KEYWORDS")
        niche_keywords = [k.strip() for k in niche_keywords_env.split(",")] if niche_keywords_env else ["artificial intelligence", "technology", "AI news", "tech industry"]
        
        output_dir = Path(os.environ.get("OUTPUT_DIR", "./output"))
        assets_dir = Path(os.environ.get("ASSETS_DIR", "./assets"))
        
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)
        
        return cls(
            GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY", ""),
            PEXELS_API_KEY=os.environ.get("PEXELS_API_KEY", ""),
            GNEWS_API_KEY=os.environ.get("GNEWS_API_KEY", ""),
            TELEGRAM_BOT_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            TELEGRAM_CHAT_ID=os.environ.get("TELEGRAM_CHAT_ID", ""),
            YOUTUBE_CLIENT_SECRET_PATH=os.environ.get("YOUTUBE_CLIENT_SECRET_PATH", "./client_secret.json"),
            NICHE_KEYWORDS=niche_keywords,
            CHANNEL_NAME=os.environ.get("CHANNEL_NAME", "Tech Daily AI"),
            CHANNEL_TAGLINE=os.environ.get("CHANNEL_TAGLINE", "Your Daily AI & Tech News"),
            OUTPUT_DIR=output_dir,
            ASSETS_DIR=assets_dir,
        )
