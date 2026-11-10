import os
import sys
import asyncio
import traceback
from inspect import getfullargspec
from io import StringIO
from time import time

from pyrogram import filters, Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from SHUKLAMUSIC import app
from config import OWNER_ID

# ==========================================
# HELPER FUNCTIONS
# ==========================================
async def aexec(code, client, message):
    """Asynchronously executes Python code inside an async function wrapper."""
    exec(
        "async def __aexec(client, message): "
        + "".join(f"\n {line}" for line in code.split("\n"))
    )
    return await locals()["__aexec"](client, message)


async def edit_or_reply(msg: Message, **kwargs):
    """Edits the message if sent by the bot, otherwise replies."""
    func = msg.edit_text if msg.from_user.is_self else msg.reply
    spec = getfullargspec(func.__wrapped__).args
    await func(**{k: v for k, v in kwargs.items() if k in spec})


# ==========================================
# PYTHON EVALUATOR (/eval) - OWNER ONLY
# ==========================================
@app.on_message(
    filters.command("eval", prefixes=["/", "!", "."])
    & filters.user(OWNER_ID)
    & ~filters.forwarded
    & ~filters.via_bot
)
@app.on_edited_message(
    filters.command("eval", prefixes=["/", "!", "."])
    & filters.user(OWNER_ID)
    & ~filters.forwarded
    & ~filters.via_bot
)
async def executor(client: Client, message: Message):
    if len(message.command) < 2:
        return await edit_or_reply(message, text="<b>What do you want to execute?</b>")
    
    cmd = message.text.split(maxsplit=1)[1]
    t1 = time()
    
    # Redirect standard output and error streams
    old_stderr = sys.stderr
    old_stdout = sys.stdout
    redirected_output = sys.stdout = StringIO()
    redirected_error = sys.stderr = StringIO()
    stdout, stderr, exc = None, None, None
    
    try:
        await aexec(cmd, client, message)
    except Exception:
        exc = traceback.format_exc()
    finally:
        # Restore system output streams immediately
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    stdout = redirected_output.getvalue()
    stderr = redirected_error.getvalue()
    
    evaluation = ""
    if exc:
        evaluation += exc
    elif stderr:
        evaluation += stderr
    elif stdout:
        evaluation += stdout
    else:
        evaluation += "Success"

    t2 = time()
    runtime_str = f"{round(t2 - t1, 3)} Seconds"
    
    # Create interactive buttons
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="⏳ Runtime", callback_data=f"runtime {runtime_str}"),
                InlineKeyboardButton(text="🗑 Close", callback_data=f"forceclose|{message.from_user.id}"),
            ]
        ]
    )

    final_output = f"<b>⥤ RESULT :</b>\n<pre language='python'>{evaluation}</pre>"
    
    # Handle long outputs by sending as a file
    if len(final_output) > 4096:
        filename = "eval_output.txt"
        with open(filename, "w+", encoding="utf-8") as out_file:
            out_file.write(evaluation)
            
        await message.reply_document(
            document=filename,
            caption=f"<b>⥤ EVAL :</b>\n<code>{cmd[:100]}...</code>\n\n<b>⥤ RESULT :</b> Attached Document",
            quote=False,
            reply_markup=keyboard,
        )
        if os.path.exists(filename):
            os.remove(filename)
    else:
        await edit_or_reply(message, text=final_output, reply_markup=keyboard)


# ==========================================
# ASYNC TERMINAL/SHELL RUNNER (/sh) - OWNER ONLY
# ==========================================
@app.on_message(
    filters.command("sh", prefixes=["/", "!", "."])
    & filters.user(OWNER_ID)
    & ~filters.forwarded
    & ~filters.via_bot
)
@app.on_edited_message(
    filters.command("sh", prefixes=["/", "!", "."])
    & filters.user(OWNER_ID)
    & ~filters.forwarded
    & ~filters.via_bot
)
async def shellrunner(client: Client, message: Message):
    if len(message.command) < 2:
        return await edit_or_reply(message, text="<b>Example :</b>\n`/sh git pull`")
    
    cmd = message.text.split(maxsplit=1)[1]
    status_msg = await edit_or_reply(message, text="<b>Running command...</b>")
    
    try:
        # NON-BLOCKING ASYNC SUBPROCESS
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except Exception as err:
        return await edit_or_reply(status_msg, text=f"<b>ERROR :</b>\n<pre>{err}</pre>")
    
    output = ""
    if stdout:
        output += stdout.decode("utf-8", errors="ignore")
    if stderr:
        output += stderr.decode("utf-8", errors="ignore")
        
    output = output.strip()
    
    if not output:
        return await edit_or_reply(status_msg, text="<b>OUTPUT :</b>\n<code>None (Success)</code>")
        
    if len(output) > 4096:
        filename = "shell_output.txt"
        with open(filename, "w+", encoding="utf-8") as file:
            file.write(output)
        await app.send_document(
            message.chat.id,
            filename,
            reply_to_message_id=message.id,
            caption="<code>Terminal Output</code>",
        )
        if os.path.exists(filename):
            os.remove(filename)
        await status_msg.delete()
    else:
        await edit_or_reply(status_msg, text=f"<b>OUTPUT :</b>\n<pre>{output}</pre>")


# ==========================================
# CALLBACK QUERY HANDLERS
# ==========================================
@app.on_callback_query(filters.regex(r"^runtime "))
async def runtime_func_cq(_, cq):
    runtime = cq.data.split(None, 1)[1]
    await cq.answer(f"Runtime: {runtime}", show_alert=True)


@app.on_callback_query(filters.regex(r"^forceclose\|"))
async def forceclose_command(_, cq):
    user_id = int(cq.data.split("|")[1])
    if cq.from_user.id != user_id:
        return await cq.answer("» You are not allowed to close this!", show_alert=True)
    
    try:
        await cq.message.delete()
        await cq.answer()
    except Exception:
        pass
