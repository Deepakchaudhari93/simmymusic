
import asyncio
import logging
import os
import shutil
import tempfile
import time

import requests
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from SHUKLAMUSIC import app

try:
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

try:
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

log = logging.getLogger("tgm")

# ───────────────────────────── CONFIG ─────────────────────────────
MAX_SIZE = 200 * 1024 * 1024          # catbox limit
CATBOX_URL = "https://catbox.moe/user/api.php"
UPLOAD_TIMEOUT = 600                  # seconds
PROGRESS_EVERY = 3.0                  # seconds between progress edits (avoids flood waits)
DELETE_STATUS_AFTER_SUCCESS = True

EMOJI_ERROR = 5535088426400874513
EMOJI_WORKING = 5573473356579078196
EMOJI_DONE = 5537203062138994712

BUTTON_TEXT = "❍ ᴄʀᴇᴀᴛᴇ ʙʏ ˹ 𝐓ɪᴅᴀʟ ꭙ 𝐌ᴜꜱɪᴄ 🥀 "

RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-flips if rejected

_active_chats: set = set()
_bg_tasks: set = set()
_MEDIA_ATTRS = ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker")


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


# ───────────────────────────── HELPERS ─────────────────────────────
def _emoji(emoji_id: int, fallback: str = "⭐") -> str:
    return f"<emoji id={emoji_id}>{fallback}</emoji>"


def _rich_emoji(emoji_id: int) -> str:
    if RICH_EMOJI_STYLE == 0:
        return f"<emoji id={emoji_id}>⭐</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">⭐</tg-emoji>'


def _status(text: str, emoji_id: int = EMOJI_WORKING) -> str:
    return f"<blockquote>{_emoji(emoji_id)} <b>{text}</b></blockquote>"


def _link_keyboard(url: str) -> InlineKeyboardMarkup:
    if ButtonStyle is not None:
        try:
            return InlineKeyboardMarkup(
                [[InlineKeyboardButton(BUTTON_TEXT, url=url, style=ButtonStyle.PRIMARY)]]
            )
        except Exception:
            pass
    return InlineKeyboardMarkup([[InlineKeyboardButton(BUTTON_TEXT, url=url)]])


def _result_rich_html(url: str) -> str:
    return (
        f"<p><b>{_rich_emoji(EMOJI_DONE)} ʏᴏᴜʀ ʟɪɴᴋ ɪs ʀᴇᴀᴅʏ</b></p>"
        f'<blockquote><a href="{url}">{url}</a></blockquote>'
        f'<tg-button-row><tg-button type="url" url="{url}" style="primary">'
        f"{BUTTON_TEXT}</tg-button></tg-button-row>"
    )


async def _send_result(client, chat_id: int, url: str, reply_to: int):
    """Rich message with a rich button, replying to the command; plain reply as fallback."""
    global RICH_EMOJI_STYLE
    last_err = None
    if InputRichMessage is not None and hasattr(client, "send_rich_message"):
        for _ in range(2):  # 2nd pass = other emoji tag style
            rich = InputRichMessage(html=_result_rich_html(url))
            attempts = []
            if ReplyParameters is not None:
                attempts.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
            attempts.append(dict(reply_to_message_id=reply_to))
            style_rejected = False
            for extra in attempts:
                try:
                    return await client.send_rich_message(chat_id, rich, **extra)
                except TypeError as e:
                    last_err = e
                    continue
                except Exception as e:
                    last_err = e
                    style_rejected = True
                    break
            if style_rejected:
                RICH_EMOJI_STYLE = 1 - RICH_EMOJI_STYLE
            else:
                break
        log.warning("rich result failed, using plain fallback: %r", last_err)

    return await client.send_message(
        chat_id,
        f"<b>{_emoji(EMOJI_DONE)} ʏᴏᴜʀ ʟɪɴᴋ ɪs ʀᴇᴀᴅʏ</b>\n\n"
        f'<blockquote><a href="{url}">{url}</a></blockquote>',
        parse_mode=ParseMode.HTML,
        reply_markup=_link_keyboard(url),
        reply_to_message_id=reply_to,
        disable_web_page_preview=True,
    )


# ───────────────────────────── UPLOAD ─────────────────────────────
class UploadFailed(Exception):
    pass


CATBOX_USERHASH = os.getenv("CATBOX_USERHASH", "")   # optional: catbox account hash (free to get)
_UA = {"User-Agent": "TidalMusicBot/1.0 (+https://t.me/hmstalker)"}
MB = 1024 * 1024


def _post(url: str, path: str, field: str, data: dict = None, params: dict = None):
    try:
        with open(path, "rb") as fh:
            return requests.post(
                url,
                data=data,
                params=params,
                files={field: (os.path.basename(path), fh)},
                headers=_UA,
                timeout=UPLOAD_TIMEOUT,
            )
    except requests.RequestException as e:
        raise UploadFailed(f"network error: {e}")


def _expect_url(resp) -> str:
    body = (resp.text or "").strip()
    if resp.status_code == 200 and body.startswith("http"):
        return body.splitlines()[0].strip()
    raise UploadFailed(f"{resp.status_code} - {body[:150] or 'empty response'}")


def _up_catbox(path: str) -> str:
    data = {"reqtype": "fileupload"}
    if CATBOX_USERHASH:
        data["userhash"] = CATBOX_USERHASH
    return _expect_url(_post(CATBOX_URL, path, "fileToUpload", data=data))


def _up_0x0(path: str) -> str:
    return _expect_url(_post("https://0x0.st", path, "file"))


def _up_uguu(path: str) -> str:
    return _expect_url(_post("https://uguu.se/upload", path, "files[]", params={"output": "text"}))


