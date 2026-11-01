import asyncio
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
import outfit
import position

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
        self.user_positions: dict[str, Position | AnchorPosition] = {}

    async def _apply_saved_position_delayed(self, pos: Position | AnchorPosition, delay: float = 2.0) -> None:
        """Wait briefly after room connect for world registration, then move bot to saved spot."""
        await asyncio.sleep(delay)
        success = await self.move_bot_to(pos)
        if success:
            logger.info(f"Successfully relocated bot to saved position: {position.format_position(pos)}")
        else:
            logger.warning(f"Failed to relocate bot to saved position: {position.format_position(pos)}")

    async def move_bot_to(self, target_pos: Position | AnchorPosition) -> bool:
        """Move or teleport the bot to target position, falling back gracefully to walk_to."""
        try:
            if isinstance(target_pos, Position) and self.bot_id:
                try:
                    await self.highrise.teleport(self.bot_id, target_pos)
                    return True
                except Exception as tp_err:
                    logger.debug(f"Teleport bot failed ({tp_err}), falling back to walk_to")
            await self.highrise.walk_to(target_pos)
            return True
        except Exception as e:
            logger.error(f"Error moving bot to {target_pos}: {e}")
            return False

    async def _get_user_position(self, target_user: User) -> Position | AnchorPosition | None:
        """Find the freshest position for a user (cache first, then room users API)."""
        if target_user.id in self.user_positions:
            return self.user_positions[target_user.id]

        try:
            response = await self.highrise.get_room_users()
            if isinstance(response, GetRoomUsersRequest.GetRoomUsersResponse):
                for room_user, pos in response.content:
                    if room_user.id == target_user.id:
                        self.user_positions[room_user.id] = pos
                        return pos
        except Exception as e:
            logger.error(f"Error fetching user position: {e}")
        return None

    async def teleport_user_to_floor(self, user: User, floor_input: str, reply) -> None:
        """Teleport a user to the specified floor spawn position."""
        floor_key = position.normalize_floor_key(floor_input)
        floor_label = position.format_floor_name(floor_key)

        target_pos = position.get_floor_position(config.ROOM_ID, floor_key)
        if not target_pos:
            all_floors = position.get_all_floors(config.ROOM_ID)
            if all_floors:
                avail_list = [f"/f{k}" if k.isdigit() else f"/{k}" for k in sorted(all_floors.keys())]
                avail_str = ", ".join(avail_list)
                await reply(
                    f"⚠️ {floor_label} spawn is not set yet.\n"
                    f"Available floors: {avail_str}\n"
                    f"To set this floor: Stand there and type !setfloor {floor_input}"
                )
            else:
                await reply(
                    f"⚠️ {floor_label} spawn has not been set yet.\n"
                    f"Stand where you want players to land and type !setfloor {floor_input} to configure it!"
                )
            return

        try:
            await self.highrise.teleport(user.id, target_pos)
            await reply(f"⚡ Teleported @{user.username} to {floor_label}!")
        except Exception as e:
            logger.error(f"Error teleporting user {user.username} to floor {floor_key}: {e}")
            await reply(
                f"⚠️ Could not teleport to {floor_label}: {e}\n"
                f"(Make sure the bot has Designer or Moderator permissions in this room!)"
            )

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        """Triggered when connection to room is established."""
        self.session_metadata = session_metadata
        self.bot_id = str(session_metadata.user_id)
        logger.info(f"Bot successfully connected to room! Bot User ID: {self.bot_id}")

        # Start the music queue runner with this bot instance for room announcements
        music_manager.start(self)

        # Restore saved outfit if available
        saved_outfit = outfit.get_active_outfit()
        if saved_outfit:
            try:
                res = await self.highrise.set_outfit(saved_outfit)
                if isinstance(res, Error):
                    logger.warning(f"Could not restore saved outfit on start: {res.message}")
                else:
                    logger.info(f"Successfully restored saved bot outfit ({len(saved_outfit)} items).")
            except Exception as e:
                logger.warning(f"Error restoring outfit on start: {e}")

        # Restore saved room position if available
        saved_pos = position.get_saved_position(config.ROOM_ID)
        if saved_pos:
            logger.info(f"Found saved position for room {config.ROOM_ID}: {position.format_position(saved_pos)}. Relocating bot...")
            asyncio.create_task(self._apply_saved_position_delayed(saved_pos))

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
        if position:
            self.user_positions[user.id] = position
        if user.id == self.bot_id:
            return
        welcome = config.WELCOME_MESSAGE.format(username=user.username)
        await self.highrise.chat(welcome)
        try:
            await self.highrise.send_emote("emote-wave")
        except Exception as e:
            logger.debug(f"Could not send wave emote on join: {e}")

    async def on_user_move(self, user: User, destination: Position | AnchorPosition) -> None:
        """Track user positions in real-time as they walk or sit."""
        self.user_positions[user.id] = destination

    async def on_user_leave(self, user: User) -> None:
        """Triggered when a user leaves the room."""
        logger.info(f"User left: {user.username} (ID: {user.id})")
        self.user_positions.pop(user.id, None)

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

    async def _handle_copyfit(self, caller: User, target_name: str, reply) -> None:
        """Helper to clone a user's outfit onto the bot."""
        try:
            users_resp = await self.highrise.get_room_users()
            if not isinstance(users_resp, GetRoomUsersRequest.GetRoomUsersResponse):
                await reply("Could not fetch room users.")
                return

            target_user = None
            clean_target = target_name.lstrip("@").lower()
            for r_user, _ in users_resp.content:
                if r_user.username.lower() == clean_target:
                    target_user = r_user
                    break

            if not target_user:
                await reply(f"User '@{clean_target}' not found in this room.")
                return

            target_outfit_resp = await self.highrise.get_user_outfit(target_user.id)
            if not hasattr(target_outfit_resp, "outfit") or not target_outfit_resp.outfit:
                await reply(f"Could not retrieve @{target_user.username}'s outfit.")
                return

            target_outfit = target_outfit_resp.outfit
            res = await self.highrise.set_outfit(target_outfit)
            if isinstance(res, Error):
                await reply(
                    f"⚠️ Cannot clone full outfit: {res.message}.\n"
                    f"(Highrise bots can only equip free starter items or items in their account inventory)"
                )
            else:
                outfit.save_active_outfit(target_outfit)
                try:
                    await self.highrise.send_emote("dance-macarena")
                except Exception:
                    pass
                await reply(f"👔 Successfully cloned @{target_user.username}'s outfit! Looking sharp.")
        except Exception as e:
            logger.error(f"Error copying outfit: {e}")
            await reply(f"Failed to copy outfit: {e}")

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
            await reply(f"🔍 Searching music for: \"{query}\"...")
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
                req_tag = " (📻 Radio AutoDJ)" if current.get("is_autoplay") else f" (Requested by @{current['requester']})"
                lines.append(f"🎧 Now Playing: \"{current['title']}\" [{current['duration_str']}]{req_tag}")
            else:
                lines.append("🎧 Nothing currently playing.")

            if queue:
                lines.append(f"📋 Upcoming User Queue ({len(queue)} songs):")
                for idx, t in enumerate(queue[:4], start=1):
                    lines.append(f"  {idx}. \"{t['title']}\" [{t['duration_str']}] (@{t['requester']})")
                if len(queue) > 4:
                    lines.append(f"  ... and {len(queue) - 4} more tracks.")
            else:
                if music_manager.autoplay_enabled:
                    lines.append("User queue is empty. 📻 Radio AutoDJ is playing station hits.\nType /play <song> to queue your request with top priority!")
                else:
                    lines.append("Queue is empty. Use /play <song> to queue music!")

            lines.append(f"📻 Live Jukebox: {player_link}")
            await reply("\n".join(lines))

        elif cmd in ("autoplay", "ap"):
            action = args[0].lower() if args else ""
            if action in ("on", "enable", "start"):
                music_manager.autoplay_enabled = True
                music_manager._queue_updated_event.set()
                await reply("📻 24/7 Radio Autoplay is now ENABLED! Songs will keep playing automatically when user queue is empty.")
            elif action in ("off", "disable", "stop"):
                music_manager.autoplay_enabled = False
                if music_manager.current and music_manager.current.is_autoplay:
                    music_manager.skip()
                await reply("📻 24/7 Radio Autoplay is now DISABLED. Station will stream idle silence when queue is empty.")
            else:
                status = "ENABLED" if music_manager.autoplay_enabled else "DISABLED"
                await reply(f"📻 24/7 Radio Autoplay is currently {status}.\nUse /autoplay on or /autoplay off to toggle.")

        elif cmd in ("np", "nowplaying"):
            state = music_manager.get_state()
            current = state.get("now_playing")
            if current:
                pos = int(state.get("position_sec", 0))
                m, s = divmod(pos, 60)
                pos_str = f"{m}:{s:02d}"
                req_info = "📻 Radio AutoDJ" if current.get("is_autoplay") else f"@{current['requester']}"
                await reply(
                    f"🎶 Now: \"{current['title']}\" [{pos_str} / {current['duration_str']}]\n"
                    f"👤 Requested by: {req_info}\n"
                    f"📻 Live Jukebox: {player_link}"
                )
            else:
                await reply("No music is currently playing. Use /play <song> to start!")

        elif cmd in ("player", "radio", "jukebox"):
            await reply(f"📻 Custom Room Radio: {base_url}/radio.mp3\n🎧 Web Player: {player_link}")

        # ---------------- ROOM & BOT COMMANDS ----------------
        elif cmd in ("help", "commands"):
            help_text = (
                f"🤖 RayDJBot Room Radio Commands:\n"
                f"/play <song> (or /p) - Request a song (takes top priority!)\n"
                f"/skip (or /s) - Skip to next song in queue\n"
                f"/queue (or /q) - View upcoming songs in queue\n"
                f"/autoplay on|off - Toggle 24/7 radio background autoplay\n"
                f"/np - Check currently playing song\n"
                f"/player - Get live player link\n\n"
                f"🏢 Floor Teleport Commands:\n"
                f"• /f1 (or /ground) - Teleport to Floor 1 (Ground Floor)\n"
                f"• /f2 - Teleport to Floor 2\n"
                f"• /floor <name/num> - Teleport to any configured floor\n"
                f"• !setfloor <name> - Set that floor's spawn to your spot (e.g. !setfloor f1, !setfloor f2)\n"
                f"• !floors - List all configured floor spawns\n"
                f"• !delfloor <name> - Remove a floor spawn\n\n"
                f"📍 Bot Position Commands:\n"
                f"• !setpos - Set bot permanent spot to where YOU are standing\n"
                f"• !come / !bring - Call bot to you right now\n"
                f"• !mypos - Show your coordinates (X, Y, Z)\n"
                f"• !botpos - Show bot saved position\n"
                f"• !resetpos - Reset bot to default room entrance\n\n"
                f"👗 Outfit & Wardrobe Commands:\n"
                f"• !fit <preset> - Change dress (dj, suit, street, camo, goth, chill, puffer)\n"
                f"• !copyfit [@user] - Clone someone's outfit\n"
                f"• !fit save <name> - Save current look as a preset\n"
                f"• !wardrobe - Check bot inventory\n\n"
                f"✨ Emotes & Fun:\n"
                f"• !emote <name> - Play emote\n"
                f"• !emotes - List popular emotes\n"
                f"• !teleport <user> - Teleport user to bot (requires mod)\n"
                f"• !wallet - Check bot tip wallet\n"
                f"• !ping - Bot status"
            )
            await reply(help_text)

        elif cmd == "ping":
            await reply("Pong! 🏓 RayDJBot is active and running 24/7.")

        # ---------------- FLOOR TELEPORTATION COMMANDS ----------------
        elif cmd in ("f1", "ground", "g"):
            await self.teleport_user_to_floor(user, "1", reply)

        elif cmd == "f2":
            await self.teleport_user_to_floor(user, "2", reply)

        elif cmd.startswith("f") and len(cmd) > 1 and cmd[1:].isdigit():
            await self.teleport_user_to_floor(user, cmd[1:], reply)

        elif cmd == "floor":
            if args:
                await self.teleport_user_to_floor(user, args[0], reply)
            else:
                all_floors = position.get_all_floors(config.ROOM_ID)
                if all_floors:
                    avail_list = [f"/f{k}" if k.isdigit() else f"/{k}" for k in sorted(all_floors.keys())]
                    avail_str = ", ".join(avail_list)
                    await reply(f"🏢 Available floors: {avail_str}\nUsage: /f1, /f2, or /floor <name>")
                else:
                    await reply("No floor spawns configured yet! Stand where you want a floor spawn and type !setfloor f1 or !setfloor f2.")

        elif cmd in ("floors", "floorlist"):
            all_floors = position.get_all_floors(config.ROOM_ID)
            if not all_floors:
                await reply("No floor spawns configured yet! Stand where you want a spawn and type !setfloor f1 or !setfloor f2.")
            else:
                lines = ["🏢 Room Floor Spawns:"]
                for f_key in sorted(all_floors.keys()):
                    f_data = all_floors[f_key]
                    f_label = position.format_floor_name(f_key)
                    cmd_hint = f"/f{f_key}" if f_key.isdigit() else f"/floor {f_key}"
                    lines.append(f"  • {f_label}: {position.format_position(f_data)} -> Use {cmd_hint}")
                lines.append("Set or update with: !setfloor <floor>")
                await reply("\n".join(lines))

        elif cmd in ("setfloor", "addfloor"):
            if not args:
                await reply("Usage: !setfloor <floor> (e.g. !setfloor f1, !setfloor f2), or !setfloor <floor> <x> <y> <z> [facing]")
                return

            floor_input = args[0]
            floor_key = position.normalize_floor_key(floor_input)
            floor_label = position.format_floor_name(floor_key)

            # Option A: Manual coordinates e.g. !setfloor f1 12.5 0.0 14.5 [FrontRight]
            if len(args) >= 4:
                try:
                    x = float(args[1])
                    y = float(args[2])
                    z = float(args[3])
                    facing = args[4] if len(args) > 4 else "FrontRight"
                    new_pos = position.save_manual_floor_position(config.ROOM_ID, floor_key, x, y, z, facing)
                    cmd_hint = f"/f{floor_key}" if floor_key.isdigit() else f"/floor {floor_key}"
                    await reply(f"📍 {floor_label} spawn set manually to {position.format_position(new_pos)}!\nPlayers can now type {cmd_hint} to teleport here.")
                    return
                except ValueError:
                    await reply("Invalid coordinates. Usage: !setfloor <floor> <x> <y> <z> [facing]")
                    return

            # Option B: Capture caller's current location
            user_pos = await self._get_user_position(user)
            if not user_pos:
                await reply(f"Could not detect your position @{user.username}. Try taking a step, then type !setfloor {floor_input} again!")
                return

            if isinstance(user_pos, AnchorPosition):
                await reply("⚠️ You are currently sitting on furniture. Please stand on the floor where players should spawn, then type !setfloor again!")
                return

            position.save_floor_position(config.ROOM_ID, floor_key, user_pos)
            cmd_hint = f"/f{floor_key}" if floor_key.isdigit() else f"/floor {floor_key}"
            await reply(
                f"📍 {floor_label} spawn saved at your location: {position.format_position(user_pos)}!\n"
                f"✅ Players can now type {cmd_hint} to teleport here anytime."
            )

        elif cmd in ("delfloor", "clearfloor", "removefloor"):
            if not args:
                await reply("Usage: !delfloor <floor> (e.g. !delfloor f1, !delfloor f2)")
                return
            floor_input = args[0]
            floor_key = position.normalize_floor_key(floor_input)
            floor_label = position.format_floor_name(floor_key)
            if position.delete_floor_position(config.ROOM_ID, floor_key):
                await reply(f"🗑️ Removed {floor_label} spawn point.")
            else:
                await reply(f"Floor '{floor_input}' was not found in saved floors for this room.")

        # ---------------- POSITION & MOVEMENT COMMANDS ----------------
        elif cmd in ("setpos", "setposition", "stayhere"):
            # Option A: Manual coordinates e.g. !setpos 10.5 0.0 14.2 [FrontRight]
            if len(args) >= 3:
                try:
                    x = float(args[0])
                    y = float(args[1])
                    z = float(args[2])
                    facing = args[3] if len(args) > 3 else "FrontRight"
                    new_pos = position.save_manual_position(config.ROOM_ID, x, y, z, facing)
                    await self.move_bot_to(new_pos)
                    await reply(f"📍 Bot position set manually: {position.format_position(new_pos)}!\nBot will return here automatically on restart.")
                    return
                except ValueError:
                    await reply("Invalid coordinates. Usage: !setpos <x> <y> <z> [facing], or just stand somewhere and type !setpos")
                    return

            # Option B: Capture caller's current standing/sitting location
            user_pos = await self._get_user_position(user)
            if not user_pos:
                await reply(f"Could not detect your position @{user.username}. Try taking a step or jumping, then type !setpos again!")
                return

            position.save_room_position(config.ROOM_ID, user_pos)
            await self.move_bot_to(user_pos)
            await reply(
                f"📍 Bot position set to your location: {position.format_position(user_pos)}!\n"
                f"✅ Saved! Bot will now stay here and auto-spawn at this exact spot on every reconnect/restart."
            )

        elif cmd in ("mypos", "pos"):
            user_pos = await self._get_user_position(user)
            if user_pos:
                await reply(f"📍 @{user.username}'s Position: {position.format_position(user_pos)}")
            else:
                await reply(f"Could not detect your position @{user.username}. Try walking a step, then try !mypos.")

        elif cmd == "botpos":
            saved = position.get_saved_position(config.ROOM_ID)
            if saved:
                await reply(f"🤖 Bot Saved Spawn: {position.format_position(saved)} (Room: {config.ROOM_ID})")
            else:
                await reply(f"🤖 Bot Spawn: Default Room Entrance (no custom position saved for room {config.ROOM_ID}). Stand anywhere and type !setpos to set one!")

        elif cmd in ("resetpos", "clearpos"):
            cleared = position.clear_room_position(config.ROOM_ID)
            if cleared:
                await reply("🗑️ Custom bot position cleared for this room! Bot will spawn at the default entrance on next start.")
            else:
                await reply("No custom position was saved for this room.")

        elif cmd in ("come", "bring"):
            user_pos = await self._get_user_position(user)
            if user_pos:
                await self.move_bot_to(user_pos)
                await reply(f"On my way, @{user.username}! 🏃")
            else:
                await reply(f"Could not determine your position, @{user.username}.")

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
            target_arg = args[0].lstrip("@").lower() if args else ""
            if target_arg in ("bot", "dj", "raydjbot"):
                # Bring bot to caller
                user_pos = await self._get_user_position(user)
                if user_pos:
                    await self.move_bot_to(user_pos)
                    await reply(f"Teleported bot to @{user.username}! ⚡")
                else:
                    await reply(f"Could not determine your position, @{user.username}.")
                return

            try:
                users_resp = await self.highrise.get_room_users()
                if not isinstance(users_resp, GetRoomUsersRequest.GetRoomUsersResponse):
                    await reply("Failed to fetch room users.")
                    return

                target_user = None
                bot_pos = None
                target_name = target_arg if target_arg else user.username.lower()

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

        # ---------------- OUTFIT & WARDROBE COMMANDS ----------------
        elif cmd in ("fit", "outfit"):
            if not args or args[0].lower() in ("help", "info"):
                presets_dict = outfit.list_available_presets()
                preset_names = ", ".join(presets_dict.keys())
                await reply(
                    f"👗 Bot Wardrobe Commands:\n"
                    f"• !fit <preset> - Change dress (Presets: {preset_names})\n"
                    f"• !copyfit [@user] - Clone someone's outfit\n"
                    f"• !fit save <name> - Save current look as a preset\n"
                    f"• !fit load <name> - Equip saved preset\n"
                    f"• !fit list - View all presets with details\n"
                    f"• !wardrobe - Check owned items in bot inventory"
                )
                return

            subcmd = args[0].lower()

            if subcmd == "list":
                presets_dict = outfit.list_available_presets()
                lines = ["👗 Available Outfits & Presets:"]
                for p_name, p_desc in presets_dict.items():
                    lines.append(f"  • {p_name}: {p_desc}")
                lines.append("Wear one using: !fit <name>")
                await reply("\n".join(lines))
                return

            if subcmd == "copy":
                target_str = args[1] if len(args) > 1 else user.username
                await self._handle_copyfit(user, target_str, reply)
                return

            if subcmd == "save":
                if len(args) < 2:
                    await reply("Usage: !fit save <preset_name>")
                    return
                save_name = args[1].lower().strip()
                try:
                    my_outfit_resp = await self.highrise.get_my_outfit()
                    if hasattr(my_outfit_resp, "outfit") and my_outfit_resp.outfit:
                        outfit.save_custom_preset(save_name, my_outfit_resp.outfit)
                        await reply(f"💾 Saved current outfit as '{save_name}'! Wear it anytime with !fit {save_name}")
                    else:
                        await reply("Could not retrieve current outfit to save.")
                except Exception as e:
                    logger.error(f"Error saving outfit preset: {e}")
                    await reply(f"Failed to save outfit: {e}")
                return

            if subcmd == "load":
                if len(args) < 2:
                    await reply("Usage: !fit load <preset_name>")
                    return
                preset_name = args[1].lower().strip()
            else:
                preset_name = subcmd

            # Equip preset
            new_outfit = outfit.get_full_preset(preset_name)
            if not new_outfit:
                await reply(f"Unknown preset '{preset_name}'. Type !fit list to see all available outfits.")
                return

            try:
                res = await self.highrise.set_outfit(new_outfit)
                if isinstance(res, Error):
                    await reply(f"⚠️ Failed to equip '{preset_name}': {res.message}")
                else:
                    outfit.save_active_outfit(new_outfit)
                    try:
                        await self.highrise.send_emote("dance-shoppingcart")
                    except Exception:
                        pass
                    await reply(f"✨ Outfit changed to '{preset_name}'! Looking stylish.")
            except Exception as e:
                logger.error(f"Error setting outfit: {e}")
                await reply(f"Error equipping outfit: {e}")

        elif cmd in ("copyfit", "copy"):
            target_str = args[0] if args else user.username
            await self._handle_copyfit(user, target_str, reply)

        elif cmd in ("wardrobe", "inventory"):
            try:
                inv_resp = await self.highrise.get_inventory()
                if hasattr(inv_resp, "items"):
                    items_count = len(inv_resp.items)
                    await reply(f"🎒 Bot Wardrobe: {items_count} clothing items in inventory. (Use !fit to change dress)")
                else:
                    await reply("Could not access bot inventory.")
            except Exception as e:
                logger.error(f"Error fetching inventory: {e}")
                await reply(f"Failed to inspect wardrobe: {e}")
