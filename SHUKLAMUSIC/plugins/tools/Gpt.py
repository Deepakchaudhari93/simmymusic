import json
import asyncio
import aiohttp
import html as html_module
import re
import time
import io
import urllib.parse
from SHUKLAMUSIC import app
from pyrogram.enums import ChatAction, ParseMode, ChatType
from pyrogram import filters, types
from pyrogram.errors import PremiumAccountRequired, BadRequest
from os import getenv

GROQ_API_KEY = getenv("GROQ_API_KEY")

# â”€â”€â”€ ENDPOINTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# â”€â”€â”€ CUSTOM EMOJIS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
EMOJI_GENERAL   = "5573473356579078196"   # âœ¨ sparkle
EMOJI_IMAGE     = "5535333750637854737"   # ðŸŽ¨ art
EMOJI_CODE      = "5537247356136718385"   # ðŸ’» code

def emoji_html(emoji_id: str, fallback: str = "âœ¨") -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

SPARKLE_HTML    = emoji_html(EMOJI_GENERAL, "âœ¨")
IMAGE_EMOJI_HTML= emoji_html(EMOJI_IMAGE, "ðŸŽ¨")
CODE_EMOJI_HTML = emoji_html(EMOJI_CODE, "ðŸ’»")

# â”€â”€â”€ MODELS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
MODEL_GENERAL = "openai/gpt-oss-20b"
MODEL_CODE    = "openai/gpt-oss-120b"
MODEL_NSFW    = "openai/gpt-oss-safeguard-20b"

# â”€â”€â”€ SUBCOMMANDS / TRIGGERS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
IMAGE_SUBCOMMANDS = {"generate", "image", "img", "gen", "draw"}
CODE_SUBCOMMANDS  = {"code"}
LONG_TRIGGERS     = {"brief", "discuss", "detailed", "explain", "long", "full", "elaborate"}

GROQ_HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json"
}

# â”€â”€â”€ SYSTEM PROMPTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SYSTEM_PROMPT_GENERAL = """You are a fast, intelligent AI assistant on Telegram created by @subdict.

