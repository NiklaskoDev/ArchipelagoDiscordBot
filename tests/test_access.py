import pytest
from cryptography.fernet import Fernet
from fakes import ENCRYPTION_KEY, USER_TOKEN, FakeInteraction

from apbot.access import Access
from apbot.crypto import TokenCipher
from apbot.errors import MissingRoleError, NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict, ReadOnlyToken, TokenInvalid

GUILD = 5000
ORGANIZER_ROLE = 777
ADMIN_ROLE = 888


def link_user(bot, discord_id=1001, manager_id=10, token=USER_TOKEN, cipher=None, locale="en"):
    cipher = cipher or bot.cipher
    bot.links.save(discord_id, manager_id, "alice", cipher.encrypt(token), token[-4:], locale)


def asker(bot, user_id=1001, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=user_id, guild_id=GUILD, **kwargs)


# --- the token of the person ---------------------------------------------------------------------------------------------
def test_someone_who_has_not_linked_is_told_to_link(bot):
    with pytest.raises(NotLinkedError) as error:
        bot.access.user_token(asker(bot))
    assert error.value.message("en") == "Link your manager account first with /link"
    assert error.value.message("de") == "Verknüpfe zuerst dein Manager-Konto mit /link"


def test_a_linked_person_gets_their_own_decrypted_token(bot):
    link_user(bot)
    assert bot.access.user_token(asker(bot)) == USER_TOKEN
    assert bot.links.get(1001).token_ciphertext != USER_TOKEN.encode()


def test_whoever_clicks_a_button_gets_their_own_token_never_someone_elses(bot):
    link_user(bot, discord_id=1001, manager_id=10, token="apm_tokenOfFirstUser")
    link_user(bot, discord_id=2002, manager_id=20, token="apm_tokenOfSecondUser")
    assert bot.access.user_token(asker(bot, user_id=2002)) == "apm_tokenOfSecondUser"
    assert bot.access.user_token(asker(bot, user_id=1001)) == "apm_tokenOfFirstUser"
    with pytest.raises(NotLinkedError):
        bot.access.user_token(asker(bot, user_id=3003))


def test_an_invalid_link_asks_to_link_again(bot):
    link_user(bot)
    bot.links.mark_invalid(1001)
    with pytest.raises(UserFacingError) as error:
        bot.access.user_token(asker(bot))
    assert error.value.message("en") == "Your manager token no longer works. Make a new one and run /link again"


def test_a_token_that_cannot_be_decrypted_keeps_the_link_and_asks_to_link_again(bot, caplog):
    link_user(bot, cipher=TokenCipher([Fernet.generate_key().decode()]))  # stored under a key the bot does not have
    before = bot.links.get(1001).token_ciphertext
    with pytest.raises(UserFacingError) as error:
        bot.access.user_token(asker(bot))
    assert "/link" in error.value.message("en")
    assert error.value.message("en") != "Your manager token no longer works. Make a new one and run /link again"
    saved = bot.links.get(1001)
    assert saved.status == "active"
    assert saved.token_ciphertext == before
    assert bot.access.is_unreadable(1001)
    assert USER_TOKEN not in caplog.text


def test_the_link_works_again_once_the_right_key_is_back(bot):
    key = Fernet.generate_key().decode()
    link_user(bot, cipher=TokenCipher([key]))
    with pytest.raises(UserFacingError):
        bot.access.user_token(asker(bot))  # the bot started with a wrong key
    restarted = Access(bot.links, bot.guild_settings, TokenCipher([key]))
    assert restarted.user_token(asker(bot)) == USER_TOKEN
    assert not restarted.is_unreadable(1001)


def test_linking_again_after_an_unreadable_token_works(bot):
    link_user(bot, cipher=TokenCipher([Fernet.generate_key().decode()]))
    with pytest.raises(UserFacingError):
        bot.access.user_token(asker(bot))
    assert bot.access.is_unreadable(1001)
    link_user(bot)  # /link again: the row is overwritten with the bot's own key
    assert bot.access.user_token(asker(bot)) == USER_TOKEN
    assert not bot.access.is_unreadable(1001)


def test_a_token_stored_under_an_old_key_is_encrypted_again_with_the_first_key(bot):
    old_key = Fernet.generate_key().decode()
    link_user(bot, cipher=TokenCipher([old_key]))
    access = Access(bot.links, bot.guild_settings, TokenCipher([ENCRYPTION_KEY, old_key]))
    assert access.user_token(asker(bot)) == USER_TOKEN
    assert TokenCipher([ENCRYPTION_KEY]).decrypt(bot.links.get(1001).token_ciphertext) == USER_TOKEN


def test_the_stored_language_follows_the_language_the_person_uses(bot):
    link_user(bot, locale="en")
    bot.access.user_token(asker(bot, locale="de"))
    assert bot.links.get(1001).locale == "de"
    bot.access.user_token(asker(bot, locale="pt-BR"))
    assert bot.links.get(1001).locale == "en"  # no translation: English, as everywhere else


