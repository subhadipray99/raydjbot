"""casino.py - Ledger and wallet management for Highrise Bot Casino games (Blackjack, etc.)

Separates deposited playing chips (wallet) from earned winnings, and manages withdrawal requests
with room-host payout confirmation.
"""
import os
import json
import time
import logging
from typing import Optional, Any
import config

logger = logging.getLogger("HighriseCasino")

CASINO_WALLETS_FILE = os.path.join(os.path.dirname(__file__), "casino_wallets.json")
CASINO_PAYOUTS_FILE = os.path.join(os.path.dirname(__file__), "casino_payouts.json")

MINIMUM_WITHDRAWAL_GOLD = 50
DEFAULT_MIN_BET = 1
DEFAULT_MAX_BET = 100


def _load_json(file_path: str, default: Any) -> Any:
    if not os.path.exists(file_path):
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return default


def _save_json(file_path: str, data: Any) -> None:
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving {file_path}: {e}")


# ---------------- WALLET & WINNINGS MANAGEMENT ----------------

def get_player_profile(user_id: str, username: str = "") -> dict:
    """Retrieve or initialize player wallet & winnings ledger entry."""
    data = _load_json(CASINO_WALLETS_FILE, {})
    user_str = str(user_id)
    if user_str not in data:
        data[user_str] = {
            "user_id": user_str,
            "username": username or "Player",
            "wallet": 0,          # Deposited playable chips
            "winnings": 0,        # Earned from casino games (eligible for withdrawal)
            "total_deposited": 0,
            "total_wagered": 0,
            "total_won": 0,
            "games_played": 0,
            "games_won": 0,
            "created_at": int(time.time()),
            "last_active": int(time.time()),
        }
        _save_json(CASINO_WALLETS_FILE, data)
    elif username and data[user_str].get("username") != username:
        data[user_str]["username"] = username
        _save_json(CASINO_WALLETS_FILE, data)

    return data[user_str]


def deposit(user_id: str, username: str, amount: int) -> int:
    """Credit gold tipped to the bot or tip jar into user's playable wallet."""
    if amount <= 0:
        return 0
    data = _load_json(CASINO_WALLETS_FILE, {})
    user_str = str(user_id)

    # Check if existing profile matches by username if user_str not in data
    if user_str not in data and username:
        for uid, p in data.items():
            if p.get("username", "").lower() == username.lower():
                user_str = uid
                break

    if user_str not in data:
        get_player_profile(user_str, username)
        data = _load_json(CASINO_WALLETS_FILE, {})

    prof = data[user_str]
    prof["username"] = username or prof.get("username", "Player")
    prof["wallet"] = int(prof.get("wallet", 0)) + int(amount)
    prof["total_deposited"] = int(prof.get("total_deposited", 0)) + int(amount)
    prof["last_active"] = int(time.time())
    _save_json(CASINO_WALLETS_FILE, data)
    _sync_to_db_async()
    logger.info(f"[Casino] Credited {amount} Gold to @{username} (ID: {user_str}). Wallet now: {prof['wallet']}")
    return prof["wallet"]


def get_balance(user_id: str) -> tuple[int, int]:
    """Returns (wallet_chips, winnings_gold)."""
    prof = get_player_profile(user_id)
    return int(prof.get("wallet", 0)), int(prof.get("winnings", 0))


def get_wallet(user_id: str) -> int:
    """Convenience getter for user's playing chips."""
    return get_balance(user_id)[0]


def get_winnings(user_id: str) -> int:
    """Convenience getter for user's earned winnings."""
    return get_balance(user_id)[1]


def get_all_wallets() -> dict:
    """Retrieve full raw dictionary of player wallets."""
    return _load_json(CASINO_WALLETS_FILE, {})


def set_wallet(user_id: str, username: str, amount: int) -> int:
    """Admin alias for setting wallet chips."""
    return admin_set_wallet(user_id, username, amount)


