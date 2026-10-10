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
EVENT_CHECK_CHANNEL_ID = 1538156892688355418  # Locked channel for event checks
LOGGING_CHANNEL_ID = 1538166153799532574      # Channel where logs will be posted
CSV_FILE = "quotas.csv"

# --- STREAMLIT DASHBOARD & QUOTA SHEET ---
st.title("📊 Event Quota Sheet & Bot Dashboard")
st.success("🤖 Bot process active and listening for events.")

# Display Live Quota Sheet Table (Accessible via web link)
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
    await interaction.response.defer(ephemeral=True)

    try:
        earned_points = int(points.strip())
    except ValueError:
        await interaction.followup.send("❌ Error: Points must be a valid number (e.g., `10`).", ephemeral=True)
        return

    check_channel = bot.get_channel(EVENT_CHECK_CHANNEL_ID)
    if not check_channel:
        try:
            check_channel = await bot.fetch_channel(EVENT_CHECK_CHANNEL_ID)
        except Exception:
            await interaction.followup.send(
                f"❌ Could not access event check channel <#{EVENT_CHECK_CHANNEL_ID}>. Check bot permissions!",
                ephemeral=True
            )
            return

    try:
        msg = await check_channel.fetch_message(int(message_id.strip()))
    except Exception:
        await interaction.followup.send(
            f"❌ Could not find message ID `{message_id}` in {check_channel.mention}.",
            ephemeral=True
        )
        return

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

    update_quotas(attendees_data, earned_points)

    log_channel = bot.get_channel(LOGGING_CHANNEL_ID)
    if not log_channel:
        try:
            log_channel = await bot.fetch_channel(LOGGING_CHANNEL_ID)
        except Exception:
            await interaction.followup.send(f"❌ Could not access logging channel ID `{LOGGING_CHANNEL_ID}`.", ephemeral=True)
            return

    log_content = (
        f"**Host:** {interaction.user.mention}\n\n"
        f"**Attendees:**\n{attendees_str}\n\n"
        f"**Points Awarded:** {earned_points}\n\n"
        f"**Proof:** {proof.url}"
    )

    await log_channel.send(
        log_content,
        allowed_mentions=discord.AllowedMentions.none()
    )

    await interaction.followup.send(
        f"✅ Successfully logged event! Added `{earned_points}` points to {len(attendees_set)} attendees and posted report to <#{LOGGING_CHANNEL_ID}>.",
        ephemeral=True
    )

@bot.tree.command(name="quota", description="Check current quota points for yourself or another user")
@app_commands.describe(user="Optional user to check (defaults to yourself)")
async def quota(interaction: discord.Interaction, user: discord.Member = None):
    target_user = user or interaction.user
    
    if not os.path.exists(CSV_FILE):
        await interaction.response.send_message("❌ No quota sheet data exists yet.", ephemeral=True)
        return

    df = pd.read_csv(CSV_FILE)
    user_id_str = str(target_user.id)

    if user_id_str in df["User ID"].astype(str).values:
        user_row = df.loc[df["User ID"].astype(str) == user_id_str].iloc[0]
        points = user_row["Points"]
        await interaction.response.send_message(f"📊 **{target_user.display_name}'s Quota:** `{points}` points", ephemeral=False)
    else:
        await interaction.response.send_message(f"📊 **{target_user.display_name}** has `0` points on the quota sheet.", ephemeral=False)

@bot.tree.command(name="remove_points", description="Remove points from a specific user's quota")
@app_commands.describe(
    user="The user you want to deduct points from",
    points="Number of points to remove"
)
async def remove_points(
    interaction: discord.Interaction,
    user: discord.Member,
    points: str
):
    await interaction.response.defer(ephemeral=True)

    try:
        deduct_points = int(points.strip())
    except ValueError:
        await interaction.followup.send("❌ Error: Points must be a valid number (e.g., `5`).", ephemeral=True)
        return

    if not os.path.exists(CSV_FILE):
        await interaction.followup.send("❌ No quota sheet data exists yet.", ephemeral=True)
        return

    df = pd.read_csv(CSV_FILE)
    user_id_str = str(user.id)

    if user_id_str in df["User ID"].astype(str).values:
        current_points = int(df.loc[df["User ID"].astype(str) == user_id_str, "Points"].values[0])
        new_points = max(0, current_points - deduct_points) 
        df.loc[df["User ID"].astype(str) == user_id_str, "Points"] = new_points
        df.to_csv(CSV_FILE, index=False)
        
        await interaction.followup.send(
            f"✅ Successfully removed `{deduct_points}` points from {user.mention}. Their new total is `{new_points}` points.",
            ephemeral=True
        )
    else:
        await interaction.followup.send(f"❌ {user.mention} is not currently found in the quota sheet.", ephemeral=True)

def run_discord_bot():
    if BOT_TOKEN:
        bot.run(BOT_TOKEN)

if "bot_thread_started" not in st.session_state:
    st.session_state.bot_thread_started = True
    if BOT_TOKEN:
        threading.Thread(target=run_discord_bot, daemon=True).start()
