import os
import re
import threading
import pandas as pd
import discord
from discord import app_commands
from discord.ext import commands
import streamlit as st

# ==========================================
# CONFIGURATION & CHANNEL ID
# ==========================================
LOGGING_CHANNEL_ID = 1538166153799532574      # Channel where hosts post their logs
CSV_FILE = "quotas.csv"

# --- STREAMLIT DASHBOARD & QUOTA SHEET ---
st.title("📊 Event Quota Sheet & Bot Dashboard")
st.success("🤖 Bot process active and listening for log messages.")

# Display Live Quota Sheet Table (Accessible via web link)
if os.path.exists(CSV_FILE):
    df = pd.read_csv(CSV_FILE)
    st.subheader("Member Quota Points Leaderboard")
    st.dataframe(df, use_container_width=True)
else:
    st.info("No points logged yet. Post event logs in the Discord logging channel to start tracking quotas!")

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

BOT_TOKEN = st.secrets.get("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")

def update_quotas(users_dict, earned_points):
    """Updates or creates the local quotas.csv file with earned points for users and host."""
    if os.path.exists(CSV_FILE):
        df = pd.read_csv(CSV_FILE)
    else:
        df = pd.DataFrame(columns=["User ID", "Username", "Points"])

    for user_id, username in users_dict.items():
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

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Only process messages posted in the designated logging channel
    if message.channel.id != LOGGING_CHANNEL_ID:
        return

    # Look for points format in the message (e.g., "Points: 10" or "10 points")
    points_match = re.search(r'(?:points|pts)[\s:]*(\d+)', message.content, re.IGNORECASE)
    if not points_match:
        points_match = re.search(r'(\d+)\s*(?:points|pts)', message.content, re.IGNORECASE)

    # If no points are found, ignore the message
    if not points_match:
        return

    earned_points = int(points_match.group(1))

    # Dictionary to hold user ID -> username mapping
    users_to_reward = {}

    # 1. Add the host (message author) so they get points too
    host_id = message.author.id
    users_to_reward[host_id] = str(message.author)

    # 2. Add valid mentioned attendees
    for member in message.mentions:
        if not member.bot and member.id != host_id:
            users_to_reward[member.id] = str(member)

    # Update quota sheet
    update_quotas(users_to_reward, earned_points)

    # React to confirm processing
    try:
        await message.add_reaction("✅")
    except Exception:
        pass

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
