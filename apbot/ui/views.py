"""Discord components shared by the commands."""

import discord

from apbot.i18n import t


class OpenInManagerButton(discord.ui.Button):
    """A link button to a page of the manager's web UI."""

    def __init__(self, url: str, lang: str):
        super().__init__(style=discord.ButtonStyle.link, label=t("Open in manager", lang), url=url)


def manager_page_url(site_url: str | None, path: str) -> str | None:
    """The address of a page of the web UI (`path` like "/games/3"), or None when the manager's address is not known
    or is not one Discord accepts for a link button."""
    if not site_url or not site_url.startswith(("http://", "https://")):
        return None
    return f"{site_url.rstrip('/')}/#{path}"


def open_in_manager_view(site_url: str | None, path: str, lang: str) -> discord.ui.View | None:
    url = manager_page_url(site_url, path)
    if url is None:
        return None
    view = discord.ui.View()
    view.add_item(OpenInManagerButton(url, lang))
    return view
