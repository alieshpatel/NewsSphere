import logging
import asyncio
import os
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Assuming Config is imported from somewhere, creating a dummy import for now
try:
    from config import Config
except ImportError:
    class Config:
        YOUTUBE_CLIENT_SECRET_PATH: str = './client_secret.json'
        CHANNEL_NAME: str = 'NewsSphere'
        OPTIMAL_PUBLISH_HOUR: int = 8

class PublisherAgent:
    def __init__(self, config: Config):
        self.config = config
        self.youtube = None
        self.SCOPES = [
            'https://www.googleapis.com/auth/youtube.upload',
            'https://www.googleapis.com/auth/youtube',
            'https://www.googleapis.com/auth/youtube.force-ssl'
        ]
        # Defer authentication until needed

    def _authenticate(self):
        """
        1. Check if token.json exists in project root
        2. If exists, load credentials from token.json
        3. If credentials expired, refresh them
        4. If no token.json, run InstalledAppFlow.from_client_secrets_file()
           with config.YOUTUBE_CLIENT_SECRET_PATH and self.SCOPES
        5. Save credentials to token.json for future runs
        6. Build and return googleapiclient.discovery.build('youtube', 'v3', credentials=creds)
        """
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
        import os.path

        creds = None
        # token.json stores the user's access and refresh tokens
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', self.SCOPES)

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.warning(f"Failed to refresh token: {e}")
                    creds = None
            if not creds:
                if not os.path.exists(self.config.YOUTUBE_CLIENT_SECRET_PATH):
                    raise FileNotFoundError(f"Missing client secret file at {self.config.YOUTUBE_CLIENT_SECRET_PATH}. Please provide it.")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.config.YOUTUBE_CLIENT_SECRET_PATH, self.SCOPES)
                creds = flow.run_local_server(port=0)

            # Save the credentials for the next run
            with open('token.json', 'w') as token:
                token.write(creds.to_json())

        return build('youtube', 'v3', credentials=creds)

    def _ensure_authenticated(self):
        """Ensure self.youtube is set. Call _authenticate() if not."""
        if self.youtube is None:
            self.youtube = self._authenticate()

    # ── Error diagnosis ──────────────────────────────────────────────────

    def _parse_http_error_reason(self, e) -> tuple[str, str]:
        """
        Extract the machine-readable `reason` and human-readable `message`
        from a googleapiclient HttpError's JSON body.

        HttpError.resp.status is just the HTTP status code (e.g. 403), and
        Google returns 403 for many unrelated causes — quota exhaustion,
        an unverified channel's video-length restriction, a policy/content
        flag, a missing OAuth scope, a comment-posting restriction, etc.
        Branching on status code alone misdiagnoses almost all of these.
        This pulls the actual `reason` string out of the response body so
        callers can react correctly. Falls back gracefully if the body
        isn't the expected shape (e.g. an HTML error page or empty body).
        """
        reason = "unknown"
        message = str(e)
        try:
            raw = e.content.decode() if isinstance(e.content, bytes) else e.content
            body = json.loads(raw)
            errors = body.get("error", {}).get("errors", [])
            if errors:
                reason = errors[0].get("reason", reason)
                message = errors[0].get("message", message)
            else:
                # Some errors use error.status instead of error.errors[]
                reason = body.get("error", {}).get("status", reason)
                message = body.get("error", {}).get("message", message)
        except Exception as parse_err:
            logger.debug(f"Could not parse HttpError body ({parse_err}); raw content: {e.content!r}")
        return reason, message

    def _raise_for_http_error(self, e) -> None:
        """
        Log a correctly-diagnosed message and raise an Exception describing
        the real cause, instead of assuming every 403 is quota exhaustion.
        Used for FATAL failures only (i.e. the main video upload itself).
        """
        reason, message = self._parse_http_error_reason(e)
        logger.error(f"YouTube API error — status {e.resp.status}, reason '{reason}': {message}")

        if reason in ("quotaExceeded", "dailyLimitExceeded", "userRateLimitExceeded"):
            raise Exception(
                "YouTube API quota exceeded (10,000 units/day). "
                "Try again after midnight Pacific Time."
            ) from e

        if reason in ("forbidden", "accessNotConfigured"):
            raise Exception(
                f"YouTube rejected the upload (reason: {reason}) — this is usually a channel "
                f"restriction (e.g. an unverified channel's video-length limit, a content/policy "
                f"flag, or a missing OAuth scope), not a quota issue. Details: {message}"
            ) from e

        if reason == "youtubeSignupRequired":
            raise Exception(
                "The authenticated Google account has no YouTube channel associated with it."
            ) from e

        raise Exception(f"HTTP Error occurred: {e.resp.status} - {reason}: {message}") from e

    # ── Upload ───────────────────────────────────────────────────────────

    async def upload_video(
        self,
        video_path: Path,
        thumbnail_path: Path,
        metadata: dict,
        publish_at: datetime | None = None,
        shorts_path: Path | None = None
    ) -> dict:
        """
        Run in thread executor since google API client is synchronous.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._upload_video_sync,
            video_path,
            thumbnail_path,
            metadata,
            publish_at,
            shorts_path
        )

    def _upload_video_sync(
        self,
        video_path: Path,
        thumbnail_path: Path,
        metadata: dict,
        publish_at: datetime | None,
        shorts_path: Path | None
    ) -> dict:
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError
        from tqdm import tqdm

        self._ensure_authenticated()

        # ── Main video upload (fatal on failure) ────────────────────────
        try:
            body = {
                'snippet': {
                    'title': metadata.get('title', 'Default Title'),
                    'description': metadata.get('description', ''),
                    'tags': metadata.get('tags', []),
                    'categoryId': metadata.get('categoryId', '25'),
                    'defaultLanguage': metadata.get('defaultLanguage', 'en')
                },
                'status': {
                    'privacyStatus': 'private' if publish_at else 'public',
                    'selfDeclaredMadeForKids': False
                }
            }
            if publish_at:
                body['status']['publishAt'] = publish_at.isoformat()

            media = MediaFileUpload(
                str(video_path),
                chunksize=10*1024*1024,
                resumable=True
            )

            request = self.youtube.videos().insert(
                part=','.join(body.keys()),
                body=body,
                media_body=media
            )

            response = None
            with tqdm(total=100, desc="Uploading video") as pbar:
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        progress = int(status.progress() * 100)
                        pbar.n = progress
                        pbar.refresh()
            pbar.n = 100
            pbar.refresh()

        except HttpError as e:
            self._raise_for_http_error(e)
        except Exception as e:
            logger.error(f"An error occurred during main video upload: {str(e)}")
            raise

        # Video is uploaded at this point — everything below is best-effort.
        # A failure in thumbnail/comment/Shorts must NOT be reported as a
        # failed upload, since the primary artifact already exists on
        # YouTube. Each sub-step below catches its own errors.
        video_id = response['id']

        result = {
            'video_id': video_id,
            'video_url': f'https://youtube.com/watch?v={video_id}',
            'shorts_id': None,
            'shorts_url': None
        }

        # ── Thumbnail (non-fatal) ────────────────────────────────────────
        if thumbnail_path and thumbnail_path.exists():
            try:
                self.youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(str(thumbnail_path))
                ).execute()
            except HttpError as e:
                reason, message = self._parse_http_error_reason(e)
                logger.warning(
                    f"Thumbnail upload failed (video {video_id} still uploaded successfully) — "
                    f"reason '{reason}': {message}"
                )
            except Exception as e:
                logger.warning(
                    f"Thumbnail upload failed (video {video_id} still uploaded successfully): {e}"
                )

        # ── Pinned comment (non-fatal) ───────────────────────────────────
        # This step commonly fails with 403 'forbidden' for OAuth apps that
        # haven't been through YouTube's comment-posting audit — unrelated
        # to whether the video itself uploaded correctly.
        if 'pinned_comment' in metadata:
            try:
                comment_body = {
                    'snippet': {
                        'videoId': video_id,
                        'topLevelComment': {
                            'snippet': {
                                'textOriginal': metadata['pinned_comment']
                            }
                        }
                    }
                }
                self.youtube.commentThreads().insert(
                    part='snippet',
                    body=comment_body
                ).execute()
            except HttpError as e:
                reason, message = self._parse_http_error_reason(e)
                logger.warning(
                    f"Pinned comment failed (video {video_id} still uploaded successfully) — "
                    f"reason '{reason}': {message}. This is commonly a YouTube API restriction "
                    f"on comment posting for unaudited OAuth apps, unrelated to upload permissions."
                )
            except Exception as e:
                logger.warning(
                    f"Pinned comment failed (video {video_id} still uploaded successfully): {e}"
                )

        # ── Shorts upload (non-fatal) ────────────────────────────────────
        # Kept separate from the main video's fatal error handling — if the
        # Shorts upload fails, the main video result is still returned.
        if shorts_path and shorts_path.exists():
            try:
                shorts_title = metadata.get('title', 'Default Title') + ' #Shorts'
                # Maximum title length for YouTube is 100 characters
                if len(shorts_title) > 100:
                    shorts_title = shorts_title[:90] + ' #Shorts'

                shorts_body = {
                    'snippet': {
                        'title': shorts_title,
                        'description': metadata.get('description', ''),
                        'tags': metadata.get('tags', []),
                        'categoryId': metadata.get('categoryId', '25'),
                        'defaultLanguage': metadata.get('defaultLanguage', 'en')
                    },
                    'status': {
                        'privacyStatus': 'private' if publish_at else 'public',
                        'selfDeclaredMadeForKids': False
                    }
                }
                if publish_at:
                    shorts_body['status']['publishAt'] = publish_at.isoformat()

                shorts_media = MediaFileUpload(
                    str(shorts_path),
                    chunksize=10*1024*1024,
                    resumable=True
                )

                shorts_request = self.youtube.videos().insert(
                    part=','.join(shorts_body.keys()),
                    body=shorts_body,
                    media_body=shorts_media
                )

                shorts_response = None
                with tqdm(total=100, desc="Uploading shorts") as pbar:
                    while shorts_response is None:
                        s_status, shorts_response = shorts_request.next_chunk()
                        if s_status:
                            progress = int(s_status.progress() * 100)
                            pbar.n = progress
                            pbar.refresh()
                pbar.n = 100
                pbar.refresh()

                shorts_id = shorts_response['id']
                result['shorts_id'] = shorts_id
                result['shorts_url'] = f'https://youtube.com/watch?v={shorts_id}'

            except HttpError as e:
                reason, message = self._parse_http_error_reason(e)
                logger.warning(
                    f"Shorts upload failed (main video {video_id} still uploaded successfully) — "
                    f"reason '{reason}': {message}"
                )
            except Exception as e:
                logger.warning(
                    f"Shorts upload failed (main video {video_id} still uploaded successfully): {e}"
                )

        return result

    async def get_optimal_publish_time(self) -> datetime:
        """
        Return next occurrence of config.OPTIMAL_PUBLISH_HOUR (default 8) in UTC.
        """
        now = datetime.now(timezone.utc)
        optimal_hour = getattr(self.config, 'OPTIMAL_PUBLISH_HOUR', 8)

        target = now.replace(hour=optimal_hour, minute=0, second=0, microsecond=0)

        if now.hour >= optimal_hour:
            target += timedelta(days=1)

        return target