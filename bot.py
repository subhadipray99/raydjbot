import logging
from highrise import (
    BaseBot,
    User,
    Position,
    AnchorPosition,
    SessionMetadata,
    Reaction,
    CurrencyItem,
    Item,
)
from highrise.models import GetRoomUsersRequest, GetWalletRequest, Error
import config
from music import music_manager

logger = logging.getLogger("HighriseBot")

# Map of friendly command names to Highrise emote identifiers
EMOTE_MAP = {
    "wave": "emote-wave",
    "tired": "emote-tired",
    "dance": "dance-macarena",
    "macarena": "dance-macarena",
    "orangejuice": "dance-orangejuice",
    "float": "emote-float",
    "teleport": "emote-teleporting",
    "hello": "emote-hello",
    "curtsey": "emote-curtsey",
    "bow": "emote-bow",
    "snowball": "emote-snowball",
    "snowangel": "emote-snowangel",
    "confused": "emote-confused",
    "charging": "emote-charging",
    "shopping": "dance-shoppingcart",
    "russian": "dance-russian",
    "pennywise": "dance-pennywise",
    "casual": "idle-dance-casual",
    "tiktok8": "dance-tiktok8",
    "tiktok2": "dance-tiktok2",
    "weird": "dance-weird",
    "hot": "emote-hot",
    "laugh": "emote-laughing",
    "thumbsup": "emoji-thumbsup",
    "angry": "emoji-angry",
    "scared": "emoji-scared",
    "celebrate": "emoji-celebrate",
}


