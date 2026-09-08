# cogs/gambling.py

from __future__ import annotations

import asyncio
import math
import random
from typing import Any, Optional

import discord
from discord.ext import commands

from config import EMBED_COLOR
from utils.economy_db import EconomyDB, format_cash, format_cash_short, parse_bet


def text_display(content: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(content=content)


def small_separator() -> discord.ui.Separator:
    return discord.ui.Separator(spacing=discord.SeparatorSpacing.small)


def simple_view(content: str, *, timeout: int = 60) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_color=EMBED_COLOR)
    container.add_item(text_display(content))
    view.add_item(container)
    return view


# ============================================================
# LIVE-EDIT MINES (MATCHING image_7.png)
# ============================================================

class MinesView(discord.ui.LayoutView):
    def __init__(self, author_id: int, bet: int, bomb_count: int):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.bet = bet
        self.bomb_count = bomb_count
        self.revealed: set[int] = set()
        self.bombs: set[int] = set(random.sample(range(25), bomb_count))
        self.game_over = False
        self.won = False
        self.exploded_idx: int | None = None

        self._render()

    def current_multiplier(self) -> float:
        safe_hits = len(self.revealed)
        if safe_hits == 0:
            return 1.0
        # Authentic 97% RTP curve
        mult = 0.97 * (math.comb(25, safe_hits) / math.comb(25 - self.bomb_count, safe_hits))
        return round(mult, 2)

    def _render(self, busted: bool = False, cashed_out: bool = False):
        self.clear_items()
        container = discord.ui.Container(accent_color=EMBED_COLOR)

        rem_gems = (25 - self.bomb_count) - len(self.revealed)
        mult = self.current_multiplier()
        potential_win = int(self.bet * mult)

        if busted:
            header = (
                f"# 💥 BOOM! Game Over\n\n"
                f"You lost **{format_cash_short(self.bet)}** 🪙\n\n"
                f"💎 **Remaining Gems:** {rem_gems}\n"
                f"📈 **Multiplier:** x{mult:.2f}\n"
                f"💰 **Potential Win:** 🪙 {format_cash_short(potential_win)}"
            )
        elif cashed_out:
            header = (
                f"# 💰 Cashout Successful!\n\n"
                f"You won **{format_cash(potential_win)}** (x{mult:.2f})!\n\n"
                f"💎 **Gems Cleared:** {len(self.revealed)}\n"
                f"💣 **Bombs Avoided:** {self.bomb_count}"
            )
        else:
            header = (
                f"# 💎 Mines\n\n"
                f"Avoid the **{self.bomb_count}** bombs to win!\n\n"
                f"💎 **Remaining Gems:** {rem_gems}\n"
                f"📈 **Multiplier:** x{mult:.2f}\n"
                f"💰 **Potential Win:** 🪙 {format_cash_short(potential_win)}"
            )

        container.add_item(text_display(header))
        container.add_item(small_separator())

        # Render 5x5 Grid Buttons
        for row in range(5):
            btn_row = []
            for col in range(5):
                idx = row * 5 + col
                btn_row.append(MineTileButton(self, idx))
            container.add_item(discord.ui.ActionRow(*btn_row))

        if not self.game_over and len(self.revealed) > 0:
            container.add_item(small_separator())
            container.add_item(discord.ui.ActionRow(MinesCashoutButton(self)))

        self.add_item(container)


