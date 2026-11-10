"""Encryption of the manager tokens the bot stores for linked users: Fernet from the `cryptography` package.

Keys come from BOT_ENCRYPTION_KEY. With several keys the first encrypts and all of them decrypt, so a key can be replaced
without everyone having to link again (add the new key in front, keep the old one until `needs_rotation` is false for
every stored token).
"""

from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class TokenUndecryptable(Exception):
    """No configured key opens this ciphertext (the key was lost or changed, or the stored bytes are damaged)."""


class TokenCipher:
    def __init__(self, keys: Sequence[str]):
        """Raises ValueError when there is no key or one of them is not a Fernet key."""
        if not keys:
            raise ValueError("at least one key is needed")
        fernets = [Fernet(key) for key in keys]  # ValueError for a malformed key, without echoing it
        self._primary = fernets[0]
        self._all = MultiFernet(fernets)

    def encrypt(self, token: str) -> bytes:
        return self._primary.encrypt(token.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._all.decrypt(ciphertext).decode("utf-8")
        except (InvalidToken, TypeError, ValueError):
            raise TokenUndecryptable("The stored token cannot be decrypted with the configured keys") from None

    def needs_rotation(self, ciphertext: bytes) -> bool:
        """True when only an older key opens it, so it should be encrypted again with the first key.
        Raises TokenUndecryptable when no key opens it."""
        try:
            self._primary.decrypt(ciphertext)
            return False
        except InvalidToken:
            pass
        except (TypeError, ValueError):
            raise TokenUndecryptable("The stored token cannot be decrypted with the configured keys") from None
        self.decrypt(ciphertext)
        return True