def _up_tmpfiles(path: str) -> str:
    resp = _post("https://tmpfiles.org/api/v1/upload", path, "file")
    try:
        url = resp.json()["data"]["url"]
    except Exception:
        raise UploadFailed(f"{resp.status_code} - {(resp.text or '')[:150]}")
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1).replace("http://", "https://", 1)


# (name, function, max size in bytes) — tried in order, first success wins
HOSTS = [
    ("catbox", _up_catbox, 200 * MB),
    ("0x0.st", _up_0x0, 200 * MB),
    ("uguu", _up_uguu, 128 * MB),
    ("tmpfiles", _up_tmpfiles, 100 * MB),
]


def upload_file(file_path: str) -> str:
    """Blocking upload (run in a thread). Tries each host in turn; returns the URL or raises UploadFailed."""
    size = os.path.getsize(file_path)
    errors = []
    for name, fn, limit in HOSTS:
        if size > limit:
            continue
        try:
            url = fn(file_path)
            log.warning("tgm uploaded via %s", name)
            return url
        except UploadFailed as e:
            log.warning("tgm host %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
        except Exception as e:  # never let one host's bug kill the chain
            log.warning("tgm host %s crashed: %r", name, e)
            errors.append(f"{name}: {type(e).__name__}")
    raise UploadFailed(" | ".join(errors) or "no upload host available for this file size")


def _pick_media(msg: Message):
    for attr in _MEDIA_ATTRS:
        obj = getattr(msg, attr, None)
        if obj:
            return obj
    return None


# ───────────────────────────── WORKER ─────────────────────────────
async def _process(client, message: Message):
    chat_id = message.chat.id
    media_msg = message.reply_to_message
    media_obj = _pick_media(media_msg)

    work_dir = tempfile.mkdtemp(prefix="tgm_")
    status = None
    try:
        status = await message.reply_text(_status("ᴘʀᴏᴄᴇssɪɴɢ..."), parse_mode=ParseMode.HTML)

        last_edit = 0.0

        async def progress(current, total):
            nonlocal last_edit
            now = time.monotonic()
            if now - last_edit < PROGRESS_EVERY or not total:
                return
            last_edit = now
            try:
                await status.edit_text(
                    _status(f"ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ... {current * 100 / total:.1f}%"),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        local_path = await media_msg.download(
            file_name=work_dir + os.sep, progress=progress
        )
        if not local_path or not os.path.isfile(local_path):
            raise UploadFailed("couldn't download that media from telegram")

        if os.path.getsize(local_path) > MAX_SIZE:
            raise UploadFailed("file is bigger than 200MB")

        try:
            await status.edit_text(_status("ᴜᴘʟᴏᴀᴅɪɴɢ..."), parse_mode=ParseMode.HTML)
        except Exception:
            pass

        url = await asyncio.to_thread(upload_file, local_path)

        # local copy is useless now
        shutil.rmtree(work_dir, ignore_errors=True)

        await _send_result(client, chat_id, url, message.id)

        if DELETE_STATUS_AFTER_SUCCESS:
            try:
                await status.delete()
            except Exception:
                pass

    except Exception as e:
        log.exception("tgm failed")
        reason = str(e) if isinstance(e, UploadFailed) else f"{type(e).__name__}: {e}"
        text = (
            f"<b>{_emoji(EMOJI_ERROR)} ғɪʟᴇ ᴜᴘʟᴏᴀᴅ ғᴀɪʟᴇᴅ</b>\n\n"
            f"<blockquote><i>ʀᴇᴀsᴏɴ: {reason}</i></blockquote>"
        )
        try:
            if status is not None:
                await status.edit_text(text, parse_mode=ParseMode.HTML)
            else:
                await message.reply_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    finally:
        _active_chats.discard((chat_id, message.from_user.id if message.from_user else 0))
        shutil.rmtree(work_dir, ignore_errors=True)


# ───────────────────────────── HANDLER ─────────────────────────────
log.warning("tgm plugin loaded")  # if you never see this line at startup, the file isn't being loaded


# Own handler group: pyrogram runs only ONE matching handler per group, so if any other plugin
# (or your old version of this file) already handles /tgm or /tl in group 0, it would swallow the
# update and this one would never fire. Group -9 runs independently and never stops propagation.
@app.on_message(filters.command(["tgm", "tgt", "telegraph", "tl"], prefixes=["/", "!", "."]), group=-9)
async def get_link_group(client, message: Message):
    log.warning("tgm command received in chat %s", message.chat.id)
    reply = message.reply_to_message
    if not reply or _pick_media(reply) is None:
        return await message.reply_text(
            f"<b>{_emoji(EMOJI_ERROR)} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇᴅɪᴀ ғɪʟᴇ ᴛᴏ ɢᴇᴛ ᴀ ʟɪɴᴋ</b>",
            parse_mode=ParseMode.HTML,
        )

    media_obj = _pick_media(reply)
    file_size = getattr(media_obj, "file_size", 0) or 0
    if file_size > MAX_SIZE:
        return await message.reply_text(
            f"<b>{_emoji(EMOJI_ERROR)} ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴍᴇᴅɪᴀ ғɪʟᴇ ᴜɴᴅᴇʀ 200MB.</b>",
            parse_mode=ParseMode.HTML,
        )

    key = (message.chat.id, message.from_user.id if message.from_user else 0)
    if key in _active_chats:
        return await message.reply_text(
            "⏳ <b>ᴀɴᴏᴛʜᴇʀ ᴜᴘʟᴏᴀᴅ ᴏғ ʏᴏᴜʀs ɪs sᴛɪʟʟ ʀᴜɴɴɪɴɢ.</b>", parse_mode=ParseMode.HTML
        )
    _active_chats.add(key)

    # run in the background so the handler returns instantly and never blocks other commands
    _spawn(_process(client, message))
