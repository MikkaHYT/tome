from __future__ import annotations

import discord


class Confirm(discord.ui.View):
    def __init__(self, message: discord.Message, invoker: discord.Member | None = None) -> None:
        super().__init__(timeout=60)
        self.value = False
        self.message = message
        self.invoker = invoker

    async def on_timeout(self) -> None:
        await self.message.edit(view=None)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish(interaction, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish(interaction, False)

    async def _finish(self, interaction: discord.Interaction, value: bool) -> None:
        if self.invoker and interaction.user.id != self.invoker.id:
            return

        await interaction.response.defer()
        await self.message.edit(view=None)
        self.value = value
        self.stop()
