import datetime
import html
import logging

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from pyrogram.errors.exceptions.bad_request_400 import ChatAdminRequired, UserAdminInvalid

try:
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

try:  # kurigram: coloured buttons
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

from SHUKLAMUSIC import app

log = logging.getLogger("ban")

BOT_ID = 6824607634

# ==========================================
# RICH MESSAGE HELPERS (same approach as start.py)
# ==========================================
RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected

EMOJI_BAN = 5472267631979405211
UNBAN_CB_PREFIX = "rbunban_"


def _remoji(emoji_id: int, style: int, fallback: str = "⭐") -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{fallback}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def rich_buttons(rows) -> str:
    out = ""
    for row in rows:
        out += "<tg-button-row>"
        for text, kind, value, style in row:
            st = f' style="{style}"' if style else ""
            label = html.escape(text, quote=False)
            if kind == "url":
                out += f'<tg-button type="url" url="{html.escape(value)}"{st}>{label}</tg-button>'
            else:
                out += f'<tg-button type="callback_data" data="{html.escape(value)}"{st}>{label}</tg-button>'
        out += "</tg-button-row>"
    return out


def classic_keyboard(rows):
    if not rows:
        return None
    kb = []
    for row in rows:
        line = []
        for text, kind, value, style in row:
            kwargs = {"url": value} if kind == "url" else {"callback_data": value}
            btn = None
            if style and ButtonStyle is not None:
                try:
                    btn = InlineKeyboardButton(
                        text, style=getattr(ButtonStyle, style.upper()), **kwargs
                    )
                except Exception:
                    btn = None
            if btn is None:
                btn = InlineKeyboardButton(text, **kwargs)
            line.append(btn)
        kb.append(line)
    return InlineKeyboardMarkup(kb)


async def send_rich(client, chat_id: int, html_func, reply_to: int = None):
    """html_func(style) -> str. Returns sent Message or None if rich failed."""
    global RICH_EMOJI_STYLE
    if InputRichMessage is None or not hasattr(client, "send_rich_message"):
        return None

    variants = []
    if reply_to:
        if ReplyParameters is not None:
            variants.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
        variants.append(dict(reply_to_message_id=reply_to))
    else:
        variants.append({})

    last_err = None
    for style in (RICH_EMOJI_STYLE, 1 - RICH_EMOJI_STYLE):
        try:
            rich = InputRichMessage(html=html_func(style))
        except Exception as e:
            last_err = e
            break
        for extra in variants:
            try:
                msg = await client.send_rich_message(chat_id, rich, **extra)
                RICH_EMOJI_STYLE = style
                return msg
            except Exception as e:
                last_err = e
                continue
    log.warning("rich message failed, using classic fallback: %r", last_err)
    return None


# ==========================================
# MENTIONS
# ==========================================
def mention(user, name, mention=True):
    """Markdown mention (used by the plain mute/unban replies)."""
    if mention:
        link = f"[{name}](tg://openmessage?user_id={user})"
    else:
        link = f"[{name}](https://t.me/{user})"
    return link


def mention_html(user_id, name) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name or "User")}</a>'


# ==========================================
# USER RESOLVING (the fix for "I can't find that user")
# ==========================================
def _clean_username(token: str) -> str:
    t = token.strip()
    for p in ("https://t.me/", "http://t.me/", "t.me/", "https://telegram.me/", "telegram.me/"):
        if t.lower().startswith(p):
            t = t[len(p):]
            break
    return t.lstrip("@").split("?")[0].strip("/")


async def _lookup_username(chat_id, username: str):
    """Try several ways to turn a @username into (id, first_name)."""
    username = _clean_username(username)
    if not username:
        return None

    # 1) direct lookup
    for candidate in (username, f"@{username}"):
        try:
            user = await app.get_users(candidate)
            if user:
                return [user.id, user.first_name]
        except Exception:
            pass

    # 2) search inside the current chat's members
    try:
        async for m in app.get_chat_members(chat_id, query=username, limit=10):
            u = m.user
            if u and (u.username or "").lower() == username.lower():
                return [u.id, u.first_name]
    except Exception:
        pass

    # 3) generic chat lookup (works for some users the bot has not seen)
    try:
        chat = await app.get_chat(username)
        return [chat.id, getattr(chat, "first_name", None) or getattr(chat, "title", None) or "User"]
    except Exception:
        pass

    return None


async def get_userid_from_username(username, chat_id=None):
    return await _lookup_username(chat_id, username)


