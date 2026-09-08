# cogs/economy.py

from __future__ import annotations

import asyncio
import random
import time
from decimal import Decimal
from typing import Optional

import aiosqlite
import discord
from discord.ext import commands

from config import EMBED_COLOR
from utils.economy_db import (
    DB_PATH,
    JOBS,
    EconomyDB,
    format_cash,
    format_cash_short,
    parse_bet,
    safe_to_int,
)


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


class DropClaimView(discord.ui.LayoutView):
    def __init__(self, amount: int, dropper_id: int):
        super().__init__(timeout=120)
        self.amount = amount
        self.dropper_id = dropper_id
        self.claimed = False

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# 💸 Money Drop!\n"
                f"Someone dropped **{format_cash(amount)}** on the floor!\n"
                f"-# Be the first one to click the button below to grab it!"
            )
        )
        container.add_item(small_separator())
        container.add_item(discord.ui.ActionRow(DropClaimButton(self)))
        self.add_item(container)


class DropClaimButton(discord.ui.Button):
    def __init__(self, parent_view: DropClaimView):
        super().__init__(label="Grab Cash 💰", style=discord.ButtonStyle.success)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if self.parent_view.claimed:
            return await interaction.response.send_message("❌ This money drop has already been claimed!", ephemeral=True)

        self.parent_view.claimed = True
        self.parent_view.clear_items()

        await EconomyDB.update_balance(
            interaction.user.id,
            wallet=self.parent_view.amount,
            description="Chat Money Drop Claim",
        )

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# 💸 Money Drop Claimed!\n"
                f"🎉 {interaction.user.mention} pocketed {format_cash(self.parent_view.amount)}!"
            )
        )
        self.parent_view.add_item(container)
        await interaction.response.edit_message(view=self.parent_view)


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await EconomyDB.init_db()

    # ==========================================
    # BALANCE & UNBOUNDED BANKING
    # ==========================================

    @commands.command(name="balance", aliases=["bal"])
    async def balance_cmd(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        user = member or ctx.author
        data = await EconomyDB.get_user(user.id)
        txs = await EconomyDB.get_recent_transactions(user.id, limit=5)
        net_worth = data["wallet"] + data["bank"]

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)

        tx_lines = []
        for t in txs:
            icon = "📥" if t["type"] == "in" else "📤"
            amt_str = format_cash_short(t["amount"])
            tx_lines.append(f"{icon} **{amt_str}** · {t['description']}")
        tx_block = "\n".join(tx_lines) if tx_lines else "*No recent activity.*"

        content = (
            f"🟣 **{user.display_name}'s Balance**\n\n"
            f"**Wallet**\n"
            f"🪙 **{format_cash_short(data['wallet'])}**\n\n"
            f"**Bank**\n"
            f"🪙 **{format_cash_short(data['bank'])}**\n\n"
            f"**Recent Transactions**\n"
            f"{tx_block}\n\n"
            f"-# Total Balance: {format_cash_short(net_worth)}"
        )
        container.add_item(text_display(content))
        view.add_item(container)
        await ctx.send(view=view)

    @commands.command(name="deposit", aliases=["dp"])
    async def deposit_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        amt = parse_bet(amount, data["wallet"])
        if not amt or amt <= 0:
            return await ctx.send(view=simple_view("❌ Enter a valid amount to deposit."))
        if amt > data["wallet"]:
            return await ctx.send(view=simple_view("❌ You don't have that much money in your wallet."))

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=-amt,
            bank=amt,
            description="Bank Deposit",
        )
        await ctx.send(view=simple_view(f"🏦 Successfully deposited {format_cash(amt)} into your bank."))

    @commands.command(name="withdraw", aliases=["wd"])
    async def withdraw_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        amt = parse_bet(amount, data["bank"])
        if not amt or amt <= 0:
            return await ctx.send(view=simple_view("❌ Enter a valid amount to withdraw."))
        if amt > data["bank"]:
            return await ctx.send(view=simple_view("❌ You don't have that much money in your bank."))

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=amt,
            bank=-amt,
            description="Bank Withdrawal",
        )
        await ctx.send(view=simple_view(f"🏧 Successfully withdrew {format_cash(amt)} into your wallet."))

    # ==========================================
    # DAILY, WEEKLY, MONTHLY
    # ==========================================

    @commands.command(name="daily")
    async def daily_cmd(self, ctx: commands.Context):
        data = await EconomyDB.get_user(ctx.author.id)
        now = time.time()
        elapsed = now - data["daily_ts"]
        if elapsed < 86400:
            rem = int(86400 - elapsed)
            return await ctx.send(
                view=simple_view(f"⏱️ You already claimed your daily reward! Come back in **{rem // 3600}h {(rem % 3600) // 60}m**.")
            )

        _, econ_mult = await EconomyDB.get_econ_state()
        base_reward = random.randint(22000, 28000)
        final_reward = int(base_reward * econ_mult)

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=final_reward,
            description="Daily Reward",
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET daily_ts = ? WHERE user_id = ?", (now, ctx.author.id))
            await db.commit()

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🟣 **Daily**\n\n"
                f"You received your daily reward of {format_cash(final_reward)} (Econ ×{econ_mult:.2f})!\n\n"
                f"**Multipliers**\n"
                f"Base: {format_cash(base_reward)} × {econ_mult:.2f}"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    @commands.command(name="weekly")
    async def weekly_cmd(self, ctx: commands.Context):
        data = await EconomyDB.get_user(ctx.author.id)
        now = time.time()
        elapsed = now - data.get("weekly_ts", 0)
        if elapsed < 604800:
            rem = int(604800 - elapsed)
            return await ctx.send(
                view=simple_view(f"⏱️ You already claimed your weekly reward! Come back in **{rem // 86400}d {(rem % 86400) // 3600}h**.")
            )

        _, econ_mult = await EconomyDB.get_econ_state()
        base_reward = random.randint(350000, 500000)
        final_reward = int(base_reward * econ_mult)

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=final_reward,
            description="Weekly Reward",
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET weekly_ts = ? WHERE user_id = ?", (now, ctx.author.id))
            await db.commit()

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🟣 **Weekly**\n\n"
                f"You received your weekly reward of {format_cash(final_reward)} (Econ ×{econ_mult:.2f})!\n\n"
                f"**Multipliers**\n"
                f"Base: {format_cash(base_reward)} × {econ_mult:.2f}"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    @commands.command(name="monthly")
    async def monthly_cmd(self, ctx: commands.Context):
        data = await EconomyDB.get_user(ctx.author.id)
        now = time.time()
        elapsed = now - data["monthly_ts"]
        if elapsed < 2592000:
            rem = int(2592000 - elapsed)
            return await ctx.send(
                view=simple_view(f"⏱️ You already claimed your monthly reward! Come back in **{rem // 86400}d {(rem % 86400) // 3600}h**.")
            )

        _, econ_mult = await EconomyDB.get_econ_state()
        base_reward = random.randint(3200000, 3800000)
        final_reward = int(base_reward * econ_mult)

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=final_reward,
            description="Monthly Reward",
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET monthly_ts = ? WHERE user_id = ?", (now, ctx.author.id))
            await db.commit()

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🟣 **Monthly**\n\n"
                f"Your monthly reward is {format_cash(final_reward)}. (Econ ×{econ_mult:.3f})\n\n"
                f"**Multipliers**\n"
                f"Base: {format_cash(base_reward)} × {econ_mult:.3f}"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    # ==========================================
    # JOBS
    # ==========================================

    @commands.group(name="job", aliases=["jobs"], invoke_without_command=True)
    async def job_group(self, ctx: commands.Context):
        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🟣 **Jobs**\n\n"
                f"Available job commands:\n"
                f"• `{ctx.prefix}job list` - View all available jobs\n"
                f"• `{ctx.prefix}job apply <job>` - Apply for a job\n"
                f"• `{ctx.prefix}job work` - Work to earn your salary\n"
                f"• `{ctx.prefix}job info` - View your job details\n\n"
                f"⚠️ **Warning:** If you don't work for more than 48 hours, you'll be fired!"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    @job_group.command(name="list")
    async def job_list(self, ctx: commands.Context):
        lines = []
        for key, j in JOBS.items():
            req_text = "70% Interview Approval" if j["high_end"] else "Open Admission (100%)"
            lines.append(f"• **{j['title']}** (`{key}`)\n  Salary: {format_cash(j['salary'])} · {req_text}")

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"### **💼 Job Openings**\n"
                f"Apply using `{ctx.prefix}job apply <job_id>`\n\n" + "\n\n".join(lines)
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    @job_group.command(name="apply")
    async def job_apply(self, ctx: commands.Context, *, job_name: str):
        key = job_name.lower().strip()
        if key not in JOBS:
            return await ctx.send(view=simple_view(f"❌ Invalid job. Run `{ctx.prefix}job list` to view careers."))

        target = JOBS[key]

        if target["high_end"] and random.random() > 0.70:
            return await ctx.send(
                view=simple_view(
                    f"❌ Your interview for **{target['title']}** was rejected by HR! Feel free to re-apply."
                )
            )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET job_key = ?, last_worked = ? WHERE user_id = ?", (key, time.time(), ctx.author.id))
            await db.commit()

        await ctx.send(view=simple_view(f"🎉 Congratulations! You were hired as a **{target['title']}**! Make sure to work regularly."))

    @job_group.command(name="work")
    async def job_work(self, ctx: commands.Context):
        data = await EconomyDB.get_user(ctx.author.id)
        now = time.time()

        if not data["job_key"] or data["job_key"] not in JOBS:
            return await ctx.send(view=simple_view(f"❌ You are currently unemployed! Run `{ctx.prefix}job list` to apply."))

        if data["last_worked"] > 0 and (now - data["last_worked"] > 172800):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE economy_users SET job_key = NULL WHERE user_id = ?", (ctx.author.id,))
                await db.commit()
            return await ctx.send(
                view=simple_view("⚠️ **You were fired!** You did not work for over 48 hours. Run `!job apply` to get a new job.")
            )

        elapsed = now - data["work_ts"]
        if elapsed < 3600:
            rem = int(3600 - elapsed)
            return await ctx.send(view=simple_view(f"⏱️ Take a break! You can work your next shift in **{rem // 60}m {rem % 60}s**."))

        _, econ_mult = await EconomyDB.get_econ_state()
        job_info = JOBS[data["job_key"]]
        payout = int(job_info["salary"] * econ_mult)

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=payout,
            description=f"Shift as {job_info['title']}",
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE economy_users
                SET work_ts = ?,
                    last_worked = ?,
                    job_shifts = job_shifts + 1
                WHERE user_id = ?
                """,
                (now, now, ctx.author.id),
            )
            await db.commit()

        await ctx.send(view=simple_view(f"💼 You worked a shift as a **{job_info['title']}** and received {format_cash(payout)}!"))

    @job_group.command(name="info")
    async def job_info(self, ctx: commands.Context):
        data = await EconomyDB.get_user(ctx.author.id)
        if not data["job_key"] or data["job_key"] not in JOBS:
            return await ctx.send(view=simple_view(f"ℹ️ You are currently unemployed. Completed shifts: **{data['job_shifts']}**."))

        job_info = JOBS[data["job_key"]]
        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"### **💼 Job Record: {ctx.author.display_name}**\n\n"
                f"• **Position:** {job_info['title']}\n"
                f"• **Salary:** {format_cash(job_info['salary'])}\n"
                f"• **Total Shifts:** {data['job_shifts']}\n"
                f"• **Status:** Active"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    @commands.command(name="work")
    async def work_shortcut(self, ctx: commands.Context):
        await self.job_work(ctx)

    # ==========================================
    # SHOP & INVENTORY
    # ==========================================

    @commands.command(name="shop", aliases=["store"])
    async def shop_cmd(self, ctx: commands.Context):
        items = await EconomyDB.get_all_shop_items()
        buyable = [i for i in items if i["can_buy"] == 1]

        lines = []
        for it in buyable:
            lines.append(
                f"• {it['emoji']} **{it['name']}**\n"
                f"  Price: {format_cash(it['buy_price'])} · Resell: {format_cash_short(it['sell_price'])}"
            )

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# 🏪 Server Marketplace\n\n"
                f"Purchase items with `{ctx.prefix}buy <name> [quantity]`\n"
                f"Sell items from inventory with `{ctx.prefix}sell <name> [quantity]`\n\n"
                + ("\n\n".join(lines) if lines else "*No items currently stocked in the store.*")
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    @commands.command(name="buy")
    async def buy_cmd(self, ctx: commands.Context, *, args: str):
        parts = args.rsplit(" ", 1)
        item_query = args
        qty = 1

        if len(parts) == 2 and parts[1].isdigit():
            item_query = parts[0]
            qty = max(1, int(parts[1]))

        item = await EconomyDB.get_shop_item(item_query)
        if not item or item["can_buy"] != 1:
            return await ctx.send(view=simple_view(f"❌ Item **{item_query}** is not available for purchase."))

        total_cost = item["buy_price"] * qty
        data = await EconomyDB.get_user(ctx.author.id)
        if data["wallet"] < total_cost:
            return await ctx.send(
                view=simple_view(
                    f"❌ You need {format_cash(total_cost)} to buy {qty}x **{item['name']}** (You have {format_cash_short(data['wallet'])})."
                )
            )

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=-total_cost,
            description=f"Purchased {qty}x {item['name']}",
        )
        await EconomyDB.add_user_inventory(
            ctx.author.id,
            item_name=item["name"],
            quantity=qty,
            default_emoji=item["emoji"],
            default_sell_price=item["sell_price"],
        )

        await ctx.send(
            view=simple_view(
                f"🛍️ Successfully purchased **{qty}x {item['emoji']} {item['name']}** for {format_cash(total_cost)}!"
            )
        )

    @commands.command(name="sell")
    async def sell_cmd(self, ctx: commands.Context, *, args: str):
        parts = args.rsplit(" ", 1)
        item_query = args
        sell_all = False
        qty = 1

        if len(parts) == 2:
            if parts[1].lower() in ("all", "max"):
                item_query = parts[0]
                sell_all = True
            elif parts[1].isdigit():
                item_query = parts[0]
                qty = max(1, int(parts[1]))

        inv = await EconomyDB.get_user_inventory(ctx.author.id)
        user_item = next((i for i in inv if i["item_name"].lower() == item_query.lower()), None)
        if not user_item:
            return await ctx.send(view=simple_view(f"❌ You don't have any **{item_query}** in your inventory."))

        if sell_all:
            qty = user_item["quantity"]

        if user_item["quantity"] < qty:
            return await ctx.send(
                view=simple_view(f"❌ You only have **{user_item['quantity']}x** of that item.")
            )

        sell_rate = user_item.get("sell_price") or 50
        payout = sell_rate * qty

        removed = await EconomyDB.remove_user_inventory(ctx.author.id, user_item["item_name"], quantity=qty)
        if not removed:
            return await ctx.send(view=simple_view("❌ Failed to process sale."))

        await EconomyDB.update_balance(
            ctx.author.id,
            wallet=payout,
            description=f"Sold {qty}x {user_item['item_name']}",
        )

        await ctx.send(
            view=simple_view(
                f"💰 Sold **{qty}x {user_item['emoji']} {user_item['item_name']}** for {format_cash(payout)}!"
            )
        )

    @commands.command(name="inventory", aliases=["inv"])
    async def inventory_cmd(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        user = member or ctx.author
        inv = await EconomyDB.get_user_inventory(user.id)

        lines = []
        total_inv_value = 0
        for it in inv:
            val = it["sell_price"] * it["quantity"]
            total_inv_value += val
            lines.append(f"• {it['emoji']} **{it['item_name']}** ×`{it['quantity']:,}` · Val: {format_cash_short(val)}")

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"### **🎒 {user.display_name}'s Inventory**\n\n"
                + ("\n".join(lines) if lines else "*Your backpack is completely empty. Go fishing or visit the shop!*")
                + f"\n\n-# Total Resale Value: {format_cash_short(total_inv_value)}"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    # ==========================================
    # LEADERBOARD
    # ==========================================

    @commands.command(name="leaderboard", aliases=["lb", "rich"])
    async def leaderboard_cmd(self, ctx: commands.Context):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT user_id, wallet, bank FROM economy_users") as cur:
                rows = await cur.fetchall()

        all_users = []
        for r in rows:
            w = safe_to_int(r["wallet"])
            b = safe_to_int(r["bank"])
            all_users.append((r["user_id"], w + b))

        all_users.sort(key=lambda x: x[1], reverse=True)
        top_10 = all_users[:10]

        lines = []
        for rank, (uid, net) in enumerate(top_10, start=1):
            user = self.bot.get_user(uid)
            tag = user.name if user else f"User {uid}"
            prefix_tag = "👑 " if rank == 1 else f"{rank}. "
            lines.append(f"{prefix_tag}{tag} ( {format_cash_short(net)} )")

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🟣 **Economy Leaderboard**\n\n"
                f"Top 10 Users by Net Balance\n" + ("\n".join(lines) if lines else "*No records found.*")
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    # ==========================================
    # REWORKED ECONOMY OVERVIEW & HEALTH
    # ==========================================

    @commands.group(name="economy", aliases=["econ"], invoke_without_command=True)
    async def economy_group(self, ctx: commands.Context):
        data = await EconomyDB.get_overview_data(ctx.author.id)

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)

        content = (
            f"📊 **Economy Overview**\n\n"
            f"Current state of the server economy\n\n"
            f"💵 **Supply**\n"
            f"**Total:** {format_cash(data['total_supply'])}\n"
            f"**Circulating:** {format_cash(data['circulating'])}\n\n"
            f"🏦 **Treasury**\n"
            f"{format_cash(data['treasury'])}\n\n"
            f"⚙️ **Rates**\n"
            f"**Fee:** {data['fee_rate'] * 100:.2f}%\n"
            f"**Passive Income:** {data['passive_rate'] * 100:.2f}%\n\n"
            f"🎲 **Global Stats**\n"
            f"**Wins:** {data['wins']:,}\n"
            f"**Losses:** {data['losses']:,}\n"
            f"**Win Rate:** {data['win_rate']:.1f}%\n\n"
            f"💼 **Your Portfolio**\n"
            f"{data['portfolio_pct']:.4f}% of total supply\n"
            f"({format_cash(data['user_net'])})\n\n"
            f"-# 💡 Use {ctx.prefix}economy health for detailed analysis • {ctx.prefix}economy trends for historical data"
        )

        container.add_item(text_display(content))
        view.add_item(container)
        await ctx.send(view=view)

    @economy_group.command(name="health")
    async def economy_health(self, ctx: commands.Context):
        data = await EconomyDB.get_overview_data(ctx.author.id)

        total_supply = Decimal(data["total_supply"]) if data["total_supply"] > 0 else Decimal(1)
        treasury = Decimal(data["treasury"])
        circulating = Decimal(data["circulating"])
        volume = Decimal(data["volume_24h"])

        treasury_ratio = float((treasury / total_supply) * Decimal(100))
        treasury_score = min(37.5, (treasury_ratio / 25.0) * 37.5) if treasury_ratio <= 25.0 else max(5.0, 37.5 - ((treasury_ratio - 25.0) * 0.8))
        treasury_dot = "🟢" if treasury_ratio >= 20.0 else ("🟡" if treasury_ratio >= 15.0 else "🔴")

        liquidity_ratio = float((circulating / total_supply) * Decimal(100))
        liquidity_score = min(31.25, (liquidity_ratio / 85.0) * 31.25)
        liquidity_dot = "🟢" if liquidity_ratio >= 75.0 else "🟡"

        velocity_ratio = float((volume / circulating * Decimal(100))) if circulating > 0 else 0.1231
        velocity_score = min(31.25, max(10.0, 31.25 - (velocity_ratio * 2.0)))
        velocity_dot = "🟢" if velocity_ratio < 1.0 else "🟡"

        overall_score = treasury_score + liquidity_score + velocity_score
        grade = "Excellent" if overall_score >= 80 else ("Good" if overall_score >= 60 else ("Fair" if overall_score >= 40 else "Poor"))

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)

        content = (
            f"🏥 **Economic Health Score**\n\n"
            f"Overall Score: **{overall_score:.1f}/100** ({grade})\n\n"
            f"{treasury_dot} **Treasury Health**\n"
            f"Value: {treasury_ratio:.2f}%\n"
            f"Score: {treasury_score:.2f}/37.5\n\n"
            f"{liquidity_dot} **Liquidity Ratio**\n"
            f"Value: {liquidity_ratio:.2f}%\n"
            f"Score: {liquidity_score:.2f}/31.25\n\n"
            f"{velocity_dot} **Velocity Of Money**\n"
            f"Value: {velocity_ratio:.4f}%\n"
            f"Score: {velocity_score:.2f}/31.25\n\n"
            f"💡 **Info**\n"
            f"🟡 Very high liquidity. Consider investments or large transactions.\n"
            f"🟡 Low economic activity. Transaction volumes are below normal.\n\n"
            f"-# Higher scores indicate better economic health"
        )

        container.add_item(text_display(content))
        view.add_item(container)
        await ctx.send(view=view)

    @commands.command(name="health", aliases=["econhealth"])
    async def health_shortcut(self, ctx: commands.Context):
        await self.economy_health(ctx)

    # ==========================================
    # ROBBING & DROPS
    # ==========================================

    @commands.command(name="rob")
    @commands.cooldown(1, 1800, commands.BucketType.user)
    async def rob_cmd(self, ctx: commands.Context, target: discord.Member):
        if target.id == ctx.author.id:
            return await ctx.send(view=simple_view("❌ You cannot rob yourself."))

        author_data = await EconomyDB.get_user(ctx.author.id)
        target_data = await EconomyDB.get_user(target.id)

        if target_data["wallet"] < 1000:
            return await ctx.send(view=simple_view(f"❌ {target.display_name}'s wallet is too empty to rob."))
        if author_data["wallet"] < 500:
            return await ctx.send(view=simple_view("❌ You need at least 🪙 500 in your wallet to risk robbing."))

        if random.random() < 0.45:
            stolen = random.randint(int(target_data["wallet"] * 0.15), int(target_data["wallet"] * 0.45))
            await EconomyDB.update_balance(target.id, wallet=-stolen, description=f"Robbed by {ctx.author.display_name}")
            await EconomyDB.update_balance(ctx.author.id, wallet=stolen, description=f"Robbery on {target.display_name}")
            await ctx.send(view=simple_view(f"🥷 You pickpocketed {format_cash(stolen)} straight from {target.mention}'s wallet!"))
        else:
            fine = min(author_data["wallet"], random.randint(500, 2000))
            await EconomyDB.update_balance(ctx.author.id, wallet=-fine, description="Robbery fine")
            await EconomyDB.modify_treasury(fine)
            await ctx.send(view=simple_view(f"🚨 Caught! You failed to rob {target.display_name} and paid a {format_cash(fine)} fine."))

    @commands.command(name="drop")
    async def drop_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        amt = parse_bet(amount, data["wallet"])
        if not amt or amt < 100 or amt > data["wallet"]:
            return await ctx.send(view=simple_view("❌ Invalid drop amount."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-amt, description="Chat Money Drop")
        await ctx.send(view=DropClaimView(amt, ctx.author.id))

    # ==========================================
    # BOT OWNER / ADMIN COMMANDS
    # ==========================================

    @commands.group(name="admin", invoke_without_command=True)
    async def admin_group(self, ctx: commands.Context):
        if not await self.bot.is_owner(ctx.author):
            return
        await ctx.send(
            view=simple_view(
                f"### **🛡️ Admin Economy Panel**\n"
                f"• `{ctx.prefix}admin givemoney <@user> <amount>`\n"
                f"• `{ctx.prefix}admin takemoney <@user> <amount>`\n"
                f"• `{ctx.prefix}admin setmoney <@user> <amount>`\n"
                f"• `{ctx.prefix}admin additem <name> <emoji> <value>`\n"
                f"• `{ctx.prefix}admin removeitem <name>`\n"
                f"• `{ctx.prefix}admin reset <@user>`\n"
                f"• `{ctx.prefix}admin setmultiplier <val>`"
            )
        )

    @admin_group.command(name="additem")
    async def admin_additem(self, ctx: commands.Context, name: str, emoji: str, value: str):
        if not await self.bot.is_owner(ctx.author):
            return
        val = parse_bet(value, 10**303)
        if not val or val <= 0:
            return await ctx.send("❌ Enter a valid value or price.")

        await EconomyDB.add_shop_item(name=name, emoji=emoji, buy_price=val)
        await ctx.send(
            f"✅ Registered item **{emoji} {name}** into the marketplace at {format_cash(val)} (Sell: {format_cash_short(int(val * 0.70))})."
        )

    @admin_group.command(name="removeitem")
    async def admin_removeitem(self, ctx: commands.Context, *, name: str):
        if not await self.bot.is_owner(ctx.author):
            return
        deleted = await EconomyDB.remove_shop_item(name)
        if deleted:
            await ctx.send(f"✅ Removed **{name}** from the marketplace registry.")
        else:
            await ctx.send(f"❌ Item **{name}** was not found in the shop database.")

    @admin_group.command(name="givemoney")
    async def admin_givemoney(self, ctx: commands.Context, member: discord.Member, *, amount: str):
        if not await self.bot.is_owner(ctx.author):
            return
        amt = parse_bet(amount, 10**303)
        if not amt or amt <= 0:
            return await ctx.send("❌ Enter a valid amount.")

        await EconomyDB.update_balance(member.id, wallet=amt, description="Admin Grant")
        await ctx.send(f"✅ Granted {format_cash(amt)} to {member.mention}.")

    @admin_group.command(name="takemoney")
    async def admin_takemoney(self, ctx: commands.Context, member: discord.Member, *, amount: str):
        if not await self.bot.is_owner(ctx.author):
            return
        amt = parse_bet(amount, 10**303)
        if not amt or amt <= 0:
            return await ctx.send("❌ Enter a valid amount.")

        await EconomyDB.update_balance(member.id, wallet=-amt, description="Admin Revoke")
        await ctx.send(f"✅ Deducted {format_cash(amt)} from {member.mention}.")

    @admin_group.command(name="setmoney")
    async def admin_setmoney(self, ctx: commands.Context, member: discord.Member, *, amount: str):
        if not await self.bot.is_owner(ctx.author):
            return
        amt = parse_bet(amount, 10**303)
        if amt is None or amt < 0:
            return await ctx.send("❌ Enter a valid amount.")

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET wallet = ? WHERE user_id = ?", (str(amt), member.id))
            await db.commit()
        await ctx.send(f"✅ Set {member.mention}'s wallet balance to {format_cash(amt)}.")

    @admin_group.command(name="reset")
    async def admin_reset(self, ctx: commands.Context, member: discord.Member):
        if not await self.bot.is_owner(ctx.author):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET wallet = '1000', bank = '0' WHERE user_id = ?", (member.id,))
            await db.execute("DELETE FROM user_inventory WHERE user_id = ?", (member.id,))
            await db.commit()
        await ctx.send(f"✅ Reset economy data and backpack for {member.mention}.")

    @admin_group.command(name="setmultiplier")
    async def admin_setmultiplier(self, ctx: commands.Context, value: float):
        if not await self.bot.is_owner(ctx.author):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_state SET value = ? WHERE key = 'multiplier'", (value,))
            await db.commit()
        await ctx.send(f"✅ Global Economy Multiplier set to **×{value:.3f}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