CRITICAL INSTRUCTIONS:
1. CREATOR ATTRIBUTION: If asked who created you, who made you, or who developed you, ALWAYS answer directly that you were created by @subdict.
2. RESPONSE LENGTH: Keep answers SHORT, CONCISE, and TO THE POINT by default. Do NOT write lengthy introductions or long essays unless explicitly asked.
3. OUTPUT FORMAT: Output RAW HTML ONLY. NEVER use Markdown (*, **, __, ```).
4. ALLOWED HTML TAGS: <b>, <i>, <u>, <s>, <code>, <pre>, <blockquote>, <a href="...">, <tg-emoji>.
5. STRUCTURE:
   <b><u>Heading / Title</u></b>
   <br><br>
   <i>Direct short answer.</i>
   <br><br>
   â€¢ <b>Key Point:</b> Brief detail.
6. ALWAYS close every opened HTML tag cleanly.
7. HYPERLINKS: When mentioning websites, frameworks, or resources, ALWAYS wrap them in <a href="URL">text</a>.
"""

SYSTEM_PROMPT_CODE = """You are an expert software developer and architect assistant created by @subdict.

CRITICAL INSTRUCTIONS:
1. CREATOR ATTRIBUTION: If asked who created you, who made you, or who developed you, ALWAYS answer directly that you were created by @subdict.
2. DETAILED & FULL CODE: Provide complete, fully functional, production-ready code. NEVER truncate code or use comments like "# rest of code here".
3. OUTPUT FORMAT: Output RAW HTML ONLY. NEVER use Markdown.
4. CODE BLOCK FORMAT:
   <b><u>ðŸ“„ filename.ext</u></b>
   <pre><code class="language-python">
   // Full code goes here
   </code></pre>
   <br>
5. RICH EXPLANATIONS: Use <b>bold</b>, <i>italic</i>, <code>inline code</code>, and <blockquote>notes/warnings</blockquote> for deep explanation.
6. Telegram rich messages support up to 32,000 characters. Use detailed explanations and full scripts.
7. ALWAYS close every opened HTML tag cleanly.
8. HYPERLINKS: Link to docs, GitHub repos, or resources using <a href="URL">text</a>.
"""

SYSTEM_PROMPT_LONG = """You are a knowledgeable AI assistant on Telegram created by @subdict.

CRITICAL INSTRUCTIONS:
1. CREATOR ATTRIBUTION: If asked who created you, who made you, or who developed you, ALWAYS answer directly that you were created by @subdict.
2. RESPONSE LENGTH: The user asked for a DETAILED / BRIEF DISCUSSION. Provide a thorough, well-structured answer with examples and depth.
3. OUTPUT FORMAT: Output RAW HTML ONLY. NEVER use Markdown.
4. ALLOWED HTML TAGS: <b>, <i>, <u>, <s>, <code>, <pre>, <blockquote>, <a href="...">, <tg-emoji>.
5. STRUCTURE with SECTIONS:
   <b><u>Section Title</u></b>
   <br><br>
   Content with <a href="...">links</a>.
   <br><br>
   <blockquote>Example or note</blockquote>
6. ALWAYS close every opened HTML tag cleanly.
7. HYPERLINKS: Always include relevant clickable links to sources, docs, or examples.
"""

HELP_HTML = (
    f"<b><u>{SPARKLE_HTML} AI Assistant</u></b>\n\n"
    f"<b>1. General Chat</b>\n"
    f"<code>/ai your question</code>\n"
    f"<i>Quick & concise answers.</i>\n\n"
    f"<b>2. Code Generator</b>\n"
    f"<code>/ai code your request</code>\n"
    f"<i>Full working code & deep technical guide (DM only).</i>\n\n"
    f"<b>3. Image Generator</b>\n"
    f"<code>/ai generate a photo of a neon cat</code>\n"
    f"<i>AI image creation from prompt.</i>\n\n"
    f"<b>4. Detailed Mode</b>\n"
    f"<code>/ai briefly discuss about physics with examples</code>\n"
    f"<i>Rich long-form replies with thinking states.</i>"
)

# â”€â”€â”€ IMAGE QUEUE GLOBALS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
image_queue = asyncio.Queue()
_image_worker_started = False
last_image_time = 0.0

# â”€â”€â”€ HTML SANITIZER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ALLOWED_TAGS = {
    "b", "i", "u", "s", "spoiler", "code", "pre", "blockquote",
    "a", "tg-emoji", "tg-thinking", "br", "p", "strong", "em",
    "ins", "del", "strike"
}

SELF_CLOSING = {"br", "p"}


def escape_code_blocks(html: str) -> str:
    def repl(m):
        open_tag = m.group(1)
        code = m.group(2)
        close_tag = m.group(3)
        code = code.replace('&', '&amp;')
        code = code.replace('<', '&lt;')
        code = code.replace('>', '&gt;')
        return open_tag + code + close_tag
    return re.sub(r'(<pre><code[^>]*>)(.*?)(</code></pre>)', repl, html, flags=re.DOTALL)


def sanitize_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    raw_html = re.sub(r"&(?![a-zA-Z0-9#]+;)", "&amp;", raw_html)

    def tag_replacer(match):
        tag = match.group(1).lower().split()[0].rstrip("/>")
        if tag.startswith("/"):
            close_tag = tag[1:]
            if close_tag in ALLOWED_TAGS:
                return match.group(0)
            return ""
        if tag in ALLOWED_TAGS:
            return match.group(0)
        return ""
    cleaned = re.sub(r"<(/?[a-zA-Z0-9-]+[^>]*)>", tag_replacer, raw_html)

    opened = []
    for m in re.finditer(r"<(/?)([a-zA-Z0-9-]+)[^>]*>", cleaned):
        slash, tag = m.group(1), m.group(2).lower()
        if tag not in ALLOWED_TAGS or tag in SELF_CLOSING:
            continue
        if slash:
            if opened and opened[-1] == tag:
                opened.pop()
        else:
            opened.append(tag)
    for tag in reversed(opened):
        cleaned += f"</{tag}>"
    return cleaned.strip()


def strip_thinking_tags(html: str) -> str:
    return re.sub(r'<tg-thinking>.*?</tg-thinking>', '', html, flags=re.DOTALL).strip()


def make_thinking_html(text: str, emoji_id: str = EMOJI_GENERAL) -> str:
    """Bold thinking block with custom emoji. Must be top-level for kurigram."""
    fallback = "âœ¨" if emoji_id == EMOJI_GENERAL else "ðŸŽ¨" if emoji_id == EMOJI_IMAGE else "ðŸ’»"
    return f'<tg-thinking><b>{emoji_html(emoji_id, fallback)} {text}</b></tg-thinking>'


def make_footer_html(mode: str = "general") -> str:
    if mode == "code":
        return f'<br><br><i>Built with {CODE_EMOJI_HTML} by <a href="https://t.me/subdict">@subdict</a></i>'
    return f'<br><br><i>Crafted by <a href="https://t.me/subdict">@subdict</a> with {SPARKLE_HTML}</i>'


# â”€â”€â”€ STREAM SESSION (HTML-BASED RICH MESSAGES) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class RichStreamSession:
    """
    Manages rich message drafts via native kurigram HTML API.
    <tg-thinking> is rendered as a native RichBlockThinking when placed at top.
    """
    def __init__(self, bot, chat_id, mode: str = "general"):
        self.bot = bot
        self.chat_id = chat_id
        self.mode = mode
        self.draft_id = bot.rnd_id()
        self.draft_works = True
        self.placeholder = None
        self._current_body = ""

    def _emoji_id(self) -> str:
        if self.mode == "code":
            return EMOJI_CODE
        elif self.mode == "image":
            return EMOJI_IMAGE
        return EMOJI_GENERAL

    async def set_thinking(self, text: str):
        """Show/update thinking block at the top of the draft."""
        thinking = make_thinking_html(text, self._emoji_id())
        full_html = f"{thinking}<br><br>{self._current_body}"
        await self._update_draft(full_html)

    async def clear_thinking(self, delay: float = 1.0):
        """Remove thinking block after a brief pause so the transition is visible."""
        await asyncio.sleep(delay)
        clean = strip_thinking_tags(self._current_body)
        await self._update_draft(clean)

    async def append_html(self, html: str):
        """Append sanitized HTML to the message body and update draft."""
        self._current_body += html
        await self._update_draft(self._current_body)

    async def set_body(self, html: str):
        """Replace body (keeps thinking if already present)."""
        self._current_body = html
        await self._update_draft(self._current_body)

    async def _update_draft(self, html: str):
        html = sanitize_html(html)
        if not html.strip():
            html = f"{SPARKLE_HTML} <i>Processing request...</i>"

        if self.draft_works:
            try:
                await self.bot.send_rich_message_draft(
                    chat_id=self.chat_id,
                    draft_id=self.draft_id,
                    rich_message=types.InputRichMessage(html=html),
                )
                return
            except Exception:
                self.draft_works = False

        # Fallback to plain message edit
        clean = strip_thinking_tags(html)
        if not clean.strip():
            clean = f"{SPARKLE_HTML} <i>Processing request...</i>"

        if not self.placeholder:
            try:
                self.placeholder = await self.bot.send_message(
                    self.chat_id, clean, parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        else:
            try:
                await self.placeholder.edit_text(clean, parse_mode=ParseMode.HTML)
            except Exception:
                pass

    async def finalize(self, final_html: str):
        """Send final rich message and clean up placeholders."""
        final_html = sanitize_html(final_html)
        if self.placeholder:
            try:
                await self.placeholder.delete()
            except Exception:
                pass

        # Ensure no thinking tag leaks into final message
        final_html = strip_thinking_tags(final_html)

        try:
            await self.bot.send_rich_message(
                chat_id=self.chat_id,
                rich_message=types.InputRichMessage(html=final_html),
            )
        except (PremiumAccountRequired, BadRequest):
            await self.bot.send_message(
                self.chat_id, final_html, parse_mode=ParseMode.HTML
            )


# â”€â”€â”€ NSFW CHECKER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async def check_nsfw(prompt: str) -> bool:
    if not GROQ_API_KEY:
        return False
    try:
        payload = {
            "model": MODEL_NSFW,
            "messages": [{
                "role": "user",
                "content": (
                    f'Determine if this prompt requests NSFW, sexual, explicit, nude, or extreme content. '
                    f'Reply ONLY with "NSFW" if unsafe, otherwise "SAFE".\n\nPrompt: {prompt}'
                )
            }],
            "stream": False
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as http:
            async with http.post(GROQ_URL, json=payload, headers=GROQ_HEADERS) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                text = data["choices"][0]["message"]["content"].upper()
                return "NSFW" in text
    except Exception:
        return False


# â”€â”€â”€ IMAGE QUEUE WORKER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async def image_worker_loop(bot):
    global last_image_time
    while True:
        item = await image_queue.get()
        message = item["message"]
        prompt = item["prompt"]
        waiting_msg = item["waiting_msg"]

        try:
            now = time.time()
            wait = 5 - (now - last_image_time)
            if wait > 0:
                try:
                    await waiting_msg.edit_text(
                        f'{IMAGE_EMOJI_HTML} <i>Queue active. Preparing image in {int(wait)}sâ€¦</i>',
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                await asyncio.sleep(wait)

            start = time.time()
            encoded_prompt = urllib.parse.quote(prompt)
            seed = abs(hash(prompt + str(start))) % 100000
            url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                f"?width=1024&height=768&seed={seed}&nologo=true"
            )

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
                async with http.get(url) as resp:
                    if resp.status != 200:
                        raise Exception("Failed to fetch generated image from server.")
                    image_bytes = io.BytesIO(await resp.read())
                    image_bytes.name = "generated_image.jpg"

            elapsed = time.time() - start
            caption = (
                f"{IMAGE_EMOJI_HTML} <b>Image Generated</b>\n\n"
                f"<b>â± Time Took:</b> <code>{elapsed:.2f}s</code>\n"
                f"<b>ðŸ“ Prompt:</b> <i>{html_module.escape(prompt)}</i>\n"
                f"<b>ðŸ‘¤ By:</b> @subdict"
            )

            await bot.send_photo(
                chat_id=message.chat.id,
                photo=image_bytes,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )

            try:
                await waiting_msg.delete()
            except Exception:
                pass
            last_image_time = time.time()

        except Exception as e:
            try:
                await waiting_msg.edit_text(
                    f"{IMAGE_EMOJI_HTML} <b>Failed to generate image:</b> <code>{html_module.escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        finally:
            image_queue.task_done()


def ensure_image_worker(bot):
    global _image_worker_started
    if not _image_worker_started:
        asyncio.create_task(image_worker_loop(bot))
        _image_worker_started = True


# â”€â”€â”€ SMART CONTENT DETECTORS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def should_use_long_mode(query: str) -> bool:
    q_lower = query.lower()
    return any(trigger in q_lower for trigger in LONG_TRIGGERS)


def split_by_sections(html: str) -> list:
    """Split long HTML into chunks at major headings for progressive reveals."""
    # Split at <b><u> headings but keep the delimiter
    parts = re.split(r'(?=<b><u>.*?</u></b>)', html)
    return [p.strip() for p in parts if p.strip()]


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MAIN HANDLER
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
@app.on_message(
    filters.command(
        ["ai", "ask", "chatgpt", "gpt", "solve"],
        prefixes=["+", ".", "/", "-", "", "$", "#", "&"],
    )
)
async def ai_handler(bot, message):
    try:
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

        # â”€â”€ Show help menu if no arguments â”€â”€
        if len(message.command) < 2:
            return await message.reply_text(
                HELP_HTML,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

        subcommand = message.command[1].lower()

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # 1) CODE GENERATION MODE
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if subcommand in CODE_SUBCOMMANDS:
            if message.chat.type != ChatType.PRIVATE:
                bot_username = (await bot.get_me()).username
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("Open DM", url=f"https://t.me/{bot_username}")]
                ])
                return await message.reply_text(
                    f"{CODE_EMOJI_HTML} <b>Coding mode is currently available in DM only.</b><br>"
                    f"<i>Click the button below to start coding in private chat.</i>",
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )

            query = " ".join(message.command[2:]) if len(message.command) > 2 else ""
            if not query:
                return await message.reply_text(
                    f"{CODE_EMOJI_HTML} <b>Usage:</b> <code>/ai code write a Pyrogram telegram bot</code>",
                    parse_mode=ParseMode.HTML
                )

            stream = RichStreamSession(bot, message.chat.id, mode="code")
            await stream.set_thinking("Analyzing architecture & building modulesâ€¦")

            payload = {
                "model": MODEL_CODE,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT_CODE},
                    {"role": "user", "content": query}
                ],
                "stream": True
            }

            full_html = ""
            last_len = 0
            THRESHOLD = 250

            async with aiohttp.ClientSession() as http:
                async with http.post(GROQ_URL, json=payload, headers=GROQ_HEADERS) as resp:
                    if resp.status != 200:
                        err_txt = await resp.text()
                        return await message.reply_text(
                            f"{CODE_EMOJI_HTML} <b>API Error ({resp.status}):</b> <code>{html_module.escape(err_txt[:100])}</code>",
                            parse_mode=ParseMode.HTML
                        )

                    async for line in resp.content:
                        line = line.decode("utf-8").strip()
                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            continue

                        try:
                            chunk = json.loads(data_str)
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            if not content:
                                continue

                            full_html += content

                            if len(full_html) - last_len >= THRESHOLD:
                                draft_html = escape_code_blocks(full_html)
                                await stream.set_thinking("Generating full code modulesâ€¦")
                                await stream.set_body(draft_html)
                                last_len = len(full_html)

                        except (json.JSONDecodeError, KeyError, IndexError):
                            pass

            final_html = escape_code_blocks(full_html.strip())
            if final_html:
                final_html += make_footer_html("code")
                await stream.clear_thinking(delay=0.8)
                await stream.finalize(final_html)
            else:
                await message.reply_text(
                    f"{CODE_EMOJI_HTML} <b>Empty response received.</b> Please try again.",
                    parse_mode=ParseMode.HTML
                )
            return

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # 2) IMAGE GENERATION MODE
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if subcommand in IMAGE_SUBCOMMANDS:
            query = " ".join(message.command[2:]) if len(message.command) > 2 else ""
            if not query:
                return await message.reply_text(
                    f"{IMAGE_EMOJI_HTML} <b>Usage:</b> <code>/ai generate a futuristic city</code>",
                    parse_mode=ParseMode.HTML
                )

            flagged = await check_nsfw(query)
            if flagged:
                return await message.reply_text(
                    f"{IMAGE_EMOJI_HTML} <b>Content Blocked</b><br>"
                    f"<blockquote><i>This prompt was flagged as NSFW and cannot be processed.</i></blockquote>",
                    parse_mode=ParseMode.HTML
                )

            waiting = await message.reply_text(
                f"{IMAGE_EMOJI_HTML} <i>Rendering AI imageâ€¦ Please wait.</i>",
                parse_mode=ParseMode.HTML
            )

            await image_queue.put({
                "message": message,
                "prompt": query,
                "waiting_msg": waiting,
            })

            queue_pos = image_queue.qsize()
            if queue_pos > 1:
                try:
                    await waiting.edit_text(
                        f"{IMAGE_EMOJI_HTML} <i>Queued in position: {queue_pos}</i>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass

            ensure_image_worker(bot)
            return

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # 3) GENERAL / LONG-FORM CHAT MODE
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        query = message.text.split(" ", 1)[1]
        is_long_mode = should_use_long_mode(query)

        system_prompt = SYSTEM_PROMPT_LONG if is_long_mode else SYSTEM_PROMPT_GENERAL
        model = MODEL_GENERAL

        stream = RichStreamSession(bot, message.chat.id, mode="general")
        await stream.set_thinking("Thinkingâ€¦")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            "stream": True
        }

        full_html = ""
        last_len = 0
        THRESHOLD = 120 if not is_long_mode else 350
        section_buffer = ""

        async with aiohttp.ClientSession() as http:
            async with http.post(GROQ_URL, json=payload, headers=GROQ_HEADERS) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    return await message.reply_text(
                        f"{SPARKLE_HTML} <b>API Error ({resp.status}):</b> <code>{html_module.escape(err_txt[:100])}</code>",
                        parse_mode=ParseMode.HTML
                    )

                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if not content:
                            continue

                        full_html += content
                        section_buffer += content

                        # Long-mode: reveal section-by-section with thinking between them
                        if is_long_mode and len(section_buffer) >= THRESHOLD:
                            if '</u></b>' in section_buffer or '<br><br>' in section_buffer:
                                draft = sanitize_html(full_html)
                                await stream.set_thinking("Researching deeperâ€¦")
                                await stream.set_body(draft)
                                section_buffer = ""
                                last_len = len(full_html)
                                continue

                        # Standard streaming update
                        if len(full_html) - last_len >= THRESHOLD:
                            draft = sanitize_html(full_html)
                            if is_long_mode:
                                await stream.set_thinking("Expanding the discussionâ€¦")
                            await stream.set_body(draft)
                            last_len = len(full_html)

                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass

        final_html = sanitize_html(full_html.strip())
        if final_html:
            final_html += make_footer_html("general")
            await stream.clear_thinking(delay=1.0 if is_long_mode else 0.6)
            await stream.finalize(final_html)
        else:
            await message.reply_text(
                f"{SPARKLE_HTML} <b>No response received.</b> Please try again.",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        await message.reply_text(
            f"{SPARKLE_HTML} <b>Error:</b> <code>{html_module.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML
        )
