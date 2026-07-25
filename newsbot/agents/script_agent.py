import logging, asyncio, json, re
import google.generativeai as genai
from config import Config

logger = logging.getLogger(__name__)

class ScriptAgent:
    def __init__(self, config: Config):
        genai.configure(api_key=config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(config.GEMINI_MODEL)
        self.config = config
    
    async def write_script(self, story: dict) -> dict:
        """
        Uses Gemini 2.5 Flash to write a complete video script.
        """
        prompt = self._build_script_prompt(story)
        
        for attempt in range(2):
            await asyncio.sleep(4) # respect rate limit
            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=self.config.GEMINI_SCRIPT_TEMPERATURE
                    )
                )
                
                text = self._strip_markdown_json(response.text)
                script_data = json.loads(text)
                
                if self._validate_script(script_data):
                    return script_data
                else:
                    raise ValueError("Script validation failed")
                    
            except Exception as e:
                logger.error(f"Error generating script: {e}")
                prompt += "\nReturn ONLY raw JSON, no markdown."
                
        raise ValueError("Failed to generate valid script after retries")
    
    def _build_script_prompt(self, story: dict) -> str:
        """Build the full prompt for script generation. Include persona instructions and output format."""
        target_words = int(self.config.VIDEO_DURATION_TARGET_SECONDS * (150 / 60))
        target_mins = int(self.config.VIDEO_DURATION_TARGET_SECONDS / 60)
        
        return f"""
You are a 'news anchor friend' - casual but authoritative, explain complex topics simply, use analogies, and address the viewer directly.

Write a complete video script for the following news story.
Headline: {story.get('headline')}
Summary: {story.get('summary')}
URL: {story.get('url')}
Target duration: {self.config.VIDEO_DURATION_TARGET_SECONDS} seconds (~{target_words} words).

Required JSON output format exactly as follows:
{{
  "title_options": ["Title 1", "Title 2", "Title 3"],
  "hook": "first 20 seconds - must stop the scroll",
  "full_script": "complete word-for-word narration ~{target_words} words",
  "segments": [
    {{"name": "Hook", "text": "...", "duration_seconds": 20, "broll_keywords": ["keyword1"]}},
    {{"name": "Context", "text": "...", "duration_seconds": 60, "broll_keywords": ["keyword2"]}}
  ],
  "broll_keywords": ["list", "of", "keywords", "for", "footage", "search"],
  "key_stats": ["any numbers or facts to display as on-screen graphics"],
  "target_audience": "who this video is for",
  "estimated_duration_minutes": {target_mins}
}}
"""
    
    def _validate_script(self, script: dict) -> bool:
        """Validate that all required fields exist in the script dict."""
        required_keys = ['title_options', 'hook', 'full_script', 'segments', 'broll_keywords']
        if not all(k in script for k in required_keys):
            return False
            
        if not isinstance(script['title_options'], list) or len(script['title_options']) != 3:
            return False
            
        if not isinstance(script['hook'], str):
            return False
            
        if not isinstance(script['full_script'], str) or len(script['full_script']) < 500:
            return False
            
        if not isinstance(script['segments'], list):
            return False
            
        for segment in script['segments']:
            if not all(k in segment for k in ['name', 'text', 'duration_seconds', 'broll_keywords']):
                return False
                
        if not isinstance(script['broll_keywords'], list):
            return False
            
        return True
    
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