def deduct_bet(user_id: str, amount: int, username: str = "") -> tuple[bool, str, str]:
    """
    Deduct bet from user's funds.
    Prefers playable wallet chips first; if wallet is insufficient, draws from winnings.
    Returns (success, message, deducted_source).
    """
    if amount <= 0:
        return False, "Bet amount must be at least 1 Gold.", ""

    data = _load_json(CASINO_WALLETS_FILE, {})
    user_str = str(user_id)
    prof = data.get(user_str)

    # If not found by user_id, check by username fallback
    if not prof and username:
        for uid, p in data.items():
            if p.get("username", "").lower() == username.lower():
                prof = p
                user_str = uid
                break

    if not prof:
        return False, "You do not have any gold in your wallet. Tip the bot or room tip jar to deposit chips!", ""

    wallet = int(prof.get("wallet", 0))
    winnings = int(prof.get("winnings", 0))
    total_avail = wallet + winnings

    if total_avail < amount:
        return (
            False,
            f"Insufficient funds! You have {wallet} in wallet chips and {winnings} in winnings (Total: {total_avail} Gold). Tip the bot to deposit more!",
            ""
        )

    deducted_source = "wallet"
    if wallet >= amount:
        prof["wallet"] = wallet - amount
        deducted_source = "wallet"
    else:
        # Draw all from wallet, remainder from winnings
        rem = amount - wallet
        prof["wallet"] = 0
        prof["winnings"] = winnings - rem
        deducted_source = "mixed"

    prof["total_wagered"] = int(prof.get("total_wagered", 0)) + amount
    prof["games_played"] = int(prof.get("games_played", 0)) + 1
    prof["last_active"] = int(time.time())
    _save_json(CASINO_WALLETS_FILE, data)
    _sync_to_db_async()
    return True, "", deducted_source


def refund_bet(user_id: str, amount: int, target_source: str = "wallet") -> None:
    """Refunds a tied/cancelled bet back to player."""
    if amount <= 0:
        return
    data = _load_json(CASINO_WALLETS_FILE, {})
    user_str = str(user_id)
    if user_str in data:
        prof = data[user_str]
        if target_source == "winnings":
            prof["winnings"] = int(prof.get("winnings", 0)) + amount
        else:
            prof["wallet"] = int(prof.get("wallet", 0)) + amount
        _save_json(CASINO_WALLETS_FILE, data)
        _sync_to_db_async()


def credit_winnings(user_id: str, amount: int) -> int:
    """Credit winnings from a game victory directly to user's winnings dataset."""
    if amount <= 0:
        return 0
    data = _load_json(CASINO_WALLETS_FILE, {})
    user_str = str(user_id)
    if user_str not in data:
        get_player_profile(user_id)
        data = _load_json(CASINO_WALLETS_FILE, {})

    prof = data[user_str]
    prof["winnings"] = int(prof.get("winnings", 0)) + int(amount)
    prof["total_won"] = int(prof.get("total_won", 0)) + int(amount)
    prof["games_won"] = int(prof.get("games_won", 0)) + 1
    prof["last_active"] = int(time.time())
    _save_json(CASINO_WALLETS_FILE, data)
    _sync_to_db_async()
    logger.info(f"[Casino] Credited {amount} Gold WINNINGS to {prof.get('username')} (ID: {user_id}). Winnings now: {prof['winnings']}")
    return prof["winnings"]


def admin_set_wallet(user_id: str, username: str, amount: int) -> int:
    """Admin manual adjustment of wallet chips."""
    data = _load_json(CASINO_WALLETS_FILE, {})
    user_str = str(user_id)
    if user_str not in data:
        get_player_profile(user_id, username)
        data = _load_json(CASINO_WALLETS_FILE, {})
    data[user_str]["wallet"] = max(0, int(amount))
    if username:
        data[user_str]["username"] = username
    _save_json(CASINO_WALLETS_FILE, data)
    _sync_to_db_async()
    return data[user_str]["wallet"]


# ---------------- WITHDRAWAL & PAYOUT SYSTEM ----------------

