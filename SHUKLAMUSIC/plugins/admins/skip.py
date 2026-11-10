import html
import importlib
import logging
import re

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, LinkPreviewOptions, Message

try:  # kurigram: rich messages (same as play.py / start.py)
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

import config
from SHUKLAMUSIC import YouTube, app
from SHUKLAMUSIC.core.call import SHUKLA
from SHUKLAMUSIC.core.mongo import mongodb
from SHUKLAMUSIC.misc import db
from SHUKLAMUSIC.utils.database import get_loop
from SHUKLAMUSIC.utils.decorators import AdminRightsCheck
from SHUKLAMUSIC.utils.inline import close_markup, stream_markup
from SHUKLAMUSIC.utils.stream.autoclear import auto_clean
from config import BANNED_USERS

log = logging.getLogger("skip")

# stream.py gets `send_now_playing` injected by play.py -> we reuse that same card
_stream_mod = importlib.import_module("SHUKLAMUSIC.utils.stream.stream")

# ─── CUSTOM CAPTION LOGIC (classic fallback only) ─────────────────────────────
captiondb = mongodb.stream_captions


async def get_stored_caption():
    """Fetches the custom caption from MongoDB."""
    data = await captiondb.find_one({"chat_id": "GLOBAL_CAPTION"})
    if data and "text" in data:
        return data["text"]
    return None


async def get_caption(_, link, title, duration, user):
    """Generates the final caption string, formatted with arguments."""
    custom_html = await get_stored_caption()
    if custom_html:
        try:
            return custom_html.format(link, title, duration, user)
        except Exception:
            pass
    return _["stream_1"].format(link, title, duration, user)


# ─── RICH HELPERS (same engine as play.py) ────────────────────────────────────
_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected


def _to_rich(text: str, style: int) -> str:
    """Classic Telegram-HTML language string -> safe inside a rich message."""

    def _emoji(m):
        eid, fb = int(m.group(1)), (m.group(2) or "⭐")
        if style == 0:
            return f"<emoji id={eid}>{fb}</emoji>"
        return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'

    text = re.sub(r'<emoji id="?(\d+)"?>(.*?)</emoji>', _emoji, text, flags=re.S)
    text = re.sub(
        r"</?spoiler>",
        lambda m: "</tg-spoiler>" if m.group(0)[1] == "/" else "<tg-spoiler>",
        text,
    )
    text = re.sub(r"</?blockquote[^>]*>", "", text)
    return text.replace("\r", "").replace("\n", "<br>")


def _buttons(rows) -> str:
    """rows = [[(text, callback_data, style), ...], ...]"""
    out = ""
    for row in rows:
        out += "<tg-button-row>"
        for text, data, style in row:
            st = f' style="{style}"' if style else ""
            out += (
                f'<tg-button type="callback_data" data="{html.escape(data)}"{st}>'
                f"{html.escape(text, quote=False)}</tg-button>"
            )
        out += "</tg-button-row>"
    return out


async def _send_rich(chat_id, build, reply_to=None):
    global _STYLE
    if InputRichMessage is None or not hasattr(app, "send_rich_message"):
        return None
    variants = []
    if reply_to:
        if ReplyParameters is not None:
            variants.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
        variants.append(dict(reply_to_message_id=reply_to))
    variants.append({})
    for style in (_STYLE, 1 - _STYLE):
        try:
            rich = InputRichMessage(html=build(style))
        except Exception as e:
            log.warning("rich build failed: %r", e)
            break
        for extra in variants:
            try:
                msg = await app.send_rich_message(chat_id, rich, **extra)
                _STYLE = style
                return msg
            except Exception as e:
                log.warning("send_rich_message failed (style=%s): %r", style, e)
    return None


async def say(message: Message, _, text: str, close: bool = False):
    """Rich text card (quote + aside) with an optional close button; classic fallback."""
    rows = [[(_["CLOSE_BUTTON"], "close", "danger")]] if close else []

    def build(s):
        return f"<blockquote><aside>{_to_rich(text, s)}</aside></blockquote>{_buttons(rows)}"

    sent = await _send_rich(message.chat.id, build, reply_to=message.id)
    if sent is not None:
        return sent
    return await message.reply_text(
        text, reply_markup=close_markup(_) if close else None
    )


async def announce(
    _, message, chat_id, link, title, duration, user, user_id, classic_tag, classic_text=None
):
    """Send the rich now-playing card (same one /play uses). Returns (msg, markup_tag)."""
    snp = getattr(_stream_mod, "send_now_playing", None)
    if snp is not None:
        try:
            run = await snp(
                _,
                message.chat.id,
                chat_id,
                link,
                title,
                duration,
                user,
                user_id or message.from_user.id,
            )
            if run is not None:
                return run, "rich"
        except Exception as e:
            log.warning("rich now-playing failed, sending classic: %r", e)

    cap = classic_text or await get_caption(_, link, title[:23], duration, user)
    run = await message.reply_text(
        text=cap,
        link_preview_options=LinkPreviewOptions(is_disabled=False, show_above_text=True),
        reply_markup=InlineKeyboardMarkup(stream_markup(_, chat_id)),
    )
    return run, classic_tag


# ─── /skip ────────────────────────────────────────────────────────────────────

