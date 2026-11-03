"""blackjack.py - Highrise Multiplayer/Single-player Blackjack Game Engine

Features:
- Standard 52-card deck with auto-reshuffle
- Real-time Highrise chat card formatting with suit symbols
- Dynamic Ace evaluation (1 or 11)
- Natural 21 Blackjack detection (3:2 payout)
- Hit, Stand, and Double Down mechanics
- Dealer hits on soft 16, stands on 17
- Tie/Push bet refunds & Win credits directly to casino.py
- Inactivity auto-stand after 60 seconds
"""
import random
import time
import asyncio
import logging
from typing import Optional
import casino

logger = logging.getLogger("HighriseBlackjack")

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


class Card:
    def __init__(self, rank: str, suit: str):
        self.rank = rank
        self.suit = suit

    @property
    def value(self) -> int:
        if self.rank in ("J", "Q", "K"):
            return 10
        elif self.rank == "A":
            return 11
        return int(self.rank)

    def __repr__(self) -> str:
        return f"{self.rank}{self.suit}"


class Deck:
    def __init__(self):
        self.cards: list[Card] = []
        self.reset()

    def reset(self):
        self.cards = [Card(r, s) for s in SUITS for r in RANKS]
        random.shuffle(self.cards)

    def draw(self) -> Card:
        if len(self.cards) < 10:
            self.reset()
        return self.cards.pop()


class Hand:
    def __init__(self):
        self.cards: list[Card] = []

    def add_card(self, card: Card):
        self.cards.append(card)

    @property
    def total(self) -> int:
        score = sum(c.value for c in self.cards)
        aces = sum(1 for c in self.cards if c.rank == "A")
        while score > 21 and aces > 0:
            score -= 10
            aces -= 1
        return score

    @property
    def is_blackjack(self) -> bool:
        return len(self.cards) == 2 and self.total == 21

    @property
    def is_bust(self) -> bool:
        return self.total > 21

    def format_cards(self, hide_second: bool = False) -> str:
        if not self.cards:
            return "[]"
        if hide_second and len(self.cards) >= 2:
            return f"[{self.cards[0]} 🂠] (Showing {self.cards[0].value})"
        cards_str = " ".join(str(c) for c in self.cards)
        return f"[{cards_str}] (Total: {self.total})"