def request_withdrawal(arg1: str, arg2: str, arg3: Any, arg4: Any = None) -> tuple[bool, str, Optional[dict]]:
    """
    Submit a withdrawal request for earned winnings.
    Accepts either (user_id, username, amount) or (room_id, user_id, username, amount).
    Requires at least MINIMUM_WITHDRAWAL_GOLD (50 Gold).
    Deducts immediately from winnings and logs pending payout for host confirmation.
    """
    if arg4 is not None:
        room_id, user_id, username, amount = str(arg1), str(arg2), str(arg3), int(arg4)
    else:
        room_id, user_id, username, amount = getattr(config, "ROOM_ID", ""), str(arg1), str(arg2), int(arg3)

    if amount < MINIMUM_WITHDRAWAL_GOLD:
        return (
            False,
            f"⚠️ Minimum withdrawal amount is {MINIMUM_WITHDRAWAL_GOLD} Gold. You requested {amount} Gold.",
            None
        )

    data = _load_json(CASINO_WALLETS_FILE, {})
    user_str = str(user_id)
    if user_str not in data:
        return False, "You do not have any registered account data.", None

    prof = data[user_str]
    curr_winnings = int(prof.get("winnings", 0))
    if curr_winnings < amount:
        return (
            False,
            f"⚠️ Insufficient winnings! You have {curr_winnings} Gold in winnings available to withdraw (Requested: {amount} Gold).",
            None
        )

    # Deduct from winnings
    prof["winnings"] = curr_winnings - amount
    _save_json(CASINO_WALLETS_FILE, data)

    # Generate ticket ID e.g. WD-101
    payouts = _load_json(CASINO_PAYOUTS_FILE, {"counter": 100, "tickets": {}})
    next_num = int(payouts.get("counter", 100)) + 1
    payouts["counter"] = next_num
    ticket_id = f"WD-{next_num}"

    ticket = {
        "id": ticket_id,
        "room_id": room_id or getattr(config, "ROOM_ID", ""),
        "user_id": user_str,
        "username": username or prof.get("username", "Player"),
        "amount": amount,
        "status": "pending",  # pending, completed, cancelled
        "created_at": int(time.time()),
        "processed_at": None,
        "processed_by": None
    }

    if "tickets" not in payouts:
        payouts["tickets"] = {}
    payouts["tickets"][ticket_id] = ticket
    _save_json(CASINO_PAYOUTS_FILE, payouts)
    _sync_to_db_async()

    logger.info(f"[Casino Payout] Created withdrawal ticket {ticket_id} for @{ticket['username']}: {amount} Gold")
    return True, f"✅ Withdrawal request submitted! Ticket: #{ticket_id} for {amount} Gold.", ticket


def complete_payout(identifier: str = "", admin_name: str = "", ticket_id: Optional[str] = None, completed_by: Optional[str] = None) -> tuple[bool, str, Optional[dict]]:
    """
    Room Host confirms they manually tipped the player in-room.
    Identifier can be ticket ID (e.g. 'WD-101' or '101') or username (e.g. '@shuvodip').
    """
    payouts = _load_json(CASINO_PAYOUTS_FILE, {"counter": 100, "tickets": {}})
    tickets = payouts.get("tickets", {})

    target_lookup = (ticket_id or identifier or "").strip().upper()
    admin_str = completed_by or admin_name or "Host"
    if target_lookup.isdigit():
        target_lookup = f"WD-{target_lookup}"
    elif target_lookup.startswith("#"):
        target_lookup = target_lookup.lstrip("#")

    target_ticket = None
    target_id = None

    # 1. Search by exact ticket ID
    if target_lookup in tickets and tickets[target_lookup].get("status") == "pending":
        target_ticket = tickets[target_lookup]
        target_id = target_lookup
    else:
        # 2. Search by username or user_id for the oldest pending ticket
        clean_user = (identifier or "").lstrip("@").strip().lower()
        for t_id, t_data in tickets.items():
            if t_data.get("status") == "pending":
                if t_data.get("username", "").lower() == clean_user or str(t_data.get("user_id")) == clean_user:
                    target_ticket = t_data
                    target_id = t_id
                    break

    if not target_ticket:
        return False, f"No pending withdrawal ticket found for '{identifier or ticket_id}'. Type !payouts to list pending queue.", None

    target_ticket["status"] = "completed"
    target_ticket["processed_at"] = int(time.time())
    target_ticket["processed_by"] = admin_str
    _save_json(CASINO_PAYOUTS_FILE, payouts)
    _sync_to_db_async()

    logger.info(f"[Casino Payout] Ticket #{target_id} marked COMPLETED by {admin_str} ({target_ticket['amount']} Gold to @{target_ticket['username']})")
    return True, f"✅ Ticket #{target_id} confirmed! {target_ticket['amount']} Gold payout to @{target_ticket['username']} marked as completed.", target_ticket


