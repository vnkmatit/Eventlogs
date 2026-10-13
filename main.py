import os
import re
import threading
import json
import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import discord
from discord import app_commands
from discord.ext import commands, tasks
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# CONFIGURATION & CHANNEL ID
# ==========================================
LOGGING_CHANNEL_ID = 1538166153799532574      # Channel where hosts post their logs
SPREADSHEET_NAME = "Event Quotas"             # Name of your Google Sheet

# Track credited users per message ID to safely support message edits without duplication
MESSAGE_CREDITS = {}

# --- GOOGLE SHEETS STYLING HELPER ---
def style_google_sheet(sheet):
    try:
        sheet.format("A1:C1", {
            "backgroundColor": {"red": 0.12, "green": 0.16, "blue": 0.22},
            "textFormat": {
                "bold": True,
                "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                "fontSize": 11
            },
            "horizontalAlignment": "CENTER"
        })
        sheet.freeze(rows=1)
    except Exception as e:
        print(f"Styling note: {e}")

# --- GOOGLE SHEETS SETUP ---
def get_google_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = json.loads(st.secrets["GOOGLE_CREDENTIALS_JSON"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    try:
        sheet = client.open(SPREADSHEET_NAME).sheet1
    except gspread.SpreadsheetNotFound:
        spreadsheet = client.create(SPREADSHEET_NAME)
        sheet = spreadsheet.sheet1
        sheet.append_row(["User ID", "Username", "Points"])
        style_google_sheet(sheet)
    return sheet

def load_quotas_from_sheet():
    try:
        sheet = get_google_sheet()
        data = sheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=["User ID", "Username", "Points"])
        return pd.DataFrame(data)
    except Exception as e:
        return pd.DataFrame(columns=["User ID", "Username", "Points"])

def update_quotas_in_sheet(users_dict, earned_points):
    sheet = get_google_sheet()
    records = sheet.get_all_records()
    
    if records:
        df = pd.DataFrame(records)
    else:
        df = pd.DataFrame(columns=["User ID", "Username", "Points"])
        sheet.append_row(["User ID", "Username", "Points"])

    for user_id, username in users_dict.items():
        user_id_str = str(user_id)
        df["User ID"] = df["User ID"].astype(str)
        
        if user_id_str in df["User ID"].values:
            df.loc[df["User ID"] == user_id_str, "Points"] += earned_points
            df.loc[df["User ID"] == user_id_str, "Username"] = username
        else:
            new_row = pd.DataFrame({
                "User ID": [user_id_str], 
                "Username": [username], 
                "Points": [earned_points]
            })
            df = pd.concat([df, new_row], ignore_index=True)
    
    sheet.clear()
    sheet.append_row(["User ID", "Username", "Points"])
    for _, row in df.iterrows():
        sheet.append_row([str(row["User ID"]), str(row["Username"]), int(row["Points"])])
    
    style_google_sheet(sheet)

# --- STREAMLIT DASHBOARD & QUOTA SHEET ---
st.title("📊 Event Quota Sheet & Bot Dashboard")
st.success("🤖 Bot process active and connected to Google Sheets.")

try:
    df_display = load_quotas_from_sheet()
    st.subheader("Member Quota Points Leaderboard")
    st.dataframe(df_display, use_container_width=True)
except Exception as e:
    st.warning("Please configure your Google Service Account in Streamlit Secrets to view the leaderboard.")

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

BOT_TOKEN = st.secrets.get("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")

# --- CUSTOM ROLE PERMISSION CHECK ---
def is_officer_or_staff():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        allowed_roles = {"officer corps", "staff"}
        if any(role.name.lower() in allowed_roles for role in interaction.user.roles):
            return True
        return False
    return app_commands.check(predicate)

# --- AUTOMATED WEEKLY RESET TASK ---
@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=ZoneInfo("America/New_York")))
async def weekly_quota_reset():
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() == 0:  # Monday
        try:
            sheet = get_google_sheet()
            sheet.clear()
            sheet.append_row(["User ID", "Username", "Points"])
            style_google_sheet(sheet)
            MESSAGE_CREDITS.clear()
            print("🔄 Weekly quota reset executed successfully!", flush=True)
        except Exception as e:
            print(f"❌ Error during weekly quota reset: {e}", flush=True)

@weekly_quota_reset.before_loop
async def before_weekly_reset():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"🤖 Bot is ONLINE as {bot.user}", flush=True)
    print(f"Connected to the following servers:", flush=True)
    for guild in bot.guilds:
        print(f" - {guild.name} (ID: {guild.id})", flush=True)
    
    # Start the weekly reset loop safely if not already running
    if not weekly_quota_reset.is_running():
        weekly_quota_reset.start()
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s).", flush=True)
    except Exception as e:
        print(f"Failed to sync commands: {e}", flush=True)

