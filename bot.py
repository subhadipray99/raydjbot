import os
import json
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
from highrise.models import GetRoomUsersRequest, GetWalletRequest, Error, RoomPermissions
import config
from music import music_manager
import outfit
import position
import tips
from bot_status import BotStatus

logger = logging.getLogger("HighriseBot")

WELCOME_MESSAGES_FILE = os.path.join(os.path.dirname(__file__), "welcome_messages.json")


def get_custom_welcome(room_id: str) -> str | None:
    """Retrieve custom welcome message for a room if configured."""
    if not os.path.exists(WELCOME_MESSAGES_FILE):
        return None
    try:
        with open(WELCOME_MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get(room_id)
    except Exception:
        return None


def set_custom_welcome(room_id: str, text: str) -> None:
    """Save custom welcome greeting for a room."""
    data = {}
    if os.path.exists(WELCOME_MESSAGES_FILE):
        try:
            with open(WELCOME_MESSAGES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[room_id] = text.strip()
    try:
        with open(WELCOME_MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving welcome message: {e}")
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import db
            asyncio.create_task(db.save_room_attribute(room_id, "custom_welcome", text.strip()))
    except Exception:
        pass


def delete_custom_welcome(room_id: str) -> bool:
    """Reset custom welcome greeting for a room to default."""
    if not os.path.exists(WELCOME_MESSAGES_FILE):
        return False
    try:
        with open(WELCOME_MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if room_id in data:
            del data[room_id]
            with open(WELCOME_MESSAGES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
    except Exception:
        pass
    return False


# Comprehensive map of friendly command names to Highrise emote identifiers (68+ emotes)
EMOTE_MAP = {
    # Dances
    "macarena": "dance-macarena",
    "dance": "dance-macarena",
    "orangejuice": "dance-orangejuice",
    "shopping": "dance-shoppingcart",
    "russian": "dance-russian",
    "pennywise": "dance-pennywise",
    "casual": "idle-dance-casual",
    "tiktok8": "dance-tiktok8",
    "tiktok2": "dance-tiktok2",
    "weird": "dance-weird",
    "floss": "dance-floss",
    "blackpink": "dance-blackpink",
    "pudgy": "dance-pudgy",
    "vogue": "dance-voguehands",
    "aerobics": "dance-aerobics",
    "metal": "dance-metal",
    "singing": "dance-singing",
    "smooch": "dance-smooch",
    "icecream": "dance-icecream",
    # Poses & Action Emotes
    "wave": "emote-wave",
    "hello": "emote-hello",
    "tired": "emote-tired",
    "float": "emote-float",
    "teleport": "emote-teleporting",
    "curtsey": "emote-curtsey",
    "bow": "emote-bow",
    "snowball": "emote-snowball",
    "snowangel": "emote-snowangel",
    "confused": "emote-confused",
    "charging": "emote-charging",
    "hot": "emote-hot",
    "laugh": "emote-laughing",
    "celebrate": "emoji-celebrate",
    "thumbsup": "emoji-thumbsup",
    "angry": "emoji-angry",
    "scared": "emoji-scared",
    # Fun & Moves
    "frog": "emote-frog",
    "zombie": "emote-zombie",
    "cutey": "emote-cutey",
    "split": "emote-splitsdrop",
    "peekaboo": "emote-peekaboo",
    "lust": "emote-lust",
    "hyped": "emote-hyped",
    "punch": "emote-superpunch",
    "maniac": "emote-maniac",
    "energy": "emote-energyball",
    "creepycute": "emote-creepycute",
    "headball": "emote-headball",
    "guitar": "idle-guitar",
    "fashion": "emote-fashionista",
    "gravity": "emote-gravity",
    "propose": "emote-proposing",
    "boxer": "emote-boxer",
    "flex": "emote-flex",
    "roll": "emote-roll",
    "boo": "emote-boo",
    "yes": "emote-yes",
    "no": "emote-no",
    "sad": "emote-sad",
    "kiss": "emote-kiss",
    "giggle": "emote-giggle",
    "model": "emote-model",
    "pose1": "emote-pose1",
    "pose3": "emote-pose3",
    "pose5": "emote-pose5",
    "pose7": "emote-pose7",
    "pose8": "emote-pose8",
    "rope": "emote-ropepull",
}

# Emote animation durations in seconds for seamless repeating loop
EMOTE_DURATIONS = {
    "dance-macarena": 11.0,
    "dance-orangejuice": 9.0,
    "dance-shoppingcart": 4.5,
    "dance-russian": 10.0,
    "dance-pennywise": 4.5,
    "idle-dance-casual": 9.0,
    "dance-tiktok8": 11.0,
    "dance-tiktok2": 10.5,
    "dance-weird": 21.5,
    "dance-floss": 21.5,
    "dance-blackpink": 7.0,
    "dance-pudgy": 12.0,
    "dance-voguehands": 9.5,
    "dance-aerobics": 8.5,
    "dance-metal": 13.5,
    "dance-singing": 10.5,
    "dance-smooch": 6.5,
    "dance-icecream": 14.5,
    "emote-wave": 3.0,
    "emote-hello": 3.0,
    "emote-tired": 4.5,
    "emote-float": 9.0,
    "emote-teleporting": 11.5,
    "emote-curtsey": 2.5,
    "emote-bow": 3.5,
    "emote-snowball": 5.5,
    "emote-snowangel": 6.5,
    "emote-confused": 9.0,
    "emote-charging": 8.0,
    "emote-hot": 4.5,
    "emote-laughing": 3.0,
    "emoji-celebrate": 3.5,
    "emoji-thumbsup": 2.5,
    "emoji-angry": 6.0,
    "emoji-scared": 3.5,
    "emote-frog": 14.5,
    "emote-zombie": 13.0,
    "emote-cutey": 3.0,
    "emote-splitsdrop": 4.5,
    "emote-peekaboo": 3.5,
    "emote-lust": 4.5,
    "emote-hyped": 7.0,
    "emote-superpunch": 3.5,
    "emote-maniac": 5.0,
    "emote-energyball": 7.5,
    "emote-creepycute": 8.0,
    "emote-headball": 10.5,
    "idle-guitar": 13.5,
    "emote-fashionista": 5.5,
    "emote-gravity": 9.0,
    "emote-proposing": 4.5,
    "emote-boxer": 5.5,
    "emote-flex": 3.0,
    "emote-roll": 4.0,
    "emote-boo": 4.5,
    "emote-yes": 2.5,
    "emote-no": 2.5,
    "emote-sad": 5.5,
    "emote-kiss": 3.0,
    "emote-giggle": 3.0,
    "emote-model": 6.0,
    "emote-pose1": 3.0,
    "emote-pose3": 5.0,
    "emote-pose5": 4.5,
    "emote-pose7": 4.5,
    "emote-pose8": 5.0,
    "emote-ropepull": 8.5,
}


class HighriseBot(BaseBot):
    def __init__(self):
        super().__init__()
        self.bot_id: str | None = None
        self.session_metadata: SessionMetadata | None = None
        self._has_announced: bool = False
        self.user_positions: dict[str, Position | AnchorPosition] = {}
        self.user_emote_loops: dict[str, asyncio.Task] = {}
        self.welcomed_users: set[str] = set()

    async def _apply_saved_position_delayed(self, pos: Position | AnchorPosition, delay: float = 1.0) -> None:
        """Wait briefly after room connect for world registration, then move bot to saved spot."""
        await asyncio.sleep(delay)
        success = await self.move_bot_to(pos)
        if success:
            logger.info(f"Successfully relocated bot to saved position: {position.format_position(pos)}")
        else:
            logger.warning(f"Failed to relocate bot to saved position: {position.format_position(pos)}")

    async def _subscription_watchdog_loop(self) -> None:
        """Periodically check if this room's hosting subscription is still active."""
        await asyncio.sleep(60)  # Wait 1 minute after initial startup
        while True:
            try:
                import db
                # Query registered bot for current room
                bots = await db._get("bots", params={"room_id": f"eq.{config.ROOM_ID}", "select": "user_id,is_active"})
                if bots and isinstance(bots, list) and len(bots) > 0:
                    bot_record = bots[0]
                    user_id = bot_record.get("user_id")
                    is_active = bot_record.get("is_active", True)
                    if user_id:
                        sub = await db.get_user_subscription(user_id)
                        if not sub.get("is_active") or not is_active:
                            logger.warning(f"[Bot] Subscription expired for room {config.ROOM_ID} (User: {user_id}). Entering sleep mode.")
                            try:
                                await self.highrise.chat("⚠️ The 24/7 DJ Bot subscription for this room has expired. Entering sleep mode. Renew at zevray.com/deploy to reactivate.")
                            except Exception:
                                pass
                            BotStatus.is_connected = False
                            break
            except Exception as e:
                logger.debug(f"[Bot] Subscription watchdog notice: {e}")
            await asyncio.sleep(600)  # Check every 10 minutes

    async def _start_user_emote_loop(self, user: User, emote_id: str, emote_name: str, reply, target_user_id: str | None = None) -> None:
        """Continuously loop an emote for a user until /stop or new emote."""
        target_id = target_user_id or user.id

        # Cancel any existing loop task for this user
        if target_id in self.user_emote_loops:
            self.user_emote_loops[target_id].cancel()
            del self.user_emote_loops[target_id]

        duration = EMOTE_DURATIONS.get(emote_id, 5.0)
        # Sleep slightly less than animation length for a seamless loop, min 2.5s
        loop_interval = max(2.5, duration - 0.2)

        async def _loop_runner():
            try:
                while True:
                    try:
                        await self.highrise.send_emote(emote_id, target_id)
                    except Exception as err:
                        logger.debug(f"Loop emote error for {target_id}: {err}")
                    await asyncio.sleep(loop_interval)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Loop runner exited for {target_id}: {e}")

        task = asyncio.create_task(_loop_runner())
        self.user_emote_loops[target_id] = task
        await reply(f"💃 Now looping '{emote_name}' for @{user.username}! Type /stop to cancel anytime.")

    async def move_bot_to(self, target_pos: Position | AnchorPosition) -> bool:
        """Move or teleport the bot to target position, falling back gracefully to walk_to."""
        try:
            if isinstance(target_pos, Position) and self.bot_id:
                try:
                    await self.highrise.teleport(self.bot_id, target_pos)
                    return True
                except Exception as tp_err:
                    logger.warning(f"Teleport bot failed ({tp_err}), falling back to walk_to")
            if isinstance(target_pos, Position):
                await self.highrise.walk_to(target_pos)
                return True
            elif isinstance(target_pos, AnchorPosition) and self.bot_id:
                await self.highrise.teleport(self.bot_id, target_pos)
                return True
        except Exception as e:
            logger.error(f"Error moving bot to {target_pos}: {e}")
            return False

    async def is_admin(self, user: User) -> bool:
        """Check if user is the room owner, moderator, or designer."""
        if not user or not user.username:
            return False
        u_name = user.username.lower()
        owner_name = getattr(config, "ROOM_OWNER", "shuvodip").lower()
        if u_name in ("shuvodip", "subhadipray99", owner_name):
            return True
        if str(user.id) in ("63f3bbdf56dab79de656aea7", getattr(config, "ROOM_OWNER_ID", "")):
            return True
        try:
            priv = await self.highrise.get_room_privilege(user.id)
            if isinstance(priv, RoomPermissions):
                if priv.moderator or priv.designer:
                    return True
        except Exception as e:
            logger.debug(f"Could not check room privilege for {user.username}: {e}")
        return False

    async def _get_user_position(self, target_user: User) -> Position | AnchorPosition | None:
        """Find the freshest position for a user directly from the live Highrise server."""
        try:
            response = await self.highrise.get_room_users()
            if isinstance(response, GetRoomUsersRequest.GetRoomUsersResponse):
                for room_user, pos in response.content:
                    self.user_positions[room_user.id] = pos
                    if room_user.id == target_user.id:
                        return pos
        except Exception as e:
            logger.error(f"Error fetching user position from server: {e}")

        # Fallback to cached position only if live server query fails
        return self.user_positions.get(target_user.id)

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
        import time as _t
        BotStatus.is_connected = True
        BotStatus.bot_id = self.bot_id
        BotStatus.connected_at = _t.time()
        logger.info(f"Bot successfully connected to room! Bot User ID: {self.bot_id}")

        # Start the music queue runner with this bot instance for room announcements
        music_manager.start(self)

        # Hydrate persistent room state from Supabase if available
        try:
            import db
            cloud_state = await db.load_room_state(config.ROOM_ID)
            if cloud_state:
                # 1. Position
                if cloud_state.get("saved_position") and isinstance(cloud_state["saved_position"], dict):
                    p_data = cloud_state["saved_position"]
                    all_pos = position.load_all_positions()
                    all_pos[config.ROOM_ID] = p_data
                    position.save_all_positions(all_pos)
                # 2. Floors
                if cloud_state.get("floors") and isinstance(cloud_state["floors"], dict):
                    all_fl = position.load_all_floors()
                    all_fl[config.ROOM_ID] = cloud_state["floors"]
                    position.save_all_floors(all_fl)
                # 3. Custom Welcome
                if cloud_state.get("custom_welcome"):
                    set_custom_welcome(config.ROOM_ID, str(cloud_state["custom_welcome"]))
                # 4. Outfit
                act_outfit = cloud_state.get("active_outfit")
                if act_outfit and isinstance(act_outfit, list) and len(act_outfit) > 0:
                    outfit_data = outfit._load_data()
                    if "active_by_room" not in outfit_data:
                        outfit_data["active_by_room"] = {}
                    outfit_data["active_by_room"][config.ROOM_ID] = act_outfit
                    outfit_data["active"] = act_outfit
                    outfit._save_data(outfit_data)
        except Exception as hyd_err:
            logger.debug(f"[DB] Hydration notice: {hyd_err}")

        # Restore saved active outfit on connect (ensures !fit and !copyfit persist across room reloads)
        saved_outfit = outfit.get_active_outfit(config.ROOM_ID)
        if saved_outfit and len(saved_outfit) > 0:
            try:
                res = await self.highrise.set_outfit(saved_outfit)
                if isinstance(res, Error):
                    logger.warning(f"Could not restore saved outfit on start: {res.message}")
                else:
                    logger.info(f"Successfully restored saved bot outfit ({len(saved_outfit)} items).")
            except Exception as e:
                logger.warning(f"Error restoring outfit on start: {e}")
        else:
            # First-time run or no active outfit saved yet: capture current avatar from Highrise and persist it!
            try:
                my_outfit_resp = await self.highrise.get_my_outfit()
                if hasattr(my_outfit_resp, "outfit") and my_outfit_resp.outfit:
                    await outfit.save_active_outfit_async(config.ROOM_ID, my_outfit_resp.outfit)
                    logger.info(f"Auto-captured and persisted initial bot outfit ({len(my_outfit_resp.outfit)} items) to Supabase.")
            except Exception as e:
                logger.debug(f"Initial outfit auto-capture notice: {e}")

        # Restore saved room position if available
        saved_pos = position.get_saved_position(config.ROOM_ID)
        if saved_pos:
            logger.info(f"Found saved position for room {config.ROOM_ID}: {position.format_position(saved_pos)}. Relocating bot...")
            asyncio.create_task(self._apply_saved_position_delayed(saved_pos))

        # Start periodic subscription validity watchdog
        asyncio.create_task(self._subscription_watchdog_loop())

        # Only broadcast welcome message once on initial startup, not on every reconnect
        if not self._has_announced:
            await self.highrise.chat(f"🤖 {config.BOT_DISPLAY_NAME} is online! Type /play <song> to queue music.")
            self._has_announced = True
        else:
            logger.info("Bot reconnected silently (avoiding chat spam).")

    async def on_user_join(self, user: User, pos: Position | AnchorPosition | None = None) -> None:
        """Triggered when a user joins the room."""
        try:
            logger.info(f"User joined: {user.username} (ID: {user.id})")
            if pos:
                self.user_positions[user.id] = pos
            if user.id == self.bot_id:
                return

            self.welcomed_users.add(user.id)

            # 1. Public room chat welcome greeting (ensures user immediately sees the welcome on screen!)
            custom_welcome = get_custom_welcome(config.ROOM_ID)
            if custom_welcome:
                welcome_chat = f"Welcome @{user.username}! {custom_welcome}"
            else:
                welcome_chat = f"Welcome to {config.ROOM_TITLE}, @{user.username}! 👋 Type /help for commands or /play to request songs."

            if len(welcome_chat) > 240:
                welcome_chat = welcome_chat[:237] + "..."

            try:
                await self.highrise.chat(welcome_chat)
            except Exception as e:
                logger.warning(f"Error sending room chat welcome greeting to {user.username}: {e}")

            # 2. Detailed private whisper guide (under 250 character limit)
            w1 = (
                "🎶 Music Commands:\n"
                "• /play <song> (or /p) - Request song\n"
                "• /queue (or /q) - View queue\n"
                "• /np - Now playing"
            )

            all_floors = position.get_all_floors(config.ROOM_ID)
            if all_floors:
                num_keys = sorted([int(k) for k in all_floors.keys() if k.isdigit()])
                named_keys = sorted([k for k in all_floors.keys() if not k.isdigit()])
                floor_items = []
                for k in num_keys:
                    floor_items.append(f"/f{k} (Ground)" if k == 1 else f"/f{k}")
                for k in named_keys:
                    floor_items.append(f"/{k}")
                floors_line = " | ".join(floor_items)
                floor_section = f"🏢 Floor Teleport:\n• {floors_line}\n\n"
            else:
                floor_section = ""

            w2 = (
                f"{floor_section}"
                "💃 Emotes & Loop:\n"
                "• /emotes - View full list of 68+ emotes\n"
                "• /emote <name> - Loop emote continuously\n"
                "• /stop - Stop your emote loop"
            )

            try:
                await asyncio.sleep(0.5)
                await self.highrise.send_whisper(user.id, w1)
                await asyncio.sleep(0.5)
                await self.highrise.send_whisper(user.id, w2)
            except Exception as e:
                logger.warning(f"Error sending welcome whisper to {user.username}: {e}")

            # Wave at the user
            try:
                await self.highrise.send_emote("emote-wave")
            except Exception as e:
                logger.debug(f"Could not send wave emote on join: {e}")
        except Exception as err:
            logger.error(f"Error handling on_user_join for {user.username}: {err}", exc_info=True)

    async def on_user_move(self, user: User, destination: Position | AnchorPosition) -> None:
        """Track user positions in real-time as they walk or sit."""
        self.user_positions[user.id] = destination

    async def on_user_leave(self, user: User) -> None:
        """Triggered when a user leaves the room."""
        logger.info(f"User left: {user.username} (ID: {user.id})")
        self.user_positions.pop(user.id, None)
        self.welcomed_users.discard(user.id)
        if user.id in self.user_emote_loops:
            self.user_emote_loops[user.id].cancel()
            del self.user_emote_loops[user.id]

    async def on_reaction(self, user: User, reaction: Reaction, receiver: User) -> None:
        """Echo reactions back to users if they react to the bot."""
        if receiver.id == self.bot_id and user.id != self.bot_id:
            logger.info(f"Received reaction '{reaction}' from {user.username}")
            try:
                await self.highrise.react(reaction, user.id)
            except Exception as e:
                logger.error(f"Error sending reaction back: {e}")

    async def on_tip(self, sender: User, receiver: User, tip: CurrencyItem | Item) -> None:
        """Triggered when someone tips the bot in the room."""
        if receiver.id == self.bot_id:
            if isinstance(tip, CurrencyItem):
                amount = int(tip.amount)
                currency = str(tip.type)
                is_new_top, top_info = tips.record_tip(
                    config.ROOM_ID, sender.id, sender.username, amount, currency
                )
                if is_new_top:
                    msg = (
                        f"🎉 WOW! Huge thank you @{sender.username} for the tip of {amount} {currency}! ❤️\n"
                        f"👑 @{sender.username} is now the #1 TOP TIPPER in the room!"
                    )
                else:
                    msg = f"Thank you so much @{sender.username} for the generous tip of {amount} {currency}! ❤️"
            else:
                msg = f"Thank you so much @{sender.username} for the gift tip! ❤️"

            logger.info(f"Tip received: {msg}")
            await self.highrise.chat(msg)
            try:
                await self.highrise.send_emote("emoji-celebrate")
            except Exception:
                try:
                    await self.highrise.send_emote("dance-pennywise")
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
            # Check for friendly chat greetings like "hi", "hello", "hey"
            clean_msg = message.strip().lower().rstrip("!.,")
            if clean_msg in ("hi", "hello", "hey", "hii", "hiii", "yo"):
                try:
                    await self.highrise.send_emote("emote-wave")
                    await self.highrise.chat(f"Hey @{user.username}! Welcome to {config.ROOM_TITLE}. Type /play <song> or /emotes!")
                except Exception:
                    pass
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
                await outfit.save_active_outfit_async(config.ROOM_ID, target_outfit)
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

        user_is_admin = await self.is_admin(user)

        # ---------------- MUSIC COMMANDS ----------------
        if cmd in ("play", "p"):
            if not args:
                await reply(f"Usage: /play <song name or YouTube URL>")
                return

            query = " ".join(args)
            await reply(f"🔍 Searching music for: \"{query}\"...")
            success, msg, track = await music_manager.search_and_queue(query, user.username)
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
                    f"👤 Requested by: {req_info}"
                )
            else:
                await reply("No music is currently playing. Use /play <song> to start!")

        elif cmd in ("player", "radio", "jukebox"):
            await reply("📻 Custom Room Radio is streaming live 24/7.")

        # ---------------- ROOM & BOT COMMANDS ----------------
        elif cmd in ("help", "commands"):
            all_floors = position.get_all_floors(config.ROOM_ID)
            if all_floors:
                num_keys = sorted([int(k) for k in all_floors.keys() if k.isdigit()])
                named_keys = sorted([k for k in all_floors.keys() if not k.isdigit()])
                floor_cmd_lines = []
                for k in num_keys:
                    floor_cmd_lines.append(f"• /f{k} (or /ground) - Teleport to Floor 1" if k == 1 else f"• /f{k} - Teleport to Floor {k}")
                for k in named_keys:
                    floor_cmd_lines.append(f"• /{k} - Teleport to {k.capitalize()}")
                floor_cmd_lines.append("• /floor <name/num> - Teleport to any floor")
                floor_cmd_lines.append("• /floors - List all floor spawns")
                floors_help_section = "🏢 Floor Teleport Commands:\n" + "\n".join(floor_cmd_lines) + "\n\n"
            else:
                floors_help_section = ""

            help_text = (
                f"🤖 {config.BOT_DISPLAY_NAME} Commands:\n"
                f"Welcome to {config.ROOM_TITLE}. This is an automated message.\n\n"
                f"🎶 Music Commands:\n"
                f"• /play <song> (or /p) - Request a song (takes top priority!)\n"
                f"• /skip (or /s) - Skip to next song in queue\n"
                f"• /queue (or /q) - View upcoming songs in queue\n"
                f"• /np - Check currently playing song\n\n"
                f"{floors_help_section}"
                f"✨ Emotes & Fun:\n"
                f"• !top - View Top Tippers leaderboard 🏆\n"
                f"• !emote <name> - Play emote\n"
                f"• !emotes - List popular emotes\n"
                f"• !wallet - Check bot tip wallet\n"
                f"• !ping - Bot status\n\n"
                f"{config.ROOM_CREDITS}\n"
                f"(Admins: Type !admin for setup & room management commands)"
            )
            await reply(help_text)

        elif cmd in ("admin", "setup"):
            admin_text = (
                f"⚙️ Admin & Setup Commands (Owner: @{config.ROOM_OWNER}):\n"
                f"• !fit <preset> - Change bot dress (Presets: dj, suit, street, camo, goth, chill, puffer)\n"
                f"• !copyfit [@user] - Clone someone's outfit onto bot\n"
                f"• !fit save <name> | !fit load <name> | !fit list\n"
                f"• !wardrobe - Check owned items in bot inventory\n"
                f"• !setpos [front/left/right/back] - Set bot spot to your location\n"
                f"• !turn - Rotate bot 90 degrees\n"
                f"• !face <front|left|right|back> - Turn bot direction\n"
                f"• !botpos - Show bot saved position\n"
                f"• !resetpos - Reset bot to room entrance\n"
                f"• !setfloor <name> - Set floor spawn (e.g. !setfloor f1, !setfloor f2)\n"
                f"• !delfloor <name> - Remove a floor spawn\n"
                f"• !settoptipper <user> [gold] - Set top tipper manually\n"
                f"• !cleartippers - Reset tip leaderboard\n"
                f"• !setwelcome <message> - Customize welcome greeting\n"
                f"• !resetwelcome - Reset welcome greeting to default\n"
                f"• /autoplay on|off - Toggle radio background autoplay"
            )
            await reply(admin_text)

        elif cmd in ("setwelcome", "welcome"):
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            if not args:
                current_w = get_custom_welcome(config.ROOM_ID)
                if current_w:
                    await reply(f"Current welcome: \"{current_w}\"\nTo change: !setwelcome <message>\nTo reset: !resetwelcome")
                else:
                    await reply(f"Default welcome: \"Welcome to {config.ROOM_TITLE}. This is an automated message.\"\nTo change: !setwelcome <message>")
                return
            custom_msg = " ".join(args).strip()
            set_custom_welcome(config.ROOM_ID, custom_msg)
            await reply(f"✅ Welcome message saved! New visitors will now receive:\n\"{custom_msg}\"")

        elif cmd in ("resetwelcome", "clearwelcome"):
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            if delete_custom_welcome(config.ROOM_ID):
                await reply(f"✅ Welcome message reset to default: \"Welcome to {config.ROOM_TITLE}. This is an automated message.\"")
            else:
                await reply("Welcome message was already using default.")

        elif cmd == "ping":
            await reply(f"Pong! 🏓 {config.BOT_DISPLAY_NAME} is active and running 24/7.")

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
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for room admins and owner (@{config.ROOM_OWNER}).")
                return
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
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for room admins and owner (@{config.ROOM_OWNER}).")
                return
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
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            # Option A: Manual coordinates e.g. !setpos 10.5 0.0 14.2 [FrontRight]
            if len(args) >= 3:
                try:
                    x = float(args[0])
                    y = float(args[1])
                    z = float(args[2])
                    facing = args[3] if len(args) > 3 else "FrontRight"
                    new_pos = await position.save_manual_position_async(config.ROOM_ID, x, y, z, facing)
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

            # Check if caller passed a desired facing direction (e.g. !setpos front, !setpos left)
            desired_facing = None
            if args:
                f_arg = args[0].lower().strip()
                if f_arg in ("front", "right", "frontright", "fr"):
                    desired_facing = "FrontRight"
                elif f_arg in ("left", "frontleft", "fl"):
                    desired_facing = "FrontLeft"
                elif f_arg in ("back", "backleft", "bl"):
                    desired_facing = "BackLeft"
                elif f_arg in ("backright", "br"):
                    desired_facing = "BackRight"

            if isinstance(user_pos, Position):
                # Highrise avatars face away from camera when walking to booth.
                # Default to FrontRight so the bot looks forward at the room/audience!
                if desired_facing:
                    final_facing = desired_facing
                elif user_pos.facing in ("BackLeft", "BackRight"):
                    final_facing = "FrontRight" if user_pos.facing == "BackLeft" else "FrontLeft"
                else:
                    final_facing = user_pos.facing or "FrontRight"

                target_pos = Position(
                    x=user_pos.x,
                    y=user_pos.y,
                    z=user_pos.z,
                    facing=final_facing
                )
            else:
                target_pos = user_pos

            await position.save_room_position_async(config.ROOM_ID, target_pos)
            await self.move_bot_to(target_pos)
            pos_env = f"{target_pos.x:.2f},{target_pos.y:.2f},{target_pos.z:.2f},{target_pos.facing}" if isinstance(target_pos, Position) else ""
            await reply(
                f"📍 Bot position set to your location: {position.format_position(target_pos)}!\n"
                f"✅ Saved! Bot will now stay here and auto-spawn at this exact spot on every reconnect/restart.\n"
                f"💡 Tip: Type !turn or !face front/left/right/back to rotate the bot anytime!"
            )

        elif cmd in ("face", "turn", "rotate"):
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            saved_pos = position.get_saved_position(config.ROOM_ID)
            if not saved_pos or not isinstance(saved_pos, Position):
                bot_user_pos = None
                try:
                    resp = await self.highrise.get_room_users()
                    if isinstance(resp, GetRoomUsersRequest.GetRoomUsersResponse):
                        for r_u, r_p in resp.content:
                            if r_u.id == self.bot_id and isinstance(r_p, Position):
                                bot_user_pos = r_p
                                break
                except Exception:
                    pass
                saved_pos = bot_user_pos

            if not saved_pos or not isinstance(saved_pos, Position):
                await reply("Could not determine bot position to rotate. Stand where you want the bot and type !setpos first!")
            else:
                cycle_map = {
                    "FrontRight": "FrontLeft",
                    "FrontLeft": "BackLeft",
                    "BackLeft": "BackRight",
                    "BackRight": "FrontRight",
                }
                target_arg = args[0].lower().strip() if args else ""
                if target_arg in ("fr", "frontright", "front_right", "right", "front"):
                    new_facing = "FrontRight"
                elif target_arg in ("fl", "frontleft", "front_left", "left"):
                    new_facing = "FrontLeft"
                elif target_arg in ("br", "backright", "back_right"):
                    new_facing = "BackRight"
                elif target_arg in ("bl", "backleft", "back_left", "back"):
                    new_facing = "BackLeft"
                else:
                    current_facing = saved_pos.facing or "BackLeft"
                    new_facing = cycle_map.get(current_facing, "FrontRight")

                new_pos = Position(
                    x=saved_pos.x,
                    y=saved_pos.y,
                    z=saved_pos.z,
                    facing=new_facing
                )
                await position.save_room_position_async(config.ROOM_ID, new_pos)
                await self.move_bot_to(new_pos)
                await reply(f"🔄 Bot turned! Now facing: {new_facing}. (Saved to room config)")

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
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            cleared = await position.clear_room_position_async(config.ROOM_ID)
            if cleared:
                await reply("🗑️ Custom bot position cleared for this room! Bot will spawn at the default entrance on next start.")
            else:
                await reply("No custom position was saved for this room.")

        elif cmd in ("come", "bring"):
            if not user_is_admin:
                await reply(f"🔒 Moving the bot is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            user_pos = await self._get_user_position(user)
            if user_pos:
                await self.move_bot_to(user_pos)
                await reply(f"On my way, @{user.username}! 🏃")
            else:
                await reply(f"Could not determine your position, @{user.username}.")

        elif cmd in ("emote", "e", "loop"):
            if not args or args[0].lower() in ("list", "all", "help"):
                await self.handle_command(user, "emotes", [], is_whisper)
                return

            emote_query = args[0].lower()
            if emote_query in ("stop", "off", "cancel"):
                await self.handle_command(user, "stop", [], is_whisper)
                return

            # Lookup emote in comprehensive map or partial match
            emote_id = EMOTE_MAP.get(emote_query, None)
            matched_name = emote_query
            if not emote_id:
                for key, eid in EMOTE_MAP.items():
                    if emote_query == key or emote_query in key:
                        emote_id = eid
                        matched_name = key
                        break
            if not emote_id:
                # Direct emote-id fallback
                emote_id = emote_query

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

            if len(args) > 1 and args[1].lower() in ("bot", "dj"):
                target_user_id = self.bot_id

            await self._start_user_emote_loop(user, emote_id, matched_name, reply, target_user_id=target_user_id)

        elif cmd in ("stop", "stoploop"):
            target_id = user.id
            if args and args[0].lower() in ("bot", "dj") and user_is_admin:
                target_id = self.bot_id

            if target_id in self.user_emote_loops:
                self.user_emote_loops[target_id].cancel()
                del self.user_emote_loops[target_id]
                await reply(f"⏹️ Emote loop stopped for @{user.username}!")
            else:
                await reply(f"You don't have an active emote loop running, @{user.username}. Type /emotes to see all!")

        elif cmd in ("emotes", "emotelist"):
            # Send the complete list of 68+ emotes grouped by category in whispers
            chunk1 = (
                "💃 Dances (1/4):\n"
                "• macarena, orangejuice, shopping, russian, pennywise, casual, weird, "
                "floss, blackpink, pudgy, vogue, aerobics, metal, singing, smooch, icecream, tiktok8, tiktok2"
            )
            chunk2 = (
                "✨ Poses & Classic (2/4):\n"
                "• wave, hello, tired, float, teleport, curtsey, bow, snowball, snowangel, "
                "confused, charging, hot, laugh, celebrate, thumbsup, angry, scared, boo, yes, no, sad"
            )
            chunk3 = (
                "⚡ Moves & Fun (3/4):\n"
                "• frog, zombie, cutey, split, peekaboo, lust, hyped, punch, maniac, energy, "
                "creepycute, headball, guitar, fashion, gravity, propose, boxer"
            )
            chunk4 = (
                "🔥 More Emotes (4/4):\n"
                "• flex, roll, kiss, giggle, model, rope, pose1, pose3, pose5, pose7, pose8\n\n"
                "💡 Type /emote <name> to loop! Type /stop to cancel."
            )
            try:
                await self.highrise.send_whisper(user.id, chunk1)
                await asyncio.sleep(0.3)
                await self.highrise.send_whisper(user.id, chunk2)
                await asyncio.sleep(0.3)
                await self.highrise.send_whisper(user.id, chunk3)
                await asyncio.sleep(0.3)
                await self.highrise.send_whisper(user.id, chunk4)
            except Exception as e:
                logger.warning(f"Error whispering emotes list to {user.username}: {e}")

            if not is_whisper:
                await reply(f"✨ Whispered full list of {len(EMOTE_MAP)} emotes to you, @{user.username}! Type /emote <name> to loop, or /stop to cancel.")

        elif cmd in ("teleport", "tp"):
            target_arg = args[0].lstrip("@").lower() if args else ""
            if target_arg in ("bot", "dj", "raydjbot"):
                if not user_is_admin:
                    await reply(f"🔒 Moving the bot is reserved for the room owner (@{config.ROOM_OWNER}).")
                    return
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

        elif cmd in ("tippers", "top", "toptippers", "leaderboard"):
            top_tippers = tips.get_top_tippers_list(config.ROOM_ID, limit=5)
            if not top_tippers:
                await reply("🏆 Top Tippers: No tips recorded yet! Click on the bot and tap 'Tip' to take the #1 spot!")
            else:
                lines = ["🏆 Room Top Tippers Leaderboard:"]
                medals = ["🥇", "🥈", "🥉", "4.", "5."]
                for idx, t in enumerate(top_tippers):
                    medal = medals[idx] if idx < len(medals) else f"{idx+1}."
                    gold_str = f"{t.get('total_gold', 0)} gold"
                    lines.append(f"  {medal} @{t['username']} - {gold_str}")
                lines.append("Tip the bot to climb the leaderboard! ❤️")
                await reply("\n".join(lines))

        elif cmd == "settoptipper":
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            if not args:
                await reply("Usage: !settoptipper <username> [gold_amount]")
                return
            target_name = args[0]
            gold_amount = int(args[1]) if len(args) > 1 and args[1].isdigit() else 100
            res = tips.set_manual_top_tipper(config.ROOM_ID, target_name, gold_amount)
            await reply(f"🏆 Top tipper manually set to @{res['username']} ({res['total_gold']} gold)!")

        elif cmd in ("cleartippers", "resettippers"):
            if not user_is_admin:
                await reply(f"🔒 This command is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            tips.clear_room_tips(config.ROOM_ID)
            await reply("🗑️ Room tip leaderboard has been reset.")

        elif cmd == "say":
            if not args:
                await reply("Usage: !say <message>")
                return
            to_say = " ".join(args)
            await self.highrise.chat(to_say)

        # ---------------- OUTFIT & WARDROBE COMMANDS (OWNER / ADMIN ONLY) ----------------
        elif cmd in ("fit", "outfit", "savefit"):
            if not user_is_admin:
                await reply(f"🔒 Outfit commands are reserved for the room owner (@{config.ROOM_OWNER}).")
                return

            # If user types !savefit, or !fit (no args), or !fit save (with no preset name) -> SAVE CURRENT LOOK!
            if cmd == "savefit" or not args or (len(args) == 1 and args[0].lower() == "save"):
                try:
                    my_outfit_resp = await self.highrise.get_my_outfit()
                    if hasattr(my_outfit_resp, "outfit") and my_outfit_resp.outfit:
                        await outfit.save_active_outfit_async(config.ROOM_ID, my_outfit_resp.outfit)
                        try:
                            await self.highrise.send_emote("emote-pose1")
                        except Exception:
                            pass
                        await reply(
                            f"👗 Current bot outfit saved permanently! ({len(my_outfit_resp.outfit)} items)\n"
                            f"✅ Bot will now wear this exact look across all room reloads and redeploys."
                        )
                    else:
                        await reply("⚠️ Could not retrieve current outfit from Highrise. Please try again.")
                except Exception as e:
                    logger.error(f"Error saving bot outfit: {e}")
                    await reply(f"⚠️ Error saving outfit: {e}")
                return

            if args[0].lower() in ("help", "info"):
                presets_dict = outfit.list_available_presets()
                preset_names = ", ".join(presets_dict.keys())
                await reply(
                    f"👗 Bot Wardrobe Commands (Owner: @{config.ROOM_OWNER}):\n"
                    f"• !fit - Save whatever the bot is currently wearing\n"
                    f"• !fit <preset> - Equip preset (Presets: {preset_names})\n"
                    f"• !copyfit [@user] - Clone someone's outfit onto bot\n"
                    f"• !fit save <name> - Save current look as a named preset\n"
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
                    await outfit.save_active_outfit_async(config.ROOM_ID, new_outfit)
                    try:
                        await self.highrise.send_emote("dance-shoppingcart")
                    except Exception:
                        pass
                    await reply(f"✨ Outfit changed to '{preset_name}'! Looking stylish.")
            except Exception as e:
                logger.error(f"Error setting outfit: {e}")
                await reply(f"Error equipping outfit: {e}")

        elif cmd in ("copyfit", "copy"):
            if not user_is_admin:
                await reply(f"🔒 Outfit commands are reserved for the room owner (@{config.ROOM_OWNER}).")
                return
            target_str = args[0] if args else user.username
            await self._handle_copyfit(user, target_str, reply)

        elif cmd in ("wardrobe", "inventory"):
            if not user_is_admin:
                await reply(f"🔒 Wardrobe inspection is reserved for the room owner (@{config.ROOM_OWNER}).")
                return
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
