import logging, asyncio, json, re
import google.generativeai as genai
from config import Config

logger = logging.getLogger(__name__)

class SEOAgent:
    def __init__(self, config: Config):
        genai.configure(api_key=config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(config.GEMINI_MODEL)
        self.config = config
    
    async def generate_metadata(self, script: dict, story: dict) -> dict:
        """Prompt Gemini for complete YouTube metadata."""
        prompt = self._build_seo_prompt(script, story)
        
        for attempt in range(2):
            await asyncio.sleep(4)
            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=self.config.GEMINI_SEO_TEMPERATURE
                    )
                )
                
                text = self._strip_markdown_json(response.text)
                metadata = json.loads(text)
                
                if self._validate_metadata(metadata):
                    metadata['description'] = self._ensure_pexels_attribution(metadata['description'])
                    if len(metadata['description']) > 5000:
                        metadata['description'] = metadata['description'][:4997] + "..."
                    return metadata
                else:
                    raise ValueError("Metadata validation failed")
                    
            except Exception as e:
                logger.error(f"Error generating metadata: {e}")
                prompt += "\nReturn ONLY raw JSON, no markdown."
                
        raise ValueError("Failed to generate valid metadata after retries")
    
    def _build_seo_prompt(self, script: dict, story: dict) -> str:
        """Build the SEO prompt with all required fields."""
        segments_str = json.dumps(script.get('segments', []))
        full_script = script.get('full_script', '')[:1000]
        
        return f"""
Generate complete YouTube metadata for the following video script.
Story URL: {story.get('url')}
Channel Name: {self.config.CHANNEL_NAME}
Channel Tagline: {self.config.CHANNEL_TAGLINE}

Script Snippet: {full_script}...
Segments: {segments_str}

Required JSON output format exactly as follows:
{{
  "title": "final title choice (60 chars max, keyword-first)",
  "description": "full 3000-char description with:\\n- Hook paragraph (first 2 lines visible before 'Show more')\\n- What you'll learn in this video (bullet points)\\n- Timestamps/chapters (auto-generated from script segments)\\n- Source attribution: 'Story sourced from: {story.get('url')}'\\n- 'Video footage from Pexels.com'\\n- Subscribe CTA with channel name\\n- Hashtags at bottom (5-8 relevant)",
  "tags": ["list of 15-20 tags, mix of broad and specific"],
  "chapters": [
    {{"time": "0:00", "title": "Introduction"}}
  ],
  "pinned_comment": "engaging question to pin as first comment",
  "category_id": "28",
  "default_language": "en"
}}
"""
    
    def _validate_metadata(self, metadata: dict) -> bool:
        """Validate all required fields exist and meet constraints."""
        required_keys = ['title', 'description', 'tags', 'chapters', 'pinned_comment', 'category_id', 'default_language']
        if not all(k in metadata for k in required_keys):
            return False
            
        if not isinstance(metadata['tags'], list):
            return False
            
        if not isinstance(metadata['chapters'], list):
            return False
            
        return True
    
    def _ensure_pexels_attribution(self, description: str) -> str:
        """Ensure Pexels attribution is in the description. Add if missing."""
        attribution = "Video footage from Pexels.com"
        if attribution.lower() not in description.lower():
            description += f"\n\n{attribution}"
        return description
    
    def _strip_markdown_json(self, text: str) -> str:
        """Strip markdown code block markers from JSON response."""
        text = text.strip()
        if text.startswith('```json'):
            text = text[7:]
        elif text.startswith('```'):
            text = text[3:]
            
        if text.endswith('```'):
            text = text[:-3]
            
        return text.strip()
