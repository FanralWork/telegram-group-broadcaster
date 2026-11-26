#!/usr/bin/env python3
"""
Безопасный запуск broadcaster с обработкой критических ошибок
"""

import asyncio
import sys
import traceback

from broadcaster import Broadcaster
from logger import setup_logger
from main import load_config


async def safe_main():
    try:
        # Загружаем конфигурацию
        config = load_config('config.yaml')

        # Создаем логгер
        logger = setup_logger(config['logging']['level'], config['logging'].get('file'))

        # Создаем broadcaster
        broadcaster = Broadcaster(config, logger, dry_run=False)

        logger.info("Запуск безопасного режима broadcaster...")

        # Запускаем broadcaster
        await broadcaster.run_forever(only_groups=True)

    except KeyboardInterrupt:
        print("\nРабота прервана пользователем")
    except Exception as e:
        print(f"Критическая ошибка: {e}")
        if "banned" in str(e).lower() or "ban" in str(e).lower():
            print("Обнаружена ошибка бана - рекомендуется немедленно остановить рассылку!")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(safe_main())
