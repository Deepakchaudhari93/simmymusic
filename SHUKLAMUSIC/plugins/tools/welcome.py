import io
import os
import time
import asyncio
import traceback
from PIL import Image, ImageDraw, ImageFont
from pyrogram import filters
from pyrogram.types import Message
from pyrogram.enums import ChatMemberStatus, ParseMode

from SHUKLAMUSIC import app
from SHUKLAMUSIC.core.mongo import mongodb

# --- DATABASE SETUP FOR WELCOME MODULE ---
welcomedb = mongodb.welcome

async def is_welcome_on(chat_id: int) -> bool:
    chat = await welcomedb.find_one({"chat_id": chat_id})
    return bool(chat)

async def welcome_on(chat_id: int):
    if not await is_welcome_on(chat_id):
        await welcomedb.insert_one({"chat_id": chat_id})

async def welcome_off(chat_id: int):
    if await is_welcome_on(chat_id):
        await welcomedb.delete_one({"chat_id": chat_id})


# --- RATE LIMITER (10 Welcomes Per Minute Per Group) ---
WELCOME_LIMIT = {}

def check_rate_limit(chat_id: int) -> bool:
    now = time.time()
    if chat_id not in WELCOME_LIMIT:
        WELCOME_LIMIT[chat_id] = []
    
    # Prune timestamps older than 60 seconds
    WELCOME_LIMIT[chat_id] = [t for t in WELCOME_LIMIT[chat_id] if now - t < 60]
    
    if len(WELCOME_LIMIT[chat_id]) >= 10:
        return False
        
    WELCOME_LIMIT[chat_id].append(now)
    return True