# Shared core logic for processing initial messages and edits
async def process_log_message(message):
    if message.author.bot:
        return

    if message.channel.id != LOGGING_CHANNEL_ID:
        return

    try:
        points_match = re.search(r'(?:points|pts)[^\d]*(\d+)', message.content, re.IGNORECASE)
        if not points_match:
            points_match = re.search(r'(\d+)\s*(?:points|pts)', message.content, re.IGNORECASE)

        if not points_match:
            print(f"⚠️ Ignored: Could not find points pattern in message.", flush=True)
            return

        earned_points = int(points_match.group(1))
        print(f"✅ Found {earned_points} points! Processing users...", flush=True)

        current_users = {}
        host_id = message.author.id
        current_users[host_id] = message.author.display_name

        for member in message.mentions:
            if not member.bot and member.id != host_id:
                current_users[member.id] = member.display_name

        credited_set = MESSAGE_CREDITS.setdefault(message.id, set())
        new_users_to_reward = {uid: uname for uid, uname in current_users.items() if uid not in credited_set}

        if new_users_to_reward:
            update_quotas_in_sheet(new_users_to_reward, earned_points)
            for uid in new_users_to_reward:
                credited_set.add(uid)
            print(f"✅ Successfully credited new users for message {message.id}!", flush=True)
        else:
            print(f"ℹ️ Message {message.id} checked/edited, but no new users to reward.", flush=True)

        if not any(reaction.me for reaction in message.reactions):
            await message.add_reaction("✅")
    except Exception as e:
        print(f"❌ Error processing log message: {e}", flush=True)

@bot.event
async def on_message(message):
    print(f"📥 GLOBAL DEBUG: Saw message '{message.content}' from {message.author} in channel ID {message.channel.id}", flush=True)
    await process_log_message(message)

@bot.event
async def on_message_edit(before, after):
    print(f"✏️ GLOBAL DEBUG: Message edited in channel ID {after.channel.id}: '{after.content}'", flush=True)
    await process_log_message(after)

@bot.tree.command(name="quota", description="Check current quota points for yourself or another user")
@app_commands.describe(user="Optional user to check (defaults to yourself)")
async def quota(interaction: discord.Interaction, user: discord.Member = None):
    target_user = user or interaction.user
    df = load_quotas_from_sheet()
    
    if df.empty or "User ID" not in df.columns:
        await interaction.response.send_message("❌ No quota sheet data exists yet.", ephemeral=True)
        return

    df["User ID"] = df["User ID"].astype(str)
    user_id_str = str(target_user.id)

    if user_id_str in df["User ID"].values:
        user_row = df.loc[df["User ID"] == user_id_str].iloc[0]
        points = user_row["Points"]
        await interaction.response.send_message(f"📊 **{target_user.display_name}'s Quota:** `{points}` points", ephemeral=False)
    else:
        await interaction.response.send_message(f"📊 **{target_user.display_name}** has `0` points on the quota sheet.", ephemeral=False)

@bot.tree.command(name="remove_points", description="Remove points from a specific user's quota")
@app_commands.describe(
    user="The user you want to deduct points from",
    points="Number of points to remove"
)
@is_officer_or_staff()
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

    df = load_quotas_from_sheet()
    if df.empty or "User ID" not in df.columns:
        await interaction.followup.send("❌ No quota sheet data exists yet.", ephemeral=True)
        return

    df["User ID"] = df["User ID"].astype(str)
    user_id_str = str(user.id)

    if user_id_str in df["User ID"].values:
        current_points = int(df.loc[df["User ID"] == user_id_str, "Points"].values[0])
        new_points = max(0, current_points - deduct_points) 
        df.loc[df["User ID"] == user_id_str, "Points"] = new_points
        
        sheet = get_google_sheet()
        sheet.clear()
        sheet.append_row(["User ID", "Username", "Points"])
        for _, row in df.iterrows():
            sheet.append_row([str(row["User ID"]), str(row["Username"]), int(row["Points"])])
        style_google_sheet(sheet)
        
        await interaction.followup.send(
            f"✅ Successfully removed `{deduct_points}` points from {user.mention}. Their new total is `{new_points}` points.",
            ephemeral=True
        )
    else:
        await interaction.followup.send(f"❌ {user.mention} is not currently found in the quota sheet.", ephemeral=True)

@remove_points.error
async def remove_points_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ You must have the **Officer Corps** or **Staff** role to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)

def run_discord_bot():
    if BOT_TOKEN:
        bot.run(BOT_TOKEN)

if "bot_thread_started" not in st.session_state:
    st.session_state.bot_thread_started = True
    if BOT_TOKEN:
        threading.Thread(target=run_discord_bot, daemon=True).start()
