import random
from pyrogram import Client, filters
from pyrogram.types import ChatMemberUpdated

# Your owner and sudo IDs
OWNER_IDS = [7659846392]   # replace with your owner id(s)
SUDO_USERS = [7659846392]  # replace with sudo user ids

MASTER_USERS = OWNER_IDS + SUDO_USERS

# Warrior style welcome messages
WARRIOR_WELCOMES = [
    "⚔️ The battlefield trembles... My master has arrived!",
    "🔥 Warriors bow down! The ruler of this realm has entered!",
    "🛡️ The protector of the clan steps into the warzone!",
    "⚡ Power surges in the air... My commander has joined!",
    "👑 The supreme leader of warriors has descended!",
    "🐉 The dragon awakens... my master is here!",
    "⚔️ Swords rise high, for the true warrior has entered the field!",
    "🔥 Blood, honor, and glory… My master has come to lead!"
]


@Client.on_chat_member_updated()
async def sudo_welcome(client: Client, update: ChatMemberUpdated):
    try:
        user = update.new_chat_member.user
        chat = update.chat

        if (
            user.id in MASTER_USERS
            and update.new_chat_member.status == "member"
        ):
            welcome_text = random.choice(WARRIOR_WELCOMES)
            await client.send_message(
                chat.id,
                f"{welcome_text}\n⚔️ )"
            )

    except Exception as e:
        print(f"Sudo Welcome Error: {e}")
