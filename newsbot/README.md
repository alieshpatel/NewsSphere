# 🌐 NewsSphere — Automated News-to-YouTube Pipeline

> **Fully automated, zero-cost pipeline** that finds trending news, writes scripts, generates voiceovers, assembles videos, and publishes to YouTube — all using free-tier tools.

**Total monthly cost: $0.00**

---

## 📋 Table of Contents

- [Features](#-features)
- [Architecture](#-architecture)
- [System Requirements](#-system-requirements)
- [Quick Start](#-quick-start)
- [API Keys Setup](#-api-keys-setup)
- [YouTube OAuth Setup](#-youtube-oauth-setup)
- [Telegram Bot Setup](#-telegram-bot-setup)
- [Assets Setup](#-assets-setup)
- [Running the Pipeline](#-running-the-pipeline)
- [Scheduling Automation](#-scheduling-automation)
- [Pipeline Flow](#-pipeline-flow)
- [Free Tier Limits](#-free-tier-limits)
- [Troubleshooting](#-troubleshooting)

---

## ✨ Features

| Feature | Tool | Cost |
|---|---|---|
| News Discovery | Google News RSS + GNews API | Free |
| Script Writing | Gemini 2.5 Flash | Free (1,500 req/day) |
| Text-to-Speech | Kokoro TTS (local) | Free |
| B-Roll Footage | Pexels API | Free (200 req/hr) |
| Video Assembly | MoviePy v2 + FFmpeg | Free (open-source) |
| Captions/Subtitles | OpenAI Whisper (local) | Free |
| Thumbnails | Pillow | Free (open-source) |
| SEO Optimization | Gemini 2.5 Flash | Free |
| Human Approval | Telegram Bot | Free |
| YouTube Upload | YouTube Data API v3 | Free (10K units/day) |

---

## 🏗 Architecture

```
main.py (Orchestrator)
  │
  ├── NewsAgent       → Google News RSS / GNews API
  ├── ScriptAgent     → Gemini 2.5 Flash
  ├── VoiceAgent      → Kokoro TTS (local)
  ├── ThumbnailAgent  → Pillow (local)
  ├── CaptionAgent    → Whisper (local)
  ├── VideoAgent      → Pexels API + MoviePy
  ├── SEOAgent        → Gemini 2.5 Flash
  └── PublisherAgent  → YouTube Data API v3
```

---

## 💻 System Requirements

- **Python**: 3.11 or higher
- **FFmpeg**: Required for video processing
- **OS**: Windows / macOS / Linux
- **RAM**: 4 GB minimum (8 GB recommended for Whisper + video processing)
- **Disk**: ~2 GB for models + generated videos

### Installing FFmpeg

**Windows:**
```powershell
# Option 1: Using winget
winget install FFmpeg

# Option 2: Using Chocolatey
choco install ffmpeg

# Option 3: Manual download from https://ffmpeg.org/download.html
# Add to PATH after downloading
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update && sudo apt install ffmpeg
```

Verify FFmpeg is installed:
```bash
ffmpeg -version
```

---

## 🚀 Quick Start

### 1. Clone/Create the project

```bash
git clone <your-repo-url> NewsSphere
cd NewsSphere/newsbot
```

### 2. Create a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. First-run model downloads

The following models download **automatically** on first run:

| Model | Size | When |
|---|---|---|
| Kokoro TTS (`kokoro-v1.9.onnx` + `voices-v1.0.bin`) | ~320 MB | First `VoiceAgent` use |
| Whisper `base` model | ~74 MB | First `CaptionAgent` use |

No manual download needed — just ensure you have internet on the first run.

### 5. Configure environment

```bash
# Copy the template
cp .env.example .env

# Edit .env with your API keys (see next section)
```

### 6. Run

```bash
python main.py
```

---

## 🔑 API Keys Setup

All services below are **100% free** — no credit card required.

### 1. Gemini API Key (AI Script + SEO)

1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Sign in with your Google account
3. Click **"Get API key"** → **"Create API key"**
4. Copy the key

```env
GEMINI_API_KEY=your_key_here
```

> Free tier: 1,500 requests/day, 15 requests/minute

### 2. Pexels API Key (B-Roll Video)

1. Go to [Pexels API](https://www.pexels.com/api/)
2. Click **"Get Started"** and create a free account
3. Request an API key (instant approval)
4. Copy the key

```env
PEXELS_API_KEY=your_key_here
```

> Free tier: 200 requests/hour, 20,000 requests/month
>
> ⚠️ **IMPORTANT**: All videos using Pexels footage must include `"Video footage from Pexels.com"` in the YouTube description. This is handled automatically by the SEO agent.

### 3. GNews API Key (Fallback News Source)

1. Go to [GNews.io](https://gnews.io/)
2. Sign up for a free account
3. Get your API key from the dashboard

```env
GNEWS_API_KEY=your_key_here
```

> Free tier: 100 requests/day (used only as fallback)

---

## 📺 YouTube OAuth Setup

This is the most involved setup step. Follow carefully:

### Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **"Select a project"** → **"NEW PROJECT"**
3. Name: `NewsSphere` → Click **"CREATE"**

### Step 2: Enable YouTube Data API v3

1. In the sidebar: **APIs & Services** → **Library**
2. Search for **"YouTube Data API v3"**
3. Click on it → Click **"ENABLE"**

### Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **"External"** → Click **"CREATE"**
3. Fill in:
   - App name: `NewsSphere`
   - User support email: your email
   - Developer contact: your email
4. Click **"SAVE AND CONTINUE"**
5. On Scopes page: Click **"ADD OR REMOVE SCOPES"**
   - Add: `https://www.googleapis.com/auth/youtube.upload`
   - Add: `https://www.googleapis.com/auth/youtube`
   - Add: `https://www.googleapis.com/auth/youtube.force-ssl`
6. Click **"SAVE AND CONTINUE"**
7. On Test Users: Add your Google account email
8. Click **"SAVE AND CONTINUE"**

### Step 4: Create OAuth 2.0 Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **"+ CREATE CREDENTIALS"** → **"OAuth client ID"**
3. Application type: **"Desktop app"**
4. Name: `NewsSphere Desktop`
5. Click **"CREATE"**
6. Click **"DOWNLOAD JSON"**
7. Save the file as `client_secret.json` in the `newsbot/` directory

```env
YOUTUBE_CLIENT_SECRET_PATH=./client_secret.json
```

> On first run, a browser window will open for OAuth consent. After authorizing, a `token.json` file is saved for future runs.

---

## 🤖 Telegram Bot Setup

### Step 1: Create a Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name: `NewsSphere Approvals`
4. Choose a username: `newssphere_approval_bot`
5. Copy the **bot token**

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

### Step 2: Get Your Chat ID

1. Search for **@userinfobot** on Telegram
2. Send `/start`
3. It will reply with your **Chat ID** (a number)

```env
TELEGRAM_CHAT_ID=your_chat_id_here
```

### Step 3: Start Your Bot

1. Search for your bot by its username
2. Send `/start` to it (required before it can message you)

> ⚠️ If Telegram is not configured, the pipeline will **auto-approve** videos without human review.

---

## 🎨 Assets Setup

Create the required asset directories:

```
newsbot/
├── assets/
│   ├── fonts/           # Bold fonts for thumbnails
│   ├── music/           # Background music (royalty-free)
│   ├── intro.mp4        # 3-second branded intro
│   └── outro.mp4        # 5-second branded outro
```

### Background Music

Download **free, royalty-free** music:

1. Go to [ccMixter](http://ccmixter.org/view/media/free) or [Free Music Archive](https://freemusicarchive.org/)
2. Find a calm/upbeat background track
3. Download as `.mp3`
4. Save to `assets/music/background.mp3`

### Fonts (Optional)

The thumbnail agent will attempt to use **Montserrat Bold**:

1. Download from [Google Fonts - Montserrat](https://fonts.google.com/specimen/Montserrat)
2. Extract and copy `Montserrat-Bold.ttf` to `assets/fonts/`

> If the font is missing, it will attempt to download automatically, or fall back to PIL's default font.

### Intro/Outro Videos (Optional)

Create simple 3-5 second clips with your channel branding:

- **Intro** (3s): Your channel logo with a quick animation
- **Outro** (5s): Subscribe CTA with social links

Save as `assets/intro.mp4` and `assets/outro.mp4`.

> If these files are missing, the pipeline will skip them without error.

---

## ▶️ Running the Pipeline

### Basic Run

```bash
cd newsbot
python main.py
```

### What Happens

1. **News Discovery** — Fetches 5 top stories from Google News RSS
2. **Story Selection** — Shows stories in terminal; auto-selects #1 after 60s
3. **Script Generation** — Gemini writes a ~1200-word video script
4. **Parallel Processing** — Voiceover (Kokoro) + Thumbnail (Pillow) simultaneously
5. **Captions** — Whisper transcribes voiceover to SRT subtitles
6. **Video Assembly** — Downloads b-roll from Pexels, assembles with MoviePy
7. **Shorts Cut** — Extracts 60s vertical clip for YouTube Shorts
8. **SEO Metadata** — Gemini generates title, description, tags, chapters
9. **Approval** — Sends preview to Telegram for human review
10. **Upload** — Publishes to YouTube (scheduled or immediate)
11. **Cleanup** — Removes temp files, prints cost summary

### Output Files

Videos are saved to `output/` with descriptive names:

```
output/
├── 2026-07-25_ai-breakthrough-changes-everything_main.mp4
├── 2026-07-25_ai-breakthrough-changes-everything_shorts.mp4
├── 2026-07-25_ai-breakthrough-changes-everything_thumbnail.jpg
└── 2026-07-25_ai-breakthrough-changes-everything_voiceover.wav
```

---

## ⏰ Scheduling Automation

### Linux/macOS — Cron Job

Run every day at 6:00 AM:

```bash
# Open crontab editor
crontab -e

# Add this line (adjust paths):
0 6 * * * cd /path/to/NewsSphere/newsbot && /path/to/venv/bin/python main.py >> /var/log/newssphere.log 2>&1
```

### Windows — Task Scheduler

1. Open **Task Scheduler** (search in Start menu)
2. Click **"Create Basic Task"**
3. Name: `NewsSphere Daily`
4. Trigger: **Daily** at **6:00 AM**
5. Action: **Start a program**
   - Program: `C:\path\to\venv\Scripts\python.exe`
   - Arguments: `main.py`
   - Start in: `D:\NewsSphere\newsbot`
6. Click **"Finish"**

**PowerShell alternative:**
```powershell
$action = New-ScheduledTaskAction -Execute "D:\NewsSphere\venv\Scripts\python.exe" -Argument "main.py" -WorkingDirectory "D:\NewsSphere\newsbot"
$trigger = New-ScheduledTaskTrigger -Daily -At "06:00AM"
Register-ScheduledTask -TaskName "NewsSphere" -Action $action -Trigger $trigger -Description "Daily news video pipeline"
```

---

## 📊 Free Tier Limits

| Service | Limit | Usage per Video | Safe Daily Runs |
|---|---|---|---|
| Gemini 2.5 Flash | 1,500 req/day | ~3 requests | ~500 |
| Pexels API | 200 req/hr | ~15 requests | ~13/hour |
| GNews API | 100 req/day | 0-5 requests | 20+ |
| YouTube Data API | 10,000 units/day | ~1,600 units | ~6 |
| Kokoro TTS | Unlimited (local) | 1 run | ∞ |
| Whisper | Unlimited (local) | 1 run | ∞ |

> With rate limiting built in, you can safely run **2-3 videos per day** within all free tier limits.

---

## 🔧 Troubleshooting

### "FFmpeg not found"
Ensure FFmpeg is installed and in your system PATH. Restart your terminal after installation.

### "Kokoro model not found"
On first run, Kokoro downloads `kokoro-v1.9.onnx` (~320MB). Ensure you have internet access and sufficient disk space.

### "YouTube API quota exceeded"
You've hit the 10,000 units/day limit. Each video upload costs ~1,600 units. Wait until midnight Pacific Time for quota reset.

### "client_secret.json not found"
Follow the [YouTube OAuth Setup](#-youtube-oauth-setup) section to create and download OAuth credentials.

### Telegram bot not responding
1. Ensure you've sent `/start` to your bot
2. Verify the `TELEGRAM_CHAT_ID` is correct
3. Check that `TELEGRAM_BOT_TOKEN` is valid

### Video quality issues
- Ensure b-roll clips are HD (1920x1080+)
- Check that FFmpeg supports H.264 encoding (`ffmpeg -codecs | grep h264`)
- Increase `VIDEO_FPS` in `config.py` if needed

---

## 📁 Project Structure

```
newsbot/
├── main.py                    # Orchestrator — runs the full pipeline
├── agents/
│   ├── __init__.py
│   ├── news_agent.py          # Finds trending news (RSS + GNews)
│   ├── script_agent.py        # Writes video script (Gemini 2.5 Flash)
│   ├── voice_agent.py         # TTS voiceover (Kokoro, local)
│   ├── video_agent.py         # B-roll + video assembly (Pexels + MoviePy)
│   ├── thumbnail_agent.py     # Thumbnail generation (Pillow)
│   ├── caption_agent.py       # Captions + Shorts clip (Whisper + MoviePy)
│   ├── seo_agent.py           # SEO metadata (Gemini 2.5 Flash)
│   └── publisher_agent.py     # YouTube upload (Data API v3)
├── utils/
│   ├── __init__.py
│   ├── telegram_notify.py     # Human approval via Telegram bot
│   ├── file_manager.py        # File paths, temp cleanup, slugification
│   └── rate_limiter.py        # API rate limit tracking
├── assets/
│   ├── fonts/                 # Montserrat-Bold.ttf for thumbnails
│   ├── music/                 # Background music (.mp3/.wav)
│   ├── intro.mp4              # 3-second branded intro
│   └── outro.mp4              # 5-second branded outro
├── output/                    # Generated videos land here
├── config.py                  # All settings (loads from .env)
├── requirements.txt           # Python dependencies
├── .env.example               # API key template
└── README.md                  # This file
```

---

## 📄 License

This project uses only free and open-source tools. All generated content is yours to use commercially.

**Attribution requirements:**
- Pexels: Include `"Video footage from Pexels.com"` in video descriptions (handled automatically)
- Background music: Follow the license terms of the specific track you download

---

**Built with ❤️ and $0.00/month**
