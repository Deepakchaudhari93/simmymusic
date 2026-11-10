import re
import random
import aiohttp
import traceback
import urllib.parse

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.raw import types as _raw_types
from pyrogram.raw.types import (
    UpdateBotGuestChatQuery,
    InputBotInlineResult,
    InputBotInlineMessageText,
    ReplyInlineMarkup,
    KeyboardButtonRow,
)
from pyrogram.raw.functions.messages import SetBotGuestChatResult, EditInlineBotMessage

try:  # kurigram: coloured buttons
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

from SHUKLAMUSIC import app

# --- BOT USERNAMES (the bot has two now) ---
BOT_URL = "https://t.me/TidalXMusicBot"
EXTRA_USERNAMES = ["TidalXMusicBot", "nyxxai"]   # always recognised, without the @

# --- BUTTONS (same links as start.py) ---
ADD_TEXT = "Add me in your Group"
ADD_URL = f"{BOT_URL}?startgroup=true"
CHANNEL_TEXT = "Join Channel"
CHANNEL_URL = "https://t.me/hmstalker"

# --- CUSTOM EMOJIS MAPPING ---
E_USDT = "<emoji id=6028584717081645421>💵</emoji>"
E_TON = "<emoji id=5188672371648634636>👛</emoji>"
E_RUB = "<emoji id=5357190623302539310>💰</emoji>"
E_STAR = "<emoji id=5420297161898204628>😁</emoji>"
E_HELLO = "<emoji id=6113857401503357100>👋</emoji>"
E_WHAT = "<emoji id=6111414389745654598>😏</emoji>"
E_MONEY = "<emoji id=5409078930659357770>💸</emoji>"
E_SPIKE = "<emoji id=5298614648138919107>📈</emoji>"
E_COINS = "<emoji id=5201873447554145566>💵</emoji>"
E_CONVERT = "<emoji id=5377336227533969892>💱</emoji>"
E_TGMONEY = "<emoji id=5463424023734014980>🛫</emoji>"


# =====================================================================
# BUTTONS
# =====================================================================
def _classic_button(text, url, style_name):
    kwargs = {"url": url}
    if ButtonStyle is not None:
        try:
            return InlineKeyboardButton(
                text, style=getattr(ButtonStyle, style_name), **kwargs
            )
        except Exception:
            pass
    return InlineKeyboardButton(text, **kwargs)


def _build_classic_markup():
    return InlineKeyboardMarkup([
        [_classic_button(ADD_TEXT, ADD_URL, "SUCCESS")],
        [_classic_button(CHANNEL_TEXT, CHANNEL_URL, "DANGER")],
    ])


SUPPORT_BUTTON = _build_classic_markup()


def _raw_button(cls, text, url, style_flag):
    """Raw url button with a colour style; falls back to an unstyled one."""
    style_cls = getattr(_raw_types, "KeyboardButtonStyle", None)
    if style_cls is not None:
        try:
            return cls(text=text, url=url, style=style_cls(**{style_flag: True}))
        except Exception:
            pass
    return cls(text=text, url=url)


def _build_raw_support_button():
    """Raw url buttons; the class name differs between pyrogram/kurigram builds.
    If none exists, return None (the inline result is then sent without buttons)."""
    for name in ("KeyboardButtonUrl", "InputKeyboardButtonUrl"):
        cls = getattr(_raw_types, name, None)
        if cls is None:
            continue
        try:
            return ReplyInlineMarkup(
                rows=[
                    KeyboardButtonRow(
                        buttons=[_raw_button(cls, ADD_TEXT, ADD_URL, "bg_success")]
                    ),
                    KeyboardButtonRow(
                        buttons=[_raw_button(cls, CHANNEL_TEXT, CHANNEL_URL, "bg_danger")]
                    ),
                ]
            )
        except Exception:
            continue
    return None


RAW_SUPPORT_BUTTON = _build_raw_support_button()

# --- CURRENCY SYMBOL MAPPING ---
CURRENCY_SYMBOLS = {
    '₱': 'PHP', '$': 'USD', '€': 'EUR', '£': 'GBP', '₹': 'INR'
}

# --- QUERY PATTERNS ---
HELP_PATTERN = re.compile(r'^(?:what|help|info|start)$', re.IGNORECASE)
IMAGE_PATTERN = re.compile(
    r'(?:generate|create|draw|make|imagine)\s+(?:an?\s+)?(?:image|picture|photo|pic)\s+(?:of\s+)?(.+)',
    re.IGNORECASE
)


