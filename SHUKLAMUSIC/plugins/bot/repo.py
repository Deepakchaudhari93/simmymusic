from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from SHUKLAMUSIC import app
from config import BOT_USERNAME
from SHUKLAMUSIC.utils.errors import capture_err
import httpx 
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

start_txt = """
Jᴀᴀ ʜᴀɪ ᴘᴀᴅʜᴀɪ ᴋʀ Jᴀᴀᴋᴇ ᴀᴀʏᴀ ʙᴀᴅᴀ ʀᴇᴘᴏ ᴅᴇᴋʜɴᴇ ᴡᴀʟᴀ !
<pre>||➥ᴜᴘᴛɪᴍᴇ: 𝟷ʜ:𝟹𝟺ᴍ:𝟻𝟺s
➥sᴇʀᴠᴇʀ sᴛᴏʀᴀɢᴇ: 𝟸𝟽.𝟺%
➥ᴄᴘᴜ ʟᴏᴀᴅ: 𝟷𝟷.𝟸%
➥ʀᴀᴍ ᴄᴏɴsᴜᴍᴘᴛɪᴏɴ: 𝟷𝟽.𝟻%||</pre>
•──────────────────•
ᴘᴏᴡєʀєᴅ ʙʏ»|| [- 𝛅ⴕ᧘ᥧ𝚱𝛜Ʀ ⌯](https://t.me/hehe_stalker)||
•──────────────────•
"""

@app.on_message(filters.command("repo"))
async def start(_, msg):
    buttons = [
        [
            InlineKeyboardButton(text=" ˹ηєᴛᴡᴏʀᴋ˼ ", url="https://t.me/thedrxnet",),
            InlineKeyboardButton(text=" ˹ϻʏ ʜᴏϻє˼ ", url="https://t.me/drx_supportchat",),
        ],
        
     [
            InlineKeyboardButton("˹ᴘʀιᴠᴧᴄʏ˼", url=f"https://telegra.ph/Privacy-Policy-08-03-101"),
            InlineKeyboardButton("˹ᴛιᴅᴧʟ ᴛᴜηєs˼♪", url=f"http://t.me/TidalXMusicBot/tidaltunes"),
        ],
        
          [
            InlineKeyboardButton("˹ ϻʏ ϻᴧsᴛєʀ ˼ 👑", url="https://t.me/hehe_stalker"),
          ]]
    
    reply_markup = InlineKeyboardMarkup(buttons)
    
    await msg.reply_photo(
        photo="https://files.catbox.moe/wifnjm.jpg",
        caption=start_txt,
        reply_markup=reply_markup
    )