# --- HELPER FUNCTIONS ---
async def delete_after_delay(message: Message, delay: int = 120):
    """Deletes the message after a specified delay in seconds (120s = 2 minutes)."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass


async def generate_welcome_image(client, user, member_id: int):
    # Determine paths and handle resources
    current_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(current_dir, "wlcmbase.jpg")
    font_path = os.path.join(current_dir, "Poppins-Bold.ttf")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found at {template_path}")

    # Load Base Image (3264 x 1963)
    base_img = Image.open(template_path).convert("RGBA")
    draw = ImageDraw.Draw(base_img)
    
    # Load Fonts (Scaled up for 3264x1963 resolution)
    try:
        font_name = ImageFont.truetype(font_path, 110)
        font_regular = ImageFont.truetype(font_path, 90)
        font_title = ImageFont.truetype(font_path, 130)
    except IOError:
        font_name = font_regular = font_title = ImageFont.load_default()

    # Text Prep
    name_text = user.first_name[:20] + ("..." if len(user.first_name) > 20 else "")
    username_text = f"@{user.username}" if user.username else "No Username"
    user_id_text = f"{user.id}"
    member_id_text = f"STKR-{member_id}"

    # 1. Write "tidalXmusic" at the top left 
    draw.text((280, 200), "tidalXmusic", fill=(0, 191, 255), font=font_title)
    
    # 2. Draw red dashed line under tidalXmusic
    x_start, y_line = 280, 350
    for x in range(x_start, 1080, 50):  # Scaled steps for 4K resolution
        draw.line([(x, y_line), (x+30, y_line)], fill=(255, 51, 51), width=10)

    # 3. Add Info Texts (Aligned with the icons on the right)
    text_x = 1550
    draw.text((text_x, 480), name_text, fill=(255, 255, 255), font=font_name)
    draw.text((text_x, 750), username_text, fill=(255, 255, 255), font=font_regular)
    draw.text((text_x, 1040), user_id_text, fill=(255, 255, 255), font=font_regular)
    draw.text((text_x, 1400), member_id_text, fill=(255, 255, 255), font=font_regular)

    # 4. Fetch Profile Picture in Memory
    pfp_size = 950 # Massive circle to fit the 3264px template
    if user.photo:
        try:
            pfp_bytes = await client.download_media(user.photo.big_file_id, in_memory=True)
            pfp = Image.open(pfp_bytes).convert("RGBA")
        except Exception:
            pfp = Image.new("RGBA", (pfp_size, pfp_size), (90, 90, 90, 255))
    else:
        pfp = Image.new("RGBA", (pfp_size, pfp_size), (90, 90, 90, 255))

    # 5. Process PFP into Circle
    pfp = pfp.resize((pfp_size, pfp_size))
    mask = Image.new("L", (pfp_size, pfp_size), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.ellipse((0, 0, pfp_size, pfp_size), fill=255)
    
    # Paste PFP inside coordinates (285, 480)
    base_img.paste(pfp, (285, 480), mask)
    
    # 6. Draw Orange Outline exactly around the PFP
    draw.ellipse([(275, 470), (285 + pfp_size + 10, 480 + pfp_size + 10)], outline=(255, 140, 50), width=18)

    # 7. Convert Back to Bytes
    final_bytes = io.BytesIO()
    base_img.save(final_bytes, format="PNG")
    final_bytes.seek(0)
    return final_bytes


# --- COMMAND: ADMIN TOGGLE ---
@app.on_message(filters.command("welcome") & filters.group)
async def welcome_toggle(client, message: Message):
    # Verify Admin Status
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        return await message.reply_text("❌ Only group admins can use this command.")
        
    if len(message.command) < 2 or message.command[1].lower() not in ["on", "off"]:
        return await message.reply_text("ℹ️ **Usage:** `/welcome on` or `/welcome off`")
        
    state = message.command[1].lower()
    if state == "on":
        await welcome_on(message.chat.id)
        await message.reply_text("✅ Welcome messages have been **enabled** for this group.")
    else:
        await welcome_off(message.chat.id)
        await message.reply_text("❌ Welcome messages have been **disabled** for this group.")


# --- EVENT LOGIC: NEW USER JOIN ---
@app.on_message(filters.new_chat_members & filters.group)
async def welcome_new_member(client, message: Message):
    chat_id = message.chat.id
    
    # Check if enabled
    if not await is_welcome_on(chat_id):
        return
        
    # Check limit (10 per min)
    if not check_rate_limit(chat_id):
        return 

    # Determine member count for ID suffix
    try:
        members_count = await client.get_chat_members_count(chat_id)
    except:
        members_count = "Unknown"
        
    for user in message.new_chat_members:
        if user.is_bot:
            continue
            
        try:
            welcome_img = await generate_welcome_image(client, user, members_count)
            
            # Caption matching your format perfectly
            caption = (
                f"<b><u>ɴᴇᴡ ᴍᴇᴍʙᴇʀ Jᴏɪɴᴇᴅ</u>!!</b>\n"
                f"<blockquote expandable>┌──── ˹ ɪɴғᴏʀᴍᴀᴛɪᴏɴ ˼───•\n┆◍<b><u>Nᴀᴍᴇ</u></b>: {user.first_name},\n"
                f"┆◍<b><u>Usᴇʀɴᴀᴍᴇ</u></b>: @{user.username if user.username else 'None'}\n"
                f"┆◍<b><u>Usᴇʀ-Iᴅ</u></b>: {user.id}\n"
                f"┆◍<b><u>Mᴇᴍ-Iᴅ</u></b>: STKR-{members_count}\n└─────────────────•</blockquote>\n\n"
                f"<b>Wᴇʟᴄᴏᴍɪɴɢ ʏᴏᴜ ɪɴ {message.chat.title}</b>"
            )
            
            sent_msg = await message.reply_photo(
                photo=welcome_img,
                caption=caption,
                has_spoiler=True
            )
            
            # Delete message in 2 minutes
            asyncio.create_task(delete_after_delay(sent_msg, 120))
            
        except Exception:
            traceback.print_exc()
            pass
