import html
import logging
import re

from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ChatMemberStatus, ChatType
from pyrogram.types import Message

try:  # kurigram: rich messages (same as play.py / start.py)
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

from SHUKLAMUSIC import app
from SHUKLAMUSIC.utils.database import set_cmode
from SHUKLAMUSIC.utils.decorators.admins import AdminActual
from config import BANNED_USERS

log = logging.getLogger("channelplay")

# ─── RICH HELPERS (same engine as play.py) ────────────────────────────────────
_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected


def _to_rich(text: str, style: int) -> str:
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


async def say(message: Message, text: str):
    """Rich text card (quote + aside); falls back to a normal reply."""
    global _STYLE
    if InputRichMessage is not None and hasattr(app, "send_rich_message"):
        variants = []
        if ReplyParameters is not None:
            variants.append(dict(reply_parameters=ReplyParameters(message_id=message.id)))
        variants.append(dict(reply_to_message_id=message.id))
        variants.append({})
        for style in (_STYLE, 1 - _STYLE):
            try:
                rich = InputRichMessage(
                    html=f"<blockquote><aside>{_to_rich(text, style)}</aside></blockquote>"
                )
            except Exception as e:
                log.warning("rich build failed: %r", e)
                break
            for extra in variants:
                try:
                    msg = await app.send_rich_message(message.chat.id, rich, **extra)
                    _STYLE = style
                    return msg
                except Exception as e:
                    log.warning("send_rich_message failed (style=%s): %r", style, e)
    return await message.reply_text(text)


# ─── /channelplay ─────────────────────────────────────────────────────────────

@app.on_message(filters.command(["channelplay"]) & filters.group & ~BANNED_USERS)
@AdminActual
async def playmode_(client, message: Message, _):
    if len(message.command) < 2:
        return await say(message, _["cplay_1"].format(message.chat.title))
    query = message.text.split(None, 2)[1].lower().strip()
    if (str(query)).lower() == "disable":
        await set_cmode(message.chat.id, None)
        return await say(message, _["cplay_7"])
    elif str(query) == "linked":
        chat = await app.get_chat(message.chat.id)
        if chat.linked_chat:
            chat_id = chat.linked_chat.id
            await set_cmode(message.chat.id, chat_id)
            return await say(
                message,
                _["cplay_3"].format(chat.linked_chat.title, chat.linked_chat.id),
            )
        else:
            return await say(message, _["cplay_2"])
    else:
        try:
            chat = await app.get_chat(query)
        except:
            return await say(message, _["cplay_4"])
        if chat.type != ChatType.CHANNEL:
            return await say(message, _["cplay_5"])
        cusn = crid = None
        try:
            async for user in app.get_chat_members(
                chat.id, filter=ChatMembersFilter.ADMINISTRATORS
            ):
                if user.status == ChatMemberStatus.OWNER:
                    cusn = user.user.username
                    crid = user.user.id
        except:
            return await say(message, _["cplay_4"])
        if crid != message.from_user.id:
            return await say(message, _["cplay_6"].format(chat.title, cusn))
        await set_cmode(message.chat.id, chat.id)
        return await say(message, _["cplay_3"].format(chat.title, chat.id))
