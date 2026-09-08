# cogs/economy.py

from __future__ import annotations

import asyncio
import random
import time
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
                f"🎉 {interaction.user.mention} pocketed **{format_cash(self.parent_view.amount)}**!"
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
        await ctx.send(view=simple_view(f"🏦 Successfully deposited **{format_cash(amt)}** into your bank."))

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
        await ctx.send(view=simple_view(f"🏧 Successfully withdrew **{format_cash(amt)}** into your wallet."))

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
    # JOBS (NO MIN SHIFTS + 70% HIGH-END CHANCE)
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

        if target["high_end"]:
            if random.random() > 0.70:
                return await ctx.send(
                    view=simple_view(
                        f"❌ Your interview for **{target['title']}** was rejected by HR (30% rejection)! Feel free to re-apply."
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
    # LEADERBOARD (MATCHING image_9.png)
    # ==========================================

    @commands.command(name="leaderboard", aliases=["lb", "rich"])
    async def leaderboard_cmd(self, ctx: commands.Context):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT user_id, (wallet + bank) as net FROM economy_users ORDER BY net DESC LIMIT 10"
            ) as cur:
                rows = await cur.fetchall()

        lines = []
        for rank, r in enumerate(rows, start=1):
            user = self.bot.get_user(r["user_id"])
            tag = user.name if user else f"User {r['user_id']}"
            crown = "👑 " if rank == 1 else f"{rank}. "
            lines.append(f"{crown}{tag} ( {format_cash_short(r['net'])} )")

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
    # REWORKED ECONOMY METRICS
    # ==========================================

    @commands.command(name="economy", aliases=["econ", "health", "treasury"])
    async def economy_cmd(self, ctx: commands.Context):
        treasury, mult = await EconomyDB.get_econ_state()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT SUM(wallet), SUM(bank), COUNT(*) FROM economy_users") as cur:
                row = await cur.fetchone()
                total_wallet = row[0] or 0
                total_bank = row[1] or 0
                total_accounts = row[2] or 0

        circulating = total_wallet + total_bank

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"### **🏛️ Central Economy & Reserve Health**\n\n"
                f"• **National Treasury Reserve:** {format_cash(treasury)}\n"
                f"• **Global Reward Multiplier:** ×{mult:.3f}\n"
                f"• **Circulating Supply:** {format_cash(circulating)}\n"
                f"• **Total Bank Deposits:** {format_cash(total_bank)}\n"
                f"• **Total Wallet Cash:** {format_cash(total_wallet)}\n"
                f"• **Active Citizen Accounts:** {total_accounts:,}\n"
                f"• **Federal Reserve Status:** 100% Unbounded Solvency"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

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
            await ctx.send(view=simple_view(f"🥷 You pickpocketed **{format_cash(stolen)}** straight from {target.mention}'s wallet!"))
        else:
            fine = min(author_data["wallet"], random.randint(500, 2000))
            await EconomyDB.update_balance(ctx.author.id, wallet=-fine, description="Robbery fine")
            await EconomyDB.modify_treasury(fine)
            await ctx.send(view=simple_view(f"🚨 Caught! You failed to rob {target.display_name} and paid a **{format_cash(fine)}** fine."))

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
                f"• `{ctx.prefix}admin reset <@user>`\n"
                f"• `{ctx.prefix}admin setmultiplier <val>`"
            )
        )

    @admin_group.command(name="givemoney")
    async def admin_givemoney(self, ctx: commands.Context, member: discord.Member, *, amount: str):
        if not await self.bot.is_owner(ctx.author):
            return
        amt = parse_bet(amount, 10**18)
        if not amt or amt <= 0:
            return await ctx.send("❌ Enter a valid amount.")

        await EconomyDB.update_balance(member.id, wallet=amt, description="Admin Grant")
        await ctx.send(f"✅ Granted **{format_cash(amt)}** to {member.mention}.")

    @admin_group.command(name="takemoney")
    async def admin_takemoney(self, ctx: commands.Context, member: discord.Member, *, amount: str):
        if not await self.bot.is_owner(ctx.author):
            return
        amt = parse_bet(amount, 10**18)
        if not amt or amt <= 0:
            return await ctx.send("❌ Enter a valid amount.")

        await EconomyDB.update_balance(member.id, wallet=-amt, description="Admin Revoke")
        await ctx.send(f"✅ Deducted **{format_cash(amt)}** from {member.mention}.")

    @admin_group.command(name="setmoney")
    async def admin_setmoney(self, ctx: commands.Context, member: discord.Member, *, amount: str):
        if not await self.bot.is_owner(ctx.author):
            return
        amt = parse_bet(amount, 10**18)
        if amt is None or amt < 0:
            return await ctx.send("❌ Enter a valid amount.")

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET wallet = ? WHERE user_id = ?", (amt, member.id))
            await db.commit()
        await ctx.send(f"✅ Set {member.mention}'s wallet balance to **{format_cash(amt)}**.")

    @admin_group.command(name="reset")
    async def admin_reset(self, ctx: commands.Context, member: discord.Member):
        if not await self.bot.is_owner(ctx.author):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET wallet = 1000, bank = 0 WHERE user_id = ?", (member.id,))
            await db.commit()
        await ctx.send(f"✅ Reset economy data for {member.mention}.")

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
