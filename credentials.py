"""Store one portal login in the operating system's macOS Keychain."""
import getpass
import json
import sys

from keyring.backends.macOS import Keyring
from keyring.errors import KeyringError

from club import ClubError, session

SERVICE = "sharks-mcp.hcsharkskids.tvoyklass.com"
ACCOUNT = "student-portal"


def load():
    try:
        entry = Keyring().get_password(SERVICE, ACCOUNT)
        if entry:
            account = json.loads(entry)
            return account["email"], account["password"]
    except (KeyringError, RuntimeError, ValueError, KeyError, TypeError):
        raise ClubError("Не удалось прочитать Sharks MCP из Связки ключей macOS.") from None
    raise ClubError("Сначала выполните локальную настройку credentials.py.")


def setup():
    if not sys.stdin.isatty():
        raise ClubError("Запустите настройку в интерактивном терминале.")
    print("Вход в https://hcsharkskids.tvoyklass.com для Sharks MCP")
    email = input("Email личного кабинета: ").strip()
    password = getpass.getpass("Пароль (ввод скрыт): ")
    if not email or not password:
        raise ClubError("Введите email и пароль кабинета.")
    with session(email, password) as club:
        count = len(club.account["users"])
    Keyring().set_password(SERVICE, ACCOUNT, json.dumps({"email": email, "password": password}))
    print(f"Вход подтверждён. Профилей учеников: {count}. Доступ сохранён в Связке ключей.")


if __name__ == "__main__":
    try:
        setup()
    except (ClubError, KeyringError, RuntimeError, KeyboardInterrupt, EOFError) as exc:
        sys.exit(str(exc) if isinstance(exc, ClubError) else "Настройка не завершена.")
