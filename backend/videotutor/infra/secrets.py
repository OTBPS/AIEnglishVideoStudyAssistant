"""API keys in the operating system's credential store (Windows Credential Manager).

Never in settings.json, never in logs, never in the learning database: a
settings file gets copied into bug reports; the credential store does not.
"""

from __future__ import annotations

import contextlib
import logging

import keyring
import keyring.errors

log = logging.getLogger("secrets")


class KeyringSecretStore:
    def __init__(self, service: str) -> None:
        self._service = service

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(self._service, name)
        except keyring.errors.KeyringError as exc:
            log.warning("credential store unavailable: %s", type(exc).__name__)
            return None

    def set(self, name: str, value: str) -> None:
        keyring.set_password(self._service, name, value)

    def delete(self, name: str) -> None:
        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(self._service, name)
