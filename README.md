# Highrise Game Bot (Render 24/7 Free Hosting Ready)

A Python bot for [Highrise](https://highrise.game) built with the official `highrise-bot-sdk` and an integrated lightweight `aiohttp` web server designed specifically to be hosted on **Render's Free Tier** and kept alive 24/7 with a free cron job or uptime monitor.

---

## 🌟 Features

- **24/7 Render Keep-Alive**: Includes a background HTTP health-check server (`0.0.0.0:$PORT`) so Render detects an active web port and your external cron job / pinger (e.g., cron-job.org or UptimeRobot) can keep it awake.
- **Auto-Reconnect**: Resilient WebSocket loop that automatically reconnects if Highrise restarts or network drops.
- **Interactive Commands**:
  - `!help` or `!commands` - Displays available commands.
  - `!ping` - Checks bot responsiveness and online status.
  - `!come` - Finds your position and walks the bot to you.
  - `!emote <name> [@user]` - Plays popular emotes (e.g. `dance`, `wave`, `macarena`, `hot`, `laugh`).
  - `!emotes` - Shows quick list of popular emote keywords.
  - `!teleport <@user>` - Teleports a player to the bot (requires room Designer rights).
  - `!wallet` - Checks bot gold/bubbles tip balance.
  - `!say <message>` - Echoes your message into chat.
- **Automated Room Interactions**:
  - Welcomes new users when they enter the room and waves.
  - Reacts back when players react (e.g. hearts back).
  - Thanks users in chat when tips (gold/items) are received.

---

## 🛠️ Step 1: Highrise Prerequisites

Before running the bot, you need your bot credentials from Highrise:

1. **Create Bot Account**:
   - Go to [Highrise Create Portal](https://create.highrise.game) and sign in.
   - Go to **Dashboard** > **Bots & API Keys** > **Create Bot**.
   - Copy your **API Token**.
2. **Get your Room ID**:
   - Open Highrise on mobile or web.
   - Enter the room where your bot will stay.
   - Open the room information panel (top right) and tap **"Share this Room"** -> **Copy Link**.
   - The string of numbers/letters in the link (or the Room ID shown) is your `ROOM_ID`.
3. **Give Bot Designer Rights** *(CRITICAL)*:
   - The bot account **must** have Designer or Moderator privileges in the room to join and execute actions like walking or teleporting.
   - In room settings > Permissions / Roles, add your bot user as a Designer.

---

## 💻 Step 2: Local Testing

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```
*(Or with `uv`: `uv sync`)*

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Open `.env` and fill in:
```env
ROOM_ID=your_highrise_room_id
API_TOKEN=your_highrise_api_token
PORT=8080
BOT_PREFIX=!
```

### 3. Run the Bot
```bash
python main.py
```
You can also run directly with CLI arguments:
```bash
python main.py <ROOM_ID> <API_TOKEN>
```
Once started, visit `http://localhost:8080/` in your browser to verify the health check server.

---

## 🚀 Step 3: Deploy to Render (Free Tier)

Render's free tier allows you to host web services for free. Because standard bots only use outbound WebSockets, Render would normally fail to detect an open port; our included HTTP server fixes this.

1. **Push your code to GitHub**:
   - Create a new repository on GitHub and push this project.
2. **Create a Web Service on Render**:
   - Go to [dashboard.render.com](https://dashboard.render.com).
   - Click **New +** > **Web Service**.
   - Connect your GitHub repository.
3. **Configure the Service**:
   - **Name**: `highrise-bot` (or your choice)
   - **Environment**: `Python 3`
   - **Region**: Choose the closest to you
   - **Branch**: `main`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Instance Type**: `Free`
4. **Add Environment Variables**:
   In the **Environment** tab on Render, add:
   - `ROOM_ID` = `your_actual_highrise_room_id`
   - `API_TOKEN` = `your_actual_highrise_api_token`
   - `PYTHON_VERSION` = `3.11.9`
5. Click **Deploy Web Service**.

Once deployed, Render gives you a public URL (e.g. `https://highrise-bot-xxxx.onrender.com`).

---

## ⏱️ Step 4: Keep It Alive 24/7 (Free Cron Job)

Render Free Tier web services go to sleep ("spin down") after **15 minutes of HTTP inactivity**.
To keep the bot online 24/7:

1. Go to a free pinging service such as:
   - [cron-job.org](https://cron-job.org) (Recommended - 100% free cron scheduler)
   - [UptimeRobot](https://uptimerobot.com) (Free 5-minute HTTP monitor)
2. Create a new cron job / monitor:
   - **Title**: `Highrise Bot Keepalive`
   - **URL**: `https://<your-service-name>.onrender.com/health`
   - **Schedule**: Every **10 minutes** (must be less than 15 minutes)
   - **HTTP Method**: `GET`
3. Save and enable the monitor.

> [!NOTE]
> **Free Hours Allocation**: Render provides **750 free instance hours per month** for Free Web Services.
> A full 31-day month has 24 * 31 = 744 hours. A single web service running continuously 24/7 stays within your 750 free monthly hours!

---

## 📂 Project Structure

```
.
├── bot.py             # HighriseBot logic, event listeners, and chat commands
├── web_server.py      # aiohttp keep-alive web server for Render & cron pings
├── main.py            # Unified entry point running bot and web server concurrently
├── config.py          # Configuration and environment variable loader
├── requirements.txt   # Python package dependencies
├── render.yaml        # Render Blueprint configuration
├── .env.example       # Example environment variables
└── README.md          # Documentation and setup guide
```