class BlackjackGame:
    def __init__(
        self,
        player_id: str,
        player_name: str,
        bet_amount: int,
        bet_source: str = "wallet"
    ):
        self.player_id = str(player_id)
        self.player_name = str(player_name)
        self.bet_amount = int(bet_amount)
        self.bet_source = str(bet_source)
        self.deck = Deck()
        self.player_hand = Hand()
        self.dealer_hand = Hand()
        self.status = "INITIALIZING"  # PLAYER_TURN, DEALER_TURN, FINISHED
        self.outcome = ""             # WIN, LOSS, PUSH, BLACKJACK, BUST
        self.payout_amount = 0
        self.created_at = time.time()
        self.last_action_at = time.time()

    def initial_deal(self) -> str:
        """Deals 2 cards to player and 2 cards to dealer. Checks natural Blackjacks."""
        self.player_hand.add_card(self.deck.draw())
        self.dealer_hand.add_card(self.deck.draw())
        self.player_hand.add_card(self.deck.draw())
        self.dealer_hand.add_card(self.deck.draw())
        self.last_action_at = time.time()

        # Check for natural Blackjack (21 on initial 2 cards)
        player_bj = self.player_hand.is_blackjack
        dealer_bj = self.dealer_hand.is_blackjack

        if player_bj and dealer_bj:
            self.status = "FINISHED"
            self.outcome = "PUSH"
            self.payout_amount = self.bet_amount
            casino.refund_bet(self.player_id, self.bet_amount, self.bet_source)
            return (
                f"🃏 Double Blackjack Push!\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"🤖 Dealer: {self.dealer_hand.format_cards()}\n"
                f"🤝 Both have Blackjack! Bet of {self.bet_amount} Gold refunded."
            )
        elif player_bj:
            self.status = "FINISHED"
            self.outcome = "BLACKJACK"
            # 3:2 payout: Bet 10 -> Wins 25 total (15 profit + 10 back)
            self.payout_amount = int(self.bet_amount * 2.5)
            casino.credit_winnings(self.player_id, self.payout_amount)
            return (
                f"🔥 NATURAL BLACKJACK! 🎉\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"🤖 Dealer: {self.dealer_hand.format_cards()}\n"
                f"🏆 3:2 Payout! You won {self.payout_amount} Gold added directly to your Winnings!"
            )
        elif dealer_bj:
            self.status = "FINISHED"
            self.outcome = "LOSS"
            self.payout_amount = 0
            return (
                f"💀 Dealer has Natural Blackjack!\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"🤖 Dealer: {self.dealer_hand.format_cards()}\n"
                f"Dealer wins. You lost {self.bet_amount} Gold."
            )

        self.status = "PLAYER_TURN"

        # Check if player hit 21 with normal cards
        if self.player_hand.total == 21:
            return self.stand()

        return (
            f"🃏 Blackjack Round Started! (Bet: {self.bet_amount} Gold)\n"
            f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
            f"🤖 Dealer: {self.dealer_hand.format_cards(hide_second=True)}\n"
            f"👉 Type /b hit to take card, /b stand to hold, or /b double to double down!"
        )

    def hit(self) -> str:
        """Player draws a card."""
        if self.status != "PLAYER_TURN":
            return "⚠️ It is not your turn to hit."

        card = self.deck.draw()
        self.player_hand.add_card(card)
        self.last_action_at = time.time()

        if self.player_hand.is_bust:
            self.status = "FINISHED"
            self.outcome = "BUST"
            self.payout_amount = 0
            return (
                f"💥 BUST! You drew {card}!\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"🤖 Dealer: {self.dealer_hand.format_cards()}\n"
                f"❌ Hand over 21. You lost {self.bet_amount} Gold."
            )

        if self.player_hand.total == 21:
            return f"🎯 Drew {card}! You hit 21!\n" + self.stand()

        return (
            f"📥 @{self.player_name} drew {card}!\n"
            f"👤 Your Hand: {self.player_hand.format_cards()}\n"
            f"🤖 Dealer Showing: {self.dealer_hand.format_cards(hide_second=True)}\n"
            f"👉 Type /b hit or /b stand"
        )

    def double_down(self) -> str:
        """Double the bet, take exactly 1 card, then stand."""
        if self.status != "PLAYER_TURN":
            return "⚠️ It is not your turn to double down."
        if len(self.player_hand.cards) != 2:
            return "⚠️ You can only double down on your first two cards!"

        # Try to deduct second half of bet
        success, err, _ = casino.deduct_bet(self.player_id, self.bet_amount)
        if not success:
            return f"⚠️ Cannot double down: {err}"

        self.bet_amount *= 2
        card = self.deck.draw()
        self.player_hand.add_card(card)
        self.last_action_at = time.time()

        if self.player_hand.is_bust:
            self.status = "FINISHED"
            self.outcome = "BUST"
            self.payout_amount = 0
            return (
                f"💥 DOUBLE DOWN BUST! Drew {card}!\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"🤖 Dealer: {self.dealer_hand.format_cards()}\n"
                f"❌ You lost {self.bet_amount} Gold."
            )

        double_msg = f"⚡ @{self.player_name} Doubled Down to {self.bet_amount} Gold and drew {card}!\n"
        return double_msg + self.stand()

    def stand(self) -> str:
        """End player's turn and let dealer play."""
        if self.status not in ("PLAYER_TURN", "INITIALIZING"):
            return "⚠️ Game is already resolved."

        self.status = "DEALER_TURN"
        self.last_action_at = time.time()

        # Dealer draws on soft 16, stands on 17+
        dealer_draws = []
        while self.dealer_hand.total < 17:
            c = self.deck.draw()
            self.dealer_hand.add_card(c)
            dealer_draws.append(str(c))

        self.status = "FINISHED"
        p_total = self.player_hand.total
        d_total = self.dealer_hand.total

        dealer_line = f"🤖 Dealer: {self.dealer_hand.format_cards()}"
        if dealer_draws:
            dealer_line += f" (Drew: {', '.join(dealer_draws)})"

        # Resolve Outcome
        if self.dealer_hand.is_bust:
            self.outcome = "WIN"
            self.payout_amount = self.bet_amount * 2
            casino.credit_winnings(self.player_id, self.payout_amount)
            return (
                f"🎉 DEALER BUSTED ({d_total})!\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"{dealer_line}\n"
                f"🏆 YOU WIN! +{self.payout_amount} Gold credited directly to your Winnings!"
            )
        elif p_total > d_total:
            self.outcome = "WIN"
            self.payout_amount = self.bet_amount * 2
            casino.credit_winnings(self.player_id, self.payout_amount)
            return (
                f"🏆 YOU WIN ({p_total} vs {d_total})! 🎉\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"{dealer_line}\n"
                f"✨ +{self.payout_amount} Gold credited directly to your Winnings!"
            )
        elif p_total == d_total:
            self.outcome = "PUSH"
            self.payout_amount = self.bet_amount
            casino.refund_bet(self.player_id, self.bet_amount, self.bet_source)
            return (
                f"🤝 PUSH / TIE ({p_total} vs {d_total})!\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"{dealer_line}\n"
                f"Your bet of {self.bet_amount} Gold has been refunded to your wallet."
            )
        else:
            self.outcome = "LOSS"
            self.payout_amount = 0
            return (
                f"💀 DEALER WINS ({d_total} vs {p_total})!\n"
                f"👤 @{self.player_name}: {self.player_hand.format_cards()}\n"
                f"{dealer_line}\n"
                f"Dealer takes the round. You lost {self.bet_amount} Gold."
            )


