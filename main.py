import os
import discord
from discord import app_commands
from discord.ext import commands
import streamlit as st

# --- STREAMLIT DASHBOARD ---
st.title("🤖 Event Logger Bot")
st.success("Bot process active.")

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

BOT_TOKEN = st.secrets.get("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print(f"🤖 Bot is ONLINE as {bot.user}")

@bot.tree.command(name="log", description="Log an event based on message reactions")
@app_commands.describe(
    message_id="The ID of the announcement/event message with reactions",
    points="Points awarded (e.g. 10 or 15)",
    proof="Upload proof image file",
    channel="The channel containing the message (optional)"
)
async def log_event(
    interaction: discord.Interaction,
    message_id: str,
    points: str,
    proof: discord.Attachment,
    channel: discord.TextChannel = None
):
    await interaction.response.defer()
    target_channel = channel or interaction.channel

    try:
        msg = await target_channel.fetch_message(int(message_id.strip()))
    except Exception:
        await interaction.followup.send(
            f"❌ Could not find message ID `{message_id}` in {target_channel.mention}.",
            ephemeral=True
        )
        return

    attendees = set()
    for reaction in msg.reactions:
        async for user in reaction.users():
            # Exclude bots AND exclude the host who executed the command
            if not user.bot and user.id != interaction.user.id:
                attendees.add(user.mention)

    attendees_str = "\n".join(sorted(attendees)) if attendees else "No attendees found."

    log_content = (
        f"**Host:** {interaction.user.mention}\n\n"
        f"**Attendees:**\n{attendees_str}\n\n"
        f"**Points Awarded:** {points}\n\n"
        f"**Proof:** {proof.url}"
    )

    await interaction.followup.send(log_content)

if __name__ == "__main__":
    if BOT_TOKEN:
        bot.run(BOT_TOKEN)
    else:
        st.error("Missing DISCORD_BOT_TOKEN in Streamlit Secrets!")