def cancel_payout(identifier: str = "", admin_name: str = "", ticket_id: Optional[str] = None, reason: str = "") -> tuple[bool, str, Optional[dict]]:
    """
    Cancel a pending payout ticket and refund the gold back to the player's winnings.
    """
    payouts = _load_json(CASINO_PAYOUTS_FILE, {"counter": 100, "tickets": {}})
    tickets = payouts.get("tickets", {})

    target_lookup = (ticket_id or identifier or "").strip().upper()
    admin_str = admin_name or reason or "Host"
    if target_lookup.isdigit():
        target_lookup = f"WD-{target_lookup}"
    elif target_lookup.startswith("#"):
        target_lookup = target_lookup.lstrip("#")

    target_ticket = tickets.get(target_lookup)
    if not target_ticket or target_ticket.get("status") != "pending":
        return False, f"Pending ticket '{target_lookup}' was not found.", None

    # Refund gold to user's winnings
    user_id = target_ticket.get("user_id")
    amount = int(target_ticket.get("amount", 0))
    wallets = _load_json(CASINO_WALLETS_FILE, {})
    if user_id in wallets:
        wallets[user_id]["winnings"] = int(wallets[user_id].get("winnings", 0)) + amount
        _save_json(CASINO_WALLETS_FILE, wallets)

    target_ticket["status"] = "cancelled"
    target_ticket["processed_at"] = int(time.time())
    target_ticket["processed_by"] = admin_str
    _save_json(CASINO_PAYOUTS_FILE, payouts)
    _sync_to_db_async()

    logger.info(f"[Casino Payout] Ticket #{target_lookup} CANCELLED by {admin_str}. Refunded {amount} Gold to @{target_ticket.get('username')}.")
    return True, f"❌ Ticket #{target_lookup} cancelled. {amount} Gold refunded to @{target_ticket.get('username')}'s winnings.", target_ticket


def get_pending_payouts(room_id: str | None = None) -> list[dict]:
    """Retrieve list of all pending withdrawal requests."""
    payouts = _load_json(CASINO_PAYOUTS_FILE, {"counter": 100, "tickets": {}})
    tickets = payouts.get("tickets", {})
    pending = []
    for t in tickets.values():
        if t.get("status") == "pending":
            if not room_id or t.get("room_id") == room_id:
                pending.append(t)
    pending.sort(key=lambda x: x.get("created_at", 0))
    return pending


def get_casino_stats() -> dict:
    """Aggregate total deposits, wagered, won, and pending payouts."""
    wallets = _load_json(CASINO_WALLETS_FILE, {})
    total_wallet_pool = sum(w.get("wallet", 0) for w in wallets.values())
    total_winnings_pool = sum(w.get("winnings", 0) for w in wallets.values())
    total_wagered = sum(w.get("total_wagered", 0) for w in wallets.values())
    total_won = sum(w.get("total_won", 0) for w in wallets.values())
    total_games = sum(w.get("games_played", 0) for w in wallets.values())

    pending = get_pending_payouts()
    pending_payout_gold = sum(p.get("amount", 0) for p in pending)

    return {
        "total_players": len(wallets),
        "total_wallet_chips": total_wallet_pool,
        "total_winnings_balance": total_winnings_pool,
        "total_wagered": total_wagered,
        "total_won_by_players": total_won,
        "total_games_played": total_games,
        "pending_withdrawals_count": len(pending),
        "pending_withdrawals_gold": pending_payout_gold,
        "house_profit": total_wagered - total_won
    }


# ---------------- DATABASE SYNCHRONIZATION ----------------

def _sync_to_db_async():
    """Asynchronously persist casino state to Supabase in the background."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop and loop.is_running():
        loop.create_task(_sync_worker())


async def _sync_worker():
    try:
        import db
        rid = getattr(config, "ROOM_ID", "")
        if not rid:
            return
        wallets = _load_json(CASINO_WALLETS_FILE, {})
        payouts = _load_json(CASINO_PAYOUTS_FILE, {})

        # Embed inside top_tipper JSONB column to persist in Supabase without requiring table migrations
        cloud_state = await db.load_room_state(rid)
        curr_tipper = cloud_state.get("top_tipper") if cloud_state else {}
        if not isinstance(curr_tipper, dict):
            curr_tipper = {}
        curr_tipper["casino_wallets"] = wallets
        curr_tipper["casino_payouts"] = payouts
        await db.save_room_attribute(rid, "top_tipper", curr_tipper)
    except Exception as e:
        logger.debug(f"[Casino DB Sync] Notice: {e}")