class MineTileButton(discord.ui.Button):
    def __init__(self, mines_view: MinesView, idx: int):
        self.mines_view = mines_view
        self.idx = idx

        if idx in mines_view.revealed:
            label = "🟢"
            style = discord.ButtonStyle.success
            disabled = True
        elif mines_view.game_over:
            if idx in mines_view.bombs:
                label = "🔴" if idx == mines_view.exploded_idx else "💣"
                style = discord.ButtonStyle.danger
            else:
                label = "🟢"
                style = discord.ButtonStyle.secondary
            disabled = True
        else:
            label = "\u200b"
            style = discord.ButtonStyle.secondary
            disabled = False

        super().__init__(label=label, style=style, disabled=disabled, row=idx // 5)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.mines_view.author_id:
            return await interaction.response.send_message("❌ This is not your game.", ephemeral=True)

        if self.idx in self.mines_view.bombs:
            self.mines_view.game_over = True
            self.mines_view.exploded_idx = self.idx
            await EconomyDB.modify_treasury(self.mines_view.bet)
            self.mines_view._render(busted=True)
        else:
            self.mines_view.revealed.add(self.idx)
            if len(self.mines_view.revealed) == (25 - self.mines_view.bomb_count):
                self.mines_view.game_over = True
                winnings = int(self.mines_view.bet * self.mines_view.current_multiplier())
                await EconomyDB.update_balance(
                    self.mines_view.author_id,
                    wallet=winnings,
                    description="Mines Full Sweep Win",
                )
                self.mines_view._render(cashed_out=True)
            else:
                self.mines_view._render()

        await interaction.response.edit_message(view=self.mines_view)


class MinesCashoutButton(discord.ui.Button):
    def __init__(self, mines_view: MinesView):
        super().__init__(label="💰 Cashout", style=discord.ButtonStyle.success)
        self.mines_view = mines_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.mines_view.author_id:
            return await interaction.response.send_message("❌ This is not your game.", ephemeral=True)

        self.mines_view.game_over = True
        winnings = int(self.mines_view.bet * self.mines_view.current_multiplier())
        await EconomyDB.update_balance(
            self.mines_view.author_id,
            wallet=winnings,
            description="Mines game cashout",
        )
        await EconomyDB.modify_treasury(self.mines_view.bet - winnings)
        self.mines_view._render(cashed_out=True)
        await interaction.response.edit_message(view=self.mines_view)


# ============================================================
# BLACKJACK SYSTEM
# ============================================================

CARD_VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
    "J": 10, "Q": 10, "K": 10, "A": 11,
}
SUITS = ["❤️", "♣️", "♦️", "♠️"]


def draw_card() -> tuple[str, str]:
    return random.choice(list(CARD_VALUES.keys())), random.choice(SUITS)


def hand_score(hand: list[tuple[str, str]]) -> int:
    score = sum(CARD_VALUES[r] for r, _ in hand)
    aces = sum(1 for r, _ in hand if r == "A")
    while score > 21 and aces:
        score -= 10
        aces -= 1
    return score


