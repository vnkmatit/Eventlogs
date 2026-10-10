import os
import threading
import pandas as pd
import discord
from discord import app_commands
from discord.ext import commands
import streamlit as st

# ==========================================
# CONFIGURATION & CHANNEL IDs
# ==========================================
EVENT_CHECK_CHANNEL_ID = 1538156892688355418  # Channel where event check messages are located
LOGGING_CHANNEL_ID = 1538166153799532574      # Channel where logs will be posted
CSV_FILE = "quotas.csv"

# --- STREAMLIT DASHBOARD & QUOTA SHEET ---
st.title("📊 Event Quota Sheet & Bot Dashboard")
st.success("🤖 Bot process active and listening for events.")

# Display Live Quota Sheet Table
if os.path.exists(CSV_FILE):
    df = pd.read_csv(CSV_FILE)
    st.subheader("Member Quota Points Leaderboard")
    st.dataframe(df, use_container_width=True)
else:
    st.info("No points logged yet. Use /log in Discord to start tracking quotas!")

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

BOT_TOKEN = st.secrets.get("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")

def update_quotas(attendees_data, earned_points):
    """Updates or creates the local quotas.csv file with earned points."""
    if os.path.exists(CSV_FILE):
        df = pd.read_csv(CSV_FILE)
    else:
        df = pd.DataFrame(columns=["User ID", "Username", "Points"])

    for user_id, username in attendees_data:
        user_id_str = str(user_id)
        if user_id_str in df["User ID"].astype(str).values:
            df.loc[df["User ID"].astype(str) == user_id_str, "Points"] += earned_points
            df.loc[df["User ID"].astype(str) == user_id_str, "Username"] = username
        else:
            new_row = pd.DataFrame({
                "User ID": [user_id_str], 
                "Username": [username], 
                "Points": [earned_points]
            })
            df = pd.concat([df, new_row], ignore_index=True)
    
    df.to_csv(CSV_FILE, index=False)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print(f"🤖 Bot is ONLINE as {bot.user}")

@bot.tree.command(name="log", description="Log an event, update quota sheet, and post proof")
@app_commands.describe(
    message_id="The ID of the announcement/event check message",
    points="Number of points awarded to attendees (e.g. 10)",
    proof="Upload proof image file"
)
async def log_event(
    interaction: discord.Interaction,
    message_id: str,
    points: str,
    proof: discord.Attachment
):
    # Defer ephemeral response so the host gets private confirmation
    await interaction.response.defer(ephemeral=True)

    # Validate points input
    try:
        earned_points = int(points.strip())
    except ValueError:
        await interaction.followup.send("❌ Error: Points must be a valid number (e.g., `10`).", ephemeral=True)
        return

    # Fetch the specific event check channel
    check_channel = bot.get_channel(EVENT_CHECK_CHANNEL_ID)
    if not check_channel:
        try:
            check_channel = await bot.fetch_channel(EVENT_CHECK_CHANNEL_ID)
        except Exception:
            await interaction.followup.send(f"❌ Could not access event check channel ID `{EVENT_CHECK_CHANNEL_ID}`.", ephemeral=True)
            return

    # Fetch the event message
    try:
        msg = await check_channel.fetch_message(int(message_id.strip()))
    except Exception:
        await interaction.followup.send(f"❌ Could not find message ID `{message_id}` in check channel.", ephemeral=True)
        return

    # Gather attendees (exclude bots and the host)
    attendees_set = set()
    attendees_data = []
    
    for reaction in msg.reactions:
        async for user in reaction.users():
            if not user.bot and user.id != interaction.user.id:
                attendees_set.add(user.mention)
                attendees_data.append((user.id, str(user)))

    if not attendees_set:
        await interaction.followup.send("⚠️ No valid attendees found on that message.", ephemeral=True)
        return

    attendees_str = "\n".join(sorted(attendees_set))

    # Update quota sheet data
    update_quotas(attendees_data, earned_points)

    # Fetch the designated logging post channel
    log_channel = bot.get_channel(LOGGING_CHANNEL_ID)
    if not log_channel:
        try:
            log_channel = await bot.fetch_channel(LOGGING_CHANNEL_ID)
        except Exception:
            await interaction.followup.send(f"❌ Could not access logging channel ID `{LOGGING_CHANNEL_ID}`.", ephemeral=True)
            return

    # Format log output content
    log_content = (
        f"**Host:** {interaction.user.mention}\n\n"
        f"**Attendees:**\n{attendees_str}\n\n"
        f"**Points Awarded:** {earned_points}\n\n"
        f"**Proof:** {proof.url}"
    )

    # Post public log to the logging channel
    await log_channel.send(
        log_content,
        allowed_mentions=discord.AllowedMentions.none()
    )

    # Confirm success privately to the host
    await interaction.followup.send(
        f"✅ Successfully logged event! Added `{earned_points}` points to {len(attendees_set)} attendees and posted report to <#{LOGGING_CHANNEL_ID>}.",
        ephemeral=True
    )

# --- RUN BOT IN BACKGROUND THREAD (Ensures Streamlit UI stays active) ---
def run_discord_bot():
    if BOT_TOKEN:
        bot.run(BOT_TOKEN)

if "bot_thread_started" not in st.session_state:
    st.session_state.bot_thread_started = True
    if BOT_TOKEN:
        threading.Thread(target=run_discord_bot, daemon=True).start()