async def resolve_target(message):
    """
    Works out who the command is aimed at.
    Returns (user_id, first_name, rest_text, error_text).
    rest_text = everything after the target (reason / time) or None.
    """
    text = message.text or ""
    parts = text.split(None, 2)
    reply = message.reply_to_message

    # reply to someone's message
    if reply:
        if reply.from_user:
            rest = text.split(None, 1)[1].strip() if len(parts) > 1 else None
            return reply.from_user.id, reply.from_user.first_name, rest or None, None
        return None, None, None, "I can't find that user"

    if len(parts) < 2:
        return None, None, None, "Please specify a valid user or reply to that user's message"

    token = parts[1]
    rest = parts[2].strip() if len(parts) > 2 else None
    rest = rest or None

    # tapped/inline text mention
    for e in message.entities or []:
        if getattr(e.type, "name", "") == "TEXT_MENTION" and getattr(e, "user", None):
            return e.user.id, e.user.first_name, rest, None

    # numeric id
    if token.lstrip("-").isdigit():
        user_id = int(token)
        name = "User"
        try:
            u = await app.get_users(user_id)
            name = u.first_name or "User"
        except Exception:
            pass
        return user_id, name, rest, None

    # username / link
    found = await _lookup_username(message.chat.id, token)
    if not found:
        return None, None, None, "I can't find that user"
    return found[0], found[1], rest, None


def _parse_duration(text):
    """'2m' / '3h' / '1d' -> timedelta, or None."""
    if not text:
        return None
    t = text.split()[0].lower()
    try:
        amount = int(t[:-1])
    except Exception:
        return None
    if amount < 1:
        return None
    unit = t[-1]
    if unit == "m":
        return datetime.timedelta(minutes=amount)
    if unit == "h":
        return datetime.timedelta(hours=amount)
    if unit == "d":
        return datetime.timedelta(days=amount)
    return None


# ==========================================
# BAN / UNBAN / MUTE HELPERS
# ==========================================
async def ban_user(user_id, first_name, admin_id, admin_name, chat_id, reason, time=None):
    """
    Returns (text, ok).
    On success `text` is HTML (mentions + reason) WITHOUT the emoji; on failure it's plain text.
    """
    try:
        await app.ban_chat_member(chat_id, user_id)
    except ChatAdminRequired:
        return "Make sure that you have given me that right", False
    except UserAdminInvalid:
        return "I won't ban an admin bruh!!", False
    except Exception as e:
        if user_id == BOT_ID:
            return "Why should I ban myself? Sorry but I'm not stupid like you", False
        return f"Oops!!\n{e}", False

    msg_text = f"{mention_html(user_id, first_name)} was banned by {mention_html(admin_id, admin_name)}"
    if reason:
        msg_text += f"<br>Reason: <code>{html.escape(str(reason).strip())}</code>"
    if time:
        msg_text += f"<br>Time: <code>{html.escape(str(time))}</code>"
    return msg_text, True


async def unban_user(user_id, first_name, admin_id, admin_name, chat_id):
    try:
        await app.unban_chat_member(chat_id, user_id)
    except ChatAdminRequired:
        return "Make sure that you have given me that right"
    except Exception as e:
        return f"Oops!!\n{e}"
    user_mention = mention(user_id, first_name)
    admin_mention = mention(admin_id, admin_name)
    return f"{user_mention} was unbanned by {admin_mention}"


async def mute_user(user_id, first_name, admin_id, admin_name, chat_id, reason, time=None):
    try:
        if time:
            mute_end_time = datetime.datetime.now() + time
            await app.restrict_chat_member(chat_id, user_id, ChatPermissions(), mute_end_time)
        else:
            await app.restrict_chat_member(chat_id, user_id, ChatPermissions())
    except ChatAdminRequired:
        return "Make sure that you have given me that right", False
    except UserAdminInvalid:
        return "I won't mute an admin bruh!!", False
    except Exception as e:
        if user_id == BOT_ID:
            return "Why should I mute myself? Sorry but I'm not stupid like you", False
        return f"Oops!!\n{e}", False

    user_mention = mention(user_id, first_name)
    admin_mention = mention(admin_id, admin_name)
    msg_text = f"{user_mention} was muted by {admin_mention}\n\n"
    if reason:
        msg_text += f"Reason: `{reason}`\n"
    if time:
        msg_text += f"Time: `{time}`\n"
    return msg_text, True


async def unmute_user(user_id, first_name, admin_id, admin_name, chat_id):
    try:
        await app.restrict_chat_member(
            chat_id,
            user_id,
            ChatPermissions(
                can_send_media_messages=True,
                can_send_messages=True,
                can_send_other_messages=True,
                can_send_polls=True,
                can_add_web_page_previews=True,
                can_invite_users=True
            )
        )
    except ChatAdminRequired:
        return "Make sure that you have given me that right"
    except Exception as e:
        return f"Oops!!\n{e}"
    user_mention = mention(user_id, first_name)
    admin_mention = mention(admin_id, admin_name)
    return f"{user_mention} was unmuted by {admin_mention}"


async def _can_restrict(chat, user_id) -> bool:
    try:
        member = await chat.get_member(user_id)
    except Exception:
        return False
    if member.status == enums.ChatMemberStatus.OWNER:
        return True
    return (
        member.status == enums.ChatMemberStatus.ADMINISTRATOR
        and member.privileges is not None
        and member.privileges.can_restrict_members
    )


async def _admin_ok(message) -> bool:
    if not message.from_user:
        return False
    return await _can_restrict(message.chat, message.from_user.id)