# =====================================================================
# USERNAME HELPERS
# =====================================================================
def bot_usernames(client: Client) -> set:
    """All usernames the bot answers to (lowercase, no @)."""
    names = {u.lower() for u in EXTRA_USERNAMES}
    me = client.me
    if me:
        if me.username:
            names.add(me.username.lower())
        for u in getattr(me, "usernames", None) or []:
            n = getattr(u, "username", None)
            if n:
                names.add(n.lower())
    return names


def primary_username(client: Client) -> str:
    me = client.me
    return (me.username if me and me.username else "TidalXMusicBot")


def clean_query_text(client: Client, text: str) -> str:
    """Removes every bot username, then any other @username."""
    for n in bot_usernames(client):
        text = re.sub(rf"@{re.escape(n)}\b", "", text, flags=re.IGNORECASE)
    return re.sub(r"@[a-zA-Z0-9_]+", "", text).strip()


async def _mentions_bot(_, client, message):
    if getattr(message, "mentioned", False):
        return True
    text = (message.text or message.caption or "").lower()
    return any(f"@{n}" in text for n in bot_usernames(client))


mentions_bot = filters.create(_mentions_bot)


def extract_currency_query(text: str):
    """Parses text to extract amounts and 3-5 character currencies or crypto symbols."""
    pattern = r"(?:what'?s\s+)?(\d+(?:\.\d+)?)\s*([a-zA-Z]{3,5}|₱|\$|€|£|₹)\s*(?:in|to)\s*([a-zA-Z]{3,5})"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return {
            "amount": float(match.group(1)),
            "from": match.group(2).upper(),
            "to": match.group(3).upper()
        }
    return None


async def fetch_exchange_rate(from_curr: str, to_curr: str):
    """Fetches market values across multiple platforms as an aggregator."""
    from_curr = CURRENCY_SYMBOLS.get(from_curr, from_curr)
    to_curr = CURRENCY_SYMBOLS.get(to_curr, to_curr)

    try:
        url = f"https://api.coinbase.com/v2/exchange-rates?currency={from_curr}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    rates = res.get("data", {}).get("rates", {})
                    if to_curr in rates:
                        return float(rates[to_curr]), "Coinbase Hub"
    except Exception:
        pass

    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{from_curr}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    rates = res.get("rates", {})
                    if to_curr in rates:
                        return float(rates[to_curr]), "ExchangeRate Core"
    except Exception:
        pass

    return None, None


