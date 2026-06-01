import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings


class SecretBoxError(ValueError):
    pass


class SecretBox:
    def __init__(self, settings: Settings):
        secret = settings.app_secret_key.strip() or settings.app_api_token.strip()
        if not secret:
            raise SecretBoxError("APP_SECRET_KEY or APP_API_TOKEN is required to store Apollo API keys.")
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretBoxError("Stored Apollo API key could not be decrypted. Check APP_SECRET_KEY.") from exc
