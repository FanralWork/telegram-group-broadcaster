import argparse
import asyncio
import yaml

from broadcaster import Broadcaster
from logger import setup_logger


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    telegram = config.get("telegram") if isinstance(config, dict) else None
    if not isinstance(telegram, dict) or not telegram.get("api_id") or not telegram.get("api_hash"):
        raise ValueError(f"Укажите telegram.api_id и telegram.api_hash в {path}")
    return config


def main():
    parser = argparse.ArgumentParser("Telegram Userbot Broadcaster")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all-chats", action="store_true")

    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        parser.error(f"Файл {args.config} не найден. Скопируйте config.example.yaml и заполните настройки.")
    except ValueError as e:
        parser.error(str(e))

    logger = setup_logger(
        level=config["logging"]["level"],
        log_file=config["logging"].get("file")
    )

    broadcaster = Broadcaster(
        config=config,
        logger=logger,
        dry_run=args.dry_run
    )

    try:
        asyncio.run(
            broadcaster.run_forever(
                only_groups=not args.all_chats
            )
        )
    except Exception as e:
        error_str = str(e).lower()
        if 'banned' in error_str or 'ban' in error_str:
            logger.error(f"Обнаружена ошибка бана: {e}")
            logger.error("Рекомендуется немедленно остановить рассылку и проверить состояние аккаунта!")
            return  # Вместо raise, просто выходим
        else:
            raise  # Если это не ошибка бана, то пробрасываем дальше


if __name__ == "__main__":
    main()