class HighriseBot(BaseBot):
    def __init__(self):
        super().__init__()
        self.bot_id: str | None = None
        self.session_metadata: SessionMetadata | None = None
        self._has_announced: bool = False

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        """Triggered when connection to room is established."""
        self.session_metadata = session_metadata
        self.bot_id = str(session_metadata.user_id)
        logger.info(f"Bot successfully connected to room! Bot User ID: {self.bot_id}")

        # Start the music queue runner with this bot instance for room announcements
        music_manager.start(self)

        # Only broadcast welcome message once on initial startup, not on every reconnect
        if not self._has_announced:
            base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"
            await self.highrise.chat(
                f"🤖 RayDJBot is online! Type /play <song> to queue music.\n"
                f"📻 Live Jukebox: {base_url}/player"
            )
            self._has_announced = True
        else:
            logger.info("Bot reconnected silently (avoiding chat spam).")

    async def on_user_join(self, user: User, position: Position | AnchorPosition | None = None) -> None:
        """Triggered when a user joins the room."""
        logger.info(f"User joined: {user.username} (ID: {user.id})")
        if user.id == self.bot_id:
            return
        welcome = config.WELCOME_MESSAGE.format(username=user.username)
        await self.highrise.chat(welcome)
        try:
            await self.highrise.send_emote("emote-wave")
        except Exception as e:
            logger.debug(f"Could not send wave emote on join: {e}")

    async def on_user_leave(self, user: User) -> None:
        """Triggered when a user leaves the room."""
        logger.info(f"User left: {user.username} (ID: {user.id})")

    async def on_reaction(self, user: User, reaction: Reaction, receiver: User) -> None:
        """Echo reactions back to users if they react to the bot."""
        if receiver.id == self.bot_id and user.id != self.bot_id:
            logger.info(f"Received reaction '{reaction}' from {user.username}")
            try:
                await self.highrise.react(reaction, user.id)
            except Exception as e:
                logger.error(f"Error sending reaction back: {e}")

    async def on_tip(self, sender: User, receiver: User, tip: CurrencyItem | Item) -> None:
        """Triggered when someone tips in the room."""
        if receiver.id == self.bot_id:
            if isinstance(tip, CurrencyItem):
                msg = f"Thank you so much @{sender.username} for the tip of {tip.amount} {tip.type}! ❤️"
            else:
                msg = f"Thank you so much @{sender.username} for the gift tip! ❤️"
            logger.info(f"Tip received: {msg}")
            await self.highrise.chat(msg)
            try:
                await self.highrise.send_emote("emote-lust")
            except Exception:
                pass

    def _extract_command(self, raw_message: str) -> tuple[str, list[str]] | None:
        """Support both '/' and '!' prefixes for commands."""
        msg = raw_message.strip()
        prefix = None
        if msg.startswith("/"):
            prefix = "/"
        elif msg.startswith("!"):
            prefix = "!"
        elif msg.startswith(config.BOT_PREFIX):
            prefix = config.BOT_PREFIX
        else:
            return None

        body = msg[len(prefix):].strip()
        parts = body.split()
        if not parts:
            return None
        return parts[0].lower(), parts[1:]

    async def on_chat(self, user: User, message: str) -> None:
        """Triggered on public room chat messages."""
        logger.info(f"[{user.username}]: {message}")
        parsed = self._extract_command(message)
        if not parsed:
            return
        cmd, args = parsed
        await self.handle_command(user, cmd, args, is_whisper=False)

    async def on_whisper(self, user: User, message: str) -> None:
        """Triggered on private whisper messages."""
        logger.info(f"[Whisper from {user.username}]: {message}")
        parsed = self._extract_command(message)
        if not parsed:
            return
        cmd, args = parsed
        await self.handle_command(user, cmd, args, is_whisper=True)

    async def handle_command(self, user: User, cmd: str, args: list[str], is_whisper: bool = False) -> None:
        """Central command dispatcher."""
        async def reply(text: str):
            if is_whisper:
                await self.highrise.send_whisper(user.id, text)
            else:
                await self.highrise.chat(text)

        base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"
        player_link = f"{base_url}/player"

        # ---------------- MUSIC COMMANDS ----------------
        if cmd in ("play", "p"):
            if not args:
                await reply(f"Usage: /play <song name or YouTube URL>")
                return

            query = " ".join(args)
            await reply(f"🔍 Searching YouTube for: \"{query}\"...")
            success, msg, track = await music_manager.search_and_queue(query, user.username)
            if success and track:
                await reply(f"{msg}\n📻 Listen along: {player_link}")
            else:
                await reply(msg)

        elif cmd in ("skip", "s"):
            success, msg = music_manager.skip()
            await reply(msg)

        elif cmd in ("queue", "q"):
            state = music_manager.get_state()
            current = state.get("now_playing")
            queue = state.get("queue", [])
            lines = []

            if current:
                lines.append(f"🎧 Now Playing: \"{current['title']}\" [{current['duration_str']}] (Requested by @{current['requester']})")
            else:
                lines.append("🎧 Nothing currently playing.")

            if queue:
                lines.append(f"📋 Upcoming Queue ({len(queue)} songs):")
                for idx, t in enumerate(queue[:4], start=1):
                    lines.append(f"  {idx}. \"{t['title']}\" [{t['duration_str']}] (@{t['requester']})")
                if len(queue) > 4:
                    lines.append(f"  ... and {len(queue) - 4} more tracks.")
            else:
                lines.append("Queue is empty. Use /play <song> to queue music!")

            lines.append(f"📻 Live Jukebox: {player_link}")
            await reply("\n".join(lines))

        elif cmd in ("np", "nowplaying"):
            state = music_manager.get_state()
            current = state.get("now_playing")
            if current:
                pos = int(state.get("position_sec", 0))
                m, s = divmod(pos, 60)
                pos_str = f"{m}:{s:02d}"
                await reply(
                    f"🎶 Now: \"{current['title']}\" [{pos_str} / {current['duration_str']}]\n"
                    f"👤 Requested by: @{current['requester']}\n"
                    f"📻 Live Jukebox: {player_link}"
                )
            else:
                await reply("No music is currently playing. Use /play <song> to start!")

        elif cmd in ("player", "radio", "jukebox"):
            await reply(f"📻 Listen to live room music here: {player_link}\n24/7 In-Room Radio: {base_url}/radio.mp3")

        elif cmd in ("station", "setstation"):
            from music import RADIO_STATIONS
            if not args:
                avail = ", ".join(RADIO_STATIONS.keys())
                await reply(f"Usage: /station <name>. Available: {avail}")
                return
            station_key = args[0].lower().lstrip("/")
            success, msg = music_manager.set_station(station_key)
            await reply(msg)

        elif cmd in ("stations", "radios"):
            from music import RADIO_STATIONS
            lines = ["📻 Available 24/7 Room Radio Stations:"]
            for k, info in RADIO_STATIONS.items():
                active = " (Active)" if k == music_manager.current_station_key else ""
                lines.append(f"• /station {k} - {info['name']}{active}")
            lines.append(f"Set your Highrise Custom Radio URL to: {base_url}/radio.mp3")
            await reply("\n".join(lines))

        # ---------------- ROOM & BOT COMMANDS ----------------
        elif cmd in ("help", "commands"):
            help_text = (
                f"🤖 RayDJBot Commands:\n"
                f"/station <name> - Switch in-room 24/7 radio (dance, lofi, pop, chill, hiphop, ibiza)\n"
                f"/stations - List all available 24/7 radio stations\n"
                f"/play <song> (or /p) - Search & queue YouTube song\n"
                f"/skip (or /s) - Skip to next song\n"
                f"/queue (or /q) - View upcoming songs\n"
                f"/np - Check currently playing song\n"
                f"/player - Get synchronized live player link\n"
                f"!come - Bot walks to your location\n"
                f"!emote <name> - Play emote\n"
                f"!emotes - List popular emotes\n"
                f"!teleport <user> - Teleport user (requires mod)\n"
                f"!wallet - Check bot tip wallet\n"
                f"!ping - Bot status"
            )
            await reply(help_text)

        elif cmd == "ping":
            await reply("Pong! 🏓 RayDJBot is active and running 24/7.")

        elif cmd == "come":
            try:
                response = await self.highrise.get_room_users()
                if isinstance(response, GetRoomUsersRequest.GetRoomUsersResponse):
                    user_pos = None
                    for room_user, pos in response.content:
                        if room_user.id == user.id:
                            user_pos = pos
                            break
                    if user_pos:
                        await self.highrise.walk_to(user_pos)
                        await reply(f"On my way, @{user.username}!")
                    else:
                        await reply(f"Could not determine your position, @{user.username}.")
                else:
                    await reply("Could not retrieve room users.")
            except Exception as e:
                logger.error(f"Error handling !come: {e}")
                await reply("Failed to walk to your position.")

        elif cmd in ("emote", "e"):
            if not args:
                await reply("Usage: !emote <name>. Type !emotes to view list.")
                return

            emote_query = args[0].lower()
            emote_id = EMOTE_MAP.get(emote_query, emote_query)

            target_user_id = user.id
            if len(args) > 1 and args[1].startswith("@"):
                target_username = args[1][1:].lower()
                try:
                    users_resp = await self.highrise.get_room_users()
                    if isinstance(users_resp, GetRoomUsersRequest.GetRoomUsersResponse):
                        for room_user, _ in users_resp.content:
                            if room_user.username.lower() == target_username:
                                target_user_id = room_user.id
                                break
                except Exception as e:
                    logger.debug(f"Target user resolution error: {e}")

            try:
                await self.highrise.send_emote(emote_id, target_user_id)
            except Exception as e:
                logger.error(f"Error executing emote {emote_id}: {e}")
                await reply(f"Could not perform emote '{emote_query}'. Is the emote ID correct?")

        elif cmd == "emotes":
            sample_emotes = ", ".join(list(EMOTE_MAP.keys())[:15])
            await reply(f"Popular emotes: {sample_emotes}... (Use: !emote <name>)")

        elif cmd in ("teleport", "tp"):
            try:
                users_resp = await self.highrise.get_room_users()
                if not isinstance(users_resp, GetRoomUsersRequest.GetRoomUsersResponse):
                    await reply("Failed to fetch room users.")
                    return

                target_user = None
                bot_pos = None
                target_name = args[0].lstrip("@").lower() if args else user.username.lower()

                for r_user, pos in users_resp.content:
                    if r_user.username.lower() == target_name:
                        target_user = r_user
                    if r_user.id == self.bot_id and isinstance(pos, Position):
                        bot_pos = pos

                if not target_user:
                    await reply(f"User '{target_name}' not found in this room.")
                    return

                if not bot_pos:
                    await reply("Bot position is not available for teleport target.")
                    return

                await self.highrise.teleport(target_user.id, bot_pos)
                await reply(f"Teleported @{target_user.username} to the bot! ⚡")
            except Exception as e:
                logger.error(f"Teleport error: {e}")
                await reply("Teleport failed. Make sure the bot has Designer rights in this room.")

        elif cmd == "wallet":
            try:
                wallet_resp = await self.highrise.get_wallet()
                if isinstance(wallet_resp, GetWalletRequest.GetWalletResponse):
                    balances = [f"{item.amount} {item.type}" for item in wallet_resp.content]
                    wallet_str = ", ".join(balances) if balances else "0 currency"
                    await reply(f"💰 Bot Wallet: {wallet_str}")
                else:
                    await reply("Could not fetch wallet details.")
            except Exception as e:
                logger.error(f"Wallet fetch error: {e}")
                await reply("Failed to get wallet balance.")

        elif cmd == "say":
            if not args:
                await reply("Usage: !say <message>")
                return
            to_say = " ".join(args)
            await self.highrise.chat(to_say)
