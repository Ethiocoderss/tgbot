import logging
import os
import asyncio
import yt_dlp
import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from yt_dlp.utils import DownloadError

# --- Configuration & Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- SECURED TOKEN ---
# This code now safely reads the token from the hosting environment.
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables.")


# --- Helper Functions ---
def format_size(size_bytes):
    """Converts bytes to a human-readable format (KB, MB, GB)."""
    if size_bytes is None or size_bytes == 0:
        return ""
    size_name = ("B", "KB", "MB", "GB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"({s} {size_name[i]})"

def escape_markdown_v2(text: str) -> str:
    """Escapes characters for Telegram's MarkdownV2 parse mode."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)


# --- Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}! 👋 Send me a YouTube link to get started."
    )

async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles YouTube links by fetching formats and showing download buttons."""
    url = update.message.text
    processing_message = await update.message.reply_text("⏳ Processing link...")

    try:
        ydl_opts = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)

        title = info_dict.get('title', 'No Title')
        context.user_data['video_title'] = title
        thumbnail_url = info_dict.get('thumbnail', None)
        video_id = info_dict.get('id', None)
        formats = info_dict.get('formats', [])
        
        keyboard = []
        
        # --- 1. VIDEO BUTTONS ---
        video_formats = sorted(
            [f for f in formats if f.get('vcodec') != 'none' and f.get('ext') == 'mp4' and f.get('height')],
            key=lambda x: x.get('height', 0),
            reverse=True
        )
        
        added_resolutions = set()
        for f in video_formats:
            height = f.get('height')
            if height and height not in added_resolutions:
                added_resolutions.add(height)
                file_size = f.get('filesize') or f.get('filesize_approx')
                keyboard.append([InlineKeyboardButton(f"🎬 {height}p {format_size(file_size)}", callback_data=f"video:{video_id}:{height}")])

        # --- 2. AUDIO BUTTON ---
        best_audio = next((f for f in reversed(formats) if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('ext') in ['m4a', 'webm', 'mp3']), None)
        if best_audio:
            file_size = best_audio.get('filesize') or best_audio.get('filesize_approx')
            keyboard.append([InlineKeyboardButton(f"🎵 Audio {format_size(file_size)}", callback_data=f"audio:{video_id}:{best_audio['format_id']}")])

        if not keyboard:
            await processing_message.edit_text("Sorry, no suitable download formats were found.")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        caption = f"*{escape_markdown_v2(title)}*"
        
        await processing_message.delete()
        if thumbnail_url:
            await update.message.reply_photo(photo=thumbnail_url, caption=caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

    except DownloadError as e:
        logger.error(f"yt-dlp DownloadError in url_handler: {e}")
        error_str = str(e).lower()
        if "video unavailable" in error_str or "private video" in error_str:
            await processing_message.edit_text("❌ Failed: This video is private, has been deleted, or is unavailable.")
        elif "is not a valid url" in error_str:
            await processing_message.edit_text("❌ This doesn't look like a valid link. Please try again.")
        else:
            await processing_message.edit_text("❌ Failed to process the link. The video may be region-locked or unsupported.")
    except Exception as e:
        logger.error(f"Generic error in url_handler: {e}")
        await processing_message.edit_text("❌ An unexpected error occurred. Please try again later.")


async def download_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles button presses to download and merge the selected format."""
    query = update.callback_query
    await query.answer()

    download_type, video_id, quality_or_id = query.data.split(':')
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    file_path = f"{query.from_user.id}_{video_id}.{'m4a' if download_type == 'audio' else 'mp4'}"
    
    try:
        await query.edit_message_caption(caption="⏳ Preparing download...")
    except Exception:
        pass

    download_format = quality_or_id
    if download_type == 'video':
        height = quality_or_id
        download_format = f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}][ext=mp4]/best"

    ydl_opts = {
        'format': download_format,
        'outtmpl': file_path,
        'quiet': True,
        'noplaylist': True,
        'merge_output_format': 'mp4',
    }

    try:
        loop = asyncio.get_running_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await loop.run_in_executor(None, ydl.download, [url])
        
        await query.edit_message_caption(caption="🚀 Uploading to Telegram...")
        
        title = context.user_data.get('video_title', 'video')

        with open(file_path, 'rb') as file_to_upload:
            if download_type == 'audio':
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=file_to_upload, title=title, read_timeout=120, write_timeout=120)
            else:
                await context.bot.send_video(chat_id=query.message.chat_id, video=file_to_upload, caption=title, supports_streaming=True, read_timeout=120, write_timeout=120)
        
        await query.message.delete()
        
    except DownloadError as e:
        logger.error(f"Error during download (yt-dlp): {e}")
        error_message = r"❌ *Download Failed*\n\nThis could be due to a YouTube error or a protected video\."
        await query.edit_message_caption(caption=error_message, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Generic error during download: {e}")
        error_message = r"❌ *An Unexpected Error Occurred*\n\nPlease try again later\."
        await query.edit_message_caption(caption=error_message, parse_mode=ParseMode.MARKDOWN_V2)
        
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")

def main() -> None:
    """Initializes and starts the bot."""
    if not BOT_TOKEN:
        logger.error("Bot token is not set in environment variables!")
        return
        
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_handler))
    application.add_handler(CallbackQueryHandler(download_button_callback))
    
    print("Bot is up and running...")
    application.run_polling()

if __name__ == '__main__':
    main()