# =====================================================================
# 1. NORMAL MODE HANDLER (BOT AS MEMBER / DM)
# =====================================================================
@app.on_message((filters.private | mentions_bot) & ~filters.bot, group=5)
async def normal_mention_handler(client: Client, message: Message):
    text = message.text or message.caption or ""

    # Ignore commands so other modules can handle them
    if text.startswith(("/", "!", "?", ".")):
        return

    me = client.me
    bot_username = primary_username(client)

    # BE SILENT when user replies to bot's message
    if message.reply_to_message:
        if message.reply_to_message.from_user and me and message.reply_to_message.from_user.id == me.id:
            return

    # Clean text: remove bot usernames (both) and other usernames
    clean_text = clean_query_text(client, text)

    # Check for valid queries only
    curr_query = extract_currency_query(clean_text)
    img_match = IMAGE_PATTERN.search(clean_text)
    is_help = HELP_PATTERN.match(clean_text)

    # BE SILENT if no valid query detected
    if not curr_query and not img_match and not is_help:
        return

    user_name = message.from_user.first_name if message.from_user else "there"

    # --- HELP / INFO ---
    if is_help:
        reply_text = (
            f"<b>{E_STAR} <u>Tidal Music Engine</u></b> {E_CONVERT}\n\n"
            f"<i>Hello {user_name}! {E_HELLO}</i>\n\n"
            f"<b>{E_SPIKE} <u>Available Queries:</u></b>\n"
            f"• <code>@{bot_username} 1 usdt to inr</code> {E_USDT}\n"
            f"• <code>@{bot_username} 100 usd to eur</code> {E_MONEY}\n"
            f"• <code>@{bot_username} generate an image of a horse</code> 🎨\n\n"
            f"<blockquote><i>Tag me with a valid query only! {E_COINS}</i></blockquote>"
        )
        await message.reply_text(reply_text, parse_mode=ParseMode.HTML, reply_markup=SUPPORT_BUTTON)
        return

    # --- IMAGE GENERATION ---
    if img_match:
        prompt = img_match.group(1).strip()
        if not prompt:
            return

        encoded_prompt = urllib.parse.quote(prompt)
        seed = random.randint(1, 999999)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}"

        status_msg = await message.reply_text(
            f"🎨 <i><u>Generating your image...</u> Please wait... {E_STAR}</i>",
            parse_mode=ParseMode.HTML
        )

        try:
            await message.reply_photo(
                photo=image_url,
                caption=(
                    f"<b>🎨 Generated Image</b>\n\n"
                    f"<i>Prompt:</i> <code>{prompt}</code>\n\n"
                    f"<blockquote>{E_TGMONEY} ʙʏ : @{bot_username}</blockquote>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=SUPPORT_BUTTON
            )
            await status_msg.delete()
        except Exception:
            # Fallback: send as text with link if photo fails
            await status_msg.edit_text(
                f"🎨 <b>Generated Image</b>\n\n"
                f"<i>Prompt:</i> <code>{prompt}</code>\n\n"
                f"<a href='{image_url}'>🔗 Click here to view image</a>\n\n"
                f"<blockquote>{E_TGMONEY} ʙʏ : @{bot_username}</blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=SUPPORT_BUTTON,
                disable_web_page_preview=False
            )
        return

    # --- CURRENCY CONVERSION ---
    if curr_query:
        status_msg = await message.reply_text(
            f"{E_CONVERT} <i><u>Calculating conversion rates from multi-platforms...</u> Please wait... {E_STAR}</i>",
            parse_mode=ParseMode.HTML
        )

        rate, platform = await fetch_exchange_rate(curr_query["from"], curr_query["to"])

        if rate:
            amount = curr_query["amount"]
            converted = round(amount * rate, 4)
            reply_text = (
                f'<b>{E_MONEY} <u>CURRENCY & TOKEN CONVERSION</u></b>\n\n'
                f'<i>{amount} {curr_query["from"]}</i> {E_CONVERT} <b>{converted} {curr_query["to"]}</b> {E_COINS}\n\n'
                f'<blockquote expandable><b>{E_SPIKE} <u>Multi-Platform Aggregator</u>:</b>\n'
                f'• Source: <b>{platform}</b>\n'
                f'• Rate: <i>1 {curr_query["from"]} = {rate} {curr_query["to"]}</i></blockquote>\n'
                f'<blockquote>{E_TGMONEY} ʙʏ : @{bot_username}</blockquote>'
            )
        else:
            reply_text = f"❌ <b>Unable to find pairs for <u>{curr_query['from']}</u> to <u>{curr_query['to']}</u> across data platforms.</b> {E_WHAT}"

        await status_msg.edit_text(reply_text, parse_mode=ParseMode.HTML, reply_markup=SUPPORT_BUTTON)
        return


# =====================================================================
# 2. GUEST MODE RAW HANDLER (BOT NOT A MEMBER)
# =====================================================================
@app.on_raw_update(group=5)
async def guest_mode_raw_handler(client: Client, update, users: dict, chats: dict):
    if not isinstance(update, UpdateBotGuestChatQuery):
        return

    try:
        query_id = update.query_id
        trigger_message = update.message

        if not hasattr(trigger_message, "message"):
            return

        text = trigger_message.message or ""

        # Ignore commands in guest mode
        if text.startswith(("/", "!", "?", ".")):
            return

        me = client.me
        bot_username = primary_username(client)

        # BE SILENT on replies to bot's message
        is_reply_to_me = False
        if hasattr(update, 'reference_messages') and update.reference_messages:
            for ref_msg in update.reference_messages:
                if hasattr(ref_msg, 'from_id'):
                    ref_user_id = getattr(ref_msg.from_id, 'user_id', None)
                    if me and ref_user_id == me.id:
                        is_reply_to_me = True
                        break

        if is_reply_to_me:
            return

        # strips @TidalXMusicBot, @nyxxai (and any other @username)
        clean_text = clean_query_text(client, text)

        # Check for valid queries only
        curr_query = extract_currency_query(clean_text)
        img_match = IMAGE_PATTERN.search(clean_text)
        is_help = HELP_PATTERN.match(clean_text)

        # BE SILENT if no valid query
        if not curr_query and not img_match and not is_help:
            return

        user_name = "there"
        if hasattr(trigger_message, 'from_id'):
            user_id = getattr(trigger_message.from_id, 'user_id', None)
            if user_id and user_id in users:
                user_name = users[user_id].first_name or "there"

        # --- HELP / INFO ---
        if is_help:
            reply_text = (
                f"<b>{E_STAR} <u>Tidal Music Engine</u></b> {E_CONVERT}\n\n"
                f"<i>Hello {user_name}! {E_HELLO}</i>\n\n"
                f"<b>{E_SPIKE} <u>Available Queries:</u></b>\n"
                f"• <code>@{bot_username} 1 usdt to inr</code> {E_USDT}\n"
                f"• <code>@{bot_username} generate an image of a horse</code> 🎨\n\n"
                f"<blockquote><i>Tag me with a valid query only! {E_COINS}</i></blockquote>"
            )
            parsed_final = await client.parser.parse(reply_text, ParseMode.HTML)
            result_id = str(random.randint(100000, 999999))
            inline_result = InputBotInlineResult(
                id=result_id,
                type="article",
                title="Tidal Music Help",
                send_message=InputBotInlineMessageText(
                    message=parsed_final["message"],
                    entities=parsed_final["entities"],
                    no_webpage=True,
                    reply_markup=RAW_SUPPORT_BUTTON
                )
            )
            await client.invoke(SetBotGuestChatResult(query_id=query_id, result=inline_result))
            return

        # --- IMAGE GENERATION ---
        if img_match:
            prompt = img_match.group(1).strip()
            if not prompt:
                return

            encoded_prompt = urllib.parse.quote(prompt)
            seed = random.randint(1, 999999)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}"

            reply_text = (
                f"<b>🎨 Image Generation</b>\n\n"
                f"<i>Prompt:</i> <code>{prompt}</code>\n\n"
                f"<a href='{image_url}'>🔗 Click here to view generated image</a>\n\n"
                f"<blockquote>{E_TGMONEY} ʙʏ : @{bot_username}</blockquote>"
            )
            parsed_final = await client.parser.parse(reply_text, ParseMode.HTML)
            result_id = str(random.randint(100000, 999999))
            inline_result = InputBotInlineResult(
                id=result_id,
                type="article",
                title="Tidal Music Image",
                send_message=InputBotInlineMessageText(
                    message=parsed_final["message"],
                    entities=parsed_final["entities"],
                    no_webpage=True,
                    reply_markup=RAW_SUPPORT_BUTTON
                )
            )
            await client.invoke(SetBotGuestChatResult(query_id=query_id, result=inline_result))
            return

        # --- CURRENCY CONVERSION ---
        if curr_query:
            calc_text = f"{E_CONVERT} <i><u>Calculating conversion rates from multi-platforms...</u> Please wait... {E_STAR}</i>"
            parsed_calc = await client.parser.parse(calc_text, ParseMode.HTML)

            result_id = str(random.randint(100000, 999999))
            inline_result = InputBotInlineResult(
                id=result_id,
                type="article",
                title="Tidal Music Conversion",
                send_message=InputBotInlineMessageText(
                    message=parsed_calc["message"],
                    entities=parsed_calc["entities"],
                    no_webpage=True,
                    reply_markup=RAW_SUPPORT_BUTTON
                )
            )

            msg_id_obj = await client.invoke(
                SetBotGuestChatResult(query_id=query_id, result=inline_result)
            )

            rate, platform = await fetch_exchange_rate(curr_query["from"], curr_query["to"])

            if rate:
                amount = curr_query["amount"]
                converted = round(amount * rate, 4)
                reply_text = (
                    f'<b>{E_MONEY} <u>CURRENCY & TOKEN CONVERSION</u></b>\n\n'
                    f'<i>{amount} {curr_query["from"]}</i> {E_CONVERT} <b>{converted} {curr_query["to"]}</b> {E_COINS}\n\n'
                    f'<blockquote expandable><b>{E_SPIKE} <u>Multi-Platform Aggregator</u>:</b>\n'
                    f'• Source: <b>{platform}</b>\n'
                    f'• Rate: <i>1 {curr_query["from"]} = {rate} {curr_query["to"]}</i></blockquote>\n'
                    f'<blockquote>{E_TGMONEY} ʙʏ : @{bot_username}</blockquote>'
                )
            else:
                reply_text = f"❌ <b>Unable to find pairs for <u>{curr_query['from']}</u> to <u>{curr_query['to']}</u> across data platforms.</b> {E_WHAT}"

            parsed_final = await client.parser.parse(reply_text, ParseMode.HTML)
            await client.invoke(
                EditInlineBotMessage(
                    id=msg_id_obj,
                    message=parsed_final["message"],
                    entities=parsed_final["entities"],
                    no_webpage=True,
                    reply_markup=RAW_SUPPORT_BUTTON
                )
            )
            return

    except Exception as e:
        traceback.print_exc()
        print(f"Guest Bot Error: {e}")
