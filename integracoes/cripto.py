"""Cifra e decifra segredos dos usuários (senhas dos sites, sessões salvas) com Fernet (AES + HMAC)."""
from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(settings.APP_ENCRYPTION_KEY.encode())


def cifrar(texto: str) -> str:
    return _fernet().encrypt((texto or "").encode()).decode()


def decifrar(cifrado: str) -> str:
    return _fernet().decrypt(cifrado.encode()).decode() if cifrado else ""
