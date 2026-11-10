import asyncio
from pyrogram import filters
from pyrogram.types import Message

# Importing your bot instance
from SHUKLAMUSIC import app

OWNER_ID = 7659846392

# prefixes=["", "/"] allows both "stpay 10" and "/stpay 10"
@app.on_message(filters.command("stpay", prefixes=["", "/"]))
async def stpay_command(client, message: Message):
    # Ignore command if it's not from the owner (does not block other bot operations)
    if message.from_user.id != OWNER_ID:
        return

    # Extract amount from the command
    if len(message.command) < 2:
        return await message.reply_text(
            "**⚠️ Invalid Usage!**\n"
            "> **Example:** `stpay 10` (Use standalone or as a reply)"
        )
        
    amount = message.command[1].strip()
    
    if not amount.isdigit():
        return await message.reply_text("**⚠️ Amount must be a valid number!**")

    # Direct UPI deep link
    upi_link = f"upi://pay?pa=iybhathstalker@fam&pn=Stalker&am={amount}&cu=INR&tn=Payment"

    # Determine message text based on whether owner tagged/replied to someone
    if message.reply_to_message and message.reply_to_message.from_user:
        replied_user = message.reply_to_message.from_user
        
        if replied_user.id != OWNER_ID:
            text = (
                f"**<emoji id=5201873447554145566>⭐</emoji> {replied_user.mention} "
                f"Pay ₹{amount} to stalker using the below link:**\n\n"
                f"👉 [Click Here to Pay ₹{amount}]({upi_link})"
            )
        else:
            text = (
                f"**<emoji id=5422814644093868925>⭐</emoji> Here is your payment link for amount ₹{amount}:**\n\n"
                f"👉 [Click Here to Pay ₹{amount}]({upi_link})"
            )
    else:
        text = (
            f"**<emoji id=5422814644093868925>⭐</emoji> Here is your payment link for amount ₹{amount}:**\n\n"
            f"👉 [Click Here to Pay ₹{amount}]({upi_link})"
        )

    # Send the payment message with hyperlink text
    sent_message = await message.reply_text(text, disable_web_page_preview=True)

    # Wait for 10 minutes (600 seconds) without blocking other commands
    await asyncio.sleep(600)

    # Silently delete the message after 10 minutes
    try:
        await sent_message.delete()
    except Exception:
        pass
