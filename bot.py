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
        
        # Only broadcast welcome message once on initial startup, not on every reconnect
        if not self._has_announced:
            await self.highrise.chat("Bot is now online! Type !help to see available commands.")
            self._has_announced = True
        else:
            logger.info("Bot reconnected silently (avoiding chat spam).")

    async def on_user_join(self, user: User, position: Position | AnchorPosition | None = None) -> None:
        """Triggered when a user joins the room."""
        logger.info(f"User joined: {user.username} (ID: {user.id})")
        # Don't welcome the bot itself
        if user.id == self.bot_id:
            return
        welcome = config.WELCOME_MESSAGE.format(username=user.username)
        await self.highrise.chat(welcome)
        # Greet with a wave emote
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

    async def on_chat(self, user: User, message: str) -> None:
        """Triggered on public room chat messages."""
        msg = message.strip()
        logger.info(f"[{user.username}]: {msg}")

        # Check for command prefix
        prefix = config.BOT_PREFIX
        if not msg.startswith(prefix):
            return

        command_body = msg[len(prefix):].strip()
        parts = command_body.split()
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        await self.handle_command(user, cmd, args, is_whisper=False)

    async def on_whisper(self, user: User, message: str) -> None:
        """Triggered on private whisper messages."""
        msg = message.strip()
        logger.info(f"[Whisper from {user.username}]: {msg}")

        prefix = config.BOT_PREFIX
        command_body = msg[len(prefix):].strip() if msg.startswith(prefix) else msg
        parts = command_body.split()
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        await self.handle_command(user, cmd, args, is_whisper=True)

    async def handle_command(self, user: User, cmd: str, args: list[str], is_whisper: bool = False) -> None:
        """Central command dispatcher."""
        async def reply(text: str):
            if is_whisper:
                await self.highrise.send_whisper(user.id, text)
            else:
                await self.highrise.chat(text)

        p = config.BOT_PREFIX

        if cmd in ("help", "commands"):
            help_text = (
                f"🤖 Available Commands:\n"
                f"{p}ping - Check bot latency\n"
                f"{p}come - Walk to your location\n"
                f"{p}emote <name> - Play an emote\n"
                f"{p}emotes - List available emotes\n"
                f"{p}teleport <user> - Teleport user to bot (requires mod)\n"
                f"{p}wallet - Show bot tip wallet\n"
                f"{p}say <text> - Bot echoes your message"
            )
            await reply(help_text)

        elif cmd == "ping":
            await reply(f"Pong! 🏓 Bot is active and running 24/7.")

        elif cmd == "come":
            # Find the user's position and walk to them
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
                await reply(f"Usage: {p}emote <name>. Type {p}emotes to view list.")
                return

            emote_query = args[0].lower()
            emote_id = EMOTE_MAP.get(emote_query, emote_query)

            # Check if a target username is provided (e.g., !emote dance @username)
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
            await reply(f"Popular emotes: {sample_emotes}... (Use: {p}emote <name>)")

        elif cmd in ("teleport", "tp"):
            # Requires designer/moderator privileges in the room
            try:
                users_resp = await self.highrise.get_room_users()
                if not isinstance(users_resp, GetRoomUsersRequest.GetRoomUsersResponse):
                    await reply("Failed to fetch room users.")
                    return

                # Determine target user
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
                await reply(f"Usage: {p}say <message>")
                return
            to_say = " ".join(args)
            await self.highrise.chat(to_say)
