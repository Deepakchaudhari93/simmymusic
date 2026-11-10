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

# Automatically detect the folder where usdt.py is located
current_dir = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(current_dir, "usdtbase.jpg")
FONT_PATH = os.path.join(current_dir, "Poppins-Bold.ttf")

@app.on_message(filters.command("usdt"))
async def usdt_price_command(client, message: Message):
    msg = await message.reply_text("<emoji id=6296218646284863141>⭐</emoji> Fetching latest USDT prices using drxAPI")

    try:
        # Fetching data from CoinGecko
        async with aiohttp.ClientSession() as session:
            api_url = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=inr&include_24hr_change=true"
            async with session.get(api_url) as resp:
                data = await resp.json()

        # Parse and convert
        inr_price = round(float(data["tether"]["inr"]), 2)
        inr_24h = round(float(data["tether"]["inr_24h_change"]), 2)

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
            font_price = ImageFont.truetype(FONT_PATH, 92)
            font_change = ImageFont.truetype(FONT_PATH, 88)
            font_dates = ImageFont.truetype(FONT_PATH, 28)
        except Exception as e:
            await msg.edit_text(f"❌ **Error loading font:** {str(e)}")
            return

        # Formatting text for overlay
        price_text = f"{inr_price:.2f}"
        daily_text = f"{'+' if inr_24h > 0 else ''}{inr_24h}%"

        # Colors 
        SKY_BLUE = (135, 206, 235)   
        WHITE = (255, 255, 255)      
        LIGHT_PINK = (255, 182, 193) 

        # Draw ₹ sign in sky blue (same position as $)
        draw.text((145, 230), "₹", font=font_price, fill=SKY_BLUE)

        # Draw INR amount in white
        draw.text((205, 230), price_text, font=font_price, fill=WHITE)

        # Draw Daily Change in light pink - dynamically centered horizontally in the image for the middle pill
        center_x = img.width / 2
        draw.text((center_x, 510), daily_text, font=font_change, fill=LIGHT_PINK, anchor="mm")

        # Draw Bottom Dates (last 8 days) - exact same configuration as ton.py
        today = datetime.now()
        start_x, spacing_x, y_coord = 145, 177, 875

        for i in range(8):
            date_calc = today - timedelta(days=(7-i))
            date_str = date_calc.strftime("%b. %d")
            x_coord = start_x + (i * spacing_x)
            draw.text((x_coord, y_coord), date_str, font=font_dates, fill=(160, 160, 160), anchor="mm")

        # Save to memory
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG') # Saving as JPEG since base is .jpg
        img_byte_arr.seek(0)
        img_byte_arr.name = "usdt_stats.jpg"
        
        # Caption formatting
        text = (
            f'<b><u>Tether USDT PRICES</u> <emoji id=6028584717081645421>⭐</emoji></b>\n'
            f'<blockquote><emoji id=6028584717081645421>⭐</emoji> 1 USDT = ₹{inr_price} <emoji id=6296218646284863141>⭐</emoji>\n'
            f'<emoji id=6296218646284863141>⭐</emoji> Daily change - {daily_text}</blockquote>\n'
            f'<blockquote>ʙʏ : @thedrxnet <emoji id=6296218646284863141>⭐</emoji></blockquote>'
        )

        await message.reply_photo(photo=img_byte_arr, caption=text, parse_mode=ParseMode.HTML)
        await msg.delete()

    except Exception:
        error_traceback = traceback.format_exc()
        await msg.edit_text(f"❌ **Critical Error:**\n\n<pre>{error_traceback}</pre>", parse_mode=ParseMode.HTML)
