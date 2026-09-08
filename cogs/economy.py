# cogs/economy.py

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

import discord
from discord.ext import commands

from config import EMBED_COLOR
from utils.economy_db import JOBS, EconomyDB, format_cash, parse_bet


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
            await interaction.response.send_message("❌ This money drop has already been claimed!", ephemeral=True)
            return

        self.parent_view.claimed = True
        self.parent_view.clear_items()

        await EconomyDB.update_balance(interaction.user.id, wallet=self.parent_view.amount)

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# 💸 Money Drop Claimed!\n"
                f"🎉 {interaction.user.mention} was the fastest and pocketed **{format_cash(self.parent_view.amount)}**!"
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
    # BALANCE & BANKING
    # ==========================================

    @commands.command(name="balance", aliases=["bal", "wallet", "bank"])
    async def balance_cmd(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        user = member or ctx.author
        data = await EconomyDB.get_user(user.id)
        net_worth = data["wallet"] + data["bank"]

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)

        header = (
            f"### **{user.display_name}'s Balance**\n"
            f"-# Detailed financial holdings"
        )
        container.add_item(
            discord.ui.Section(
                text_display(header),
                accessory=discord.ui.Thumbnail(media=user.display_avatar.url),
            )
        )
        container.add_item(small_separator())

        fields = [
            f"**Wallet**\n{format_cash(data['wallet'])}",
            f"**Bank**\n{format_cash(data['bank'])} / {format_cash(data['bank_max'])}",
            f"**Net Worth**\n{format_cash(net_worth)}",
        ]
        if data["loan_amount"] > 0:
            fields.append(f"**Debt / Active Loan**\n{format_cash(data['loan_amount'])}")

        container.add_item(text_display("\n\n".join(fields)))
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

        bank_room = max(0, data["bank_max"] - data["bank"])
        if bank_room <= 0:
            return await ctx.send(view=simple_view("❌ Your bank vault is completely full!"))

        deposit_amt = min(amt, bank_room)
        await EconomyDB.update_balance(ctx.author.id, wallet=-deposit_amt, bank=deposit_amt)

        await ctx.send(
            view=simple_view(
                f"🏦 Successfully deposited **{format_cash(deposit_amt)}** into your bank vault."
            )
        )

    @commands.command(name="withdraw", aliases=["wd"])
    async def withdraw_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        amt = parse_bet(amount, data["bank"])
        if not amt or amt <= 0:
            return await ctx.send(view=simple_view("❌ Enter a valid amount to withdraw."))
        if amt > data["bank"]:
            return await ctx.send(view=simple_view("❌ You don't have that much money in your bank."))

        await EconomyDB.update_balance(ctx.author.id, wallet=amt, bank=-amt)
        await ctx.send(
            view=simple_view(
                f"🏧 Successfully withdrew **{format_cash(amt)}** into your wallet."
            )
        )

    # ==========================================
    # DAILY & MONTHLY
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

        await EconomyDB.update_balance(ctx.author.id, wallet=final_reward)
        import aiosqlite
        from utils.economy_db import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET daily_ts = ? WHERE user_id = ?", (now, ctx.author.id))
            await db.commit()

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🔮 **Daily**\n\n"
                f"You received your daily reward of {format_cash(final_reward)} (Econ ×{econ_mult:.2f})!\n\n"
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

        await EconomyDB.update_balance(ctx.author.id, wallet=final_reward)
        import aiosqlite
        from utils.economy_db import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET monthly_ts = ? WHERE user_id = ?", (now, ctx.author.id))
            await db.commit()

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🔮 **Monthly**\n\n"
                f"Your monthly reward is {format_cash(final_reward)}. (Econ ×{econ_mult:.3f})\n\n"
                f"**Multipliers**\n"
                f"Base: {format_cash(base_reward)} × {econ_mult:.3f}"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    # ==========================================
    # JOBS & WORKING
    # ==========================================

    @commands.group(name="job", aliases=["jobs"], invoke_without_command=True)
    async def job_group(self, ctx: commands.Context):
        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"🔮 **Jobs**\n\n"
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
            lines.append(f"• **{j['title']}** (`{key}`)\n  Salary: {format_cash(j['salary'])} · Requires {j['min_shifts']} shift(s)")

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

        data = await EconomyDB.get_user(ctx.author.id)
        target = JOBS[key]
        if data["job_shifts"] < target["min_shifts"]:
            return await ctx.send(
                view=simple_view(f"❌ You need at least **{target['min_shifts']}** completed shifts to qualify for **{target['title']}** (You have {data['job_shifts']}).")
            )

        import aiosqlite
        from utils.economy_db import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_users SET job_key = ?, last_worked = ? WHERE user_id = ?", (key, time.time(), ctx.author.id))
            await db.commit()

        await ctx.send(view=simple_view(f"🎉 Congratulations! You were hired as a **{target['title']}**! Make sure to work regularly!"))

    @job_group.command(name="work")
    async def job_work(self, ctx: commands.Context):
        data = await EconomyDB.get_user(ctx.author.id)
        now = time.time()

        if not data["job_key"] or data["job_key"] not in JOBS:
            return await ctx.send(view=simple_view(f"❌ You are currently unemployed! Run `{ctx.prefix}job list` to apply."))

        # 48-Hour Inactivity Firing
        if data["last_worked"] > 0 and (now - data["last_worked"] > 172800):
            import aiosqlite
            from utils.economy_db import DB_PATH
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE economy_users SET job_key = NULL WHERE user_id = ?", (ctx.author.id,))
                await db.commit()
            return await ctx.send(
                view=simple_view("⚠️ **You were fired!** You did not work for over 48 hours. You must apply for a new job.")
            )

        elapsed = now - data["work_ts"]
        if elapsed < 3600:
            rem = int(3600 - elapsed)
            return await ctx.send(view=simple_view(f"⏱️ Take a break! You can work your next shift in **{rem // 60}m {rem % 60}s**."))

        _, econ_mult = await EconomyDB.get_econ_state()
        job_info = JOBS[data["job_key"]]
        payout = int(job_info["salary"] * econ_mult)

        import aiosqlite
        from utils.economy_db import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE economy_users
                SET wallet = wallet + ?,
                    work_ts = ?,
                    last_worked = ?,
                    job_shifts = job_shifts + 1
                WHERE user_id = ?
                """,
                (payout, now, now, ctx.author.id),
            )
            await db.commit()

        await ctx.send(
            view=simple_view(
                f"💼 You finished your shift as a **{job_info['title']}** and received {format_cash(payout)}!"
            )
        )

    @job_group.command(name="info")
    async def job_info(self, ctx: commands.Context):
        data = await EconomyDB.get_user(ctx.author.id)
        if not data["job_key"] or data["job_key"] not in JOBS:
            return await ctx.send(view=simple_view(f"ℹ️ You are currently unemployed. Total completed shifts: **{data['job_shifts']}**."))

        job_info = JOBS[data["job_key"]]
        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"### **💼 Job Record: {ctx.author.display_name}**\n\n"
                f"• **Current Position:** {job_info['title']}\n"
                f"• **Base Salary:** {format_cash(job_info['salary'])}\n"
                f"• **Career Shifts:** {data['job_shifts']}\n"
                f"• **Status:** Active (Inactivity Timer resets each shift)"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    # Shortcut alias
    @commands.command(name="work")
    async def work_shortcut(self, ctx: commands.Context):
        await self.job_work(ctx)

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
            return await ctx.send(view=simple_view(f"❌ {target.display_name}'s wallet is too empty to bother robbing."))
        if author_data["wallet"] < 500:
            return await ctx.send(view=simple_view("❌ You need at least 🪙 500 in your wallet to cover court fees if you get caught."))

        success = random.random() < 0.45
        if success:
            stolen = random.randint(int(target_data["wallet"] * 0.15), int(target_data["wallet"] * 0.45))
            await EconomyDB.update_balance(target.id, wallet=-stolen)
            await EconomyDB.update_balance(ctx.author.id, wallet=stolen)
            await ctx.send(
                view=simple_view(f"🥷 Sneak 100! You pickpocketed **{format_cash(stolen)}** straight from {target.mention}'s wallet!")
            )
        else:
            fine = min(author_data["wallet"], random.randint(500, 2000))
            await EconomyDB.update_balance(ctx.author.id, wallet=-fine)
            await EconomyDB.modify_treasury(fine)
            await ctx.send(
                view=simple_view(f"🚨 Caught in 4K! The police caught you trying to rob {target.display_name}. You were fined **{format_cash(fine)}**!")
            )

    @commands.command(name="drop")
    async def drop_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        amt = parse_bet(amount, data["wallet"])
        if not amt or amt < 100:
            return await ctx.send(view=simple_view("❌ Minimum drop amount is 🪙 100."))
        if amt > data["wallet"]:
            return await ctx.send(view=simple_view("❌ You don't have that much money in your wallet."))

        await EconomyDB.update_balance(ctx.author.id, wallet=-amt)
        drop_view = DropClaimView(amt, ctx.author.id)
        await ctx.send(view=drop_view)

    # ==========================================
    # LOANS & TREASURY
    # ==========================================

    @commands.command(name="loan")
    async def loan_cmd(self, ctx: commands.Context, *, amount: str):
        data = await EconomyDB.get_user(ctx.author.id)
        if data["loan_amount"] > 0:
            return await ctx.send(
                view=simple_view(f"❌ You already have an active loan of **{format_cash(data['loan_amount'])}**! Repay it first.")
            )

        amt = parse_bet(amount, data["bank_max"])
        max_allowed = data["bank_max"] * 2
        if not amt or amt <= 0 or amt > max_allowed:
            return await ctx.send(view=simple_view(f"❌ You can borrow up to **{format_cash(max_allowed)}** based on your credit."))

        total_due = int(amt * 1.15)  # 15% interest
        import aiosqlite
        from utils.economy_db import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE economy_users
                SET wallet = wallet + ?,
                    loan_amount = ?,
                    loan_due = ?
                WHERE user_id = ?
                """,
                (amt, total_due, time.time() + 604800, ctx.author.id),
            )
            await db.commit()

        await ctx.send(
            view=simple_view(
                f"💳 **Loan Approved!**\n"
                f"Received: {format_cash(amt)}\n"
                f"Total to Repay (15% interest): {format_cash(total_due)}"
            )
        )

    @commands.command(name="repay")
    async def repay_cmd(self, ctx: commands.Context, *, amount: str = "all"):
        data = await EconomyDB.get_user(ctx.author.id)
        if data["loan_amount"] <= 0:
            return await ctx.send(view=simple_view("❌ You do not have any outstanding loans."))

        amt = parse_bet(amount, min(data["wallet"], data["loan_amount"]))
        if not amt or amt <= 0:
            return await ctx.send(view=simple_view("❌ Enter a valid repayment amount."))

        repay_amt = min(amt, data["wallet"], data["loan_amount"])
        import aiosqlite
        from utils.economy_db import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE economy_users
                SET wallet = wallet - ?,
                    loan_amount = loan_amount - ?
                WHERE user_id = ?
                """,
                (repay_amt, repay_amt, ctx.author.id),
            )
            await db.commit()

        await EconomyDB.modify_treasury(repay_amt)
        new_due = data["loan_amount"] - repay_amt
        await ctx.send(
            view=simple_view(f"✅ Paid **{format_cash(repay_amt)}** towards your loan. Remaining balance: **{format_cash(new_due)}**.")
        )

    @commands.command(name="treasury", aliases=["economy", "econhealth", "health"])
    async def treasury_cmd(self, ctx: commands.Context):
        treasury, mult = await EconomyDB.get_econ_state()
        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"### **🏛️ Central Economy Health**\n\n"
                f"• **National Treasury:** {format_cash(treasury)}\n"
                f"• **Global Econ Multiplier:** ×{mult:.3f}\n"
                f"• **Federal Interest Rate:** 15.0%\n"
                f"• **Vault Reserve Ratio:** Optimal (100% Solvency)"
            )
        )
        view.add_item(container)
        await ctx.send(view=view)

    @commands.command(name="leaderboard", aliases=["lb", "rich"])
    async def leaderboard_cmd(self, ctx: commands.Context):
        import aiosqlite
        from utils.economy_db import DB_PATH
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
            lines.append(f"`#{rank:02d}` **{tag}** -- {format_cash(r['net'])}")

        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"### **🏆 Global Wealth Leaderboard**\n\n" + ("\n".join(lines) if lines else "No records found.")
            )
        )
        view.add_item(container)
        await ctx.send(view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
