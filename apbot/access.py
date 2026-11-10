"""Who the bot acts as, and who may use a command.

Public views use the bot's own token (see the cogs). Everything else acts as the person who asked, with the manager
token they linked: the manager decides what that person may do, the bot never widens it. On top of that a server can
limit commands to a Discord role, which this module checks.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

import discord

from apbot.crypto import TokenCipher, TokenUndecryptable
from apbot.db.guilds import GuildSettingsStore
from apbot.db.links import STATUS_ACTIVE, LinkStore, UserLink
from apbot.errors import MissingRoleError, NotLinkedError, UserFacingError
from apbot.i18n import language_of
from apbot.manager.errors import ReadOnlyToken, TokenInvalid

log = logging.getLogger(__name__)

RoleKind = Literal["organizer", "admin"]
T = TypeVar("T")


class Access:
    def __init__(self, links: LinkStore, guilds: GuildSettingsStore, cipher: TokenCipher):
        self._links = links
        self._guilds = guilds
        self._cipher = cipher
        self._unreadable: set[int] = set()  # people whose stored token the configured keys cannot decrypt

    def user_token(self, interaction: discord.Interaction) -> str:
        """The manager token of the person who caused this interaction (a command or a button: whoever clicked, never
        someone named in a custom_id). Raises NotLinkedError, or UserFacingError when the link needs to be made again."""
        discord_id = interaction.user.id
        link = self._links.get(discord_id)
        if link is None:
            raise NotLinkedError
        language = language_of(interaction.locale)
        if link.locale != language:
            self._links.set_locale(discord_id, language)  # direct messages later follow the language they use now
        token = self.stored_token(link)
        if token is None:
            raise _link_unreadable() if self.is_unreadable(discord_id) else _link_again()
        return token

    def stored_token(self, link: UserLink) -> str | None:
        """The token of a link, decrypted just now; None when the link is not active or cannot be decrypted. A failed
        decryption leaves the link as it is: a wrong key must not destroy it, the right key brings it back."""
        if link.status != STATUS_ACTIVE or link.token_ciphertext is None:
            return None
        try:
            token = self._cipher.decrypt(link.token_ciphertext)
            if self._cipher.needs_rotation(link.token_ciphertext):
                self._links.replace_ciphertext(link.discord_user_id, self._cipher.encrypt(token))
        except TokenUndecryptable:
            self._unreadable.add(link.discord_user_id)
            log.debug("A stored manager token cannot be decrypted with the configured keys")
            return None
        self._unreadable.discard(link.discord_user_id)
        return token

    def is_unreadable(self, discord_id: int) -> bool:
        """Whether the last look at this person's stored token failed to decrypt it (as opposed to the link not being
        active)."""
        return discord_id in self._unreadable

    async def run(self, interaction: discord.Interaction, call: Callable[[str], Awaitable[T]]) -> T:
        """Calls the manager as the person: `call` gets their token. A token the manager refuses marks the link invalid;
        a read-only token is explained. Other manager errors pass through to the bot's error handler."""
        return await self.call_as(interaction, self.user_token(interaction), call)

    async def call_as(self, interaction: discord.Interaction, token: str, call: Callable[[str], Awaitable[T]]) -> T:
        """Like `run` for a caller that resolved the token itself (to answer "not linked" before deferring)."""
        try:
            return await call(token)
        except TokenInvalid:
            self.on_token_invalid(interaction.user.id)
            raise _link_again() from None
        except ReadOnlyToken:
            raise UserFacingError(
                "Your linked token can only read. Link a token with *Read and change* access"
            ) from None

    def on_token_invalid(self, discord_id: int) -> None:
        self._links.mark_invalid(discord_id)

    def require_role(self, interaction: discord.Interaction, kind: RoleKind) -> None:
        """Raises MissingRoleError unless the person may use a command limited to `kind`.

        organizer: needs the organizer role, but only when the server has set one.
        admin: Manage Server always counts; the admin role does too when the server has set one."""
        guild = interaction.guild
        if guild is None:
            raise UserFacingError("This command only works in a server")
        settings = self._guilds.get(guild.id)
        if kind == "admin":
            if interaction.permissions.manage_guild:
                return
            role_id = settings.admin_role_id
        else:
            role_id = settings.organizer_role_id
            if role_id is None:
                return
        if role_id is not None and interaction.user.get_role(role_id) is not None:
            return
        raise MissingRoleError(role_id, manage_server_counts=kind == "admin")


def _link_again() -> UserFacingError:
    return UserFacingError("Your manager token no longer works. Make a new one and run /link again")


def _link_unreadable() -> UserFacingError:
    return UserFacingError("Your link to the manager cannot be read any more. Run /link again")
