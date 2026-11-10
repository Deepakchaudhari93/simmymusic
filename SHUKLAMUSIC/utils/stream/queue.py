import asyncio
import html
import importlib
import logging
import re
from typing import Union

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:  # kurigram: rich messages (same engine as play.py / start.py)
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

from SHUKLAMUSIC import app
from SHUKLAMUSIC.misc import db
from SHUKLAMUSIC.utils.formatters import check_duration, seconds_to_min
from config import autoclean, time_to_seconds

log = logging.getLogger("queue")

# ─── RICH "ADDED TO QUEUE" CARD ───────────────────────────────────────────────
# stream.py is NOT edited. On the first put_queue() call this file hooks the stream
# module so that its old `queue_4` message (+ aq_markup inline keyboard) is replaced
# by the rich card below. Only that exact message is replaced, so playlist summaries
# and everything else in stream.py behave as before.

HOOK_STREAM = True  # False = leave stream.py's old queue message alone

EMOJI_1 = 5042334757040423886  # Added To Queue
EMOJI_2 = 5409262351532701571  # song title
EMOJI_3 = 5408910121264756249  # duration
EMOJI_4 = 5409132617750555920  # requested by

LBL_RESUME = "𝖱𝖾𝗌𝗎𝗆𝖾"
LBL_PAUSE = "𝖯𝖺𝗎𝗌𝖾"
LBL_STOP = "𝖲𝗍𝗈𝗉"
LBL_CLOSE = "ᑕᒪOՏᗴ"

_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected


def _remoji(emoji_id: int, style: int, fallback: str = "⭐") -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{fallback}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _fmt_duration(d) -> str:
    """4:22 -> '4:22 ᴍιηᴜᴛєs'. Live / unknown values are shown as they are."""
    d = str(d or "").strip()
    if re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", d) and d not in ("0:00", "00:00"):
        return f"{d} ᴍιηᴜᴛєs"
    return d or "00:00"


def _mention(user_id, name) -> str:
    n = html.escape(str(name or "User"), quote=False)
    if user_id:
        try:
            return f'<a href="tg://user?id={int(user_id)}">{n}</a>'
        except Exception:
            pass
    return n


def _rows(chat_id):
    # (text, callback_data, style) - same callbacks as the old aq_markup
    return [
        [
            (LBL_RESUME, f"ADMIN Resume|{chat_id}", "primary"),
            (LBL_PAUSE, f"ADMIN Pause|{chat_id}", "primary"),
            (LBL_STOP, f"ADMIN Stop|{chat_id}", "primary"),
        ],
        [(LBL_CLOSE, "close", "danger")],
    ]


def _rich_buttons(rows) -> str:
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


def _queued_html(style, position, title, duration, mention, with_cite=True) -> str:
    e1, e2, e3, e4 = (_remoji(i, style) for i in (EMOJI_1, EMOJI_2, EMOJI_3, EMOJI_4))
    t = html.escape(str(title or "Unknown")[:40], quote=False)
    head = f"{e1} <b>Aᴅᴅᴇᴅ Tᴏ Qᴜᴇᴜᴇ Aᴛ #{position}</b>"
    if with_cite:
        # quote: heading, author = song title
        quote = f"<blockquote>{head}<cite>{e2} {t}</cite></blockquote>"
    else:
        quote = f"<blockquote>{head}<br>{e2} <i>{t}</i></blockquote>"
    # pullquote: duration + requested by
    aside = (
        "<aside>"
        f"{e3} <b>ᴅᴜʀᴀᴛɪᴏɴ</b>: <i>{html.escape(_fmt_duration(duration), quote=False)}</i><br>"
        f"{e4} <b>ʀᴇǫᴜᴇsᴛᴇᴅ</b>: <i>{mention}</i>"
        "</aside>"
    )
    return quote + aside


