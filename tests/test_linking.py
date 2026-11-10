import discord
import pytest
from fakes import USER_TOKEN, FakeInteraction, game_json, json_text

from apbot.cogs.linking import LinkingCog, LinkModal
from apbot.errors import UserFacingError

STATUS = {"user": {"id": 10, "username": "alice", "role": "user", "disabled": False}, "site_url": None}


def accept(manager, body=STATUS):
    manager.add("GET", "/api/auth/status", body)


async def submit(bot, token=USER_TOKEN, user_id=1001, locale="en-US") -> FakeInteraction:
    interaction = FakeInteraction(bot, locale=locale, user_id=user_id)
    await LinkingCog(bot).link_with_token(interaction, token)
    return interaction


# --- /link -------------------------------------------------------------------------------------------------------------
async def test_link_opens_a_modal_with_one_token_field(bot):
    interaction = FakeInteraction(bot)
    await LinkingCog(bot).link.callback(LinkingCog(bot), interaction)
    [modal] = interaction.response.modals
    assert isinstance(modal, LinkModal)
    assert modal.title == "Link manager account"
    assert modal.token.min_length == 10 and modal.token.max_length == 200 and modal.token.required
    assert any(isinstance(item, discord.ui.TextDisplay) for item in modal.children)


async def test_the_modal_follows_the_users_language(bot):
    interaction = FakeInteraction(bot, locale="de")
    await LinkingCog(bot).link.callback(LinkingCog(bot), interaction)
    assert interaction.response.modals[0].title == "Manager-Konto verknüpfen"


async def test_a_valid_token_is_stored_encrypted_with_its_last_four_characters(bot, manager):
    accept(manager)
    interaction = await submit(bot, locale="de")
    saved = bot.links.get(1001)
    assert (saved.manager_user_id, saved.manager_username, saved.status, saved.locale) == (10, "alice", "active", "de")
    assert saved.token_hint == USER_TOKEN[-4:]
    assert saved.token_ciphertext != USER_TOKEN.encode() and USER_TOKEN.encode() not in saved.token_ciphertext
    assert bot.cipher.decrypt(saved.token_ciphertext) == USER_TOKEN
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert interaction.response.defer_kwargs == {"ephemeral": True}


async def test_the_reply_is_private_names_the_account_and_never_contains_the_token(bot, manager):
    accept(manager)
    interaction = await submit(bot, locale="de")
    [message] = interaction.messages
    assert message["ephemeral"] is True
    assert message["embed"].description == "Verknüpft als **alice**"
    assert USER_TOKEN not in json_text(message["embed"])


async def test_the_token_with_spaces_around_it_is_accepted(bot, manager):
    accept(manager)
    await submit(bot, token=f"  {USER_TOKEN}\n")
    assert bot.cipher.decrypt(bot.links.get(1001).token_ciphertext) == USER_TOKEN
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"


@pytest.mark.parametrize("pasted", ["not-a-token-at-all", "apm_has space inside", "bearer apm_abcdefghij", "apm_"])
async def test_text_that_is_not_a_token_never_reaches_the_manager(bot, manager, pasted):
    with pytest.raises(UserFacingError) as error:
        await submit(bot, token=pasted)
    assert error.value.message("en") == "This token is not valid"
    assert manager.requests == [] and bot.links.get(1001) is None


