import logging, asyncio, re, feedparser, aiohttp
from datetime import datetime, timedelta, timezone
from config import Config

logger = logging.getLogger(__name__)

class NewsAgent:
    def __init__(self, config: Config):
        self.config = config
        self.gnews_base = 'https://gnews.io/api/v4'
    
    async def fetch_stories(self, num_stories: int = 5) -> list[dict]:
        """
        1. Try Google News RSS for each keyword in config.NICHE_KEYWORDS
        2. Deduplicate stories by title similarity
        3. Score each story
        4. Sort and return
        5. Fallback if necessary
        """
        all_stories = []
        for keyword in self.config.NICHE_KEYWORDS:
            stories = self._parse_rss_feed(keyword)
            all_stories.extend(stories)
            
        all_stories = self._deduplicate(all_stories)
        
        for story in all_stories:
            story['score'] = self._score_story(story)
            
        all_stories.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        if len(all_stories) < 3:
            for keyword in self.config.NICHE_KEYWORDS:
                fallback_stories = await self._fetch_gnews_fallback(keyword)
                all_stories.extend(fallback_stories)
            all_stories = self._deduplicate(all_stories)
            for story in all_stories:
                if 'score' not in story:
                    story['score'] = self._score_story(story)
            all_stories.sort(key=lambda x: x.get('score', 0), reverse=True)
            
        return all_stories[:num_stories]
    
    def _parse_rss_feed(self, keyword: str) -> list[dict]:
        """Parse Google News RSS feed for a keyword. Returns list of story dicts."""
        stories = []
        url = f"https://news.google.com/rss/search?q={keyword}&hl=en-US&gl=US&ceid=US:en"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                stories.append({
                    'headline': getattr(entry, 'title', ''),
                    'summary': getattr(entry, 'summary', ''),
                    'url': getattr(entry, 'link', ''),
                    'published': getattr(entry, 'published', ''),
                    'keywords_matched': [keyword]
                })
        except Exception as e:
            logger.error(f"Error parsing RSS for {keyword}: {e}")
        return stories
    
    def _score_story(self, story: dict) -> float:
        """Score a story for YouTube viability. Return float score."""
        score = 0.0
        headline = story.get('headline', '').lower()
        summary = story.get('summary', '')
        published = story.get('published', '')
        
        if re.search(r'\d+', headline):
            score += 2.0
            
        if any(word in headline for word in ['breaking', 'exclusive', 'first']):
            score += 2.0
            
        if any(word in headline for word in ['who', 'what', 'why']):
            score += 1.0
            
        if len(summary) > 200:
            score += 1.0
            
        if published:
            try:
                # Try parsing RFC 822 format from RSS
                pub_date = datetime.strptime(published[:25], "%a, %d %b %Y %H:%M:%S")
                pub_date = pub_date.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - pub_date) <= timedelta(hours=24):
                    score += 2.0
            except ValueError:
                try:
                    # GNews fallback ISO format
                    pub_date = datetime.fromisoformat(published.replace('Z', '+00:00'))
                    if (datetime.now(timezone.utc) - pub_date) <= timedelta(hours=24):
                        score += 2.0
                except ValueError:
                    pass
                
        return score
    
    def _deduplicate(self, stories: list[dict]) -> list[dict]:
        """Remove duplicate stories based on >60% word overlap in headlines."""
        unique_stories = []
        for story in stories:
            is_dup = False
            words = set(re.findall(r'\w+', story.get('headline', '').lower()))
            for u_story in unique_stories:
                u_words = set(re.findall(r'\w+', u_story.get('headline', '').lower()))
                if not words or not u_words:
                    continue
                overlap = len(words.intersection(u_words)) / max(len(words), len(u_words))
                if overlap > 0.6:
                    is_dup = True
                    u_story['keywords_matched'] = list(set(u_story['keywords_matched'] + story['keywords_matched']))
                    break
            if not is_dup:
                unique_stories.append(story)
        return unique_stories
    
    async def _fetch_gnews_fallback(self, keyword: str) -> list[dict]:
        """Fallback to GNews API."""
        await asyncio.sleep(1)
        stories = []
        url = f"{self.gnews_base}/search?q={keyword}&lang=en&max=10&token={self.config.GNEWS_API_KEY}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        for article in data.get('articles', []):
                            stories.append({
                                'headline': article.get('title', ''),
                                'summary': article.get('description', ''),
                                'url': article.get('url', ''),
                                'published': article.get('publishedAt', ''),
                                'keywords_matched': [keyword]
                            })
                    else:
                        logger.error(f"GNews API returned {response.status}")
        except Exception as e:
            logger.error(f"Error fetching GNews fallback for {keyword}: {e}")
        return stories
