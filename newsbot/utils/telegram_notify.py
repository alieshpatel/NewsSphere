import asyncio
import logging
from pathlib import Path
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, config: dict):
        self.token = config.get('telegram_token', '')
        self.chat_id = config.get('telegram_chat_id', '')
        
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.warning("Telegram token or chat_id is missing. TelegramNotifier is disabled.")
        else:
            self.bot = Bot(token=self.token)
            
        self._approval_status: Optional[bool] = None

    async def send_message(self, text: str) -> None:
        if not self.enabled:
            return
            
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text)
            logger.info("Sent Telegram message.")
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")

    async def send_photo(self, photo_path: Path, caption: str = '') -> None:
        if not self.enabled:
            return
            
        if not photo_path.exists():
            logger.error(f"Photo path does not exist: {photo_path}")
            return
            
        try:
            with open(photo_path, 'rb') as photo:
                await self.bot.send_photo(chat_id=self.chat_id, photo=photo, caption=caption)
            logger.info(f"Sent photo {photo_path.name} to Telegram.")
        except TelegramError as e:
            logger.error(f"Failed to send Telegram photo: {e}")
        except Exception as e:
            logger.error(f"Error sending photo to Telegram: {e}")

    async def send_approval_request(
        self,
        script: dict,
        metadata: dict,
        thumbnail_path: Path,
        video_preview_path: Optional[Path] = None
    ) -> None:
        if not self.enabled:
            logger.info("Telegram not configured. Auto-approving request.")
            return

        title = metadata.get('title', 'No Title')
        hook_text = script.get('hook', '')
        hook_words = ' '.join(hook_text.split()[:50]) + ("..." if len(hook_text.split()) > 50 else "")
        tags = metadata.get('tags', [])[:3]
        tags_str = ' '.join([f"#{tag}" for tag in tags])

        caption = f"🎬 *Approval Request*\n\n"
        caption += f"📌 *Title:* {title}\n\n"
        caption += f"📝 *Hook Preview:*\n_{hook_words}_\n\n"
        if tags_str:
            caption += f"🏷 *Tags:* {tags_str}"

        keyboard = [
            [
                InlineKeyboardButton("✅ APPROVE", callback_data='approve'),
                InlineKeyboardButton("❌ REJECT", callback_data='reject')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if thumbnail_path.exists():
                with open(thumbnail_path, 'rb') as photo:
                    await self.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=photo,
                        caption=caption,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
            else:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=caption,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            logger.info("Approval request sent.")
        except TelegramError as e:
            logger.error(f"Failed to send approval request: {e}")
        except Exception as e:
            logger.error(f"Error sending approval request: {e}")

    async def wait_for_approval(self, timeout_minutes: int = 120) -> bool:
        if not self.enabled:
            logger.warning("Telegram is not configured. Auto-approving (returning True).")
            return True

        self._approval_status = None

        application = Application.builder().token(self.token).build()

        async def button_callback(update, context: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if query:
                await query.answer()
                if query.data == 'approve':
                    self._approval_status = True
                    await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ *APPROVED*")
                elif query.data == 'reject':
                    self._approval_status = False
                    await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ *REJECTED*")
                
                # Cannot cleanly stop the app here due to EventLoop issues, so polling loop terminates instead

        application.add_handler(CallbackQueryHandler(button_callback))
        
        await application.initialize()
        await application.start()
        if application.updater:
            await application.updater.start_polling()

        logger.info(f"Waiting up to {timeout_minutes} minutes for approval...")
        
        timeout_seconds = timeout_minutes * 60
        reminder_time = 30 * 60
        elapsed = 0
        poll_interval = 2
        
        try:
            while elapsed < timeout_seconds:
                if self._approval_status is not None:
                    return self._approval_status
                
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                
                if elapsed == reminder_time:
                    await self.send_message("⏰ Reminder: Still waiting for video approval!")
                    
            logger.warning("Approval wait timed out. Assuming rejected.")
            return False
            
        finally:
            if application.updater:
                await application.updater.stop()
            await application.stop()
            await application.shutdown()

    async def send_success_notification(self, video_id: str, shorts_id: Optional[str] = None) -> None:
        if not self.enabled:
            return
            
        message = "🎉 *Upload Successful!*\n\n"
        if video_id:
            message += f"🎥 Main Video: https://youtube.com/watch?v={video_id}\n"
        if shorts_id:
            message += f"📱 Short: https://youtube.com/shorts/{shorts_id}\n"
            
        try:
            await self.bot.send_message(
                chat_id=self.chat_id, 
                text=message,
                parse_mode='Markdown'
            )
            logger.info("Sent success notification to Telegram.")
        except TelegramError as e:
            logger.error(f"Failed to send success notification: {e}")
