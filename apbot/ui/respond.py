"""Sending a reply whether or not the interaction was already answered (deferred while the manager is asked)."""

import discord


async def reply(interaction: discord.Interaction, **kwargs) -> None:
    """Answers the interaction, or follows up when it was deferred. Takes what send_message and followup.send take;
    options given as None (no view, say) are left out."""
    options = {name: value for name, value in kwargs.items() if value is not None}
    if interaction.response.is_done():
        await interaction.followup.send(**options)
    else:
        await interaction.response.send_message(**options)