async def test_a_token_the_manager_refuses_creates_no_link(bot, manager):
    manager.add("GET", "/api/auth/status", (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError) as error:
        await submit(bot)
    assert error.value.message("en") == "This token is not valid"
    assert bot.links.get(1001) is None


@pytest.mark.parametrize(
    "body",
    [
        {"user": None},
        {"user": {"id": 10, "username": "alice", "disabled": True}},
        {"user": {"id": "10", "username": "alice"}},
        {"user": {"id": True, "username": "alice"}},
        {"user": {"id": 10, "username": ""}},
        {"user": {"id": 10}},
        ["not", "an", "object"],
    ],
)
async def test_an_answer_without_a_usable_enabled_account_creates_no_link(bot, manager, body):
    accept(manager, body)
    with pytest.raises(UserFacingError) as error:
        await submit(bot)
    assert error.value.message("en") == "This token is not valid"
    assert bot.links.get(1001) is None


async def test_a_manager_account_linked_to_another_discord_user_is_refused(bot, manager):
    accept(manager)
    await submit(bot, user_id=1001)
    with pytest.raises(UserFacingError) as error:
        await submit(bot, user_id=2002)
    assert error.value.message("en") == (
        "This manager account is already linked to another Discord account. Unlink it there first"
    )
    assert bot.links.get(2002) is None
    assert bot.links.get(1001).manager_user_id == 10


async def test_linking_again_as_the_same_person_replaces_the_token(bot, manager):
    accept(manager)
    await submit(bot, token="apm_firstTokenValue")
    await submit(bot, token="apm_secondTokenValue")
    assert bot.cipher.decrypt(bot.links.get(1001).token_ciphertext) == "apm_secondTokenValue"
    assert bot.links.get(1001).token_hint == "alue"


async def test_a_manager_that_cannot_be_reached_creates_no_link_and_the_error_goes_to_the_handler(bot, manager):
    import httpx

    from apbot.manager.errors import ManagerUnavailable

    manager.add("GET", "/api/auth/status", httpx.ConnectError("refused"))
    with pytest.raises(ManagerUnavailable):
        await submit(bot)
    assert bot.links.get(1001) is None


async def test_errors_in_the_modal_go_to_the_bots_error_handler_privately(bot, manager):
    interaction = FakeInteraction(bot, user_id=1001)
    modal = LinkModal(LinkingCog(bot), "en")
    await modal.on_error(interaction, UserFacingError("This token is not valid"))
    [message] = interaction.messages
    assert message["ephemeral"] is True and message["embed"].description == "This token is not valid"


async def test_submitting_the_modal_links_with_the_typed_value(bot, manager):
    accept(manager)
    interaction = FakeInteraction(bot, user_id=1001)
    modal = LinkModal(LinkingCog(bot), "en")
    modal.token._value = USER_TOKEN  # what Discord fills in on submit
    await modal.on_submit(interaction)
    assert bot.links.get(1001) is not None


# --- /mydata -----------------------------------------------------------------------------------------------------------
async def test_mydata_lists_exactly_what_is_stored_and_privately(bot, manager):
    accept(manager)
    await submit(bot, locale="nl")
    interaction = FakeInteraction(bot, user_id=1001, locale="en-US")
    await LinkingCog(bot).mydata.callback(LinkingCog(bot), interaction)
    [message] = interaction.messages
    assert message["ephemeral"] is True
    fields = {f.name: f.value for f in message["embed"].fields}
    saved = bot.links.get(1001)
    assert fields == {
        "Manager account": "alice",
        "Token": f"…{USER_TOKEN[-4:]}",
        "Status": "Active",
        "Language": "Nederlands",
        "Linked": f"<t:{int(saved.linked_at)}:f>",
        "Last checked": f"<t:{int(saved.checked_at)}:f>",
        "Alerts": "None",
    }
    assert USER_TOKEN not in json_text(message["embed"])


async def test_mydata_without_a_link_says_nothing_is_stored(bot):
    interaction = FakeInteraction(bot, user_id=1001)
    await LinkingCog(bot).mydata.callback(LinkingCog(bot), interaction)
    [message] = interaction.messages
    assert message["ephemeral"] is True
    assert "Nothing else is stored about you" in message["embed"].description


async def test_mydata_shows_an_invalid_link_as_not_working(bot, manager):
    accept(manager)
    await submit(bot)
    bot.links.mark_invalid(1001)
    interaction = FakeInteraction(bot, user_id=1001)
    await LinkingCog(bot).mydata.callback(LinkingCog(bot), interaction)
    fields = {f.name: f.value for f in interaction.messages[0]["embed"].fields}
    assert fields["Status"] == "Not working. Run /link again."


# --- /unlink -----------------------------------------------------------------------------------------------------------
async def start_unlink(bot, user_id=1001):
    import asyncio

    interaction = FakeInteraction(bot, user_id=user_id)
    task = asyncio.create_task(LinkingCog(bot).unlink.callback(LinkingCog(bot), interaction))
    for _ in range(10):
        await asyncio.sleep(0)
    return interaction, task


async def test_unlink_without_a_link_says_so(bot):
    with pytest.raises(UserFacingError) as error:
        await LinkingCog(bot).unlink.callback(LinkingCog(bot), FakeInteraction(bot))
    assert error.value.message("en") == "No manager account is linked to your Discord account"


async def test_unlink_asks_first_and_deletes_nothing_until_confirmed(bot, manager):
    accept(manager)
    await submit(bot)
    interaction, task = await start_unlink(bot)
    [question] = interaction.messages
    assert question["ephemeral"] is True
    assert bot.links.get(1001) is not None
    view = question["view"]
    assert [button.label for button in view.children] == ["Unlink", "Cancel"]
    assert view.children[0].style == discord.ButtonStyle.danger
    await view.children[1].callback(FakeInteraction(bot, user_id=1001))  # cancel
    await task
    assert bot.links.get(1001) is not None


async def test_confirming_deletes_the_link_and_tells_to_revoke_the_token(bot, manager):
    accept(manager)
    await submit(bot)
    interaction, task = await start_unlink(bot)
    view = interaction.messages[0]["view"]
    await view.children[0].callback(FakeInteraction(bot, user_id=1001))  # confirm
    await task
    assert bot.links.get(1001) is None and bot.links.by_manager_user(10) is None
    [edit] = interaction.original_edits
    assert edit["embed"].description == "Unlinked. Revoke the token in the manager under API tokens as well."
    assert edit["view"] is None


async def test_someone_else_cannot_confirm_my_unlink(bot, manager):
    accept(manager)
    await submit(bot)
    interaction, task = await start_unlink(bot)
    view = interaction.messages[0]["view"]
    stranger = FakeInteraction(bot, user_id=9999)
    assert await view.interaction_check(stranger) is False
    assert stranger.messages[0]["ephemeral"] is True
    assert bot.links.get(1001) is not None
    view.stop()
    await task


# --- the person's private games are forgotten when the link changes ------------------------------------------------------
async def test_linking_again_forgets_the_kept_game_list(bot, manager):
    accept(manager)
    await submit(bot)
    manager.add("GET", "/api/games", [game_json(1)])
    await bot.my_games.games(1001, USER_TOKEN)
    assert bot.my_games.cached(1001) is not None
    await submit(bot)
    assert bot.my_games.cached(1001) is None


async def test_unlinking_forgets_the_kept_game_list(bot, manager):
    accept(manager)
    await submit(bot)
    manager.add("GET", "/api/games", [game_json(1)])
    await bot.my_games.games(1001, USER_TOKEN)
    interaction, task = await start_unlink(bot)
    await interaction.messages[0]["view"].children[0].callback(FakeInteraction(bot, user_id=1001))
    await task
    assert bot.my_games.cached(1001) is None