# ---------------- ACTIVE GAME MANAGER ----------------

class BlackjackManager:
    def __init__(self):
        self.active_games: dict[str, BlackjackGame] = {}  # user_id -> game
        self._watchdog_running = False

    def get_game(self, user_id: str) -> Optional[BlackjackGame]:
        return self.active_games.get(str(user_id))

    def start_game(
        self,
        user_id: str,
        username: str,
        bet: int
    ) -> tuple[bool, str]:
        """Start a new game for user if none is active."""
        user_str = str(user_id)
        if user_str in self.active_games:
            existing = self.active_games[user_str]
            if existing.status == "PLAYER_TURN":
                return (
                    False,
                    f"⚠️ You already have an active game! (Hand: {existing.player_hand.format_cards()}). Type /b hit or /b stand to finish it."
                )
            else:
                del self.active_games[user_str]

        if bet < casino.DEFAULT_MIN_BET:
            return False, f"⚠️ Minimum bet is {casino.DEFAULT_MIN_BET} Gold."
        if bet > casino.DEFAULT_MAX_BET:
            return False, f"⚠️ Maximum bet is {casino.DEFAULT_MAX_BET} Gold."

        # Deduct bet from casino funds
        success, err, deducted_source = casino.deduct_bet(user_id, bet)
        if not success:
            return False, f"⚠️ {err}"

        game = BlackjackGame(user_id, username, bet, deducted_source)
        result_msg = game.initial_deal()

        if game.status == "FINISHED":
            # Hand was instant blackjack or push
            if user_str in self.active_games:
                del self.active_games[user_str]
        else:
            self.active_games[user_str] = game

        return True, result_msg

    def play(self, user_id: str, username: str, bet: int) -> str:
        """Convenience single-string entrypoint for playing a hand."""
        _, msg = self.start_game(user_id, username, bet)
        return msg

    def hit(self, user_id: str) -> str:
        user_str = str(user_id)
        game = self.active_games.get(user_str)
        if not game or game.status != "PLAYER_TURN":
            return "⚠️ You don't have an active Blackjack game. Type /b play <bet> to start!"

        msg = game.hit()
        if game.status == "FINISHED":
            self.active_games.pop(user_str, None)
        return msg

    def stand(self, user_id: str) -> str:
        user_str = str(user_id)
        game = self.active_games.get(user_str)
        if not game or game.status != "PLAYER_TURN":
            return "⚠️ You don't have an active Blackjack game. Type /b play <bet> to start!"

        msg = game.stand()
        self.active_games.pop(user_str, None)
        return msg

    def double_down(self, user_id: str) -> str:
        user_str = str(user_id)
        game = self.active_games.get(user_str)
        if not game or game.status != "PLAYER_TURN":
            return "⚠️ You don't have an active Blackjack game. Type /b play <bet> to start!"

        msg = game.double_down()
        if game.status == "FINISHED":
            self.active_games.pop(user_str, None)
        return msg

    async def auto_stand_watchdog(self, bot_instance):
        """Monitors for inactive player turns and automatically stands after 60s."""
        while True:
            await asyncio.sleep(15)
            now = time.time()
            expired_users = []
            for u_id, game in list(self.active_games.items()):
                if game.status == "PLAYER_TURN" and (now - game.last_action_at) > 60:
                    expired_users.append((u_id, game))

            for u_id, game in expired_users:
                logger.info(f"[Blackjack] Inactivity timeout for @{game.player_name}. Auto-standing...")
                res_msg = game.stand()
                self.active_games.pop(u_id, None)
                try:
                    await bot_instance.highrise.chat(f"⏰ Turn timer expired for @{game.player_name} (Auto-Stood):\n{res_msg}")
                except Exception as e:
                    logger.debug(f"[Blackjack] Auto-stand announcement notice: {e}")


blackjack_manager = BlackjackManager()
