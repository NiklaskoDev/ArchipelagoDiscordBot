import pytest
from cryptography.fernet import Fernet

from apbot.crypto import TokenCipher, TokenUndecryptable

TOKEN = "apm_exampleTokenValue123"


def key() -> str:
    return Fernet.generate_key().decode()


def test_a_token_survives_a_round_trip_and_the_ciphertext_hides_it():
    cipher = TokenCipher([key()])
    ciphertext = cipher.encrypt(TOKEN)
    assert isinstance(ciphertext, bytes)
    assert TOKEN.encode() not in ciphertext
    assert cipher.decrypt(ciphertext) == TOKEN


def test_the_same_token_encrypts_differently_each_time():
    cipher = TokenCipher([key()])
    assert cipher.encrypt(TOKEN) != cipher.encrypt(TOKEN)


def test_a_wrong_key_cannot_decrypt():
    ciphertext = TokenCipher([key()]).encrypt(TOKEN)
    with pytest.raises(TokenUndecryptable) as error:
        TokenCipher([key()]).decrypt(ciphertext)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("damaged", [b"", b"not a fernet token", b"gAAAAA"])
def test_damaged_ciphertext_is_undecryptable(damaged):
    cipher = TokenCipher([key()])
    with pytest.raises(TokenUndecryptable):
        cipher.decrypt(damaged)
    with pytest.raises(TokenUndecryptable):
        cipher.needs_rotation(damaged)


def test_after_adding_a_new_key_in_front_old_tokens_still_open_and_ask_to_be_encrypted_again():
    old_key, new_key = key(), key()
    old_ciphertext = TokenCipher([old_key]).encrypt(TOKEN)
    cipher = TokenCipher([new_key, old_key])
    assert cipher.decrypt(old_ciphertext) == TOKEN
    assert cipher.needs_rotation(old_ciphertext) is True
    fresh = cipher.encrypt(TOKEN)
    assert cipher.needs_rotation(fresh) is False
    assert TokenCipher([new_key]).decrypt(fresh) == TOKEN  # the old key is no longer needed for it


def test_with_the_old_key_removed_an_unrotated_token_is_lost():
    old_key, new_key = key(), key()
    old_ciphertext = TokenCipher([old_key]).encrypt(TOKEN)
    with pytest.raises(TokenUndecryptable):
        TokenCipher([new_key]).decrypt(old_ciphertext)


@pytest.mark.parametrize("keys", [[], ["not-a-key"], [key(), "short"]])
def test_no_key_or_a_malformed_key_is_refused_without_echoing_it(keys):
    with pytest.raises(ValueError) as error:
        TokenCipher(keys)
    assert not any(k in str(error.value) for k in keys if k)
