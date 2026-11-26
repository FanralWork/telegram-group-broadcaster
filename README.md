# Telegram Broadcaster

A private Telegram user-account automation prototype with group filters, delivery intervals, and optional GigaChat integration.

## Stack and architecture

`main.py` reads YAML configuration and starts the asynchronous Telethon client. `broadcaster.py` handles chat selection, message preparation, and delivery. `logger.py` configures console and optional file logging. The sample configuration includes settings for a test group and a local message template.

## Local setup and verification

Use Python 3.13 and install the versions in `requirements.txt`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --help
```

For an account-level check, copy `config.example.yaml` to the ignored `config.yaml`, fill in the Telegram settings locally, and set the filter to the name of a group you control. The dry-run command may authenticate the account and create a Telethon session file. It does not send messages.

```powershell
.\.venv\Scripts\python.exe main.py --config config.yaml --dry-run
```

## Status and limitations

This is a private prototype. Account credentials, session files, chat names from logs, and real message templates belong in local storage. Optional GigaChat calls send prompts to an external service. The review verified CLI startup without signing in or sending messages.

## Кратко для портфолио

Закрытый эксперимент по автоматизации Telegram с фильтрацией групп, журналированием и опциональной генерацией текста через GigaChat. Применение требует контроля получателей и учётных данных.