# ==========================================
# BAN RESULT MESSAGE (rich + rich buttons)
# ==========================================
async def send_ban_result(client, message, user_id, body_html):
    rows = [
        [("View User", "url", f"tg://user?id={user_id}", "primary")],
        [("Unban User", "callback", f"{UNBAN_CB_PREFIX}{user_id}", "danger")],
    ]

    sent = await send_rich(
        client,
        message.chat.id,
        lambda st: f"<aside>{_remoji(EMOJI_BAN, st)} {body_html}</aside>{rich_buttons(rows)}",
        reply_to=message.id,
    )
    if sent is not None:
        return sent

    # classic fallback
    return await message.reply_text(
        f'<emoji id={EMOJI_BAN}>🔨</emoji> {body_html.replace("<br>", chr(10))}',
        parse_mode=enums.ParseMode.HTML,
        reply_markup=classic_keyboard(rows),
        disable_web_page_preview=True,
    )


# ==========================================
# UNBAN BUTTON (admins with ban rights only)
# ==========================================
@app.on_callback_query(filters.regex(rf"^{UNBAN_CB_PREFIX}(\d+)$"))
async def unban_button_handler(client, query):
    user_id = int(query.matches[0].group(1))
    chat = query.message.chat
    admin = query.from_user

    if not await _can_restrict(chat, admin.id):
        return await query.answer(
            "Only admins with ban rights can use this button.", show_alert=True
        )

    try:
        await app.unban_chat_member(chat.id, user_id)
    except ChatAdminRequired:
        return await query.answer("Make sure that you have given me that right", show_alert=True)
    except Exception as e:
        return await query.answer(f"Oops!! {e}"[:200], show_alert=True)

    try:
        target = await app.get_users(user_id)
        target_name = target.first_name
    except Exception:
        target_name = "User"

    new_text = (
        f'<emoji id={EMOJI_BAN}>🔨</emoji> '
        f"{mention_html(user_id, target_name)} was unbanned by {mention_html(admin.id, admin.first_name)}"
    )

    await query.answer("User unbanned ✅")

    # edit the message into the unbanned text, with no buttons
    try:
        await query.message.edit_text(
            new_text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.warning("edit after unban failed (%r), resending", e)
        try:
            await query.message.delete()
        except Exception:
            pass
        await app.send_message(
            chat.id, new_text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True
        )


# ==========================================
# COMMANDS
# ==========================================
@app.on_message(filters.command(["ban"]) & filters.group)
async def ban_command_handler(client, message):
    if not await _admin_ok(message):
        return await message.reply_text("You don't have permission to ban someone")

    user_id, first_name, reason, err = await resolve_target(message)
    if err:
        return await message.reply_text(err)

    admin = message.from_user
    msg_text, result = await ban_user(user_id, first_name, admin.id, admin.first_name, message.chat.id, reason)
    if result:
        await send_ban_result(client, message, user_id, msg_text)
    else:
        await message.reply_text(msg_text, parse_mode=enums.ParseMode.DISABLED)


@app.on_message(filters.command(["unban"]) & filters.group)
async def unban_command_handler(client, message):
    if not await _admin_ok(message):
        return await message.reply_text("You don't have permission to unban someone")

    user_id, first_name, _, err = await resolve_target(message)
    if err:
        return await message.reply_text(err)

    admin = message.from_user
    msg_text = await unban_user(user_id, first_name, admin.id, admin.first_name, message.chat.id)
    await message.reply_text(msg_text)


@app.on_message(filters.command(["mute"]) & filters.group)
async def mute_command_handler(client, message):
    if not await _admin_ok(message):
        return await message.reply_text("You don't have permission to mute someone")

    user_id, first_name, reason, err = await resolve_target(message)
    if err:
        return await message.reply_text(err)

    admin = message.from_user
    msg_text, _ = await mute_user(user_id, first_name, admin.id, admin.first_name, message.chat.id, reason)
    await message.reply_text(msg_text)


@app.on_message(filters.command(["unmute"]) & filters.group)
async def unmute_command_handler(client, message):
    if not await _admin_ok(message):
        return await message.reply_text("You don't have permission to unmute someone")

    user_id, first_name, _, err = await resolve_target(message)
    if err:
        return await message.reply_text(err)

    admin = message.from_user
    msg_text = await unmute_user(user_id, first_name, admin.id, admin.first_name, message.chat.id)
    await message.reply_text(msg_text)


@app.on_message(filters.command(["tmute"]) & filters.group)
async def tmute_command_handler(client, message):
    if not await _admin_ok(message):
        return await message.reply_text("You don't have permission to mute someone")

    user_id, first_name, rest, err = await resolve_target(message)
    if err:
        return await message.reply_text(
            err + "\nFormat: /tmute <username> <time>" if "specify" in err else err
        )

    duration = _parse_duration(rest)
    if duration is None:
        return await message.reply_text(
            "Wrong format!!\nFormat: /tmute 2m\nm: Minutes\nh: Hours\nd: Days"
        )

    admin = message.from_user
    msg_text, _ = await mute_user(
        user_id, first_name, admin.id, admin.first_name, message.chat.id, reason=None, time=duration
    )
    await message.reply_text(msg_text)