# --- calling the manager as the person -------------------------------------------------------------------------------------
async def test_run_hands_the_persons_token_to_the_call_and_returns_its_result(bot):
    link_user(bot)
    seen = []

    async def call(token):
        seen.append(token)
        return {"ok": True}

    assert await bot.access.run(asker(bot), call) == {"ok": True}
    assert seen == [USER_TOKEN]


async def test_run_without_a_link_never_calls_the_manager(bot):
    async def call(token):
        raise AssertionError("must not be called")

    with pytest.raises(NotLinkedError):
        await bot.access.run(asker(bot), call)


async def test_a_token_the_manager_refuses_marks_the_link_invalid_and_says_what_to_do(bot):
    link_user(bot)

    async def call(token):
        raise TokenInvalid(401, "This API token is not valid (any more)")

    with pytest.raises(UserFacingError) as error:
        await bot.access.run(asker(bot), call)
    assert error.value.message("en") == "Your manager token no longer works. Make a new one and run /link again"
    saved = bot.links.get(1001)
    assert saved.status == "invalid" and saved.token_ciphertext is None


async def test_a_read_only_token_is_explained_and_the_link_stays_active(bot):
    link_user(bot)

    async def call(token):
        raise ReadOnlyToken(403, "This API token can only read")

    with pytest.raises(UserFacingError) as error:
        await bot.access.run(asker(bot), call)
    assert error.value.message("en") == "Your linked token can only read. Link a token with *Read and change* access"
    assert bot.links.get(1001).status == "active"


async def test_other_manager_errors_pass_through_unchanged(bot):
    link_user(bot)

    async def call(token):
        raise Conflict(409, "The game is already running")

    with pytest.raises(Conflict):
        await bot.access.run(asker(bot), call)
    assert bot.links.get(1001).status == "active"


# --- who may use a command ---------------------------------------------------------------------------------------------
def test_organizer_commands_are_open_while_the_server_has_set_no_organizer_role(bot):
    bot.access.require_role(asker(bot), "organizer")


def test_organizer_role_holder_may_and_everyone_else_may_not(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    bot.access.require_role(asker(bot, roles=[ORGANIZER_ROLE]), "organizer")
    with pytest.raises(MissingRoleError) as error:
        bot.access.require_role(asker(bot, roles=[999]), "organizer")
    assert error.value.message("en") == f"You need the role <@&{ORGANIZER_ROLE}> to do this"


def test_a_role_that_was_set_but_is_missing_from_the_person_refuses_even_manage_server_holders(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    with pytest.raises(MissingRoleError):
        bot.access.require_role(asker(bot, manage_guild=True), "organizer")


def test_manage_server_is_enough_for_admin_commands_with_or_without_an_admin_role(bot):
    bot.access.require_role(asker(bot, manage_guild=True), "admin")
    bot.guild_settings.update(GUILD, admin_role_id=ADMIN_ROLE)
    bot.access.require_role(asker(bot, manage_guild=True), "admin")


def test_the_admin_role_is_enough_too(bot):
    bot.guild_settings.update(GUILD, admin_role_id=ADMIN_ROLE)
    bot.access.require_role(asker(bot, roles=[ADMIN_ROLE]), "admin")


def test_without_manage_server_or_the_admin_role_admin_commands_are_refused(bot):
    bot.guild_settings.update(GUILD, admin_role_id=ADMIN_ROLE)
    with pytest.raises(MissingRoleError) as error:
        bot.access.require_role(asker(bot, roles=[ORGANIZER_ROLE]), "admin")
    assert error.value.message("en") == f"You need the role <@&{ADMIN_ROLE}> or the Manage Server permission to do this"


def test_without_an_admin_role_only_manage_server_counts(bot):
    with pytest.raises(MissingRoleError) as error:
        bot.access.require_role(asker(bot, roles=[ORGANIZER_ROLE]), "admin")
    assert error.value.message("en") == "You need the Manage Server permission to do this"
    assert error.value.message("nl") == "Hiervoor heb je het recht Server beheren nodig"


def test_the_organizer_role_does_not_open_admin_commands(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    with pytest.raises(MissingRoleError):
        bot.access.require_role(asker(bot, roles=[ORGANIZER_ROLE]), "admin")


@pytest.mark.parametrize("kind", ["organizer", "admin"])
def test_role_gates_need_a_server(bot, kind):
    with pytest.raises(UserFacingError) as error:
        bot.access.require_role(FakeInteraction(bot, guild_id=None), kind)
    assert error.value.message("en") == "This command only works in a server"


def test_settings_of_one_server_do_not_apply_to_another(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    bot.access.require_role(FakeInteraction(bot, guild_id=6000), "organizer")