class BlackjackView(discord.ui.LayoutView):
    def __init__(self, author_id: int, bet: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.bet = bet
        self.player_hand = [draw_card(), draw_card()]
        self.dealer_hand = [draw_card(), draw_card()]
        self.game_over = False
        self._render()

    def _render(self, result_msg: str | None = None):
        self.clear_items()
        container = discord.ui.Container(accent_color=EMBED_COLOR)

        p_cards = ", ".join(f"{r}{s}" for r, s in self.player_hand)
        p_score = hand_score(self.player_hand)

        if not self.game_over:
            d_cards = f"{self.dealer_hand[0][0]}{self.dealer_hand[0][1]}"
            header = (
                f"# 🃏 Blackjack\n\n"
                f"**Your cards:** {p_cards} (Total: {p_score})\n"
                f"**Bots visible card:** {d_cards}\n"
                f"**Current bet:** {format_cash(self.bet)}"
            )
        else:
            d_cards = ", ".join(f"{r}{s}" for r, s in self.dealer_hand)
            d_score = hand_score(self.dealer_hand)
            header = (
                f"# 🃏 Blackjack\n\n"
                f"**Your cards:** {p_cards} (Total: {p_score})\n"
                f"**Dealer cards:** {d_cards} (Total: {d_score})\n"
                f"**Result:** {result_msg}\n"
                f"**Bet:** {format_cash(self.bet)}"
            )

        container.add_item(text_display(header))
        if not self.game_over:
            container.add_item(small_separator())
            container.add_item(
                discord.ui.ActionRow(
                    BlackjackHit(self),
                    BlackjackStay(self),
                    BlackjackDouble(self),
                )
            )

        self.add_item(container)


class BlackjackHit(discord.ui.Button):
    def __init__(self, bj_view: BlackjackView):
        super().__init__(label="Hit", style=discord.ButtonStyle.primary)
        self.bj_view = bj_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.bj_view.author_id:
            return await interaction.response.send_message("❌ Not your game.", ephemeral=True)

        self.bj_view.player_hand.append(draw_card())
        if hand_score(self.bj_view.player_hand) > 21:
            self.bj_view.game_over = True
            self.bj_view._render("💥 Busted! You lose.")
            await EconomyDB.modify_treasury(self.bj_view.bet)
        else:
            self.bj_view._render()
        await interaction.response.edit_message(view=self.bj_view)


class BlackjackStay(discord.ui.Button):
    def __init__(self, bj_view: BlackjackView):
        super().__init__(label="Stay", style=discord.ButtonStyle.secondary)
        self.bj_view = bj_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.bj_view.author_id:
            return await interaction.response.send_message("❌ Not your game.", ephemeral=True)

        self.bj_view.game_over = True
        while hand_score(self.bj_view.dealer_hand) < 17:
            self.bj_view.dealer_hand.append(draw_card())

        p_score = hand_score(self.bj_view.player_hand)
        d_score = hand_score(self.bj_view.dealer_hand)

        if d_score > 21 or p_score > d_score:
            winnings = self.bj_view.bet * 2
            await EconomyDB.update_balance(self.bj_view.author_id, wallet=winnings, description="Blackjack Win")
            await EconomyDB.modify_treasury(-self.bj_view.bet)
            self.bj_view._render(f"🎉 You won **{format_cash(winnings)}**!")
        elif p_score == d_score:
            await EconomyDB.update_balance(self.bj_view.author_id, wallet=self.bj_view.bet, description="Blackjack Push")
            self.bj_view._render("🤝 Push! Your bet was returned.")
        else:
            await EconomyDB.modify_treasury(self.bj_view.bet)
            self.bj_view._render("💀 Dealer wins! You lose.")

        await interaction.response.edit_message(view=self.bj_view)


class BlackjackDouble(discord.ui.Button):
    def __init__(self, bj_view: BlackjackView):
        super().__init__(label="Double Down", style=discord.ButtonStyle.success)
        self.bj_view = bj_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.bj_view.author_id:
            return await interaction.response.send_message("❌ Not your game.", ephemeral=True)

        user_data = await EconomyDB.get_user(self.bj_view.author_id)
        if user_data["wallet"] < self.bj_view.bet:
            return await interaction.response.send_message("❌ Insufficient funds to double down.", ephemeral=True)

        await EconomyDB.update_balance(self.bj_view.author_id, wallet=-self.bj_view.bet, description="Blackjack Double Down")
        self.bj_view.bet *= 2
        self.bj_view.player_hand.append(draw_card())

        self.bj_view.game_over = True
        if hand_score(self.bj_view.player_hand) > 21:
            self.bj_view._render("💥 Busted on double down! You lose.")
            await EconomyDB.modify_treasury(self.bj_view.bet)
        else:
            while hand_score(self.bj_view.dealer_hand) < 17:
                self.bj_view.dealer_hand.append(draw_card())
            p_score = hand_score(self.bj_view.player_hand)
            d_score = hand_score(self.bj_view.dealer_hand)

            if d_score > 21 or p_score > d_score:
                winnings = self.bj_view.bet * 2
                await EconomyDB.update_balance(self.bj_view.author_id, wallet=winnings, description="Blackjack Double Win")
                await EconomyDB.modify_treasury(-self.bj_view.bet)
                self.bj_view._render(f"🎉 Double down win! Paid **{format_cash(winnings)}**!")
            elif p_score == d_score:
                await EconomyDB.update_balance(self.bj_view.author_id, wallet=self.bj_view.bet, description="Blackjack Push")
                self.bj_view._render("🤝 Push! Doubled bet was returned.")
            else:
                await EconomyDB.modify_treasury(self.bj_view.bet)
                self.bj_view._render("💀 Dealer wins! You lose.")

        await interaction.response.edit_message(view=self.bj_view)


# ============================================================
# MULTIPLAYER CRASH
# ============================================================

class CrashLobbyView(discord.ui.LayoutView):
    def __init__(self, host_id: int):
        super().__init__(timeout=18)
        self.host_id = host_id
        self.players: dict[int, dict[str, Any]] = {}
        self.closed = False
        self._render()

    def _render(self):
        self.clear_items()
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        lines = [f"• <@{uid}>: **{format_cash(data['bet'])}**" for uid, data in self.players.items()]
        player_list = "\n".join(lines) if lines else "*No one has joined yet.*"

        container.add_item(
            text_display(
                f"# 🚀 Multiplayer Crash Lobby\n"
                f"Click **Join Crash** to enter your bet before blast off!\n\n"
                f"**Players ({len(self.players)}):**\n{player_list}"
            )
        )
        if not self.closed:
            container.add_item(small_separator())
            container.add_item(discord.ui.ActionRow(CrashJoinButton(self)))

        self.add_item(container)


class CrashJoinModal(discord.ui.Modal, title="Enter Crash Bet"):
    bet_input = discord.ui.TextInput(label="Bet Amount (e.g. 100k, half, max)", placeholder="10k", required=True)

    def __init__(self, lobby_view: CrashLobbyView):
        super().__init__()
        self.lobby_view = lobby_view

    async def on_submit(self, interaction: discord.Interaction):
        user_data = await EconomyDB.get_user(interaction.user.id)
        amt = parse_bet(str(self.bet_input.value), user_data["wallet"])
        if not amt or amt <= 0 or amt > user_data["wallet"]:
            return await interaction.response.send_message("❌ Invalid bet amount.", ephemeral=True)

        await EconomyDB.update_balance(interaction.user.id, wallet=-amt, description="Crash Bet")

        r = random.random()
        crash_point = round(max(1.05, 0.96 / (1.0 - r)), 2)

        self.lobby_view.players[interaction.user.id] = {
            "name": interaction.user.display_name,
            "bet": amt,
            "crash_point": crash_point,
            "cashed_out": False,
            "cash_mult": 0.0,
            "winnings": 0,
        }
        self.lobby_view._render()
        await interaction.response.edit_message(view=self.lobby_view)


class CrashJoinButton(discord.ui.Button):
    def __init__(self, lobby_view: CrashLobbyView):
        super().__init__(label="Join Crash 🚀", style=discord.ButtonStyle.primary)
        self.lobby_view = lobby_view

    async def callback(self, interaction: discord.Interaction):
        if self.lobby_view.closed:
            return await interaction.response.send_message("❌ Lobby closed!", ephemeral=True)
        await interaction.response.send_modal(CrashJoinModal(self.lobby_view))


class CrashActiveView(discord.ui.LayoutView):
    def __init__(self, players: dict[int, dict[str, Any]]):
        super().__init__(timeout=180)
        self.players = players
        self.current_mult = 1.00
        self.all_done = False
        self._render()

    def _render(self):
        self.clear_items()
        container = discord.ui.Container(accent_color=EMBED_COLOR)

        player_status = []
        for uid, p in self.players.items():
            if p["cashed_out"]:
                player_status.append(f"✅ **{p['name']}**: Cashed out at **x{p['cash_mult']:.2f}** (+{format_cash_short(p['winnings'])}) · Potential: x{p['crash_point']:.2f}")
            elif self.current_mult >= p["crash_point"]:
                player_status.append(f"💥 **{p['name']}**: Crashed at **x{p['crash_point']:.2f}**! (-{format_cash_short(p['bet'])})")
            else:
                player_status.append(f"🟢 **{p['name']}**: In Play ({format_cash_short(p['bet'])})")

        container.add_item(
            text_display(
                f"# 🚀 Crash Multiplier: **x{self.current_mult:.2f}**\n\n"
                f"**Player Board:**\n" + "\n".join(player_status)
            )
        )
        if not self.all_done:
            container.add_item(small_separator())
            container.add_item(discord.ui.ActionRow(CrashCashoutButton(self)))

        self.add_item(container)


class CrashCashoutButton(discord.ui.Button):
    def __init__(self, active_view: CrashActiveView):
        super().__init__(label="💰 Cashout Now", style=discord.ButtonStyle.success)
        self.active_view = active_view

    async def callback(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid not in self.active_view.players:
            return await interaction.response.send_message("❌ You are not in this game.", ephemeral=True)

        p = self.active_view.players[uid]
        if p["cashed_out"]:
            return await interaction.response.send_message("❌ Already cashed out!", ephemeral=True)
        if self.active_view.current_mult >= p["crash_point"]:
            return await interaction.response.send_message("❌ You already crashed!", ephemeral=True)

        p["cashed_out"] = True
        p["cash_mult"] = self.active_view.current_mult
        p["winnings"] = int(p["bet"] * self.active_view.current_mult)
        await EconomyDB.update_balance(uid, wallet=p["winnings"], description="Crash Cashout")

        self.active_view._render()
        await interaction.response.edit_message(view=self.active_view)


# ============================================================
# GAMBLING COG
# ============================================================

class Gambling(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==========================================
    # REGULAR GAMBLE (COIN FLIP / ROLL)
    # ==========================================

    @commands.command(name="gamble", aliases=["bet", "roll"])
    async def gamble_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0 or bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid bet amount."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Gamble Bet")

        win = random.random() < 0.49
        if win:
            winnings = bet * 2
            await EconomyDB.update_balance(ctx.author.id, wallet=winnings, description="Gamble Win")
            await EconomyDB.modify_treasury(-bet)
            msg = f"🎉 You rolled a winning number and won **{format_cash(winnings)}**!"
        else:
            await EconomyDB.modify_treasury(bet)
            msg = f"💀 The roll didn't go your way. You lost **{format_cash(bet)}**."

        await ctx.send(
            view=simple_view(
                f"# 🎲 Gamble Result\n\n"
                f"{msg}\n\n"
                f"-# Bet: {format_cash_short(bet)}"
            )
        )

    # ==========================================
    # MINES
    # ==========================================

    @commands.command(name="mines")
    async def mines_cmd(self, ctx: commands.Context, bombs: int, *, amount: str):
        if bombs < 1 or bombs > 24:
            return await ctx.send(view=simple_view("❌ Bombs must be between **1** and **24**."))

        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0:
            return await ctx.send(view=simple_view("❌ Enter a valid bet amount."))
        if bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ You don't have that much money in your wallet."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Mines game bet")
        view = MinesView(ctx.author.id, bet, bombs)
        await ctx.send(view=view)

    # ==========================================
    # BLACKJACK
    # ==========================================

    @commands.command(name="blackjack", aliases=["bj"])
    async def blackjack_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0:
            return await ctx.send(view=simple_view("❌ Enter a valid bet amount."))
        if bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ You don't have that much money in your wallet."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Blackjack Bet")
        view = BlackjackView(ctx.author.id, bet)
        await ctx.send(view=view)

    # ==========================================
    # SLOTS, DOUBLE, LADDER
    # ==========================================

    @commands.command(name="slots", aliases=["slot"])
    async def slots_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0 or bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid bet amount."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Slots Bet")
        reels = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
        r1, r2, r3 = random.choice(reels), random.choice(reels), random.choice(reels)

        if r1 == r2 == r3 == "7️⃣":
            mult = 25.0
        elif r1 == r2 == r3 == "💎":
            mult = 15.0
        elif r1 == r2 == r3:
            mult = 6.0
        elif r1 == r2 or r2 == r3 or r1 == r3:
            mult = 1.5
        else:
            mult = 0.0

        winnings = int(bet * mult)
        if winnings > 0:
            await EconomyDB.update_balance(ctx.author.id, wallet=winnings, description="Slots Win")
            await EconomyDB.modify_treasury(-winnings + bet)
            res = f"🎉 Won **{format_cash(winnings)}** (x{mult})!"
        else:
            await EconomyDB.modify_treasury(bet)
            res = "💀 Better luck next spin!"

        await ctx.send(
            view=simple_view(
                f"### **🎰 Slot Machine**\n\n"
                f"**[ {r1} | {r2} | {r3} ]**\n\n"
                f"{res}\n"
                f"**Bet:** {format_cash_short(bet)}"
            )
        )

    @commands.command(name="double")
    async def double_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0 or bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid bet amount."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Double Bet")

        class DoubleView(discord.ui.LayoutView):
            def __init__(self, uid: int, original_bet: int):
                super().__init__(timeout=60)
                self.uid = uid
                self.current_pot = original_bet * 2
                self.done = False
                self._render()

            def _render(self, msg: str = ""):
                self.clear_items()
                container = discord.ui.Container(accent_color=EMBED_COLOR)
                container.add_item(
                    text_display(
                        f"# 💥 Double or Nothing\n\n"
                        f"Current Winnings: **{format_cash(self.current_pot)}**\n"
                        f"{msg}"
                    )
                )
                if not self.done:
                    container.add_item(small_separator())
                    container.add_item(
                        discord.ui.ActionRow(
                            DoubleAgainBtn(self),
                            DoubleCashoutBtn(self),
                        )
                    )
                self.add_item(container)

        class DoubleAgainBtn(discord.ui.Button):
            def __init__(self, d_view: DoubleView):
                super().__init__(label="Double Again 🎲", style=discord.ButtonStyle.primary)
                self.d_view = d_view

            async def callback(self, interaction: discord.Interaction):
                if interaction.user.id != self.d_view.uid:
                    return
                if random.random() < 0.48:
                    self.d_view.current_pot *= 2
                    self.d_view._render("🎉 Multiplied!")
                else:
                    self.d_view.done = True
                    self.d_view._render("💀 Bust! You lost everything.")
                await interaction.response.edit_message(view=self.d_view)

        class DoubleCashoutBtn(discord.ui.Button):
            def __init__(self, d_view: DoubleView):
                super().__init__(label="Cashout 💰", style=discord.ButtonStyle.success)
                self.d_view = d_view

            async def callback(self, interaction: discord.Interaction):
                if interaction.user.id != self.d_view.uid:
                    return
                self.d_view.done = True
                await EconomyDB.update_balance(self.d_view.uid, wallet=self.d_view.current_pot, description="Double Cashout")
                self.d_view._render(f"💰 Successfully cashed out **{format_cash(self.d_view.current_pot)}**!")
                await interaction.response.edit_message(view=self.d_view)

        await ctx.send(view=DoubleView(ctx.author.id, bet))

    @commands.command(name="ladder")
    async def ladder_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0 or bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid bet amount."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Ladder Bet")
        rungs = [1.3, 1.8, 2.6, 4.0, 7.0, 15.0, 35.0, 100.0]

        class LadderView(discord.ui.LayoutView):
            def __init__(self, uid: int, start_bet: int):
                super().__init__(timeout=60)
                self.uid = uid
                self.bet = start_bet
                self.step = 0
                self.done = False
                self._render()

            def _render(self, msg: str = ""):
                self.clear_items()
                container = discord.ui.Container(accent_color=EMBED_COLOR)
                ladder_lines = []
                for i, r in enumerate(reversed(rungs)):
                    idx = len(rungs) - 1 - i
                    ptr = "👉 " if idx == self.step else "   "
                    ladder_lines.append(f"{ptr}Rung {idx + 1}: **x{r}** ({format_cash_short(self.bet * r)})")

                container.add_item(
                    text_display(
                        f"# 🪜 Multiplier Ladder\n\n" + "\n".join(ladder_lines) + f"\n\n{msg}"
                    )
                )
                if not self.done:
                    container.add_item(small_separator())
                    container.add_item(
                        discord.ui.ActionRow(
                            LadderStepBtn(self),
                            LadderCashoutBtn(self),
                        )
                    )
                self.add_item(container)

        class LadderStepBtn(discord.ui.Button):
            def __init__(self, l_view: LadderView):
                super().__init__(label="Step Up 🪜", style=discord.ButtonStyle.primary)
                self.l_view = l_view

            async def callback(self, interaction: discord.Interaction):
                if interaction.user.id != self.l_view.uid:
                    return
                chance = 0.75 - (self.l_view.step * 0.06)
                if random.random() < chance:
                    self.l_view.step += 1
                    if self.l_view.step >= len(rungs):
                        self.l_view.done = True
                        pot = int(self.l_view.bet * rungs[-1])
                        await EconomyDB.update_balance(self.l_view.uid, wallet=pot, description="Ladder Top Win")
                        self.l_view._render(f"🏆 TOP OF THE LADDER! Won **{format_cash(pot)}**!")
                    else:
                        self.l_view._render("🧗 Advanced to next rung!")
                else:
                    self.l_view.done = True
                    self.l_view._render("💨 Slipped off! You lost your bet.")
                await interaction.response.edit_message(view=self.l_view)

        class LadderCashoutBtn(discord.ui.Button):
            def __init__(self, l_view: LadderView):
                super().__init__(label="Cashout 💰", style=discord.ButtonStyle.success)
                self.l_view = l_view

            async def callback(self, interaction: discord.Interaction):
                if interaction.user.id != self.l_view.uid:
                    return
                self.l_view.done = True
                pot = int(self.l_view.bet * rungs[max(0, self.l_view.step - 1)]) if self.l_view.step > 0 else self.l_view.bet
                await EconomyDB.update_balance(self.l_view.uid, wallet=pot, description="Ladder Cashout")
                self.l_view._render(f"💰 Cashed out **{format_cash(pot)}**!")
                await interaction.response.edit_message(view=self.l_view)

        await ctx.send(view=LadderView(ctx.author.id, bet))

    # ==========================================
    # SUPERGAMBLE (5M COOLDOWN), DICE, ROULETTE, CRASH
    # ==========================================

    @commands.command(name="supergamble", aliases=["sg"])
    @commands.cooldown(1, 300, commands.BucketType.user)
    async def supergamble_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0 or bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid bet amount."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Supergamble Bet")
        win = random.random() < 0.03
        if win:
            mult = random.randint(50, 100)
            winnings = bet * mult
            await EconomyDB.update_balance(ctx.author.id, wallet=winnings, description="Supergamble Jackpot")
            await EconomyDB.modify_treasury(-winnings + bet)
            await ctx.send(
                view=simple_view(
                    f"# ⚡ SUPERGAMBLE JACKPOT! ⚡\n\n"
                    f"🎰 Against all odds, you hit a **x{mult}** multiplier!\n"
                    f"🏆 Payout: **{format_cash(winnings)}**!"
                )
            )
        else:
            await EconomyDB.modify_treasury(bet)
            await ctx.send(
                view=simple_view(
                    f"# ⚡ Supergamble Missed\n\n"
                    f"💀 The 3% jackpot slipped away. Lost **{format_cash(bet)}**.\n"
                    f"-# You can try again in **5 minutes**."
                )
            )

    @commands.command(name="dice")
    async def dice_cmd(self, ctx: commands.Context, choice: str, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0 or bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid bet amount."))

        c = choice.lower().strip()
        if c not in ("even", "odd", "1", "2", "3", "4", "5", "6"):
            return await ctx.send(view=simple_view("❌ Choice must be `even`, `odd`, or a single number `1-6`."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Dice Bet")
        roll = random.randint(1, 6)

        won = False
        mult = 0.0
        if c == "even" and roll % 2 == 0:
            won, mult = True, 2.0
        elif c == "odd" and roll % 2 != 0:
            won, mult = True, 2.0
        elif c.isdigit() and int(c) == roll:
            won, mult = True, 6.0

        if won:
            winnings = int(bet * mult)
            await EconomyDB.update_balance(ctx.author.id, wallet=winnings, description="Dice Win")
            await ctx.send(view=simple_view(f"🎲 Rolled a **{roll}**! You correctly predicted **{c}** and won **{format_cash(winnings)}** (x{mult})!"))
        else:
            await EconomyDB.modify_treasury(bet)
            await ctx.send(view=simple_view(f"🎲 Rolled a **{roll}**! Your prediction **{c}** failed. Lost **{format_cash(bet)}**."))

    @commands.command(name="roulette", aliases=["rr"])
    async def roulette_cmd(self, ctx: commands.Context, space: str, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        bet = parse_bet(amount, data["wallet"])
        if not bet or bet <= 0 or bet > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid bet amount."))

        sp = space.lower().strip()
        await EconomyDB.update_balance(ctx.author.id, wallet=-bet, description="Roulette Bet")

        landed = random.randint(0, 36)
        red_nums = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        color = "🟢 Green" if landed == 0 else ("🔴 Red" if landed in red_nums else "⚫ Black")

        won = False
        mult = 0.0
        if sp in ("red", "r") and landed in red_nums:
            won, mult = True, 2.0
        elif sp in ("black", "b") and landed != 0 and landed not in red_nums:
            won, mult = True, 2.0
        elif sp == "even" and landed != 0 and landed % 2 == 0:
            won, mult = True, 2.0
        elif sp == "odd" and landed % 2 != 0:
            won, mult = True, 2.0
        elif sp.isdigit() and int(sp) == landed:
            won, mult = True, 36.0

        if won:
            winnings = int(bet * mult)
            await EconomyDB.update_balance(ctx.author.id, wallet=winnings, description="Roulette Win")
            await ctx.send(view=simple_view(f"🎡 Landed on **{landed} ({color})**!\n🎉 Win on `{sp}`! Received **{format_cash(winnings)}** (x{mult})!"))
        else:
            await EconomyDB.modify_treasury(bet)
            await ctx.send(view=simple_view(f"🎡 Landed on **{landed} ({color})**!\n💀 Better luck next spin. Lost **{format_cash(bet)}**."))

    @commands.command(name="crash")
    async def crash_cmd(self, ctx: commands.Context):
        lobby = CrashLobbyView(ctx.author.id)
        msg = await ctx.send(view=lobby)

        await asyncio.sleep(15)
        lobby.closed = True
        lobby._render()
        await msg.edit(view=lobby)

        if not lobby.players:
            return await msg.edit(view=simple_view("❌ No players joined the crash game in time."))

        active_game = CrashActiveView(lobby.players)
        await msg.edit(view=active_game)

        while not active_game.all_done:
            await asyncio.sleep(0.9)
            active_game.current_mult = round(active_game.current_mult + 0.15 + (active_game.current_mult * 0.05), 2)

            all_settled = True
            for uid, p in active_game.players.items():
                if not p["cashed_out"] and active_game.current_mult < p["crash_point"]:
                    all_settled = False

            if all_settled or active_game.current_mult >= 100.0:
                active_game.all_done = True

            active_game._render()
            await msg.edit(view=active_game)


async def setup(bot: commands.Bot):
    await bot.add_cog(Gambling(bot))
