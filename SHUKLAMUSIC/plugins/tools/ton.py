import aiohttp
import io
import os
import traceback
from datetime import datetime, timedelta
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message
from PIL import Image, ImageDraw, ImageFont
from SHUKLAMUSIC import app
from pyrogram.types import MessageEntity

# Automatically detect the folder where ton.py is located
current_dir = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(current_dir, "base_template.png")
FONT_PATH = os.path.join(current_dir, "Poppins-Bold.ttf")

@app.on_message(filters.command(["ton", "gram"]))
async def ton_price_command(client, message: Message):
    msg = await message.reply_text("<emoji id=6296218646284863141>⭐</emoji> Fetching latest Gram (ex. TON) prices using drxAPI")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://tonapi.io/v2/rates?tokens=ton&currencies=usd") as resp_usd:
                usd_data = await resp_usd.json()
            async with session.get("https://tonapi.io/v2/rates?tokens=ton&currencies=inr") as resp_inr:
                inr_data = await resp_inr.json()

        # Helper function to clean strings like '+3.05%' into floats
        def clean_float(value):
            if isinstance(value, str):
                return float(value.replace('%', '').replace('+', '').replace('−', '-') .strip())
            return float(value)

        # Parse and convert
        ton_usd = usd_data["rates"]["TON"]
        usd_price = round(float(ton_usd["prices"]["USD"]), 4) 
        usd_24h = round(clean_float(ton_usd["diff_24h"]["USD"]), 2)
        usd_7d = round(clean_float(ton_usd["diff_7d"]["USD"]), 2)
        usd_30d = ton_usd["diff_30d"]["USD"]

        ton_inr = inr_data["rates"]["TON"]
        inr_price = round(float(ton_inr["prices"]["INR"]), 2)
        inr_24h = ton_inr["diff_24h"]["INR"]
        inr_7d = ton_inr["diff_7d"]["INR"]
        inr_30d = ton_inr["diff_30d"]["INR"]

        # --- IMAGE GENERATION ---
        if not os.path.exists(TEMPLATE_PATH):
            await msg.edit_text(f"❌ **Template not found!**\nPath checked: `{TEMPLATE_PATH}`")
            return

        try:
            img = Image.open(TEMPLATE_PATH)
        except Exception as e:
            await msg.edit_text(f"❌ **Error opening image:** {str(e)}")
            return

        draw = ImageDraw.Draw(img)

        if not os.path.exists(FONT_PATH):
            await msg.edit_text(f"❌ **Font not found!**\nPath checked: `{FONT_PATH}`")
            return

        try:
            # Adjust sizes to fit your image resolution
            font_price = ImageFont.truetype(FONT_PATH, 86)
            font_change = ImageFont.truetype(FONT_PATH, 60)
            font_dates = ImageFont.truetype(FONT_PATH, 28)
        except Exception as e:
            await msg.edit_text(f"❌ **Error loading font:** {str(e)}")
            return

        # Formatting text for overlay
        price_text = f"{usd_price:.4f}"
        daily_text = f"{'+' if usd_24h > 0 else ''}{usd_24h}%"
        weekly_text = f"{'+' if usd_7d > 0 else ''}{usd_7d}%"

        # Colors - Updated as requested
        SKY_BLUE = (135, 206, 235)   # Sky blue for $ sign
        WHITE = (255, 255, 255)      # White for USD amount
        LIGHT_PINK = (255, 182, 193) # Light pink #FFB6C1 for daily and weekly changes

        # Draw $ sign in sky blue (position from image 5 - sky blue area)
        draw.text((145, 230), "$", font=font_price, fill=SKY_BLUE)

        # Draw USD amount in white (right after $ sign)
        # Offset x by ~50 pixels to position after the $
        draw.text((205, 230), price_text, font=font_price, fill=WHITE)

        # Draw Daily Change in light pink - positioned in first pill (red area in image 5)
        draw.text((585, 475), daily_text, font=font_change, fill=LIGHT_PINK, anchor="mm")

        # Draw Weekly Change in light pink - positioned in second pill (white area in image 5)
        draw.text((945, 475), weekly_text, font=font_change, fill=LIGHT_PINK, anchor="mm")

        # Draw Bottom Dates (last 8 days) - adjusted positions
        today = datetime.now()
        start_x, spacing_x, y_coord = 145, 177, 875

        for i in range(8):
            date_calc = today - timedelta(days=(7-i))
            date_str = date_calc.strftime("%b. %d")
            x_coord = start_x + (i * spacing_x)
            draw.text((x_coord, y_coord), date_str, font=font_dates, fill=(160, 160, 160), anchor="mm")

        # Save to memory
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        img_byte_arr.name = "ton_stats.png"
        
        text = (
            f'<b><emoji id=6030549140633555631>⭐</emoji>  <u>TON PRICES</u>:</b>\n'
            f'1 GRAM = ${usd_price} <emoji id=6028584717081645421>⭐</emoji>\n'
            f'1 GRAM = ₹{inr_price} <emoji id=6296218646284863141>⭐</emoji>\n'
            f'<blockquote expandable><b><emoji id=6028584717081645421>⭐</emoji> <u>USD Changes</u>:</b>\n24h: {usd_24h}%\n7d: {usd_7d}%\n30d: {usd_30d}%\n\n'
            f'<b><emoji id=6296218646284863141>⭐</emoji> <u>INR Changes</u>:</b>\n24h: {inr_24h}\n7d: {inr_7d}\n30d: {inr_30d}</blockquote>\n'
            f'<blockquote><emoji id=6296218646284863141>⭐</emoji> ʙʏ : @hehe_stalker</blockquote>'
        )

        await message.reply_photo(photo=img_byte_arr, has_spoiler=True, caption=text, parse_mode=ParseMode.HTML)
        await msg.delete()

    except Exception:
        error_traceback = traceback.format_exc()
        await msg.edit_text(f" Error", parse_mode=ParseMode.HTML)