@app.on_message(
    filters.command(["skip", "cskip", "next", "cnext"]) & filters.group & ~BANNED_USERS
)
@AdminRightsCheck
async def skip(cli, message: Message, _, chat_id):
    if not len(message.command) < 2:
        loop = await get_loop(chat_id)
        if loop != 0:
            return await say(message, _, _["admin_8"])
        state = message.text.split(None, 1)[1].strip()
        if state.isnumeric():
            state = int(state)
            check = db.get(chat_id)
            if check:
                count = len(check)
                if count > 2:
                    count = int(count - 1)
                    if 1 <= state <= count:
                        for x in range(state):
                            popped = None
                            try:
                                popped = check.pop(0)
                            except:
                                return await say(message, _, _["admin_12"])
                            if popped:
                                await auto_clean(popped)
                            if not check:
                                try:
                                    await say(
                                        message,
                                        _,
                                        _["admin_6"].format(
                                            message.from_user.mention,
                                            message.chat.title,
                                        ),
                                        close=True,
                                    )
                                    await SHUKLA.stop_stream(chat_id)
                                except:
                                    return
                                break
                    else:
                        return await say(message, _, _["admin_11"].format(count))
                else:
                    return await say(message, _, _["admin_10"])
            else:
                return await say(message, _, _["queue_2"])
        else:
            return await say(message, _, _["admin_9"])
    else:
        check = db.get(chat_id)
        popped = None
        try:
            popped = check.pop(0)
            if popped:
                await auto_clean(popped)
            if not check:
                await say(
                    message,
                    _,
                    _["admin_6"].format(message.from_user.mention, message.chat.title),
                    close=True,
                )
                try:
                    return await SHUKLA.stop_stream(chat_id)
                except:
                    return
        except:
            try:
                await say(
                    message,
                    _,
                    _["admin_6"].format(message.from_user.mention, message.chat.title),
                    close=True,
                )
                return await SHUKLA.stop_stream(chat_id)
            except:
                return

    queued = check[0]["file"]
    title = (check[0]["title"]).title()
    user = check[0]["by"]
    user_id = check[0].get("user_id")  # index/m3u8 entries have none
    streamtype = check[0]["streamtype"]
    videoid = check[0]["vidid"]
    status = True if str(streamtype) == "video" else None
    db[chat_id][0]["played"] = 0
    exis = (check[0]).get("old_dur")
    if exis:
        db[chat_id][0]["dur"] = exis
        db[chat_id][0]["seconds"] = check[0]["old_second"]
        db[chat_id][0]["speed_path"] = None
        db[chat_id][0]["speed"] = 1.0

    dur = check[0]["dur"]

    # ── live ──────────────────────────────────────────────────────────────────
    if "live_" in queued:
        n, link = await YouTube.video(videoid, True)
        if n == 0:
            return await say(message, _, _["admin_7"].format(title))
        try:
            image = await YouTube.thumbnail(videoid, True)
        except:
            image = None
        try:
            await SHUKLA.skip_stream(chat_id, link, video=status, image=image)
        except:
            return await say(message, _, _["call_6"])

        vid_link = f"https://t.me/{app.username}?start=info_{videoid}"
        run, mk = await announce(
            _, message, chat_id, vid_link, title, dur, user, user_id, "tg"
        )
        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = mk

    # ── youtube (downloaded on demand) ────────────────────────────────────────
    elif "vid_" in queued:
        mystic = await message.reply_text(_["call_7"], disable_web_page_preview=True)
        try:
            file_path, direct = await YouTube.download(
                videoid,
                mystic,
                videoid=True,
                video=status,
            )
        except:
            return await mystic.edit_text(_["call_6"])
        try:
            image = await YouTube.thumbnail(videoid, True)
        except:
            image = None
        try:
            await SHUKLA.skip_stream(chat_id, file_path, video=status, image=image)
        except:
            return await mystic.edit_text(_["call_6"])

        vid_link = f"https://t.me/{app.username}?start=info_{videoid}"
        run, mk = await announce(
            _, message, chat_id, vid_link, title, dur, user, user_id, "stream"
        )
        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = mk
        await mystic.delete()

    # ── index / m3u8 ──────────────────────────────────────────────────────────
    elif "index_" in queued:
        try:
            await SHUKLA.skip_stream(chat_id, videoid, video=status)
        except:
            return await say(message, _, _["call_6"])

        run, mk = await announce(
            _,
            message,
            chat_id,
            videoid,
            "ɪɴᴅᴇx ᴏʀ ᴍ3ᴜ8 ʟɪɴᴋ",
            dur,
            user,
            user_id,
            "tg",
            classic_text=_["stream_2"].format(user),
        )
        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = mk

    # ── drx / telegram / soundcloud / direct files ────────────────────────────
    else:
        if videoid in ("telegram", "soundcloud"):
            image = None
        else:
            try:
                image = await YouTube.thumbnail(videoid, True)
            except:
                image = None
        try:
            await SHUKLA.skip_stream(chat_id, queued, video=status, image=image)
        except:
            return await say(message, _, _["call_6"])

        if videoid in ("telegram", "soundcloud"):
            run, mk = await announce(
                _, message, chat_id, config.SUPPORT_CHAT, title, dur, user, user_id, "tg"
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = mk
        else:
            vid_link = f"https://t.me/{app.username}?start=info_{videoid}"
            run, mk = await announce(
                _, message, chat_id, vid_link, title, dur, user, user_id, "stream"
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = mk
