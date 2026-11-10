from datetime import datetime
from pyrogram import filters
from pyrogram.types import Message
from config import *
from SHUKLAMUSIC import app
from SHUKLAMUSIC.core.call import SHUKLA
from SHUKLAMUSIC.utils import bot_sys_stats
from SHUKLAMUSIC.utils.decorators.language import language
from SHUKLAMUSIC.utils.inline import supp_markup
from config import BANNED_USERS

# Aapke database.py ke mutabiq mongodb ko import kar rahe hain
from SHUKLAMUSIC.core.mongo import mongodb as db

# Ab ye 'db' wahi 'mongodb' hai jo aapke core mein hai
ping_db = db.ping_config 

@app.on_message(filters.command("setping") & filters.user(7659846392))
async def set_ping_msg(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Usage:** `/setping [Message]`\n\n"
            "**Aap ye placeholders use kar sakte hain:**\n"
            "• `{ping}` - Bot ki speed\n"
            "• `{pytgping}` - Call server speed\n"
            "• `{uptime}` - Bot kabse online hai\n"
            "• `{ram}` - RAM usage\n"
            "• `{cpu}` - CPU usage\n"
            "• `{disk}` - Disk space\n"
            "• `{mention}` - Bot ka naam"
        )
    try:
        # Formatting (bold/italic) ko preserve karne ke liye
        new_msg = message.text.html.split(None, 1)[1]
    except IndexError:
        return await message.reply_text("❌ Kuch text likhein command ke baad.")

    await ping_db.update_one(
        {"_id": "ping_msg"}, 
        {"$set": {"message": new_msg}}, 
        upsert=True
    )
    await message.reply_text("✅ **Ping message placeholders ke saath set ho gaya hai!**")

@app.on_message(filters.command("ping", prefixes=["/"]) & ~BANNED_USERS)
@language
async def ping_com(client, message: Message, _):
    start = datetime.now()
    response = await message.reply_video(
        video="https://files.catbox.moe/so1jux.mp4",
        caption=_["ping_1"].format(app.mention),
    )
    
    # Stats calculate karna
    pytgping = await SHUKLA.ping()
    UP, CPU, RAM, DISK = await bot_sys_stats()
    resp = (datetime.now() - start).microseconds / 1000
    
    # Database se custom message uthana
    data = await ping_db.find_one({"_id": "ping_msg"})
    
    if data and "message" in data:
        custom_text = data["message"]
        # Saare placeholders ko asli data se replace karna
        final_caption = custom_text.replace("{ping}", str(resp)) \
                                   .replace("{pytgping}", str(pytgping)) \
                                   .replace("{uptime}", str(UP)) \
                                   .replace("{ram}", str(RAM)) \
                                   .replace("{cpu}", str(CPU)) \
                                   .replace("{disk}", str(DISK)) \
                                   .replace("{mention}", app.mention)
    else:
        # Agar koi message set nahi hai toh default chalega
        final_caption = _["ping_2"].format(resp, app.mention, UP, RAM, CPU, DISK, pytgping)

    await response.edit_text(final_caption, reply_markup=supp_markup(_))
    