async def send_queued(
    chat_id,
    original_chat_id,
    position,
    title,
    duration,
    user,
    user_id=None,
    classic_fallback=True,
):
    """Send the rich 'added to queue' card (rich buttons inside)."""
    global _STYLE
    mention = _mention(user_id, user)
    rows = _rows(chat_id)

    if InputRichMessage is not None and hasattr(app, "send_rich_message"):
        for with_cite in (True, False):  # <cite> author first, plain line as backup
            for style in (_STYLE, 1 - _STYLE):
                try:
                    rich = InputRichMessage(
                        html=_queued_html(
                            style, position, title, duration, mention, with_cite
                        )
                        + _rich_buttons(rows)
                    )
                except Exception as e:
                    log.warning("rich build failed: %r", e)
                    continue
                try:
                    msg = await app.send_rich_message(original_chat_id, rich)
                    _STYLE = style
                    return msg
                except Exception as e:
                    log.warning(
                        "send_rich_message failed (cite=%s style=%s): %r", with_cite, style, e
                    )

    if not classic_fallback:
        return None
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d, _s in row] for row in rows]
    )
    try:
        return await app.send_message(
            original_chat_id,
            f"⭐ <b>Aᴅᴅᴇᴅ Tᴏ Qᴜᴇᴜᴇ Aᴛ #{position}</b>\n"
            f"⭐ <b>ᴛιᴛʟє</b>: {html.escape(str(title or 'Unknown')[:40], quote=False)}\n"
            f"⭐ <b>ᴅᴜʀᴀᴛɪᴏɴ</b>: {html.escape(_fmt_duration(duration), quote=False)}\n"
            f"⭐ <b>ʀᴇǫᴜᴇsᴛᴇᴅ</b>: {mention}",
            reply_markup=kb,
        )
    except Exception as e:
        log.warning("classic queue card failed: %r", e)
        return None


# ─── STREAM.PY HOOK (replaces the old queue_4 message, no duplicate) ──────────

class _AQList(list):
    """aq_markup() result tagged with its chat_id, so the proxy can recognise it."""

    chat_id = None


class _AppProxy:
    """Wraps `app` inside stream.py: swaps ONLY the old 'added to queue' message
    (recognised by its aq_markup keyboard) for the rich card; all else passes through."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def send_message(self, chat_id, *args, **kwargs):
        kb = getattr(kwargs.get("reply_markup"), "inline_keyboard", None)
        if isinstance(kb, _AQList):
            try:
                entries = db.get(kb.chat_id) or []
                if entries:
                    e = entries[-1]
                    sent = await send_queued(
                        kb.chat_id,
                        chat_id,
                        len(entries) - 1,
                        e.get("title"),
                        e.get("dur"),
                        e.get("by"),
                        e.get("user_id"),
                        classic_fallback=False,
                    )
                    if sent is not None:
                        return sent
            except Exception as ex:
                log.warning("rich queue card failed, using old message: %r", ex)
        return await self._real.send_message(chat_id, *args, **kwargs)


_hooked = False


def _hook_stream():
    global _hooked
    if _hooked or not HOOK_STREAM:
        return
    _hooked = True
    try:
        mod = importlib.import_module("SHUKLAMUSIC.utils.stream.stream")
        orig_aq = mod.aq_markup

        def aq_markup_tagged(*args, **kwargs):
            lst = _AQList(orig_aq(*args, **kwargs))
            lst.chat_id = args[1] if len(args) > 1 else kwargs.get("chat_id")
            return lst

        mod.aq_markup = aq_markup_tagged
        mod.app = _AppProxy(mod.app)
    except Exception as e:  # pragma: no cover
        log.warning("couldn't hook stream.py queue message: %r", e)


# ─── QUEUE STORAGE ────────────────────────────────────────────────────────────

async def put_queue(
    chat_id,
    original_chat_id,
    file,
    title,
    duration,
    user,
    vidid,
    user_id,
    stream,
    forceplay: Union[bool, str] = None,
):
    _hook_stream()
    title = title.title()
    try:
        duration_in_seconds = time_to_seconds(duration) - 3
    except:
        duration_in_seconds = 0
    put = {
        "title": title,
        "dur": duration,
        "streamtype": stream,
        "by": user,
        "user_id": user_id,
        "chat_id": original_chat_id,
        "file": file,
        "vidid": vidid,
        "seconds": duration_in_seconds,
        "played": 0,
    }
    if forceplay:
        check = db.get(chat_id)
        if check:
            check.insert(0, put)
        else:
            db[chat_id] = []
            db[chat_id].append(put)
    else:
        db[chat_id].append(put)
    autoclean.append(file)


async def put_queue_index(
    chat_id,
    original_chat_id,
    file,
    title,
    duration,
    user,
    vidid,
    stream,
    forceplay: Union[bool, str] = None,
):
    if "20.212.146.162" in vidid:
        try:
            dur = await asyncio.get_event_loop().run_in_executor(
                None, check_duration, vidid
            )
            duration = seconds_to_min(dur)
        except:
            duration = "ᴜʀʟ sᴛʀᴇᴀᴍ"
            dur = 0
    else:
        dur = 0
    put = {
        "title": title,
        "dur": duration,
        "streamtype": stream,
        "by": user,
        "chat_id": original_chat_id,
        "file": file,
        "vidid": vidid,
        "seconds": dur,
        "played": 0,
    }
    if forceplay:
        check = db.get(chat_id)
        if check:
            check.insert(0, put)
        else:
            db[chat_id] = []
            db[chat_id].append(put)
    else:
        db[chat_id].append(put)
