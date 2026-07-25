import asyncio
import logging
import time
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self):
        # Limits format: (max_requests, time_window_in_seconds)
        self.limits = {
            'gemini': (15, 60),            # 15 req/min
            'pexels': (200, 3600),         # 200 req/hour
            'gnews': (100, 86400),         # 100 req/day
            'youtube': (10000, 86400)      # 10000 units/day
        }
        # Format: {api_name: [(timestamp, units), ...]}
        self.usage: Dict[str, List[Tuple[float, int]]] = {
            'gemini': [],
            'pexels': [],
            'gnews': [],
            'youtube': []
        }
        self._lock = asyncio.Lock()

    async def _clean_old_requests(self, api_name: str, current_time: float) -> None:
        if api_name not in self.limits:
            return
        _, window = self.limits[api_name]
        cutoff = current_time - window
        
        valid_requests = []
        for ts, units in self.usage[api_name]:
            if ts > cutoff:
                valid_requests.append((ts, units))
        self.usage[api_name] = valid_requests

    def _get_current_usage(self, api_name: str) -> int:
        return sum(units for _, units in self.usage.get(api_name, []))

    async def acquire(self, api_name: str, units: int = 1) -> None:
        if api_name not in self.limits:
            return

        max_req, window = self.limits[api_name]

        while True:
            async with self._lock:
                current_time = time.time()
                await self._clean_old_requests(api_name, current_time)
                current_usage = self._get_current_usage(api_name)

                if current_usage + units <= max_req:
                    self.usage[api_name].append((current_time, units))
                    
                    new_usage = current_usage + units
                    usage_percent = (new_usage / max_req) * 100
                    if usage_percent > 80:
                        logger.warning(f"Rate limit for {api_name} is at {usage_percent:.1f}% ({new_usage}/{max_req})")
                    return

                # Calculate sleep time
                # We need to wait until enough old requests expire to make room for `units`
                oldest_ts = self.usage[api_name][0][0]
                sleep_time = (oldest_ts + window) - current_time + 0.1
                
            logger.info(f"Rate limit exceeded for {api_name}. Waiting for {sleep_time:.2f} seconds.")
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def record(self, api_name: str, units: int = 1) -> None:
        if api_name not in self.limits:
            logger.warning(f"Unknown API {api_name} recorded.")
            return

        async with self._lock:
            current_time = time.time()
            await self._clean_old_requests(api_name, current_time)
            self.usage[api_name].append((current_time, units))

            max_req, _ = self.limits[api_name]
            current_usage = self._get_current_usage(api_name)
            usage_percent = (current_usage / max_req) * 100
            
            if usage_percent > 80:
                logger.warning(f"Rate limit for {api_name} is at {usage_percent:.1f}% ({current_usage}/{max_req})")

    async def get_usage_summary(self) -> dict:
        summary = {}
        async with self._lock:
            current_time = time.time()
            for api_name in self.limits:
                await self._clean_old_requests(api_name, current_time)
                current_usage = self._get_current_usage(api_name)
                max_req, _ = self.limits[api_name]
                summary[api_name] = {
                    'used': current_usage,
                    'limit': max_req,
                    'remaining': max_req - current_usage,
                    'percent_used': round((current_usage / max_req) * 100, 2)
                }
        return summary
