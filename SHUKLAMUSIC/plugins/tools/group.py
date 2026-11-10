import re
import ast
import html
import logging
import operator

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

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
from config import OWNER_ID

log = logging.getLogger("group")

# ==========================================
# RICH MESSAGE HELPERS (same approach as start.py)
# ==========================================
RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected

EMOJI_VC_STARTED = 6019542324864882867
EMOJI_VC_ENDED = 6021531380054235474

# (text, kind, value, style)   kind: "url" | "callback"
_VC_LINK_CACHE = {}


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


def _mention_html(user) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "User")}</a>'


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


async def send_rich_or_classic(client, message: Message, body_html, rows):
    """
    body_html(style, rich: bool) -> str   (inner text, no buttons)
    rows -> rich button rows (may be empty)
    """
    sent = await send_rich(
        client,
        message.chat.id,
        lambda st: f"<aside>{body_html(st, True)}</aside>{rich_buttons(rows)}",
        reply_to=message.id,
    )
    if sent is not None:
        return sent
    return await message.reply_text(
        body_html(0, False),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=classic_keyboard(rows),
        disable_web_page_preview=True,
    )


# ==========================================
# VC JOIN LINK
# ==========================================
async def get_vc_link(client: Client, chat):
    """
    Public group  -> https://t.me/<username>?videochat
    Private group -> <invite link>?videochat  (bot needs 'invite users' admin right)
    Returns None if a link can't be made (then the message is sent without the button).
    """
    if chat.username:
        return f"https://t.me/{chat.username}?videochat"

    if chat.id in _VC_LINK_CACHE:
        return _VC_LINK_CACHE[chat.id]

    try:
        link = getattr(chat, "invite_link", None) or await client.export_chat_invite_link(chat.id)
        link = f"{link}?videochat"
        _VC_LINK_CACHE[chat.id] = link
        return link
    except Exception as e:
        log.warning("couldn't build VC link for %s: %r", chat.id, e)
        return None


def _join_rows(link, style):
    if not link:
        return []
    return [[("Join VC", "url", link, style)]]


# ==========================================
# SAFE MATH EVALUATOR SETUP
# ==========================================
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.BitXor: operator.xor,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def safe_eval(node):
    """
    Safely evaluate a mathematical expression using Python's AST.
    Rejects any functions, imports, or variables.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("Invalid constant")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in SAFE_OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type}")
        left = safe_eval(node.left)
        right = safe_eval(node.right)
        # stop things like 9**9**9 from freezing the bot
        if op_type is ast.Pow and abs(right) > 1000:
            raise ValueError("Exponent too large")
        return SAFE_OPERATORS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in SAFE_OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type}")
        return SAFE_OPERATORS[op_type](safe_eval(node.operand))
    else:
        raise ValueError("Invalid expression: Only numbers and math operators are allowed.")


def evaluate_math_expression(expression: str):
    tree = ast.parse(expression, mode="eval")
    return safe_eval(tree.body)


# ==========================================
# VIDEO CHAT HANDLERS (rich message + rich buttons)
# ==========================================
@app.on_message(filters.video_chat_started)
async def vc_started(client: Client, msg: Message):
    link = await get_vc_link(client, msg.chat)

    def body(style, rich):
        return f"{_remoji(EMOJI_VC_STARTED, style)} <b>Video Chat Started!!</b>"

    await send_rich_or_classic(client, msg, body, _join_rows(link, "primary"))


@app.on_message(filters.video_chat_ended)
async def vc_ended(client: Client, msg: Message):
    def body(style, rich):
        return f"{_remoji(EMOJI_VC_ENDED, style)} <b>Voice Chat ended by admin</b>"

    await send_rich_or_classic(client, msg, body, [])


@app.on_message(filters.video_chat_members_invited)
async def vc_invited(client: Client, message: Message):
    inviter = _mention_html(message.from_user) if message.from_user else "Someone"
    invited = ", ".join(
        _mention_html(u) for u in message.video_chat_members_invited.users
    ) or "someone"

    link = await get_vc_link(client, message.chat)

    def body(style, rich):
        return (
            f"{inviter} invited {invited} to Voicechat, "
            f"Click button below to quickly join VC"
        )

    try:
        await send_rich_or_classic(client, message, body, _join_rows(link, "success"))
    except Exception as e:
        log.warning("vc_invited failed: %r", e)


# ==========================================
# SAFE MATH COMMAND
# ==========================================
@app.on_message(filters.command("math", prefixes=["/", "!", "."]))
async def calculate_math(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("**Please provide an expression!**\nExample: `/math 5 * (10 + 2)`")

    expression = message.text.split(maxsplit=1)[1]

    try:
        result = evaluate_math_expression(expression)
        response = f"**ᴛʜᴇ ʀᴇsᴜʟᴛ ɪs :** `{result}`"
    except ZeroDivisionError:
        response = "❌ **Error:** Division by zero is not allowed."
    except Exception:
        response = "❌ **ɪɴᴠᴀʟɪᴅ ᴇxᴘʀᴇssɪᴏɴ.** Only numbers and mathematical operators `(+, -, *, /, %, ^)` are permitted."

    await message.reply_text(response)


# ==========================================
# OWNER LEAVE GROUP COMMAND
# ==========================================
@app.on_message(filters.command("leavegroup") & filters.user(OWNER_ID))
async def bot_leave(client: Client, message: Message):
    chat_id = message.chat.id
    await message.reply_text("sᴜᴄᴄᴇssғᴜʟʟʏ ʟᴇғᴛ !!")
    await client.leave_chat(chat_id=chat_id, delete=True)


# ==========================================
# GOOGLE SEARCH COMMAND
# ==========================================
@app.on_message(filters.command("spg", prefixes=["/", "!", "."]))
async def search(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Please provide a search query!")

    query = message.text.split(maxsplit=1)[1]
    msg = await message.reply_text("Searching...")

    # Put your real key / cx in config or environment variables, not in the code
    GOOGLE_API_KEY = "YOUR_NEW_GOOGLE_API_KEY"
    SEARCH_ENGINE_ID = "ec8db9e1f9e41e65e"

    start = 1
    url = "https://content-customsearch.googleapis.com/customsearch/v1"
    params = {"cx": SEARCH_ENGINE_ID, "q": query, "key": GOOGLE_API_KEY, "start": start}

    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, params=params, headers={"x-referer": "https://explorer.apis.google.com"}
        ) as r:
            response = await r.json()
            result = ""

            if not response.get("items"):
                return await msg.edit_text("No results found!")

            for item in response["items"]:
                title = item.get("title", "No Title")
                link = item.get("link", "")

                # Clean links
                if "/s" in link:
                    link = link.replace("/s", "")
                elif re.search(r"\/\d", link):
                    link = re.sub(r"\/\d", "", link)
                if "?" in link:
                    link = link.split("?")[0]

                if link in result:
                    continue
                result += f"**{title}**\n{link}\n\n"

            buttons = InlineKeyboardMarkup(
                [[InlineKeyboardButton("▶️ Next ▶️", callback_data=f"next_{start+10}_{query}")]]
            )

            await msg.edit_text(result, disable_web_page_preview=True, reply_markup=buttons)
