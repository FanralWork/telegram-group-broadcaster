import asyncio
import json
import random
import time
import uuid
from datetime import datetime
import base64

import requests

from telethon import TelegramClient, errors
from telethon.tl.types import Chat, Channel
from gigachat import GigaChat
from gigachat.exceptions import ResponseError

class Broadcaster:
    def __init__(self, config: dict, logger, dry_run=False):
        tg = config["telegram"]
        limits = config["limits"]
        message_template = config["message_templates"]

        self.client = TelegramClient(
            tg["session"],
            tg["api_id"],
            tg["api_hash"]
        )

        # Load message templates from the separate file
        try:
            with open(message_template["file"], "r", encoding="utf-8") as f:
                self.texts = json.load(f)
        except FileNotFoundError:
            self.texts = config.get("message", {}).get("texts", [])

        # Initialize GigaChat client if credentials are provided
        giga_config = config.get("gigachat", {})
        self.giga_client = None
        self.access_token = giga_config.get("access_token", "").strip()
        self.authorization_key = giga_config.get("authorization_key", "").strip()
        self.ca_bundle_file = giga_config.get("ca_bundle_file", None)
        self.token_expiry = 0
        
        if self.authorization_key:
            try:
                giga_kwargs = {}
                
                # Add CA bundle file for SSL verification if provided
                if self.ca_bundle_file:
                    giga_kwargs["ca_bundle_file"] = self.ca_bundle_file
                
                giga_kwargs["credentials"] = self.authorization_key
                self.giga_client = GigaChat(**giga_kwargs)
                self.use_ai_generation = True
                logger.info("[GIGACHAT] Инициализация с Authorization key")
            except Exception as e:
                logger.error(f"Ошибка инициализации GigaChat клиента: {e}")
                self.use_ai_generation = False
        elif self.access_token:
            self.use_ai_generation = True
            logger.info("[GIGACHAT] Инициализация с Access token")
        else:
            self.use_ai_generation = False
            logger.info("[GIGACHAT] Не настроен - работа без ИИ")

        self.include = [k.lower() for k in config["filters"]["include_keywords"]]
        self.exclude = [k.lower() for k in config["filters"]["exclude_keywords"]]
        
        # Фильтр по количеству участников в группе
        self.min_group_participants = config["filters"].get("min_participants", 10)
        self.max_group_participants = config["filters"].get("max_participants", 5000)

        self.msg_delay_min = limits["per_message_interval"]["min"]
        self.msg_delay_max = limits["per_message_interval"]["max"]

        self.logger = logger
        self.dry_run = dry_run
        self.current_message = None

        # Time restriction configuration
        self.time_restriction_config = config.get("time_restriction", {})

        # Tagging configuration
        tagging_config = config.get("tagging", {})
        self.tagging_enabled = tagging_config.get("enabled", False)
        self.tag_duration = tagging_config.get("tag_duration", 5)
        self.tag_min_participants = tagging_config.get("min_participants", 1)
        self.tag_max_participants = tagging_config.get("max_participants", 3)
        self.include_admins = tagging_config.get("include_admins", False)
        self.include_bots = tagging_config.get("include_bots", False)

    def _get_gigachat_token(self):
        """Получает новый access token от GigaChat API"""
        if not self.authorization_key:
            return None
            
        try:
            url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'RqUID': str(uuid.uuid4()),
                'Authorization': f'Basic {self.authorization_key}'
            }
            
            payload = {'scope': 'GIGACHAT_API_PERS'}
            
            response = requests.post(
                url, 
                headers=headers, 
                data=payload, 
                verify=self.ca_bundle_file if self.ca_bundle_file else True
            )
            
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get('access_token', '')
                # Токен действует 30 минут, обновляем за 5 минут до истечения
                self.token_expiry = time.time() + 25 * 60
                self.logger.info("[GIGACHAT] Получен новый access token")
                return self.access_token
            else:
                self.logger.error(f"[GIGACHAT] Ошибка получения токена: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            self.logger.error(f"[GIGACHAT] Ошибка при получении токена: {e}")
            return None

    def _ensure_gigachat_token(self):
        """Проверяет актуальность токена и обновляет при необходимости"""
        if not self.authorization_key:
            return self.access_token is not None
            
        # Если токена нет или он истекает
        if not self.access_token or time.time() > self.token_expiry:
            self._get_gigachat_token()
            
        return self.access_token is not None

    def _is_time_allowed(self) -> bool:
        """Check if the current time is within the allowed range."""
        # Time restriction configuration
        time_restriction_config = getattr(self, 'time_restriction_config', {})
        enabled = time_restriction_config.get('enabled', False)
        
        if not enabled:
            return True

        current_time = datetime.now()
        current_hour = current_time.hour
        current_minute = current_time.minute
        
        start_hour = time_restriction_config.get('start_hour', 7)
        end_hour = time_restriction_config.get('end_hour', 0)
        end_minute = time_restriction_config.get('end_minute', 30)

        # Handle case where time range crosses midnight (e.g., 7 AM to 0:30 AM)
        if (start_hour > end_hour) or (end_hour == 0 and end_minute >= 0 and start_hour > 0):
            # Time range crosses midnight
            if (start_hour <= current_hour <= 23) or \
               (current_hour == 0 and current_minute <= end_minute):
                return True
            else:
                return False
        else:
            # Normal case where time range doesn't cross midnight
            if current_hour < start_hour:
                return False
            elif current_hour > end_hour:
                return False
            elif current_hour == end_hour and current_minute > end_minute:
                return False
            else:
                return True

    async def _get_group_participants(self, entity):
        """Get list of participants from a group/channel."""
        if not self.tagging_enabled:
            return []
        
        try:
            participants = []
            async for participant in self.client.iter_participants(entity):
                # Skip bots if not included
                if not self.include_bots and participant.bot:
                    continue
                # Skip admins if not included
                if not self.include_admins and hasattr(participant, 'participant') and \
                   hasattr(participant.participant, 'admin_rights'):
                    continue
                participants.append(participant)
            return participants
        except Exception as e:
            self.logger.error(f"[TAGGING_ERROR] Could not get participants: {e}")
            return []

    def _select_random_participants(self, participants):
        """Select random participants to tag."""
        if not participants:
            return []
        
        # Determine how many participants to tag
        num_to_tag = random.randint(
            min(self.min_participants, len(participants)),
            min(self.max_participants, len(participants))
        )
        
        # Randomly select participants
        selected = random.sample(participants, num_to_tag)
        return selected

    def _create_mention_message(self, original_message, participants):
        """Create a message with mentions in 'Привет @' format."""
        if not participants:
            return original_message
        
        mentions = []
        for participant in participants:
            # Create mention using Telegram's inline mention format
            if participant.username:
                mentions.append(f"@{participant.username}")
            else:
                # Use full name if no username
                name = participant.first_name or ""
                if participant.last_name:
                    name += f" {participant.last_name}"
                # Telegram inline mention format: [display_text](tg://user?id=user_id)
                mentions.append(f"[{name.strip()}](tg://user?id={participant.id})")
        
        # Create the initial message as "Привет @" followed by mentions
        mention_text = " ".join(mentions)
        tagged_message = f"Привет {mention_text}"
        
        return tagged_message

    def _chat_allowed(self, name: str) -> bool:
        name = name.lower()

        if self.include and not any(k in name for k in self.include):
            return False
        if any(k in name for k in self.exclude):
            return False

        return True

    def _generate_ai_message(self):
        """Generate a message using GigaChat AI based on examples from self.texts"""
        # Проверяем и обновляем токен при необходимости
        if not self._ensure_gigachat_token():
            self.logger.warning("[GIGACHAT] Нет токена, использую fallback")
            return self._select_message_from_examples()

        try:
            # Создаём клиент с access_token если giga_client не инициализирован
            if not self.giga_client and self.access_token:
                giga_kwargs = {}
                if self.ca_bundle_file:
                    giga_kwargs["ca_bundle_file"] = self.ca_bundle_file
                giga_kwargs["access_token"] = self.access_token
                self.giga_client = GigaChat(**giga_kwargs)
            
            if not self.giga_client:
                return self._select_message_from_examples()
            
            # Create a prompt for the AI based on the example messages
            examples_text = "\n\n".join(self.texts[:5])  # Use first 5 examples

            prompt = f"""Создай уникальное рекламное сообщение на основе следующих примеров:

{examples_text}

Требования:
1. Сохрани стиль и структуру оригинальных примеров
2. Сделай сообщение максимально уникальным — измени формулировки, порядок слов, лексику
3. Не копируй фразы из примеров дословно — перефразируй их
4. Сообщение должно быть кратким (100-150 символов)
5. Выгляди естественно, без спама и навязчивости
6. В конце добавь ОДИН призыв к действию, выделенный жирным: **По всем вопросам пишите мне в личные сообщения!** или **Пишите в ЛС!** или **Обращайтесь в личные сообщения!**
7. Не повторяй призыв к действию в тексте — только один раз в конце
8. Не используй #

Сгенерируй одно уникальное сообщение:"""

            response = self.giga_client.chat(prompt)

            ai_generated_message = response.choices[0].message.content.strip()
            return ai_generated_message

        except ResponseError as e:
            self.logger.error(f"Ошибка ответа от GigaChat: {e}")
            return self._select_message_from_examples()
        except Exception as e:
            error_msg = str(e)
            if 'SSL' in error_msg or 'CERTIFICATE' in error_msg:
                self.logger.warning(f"[SSL_ERROR] Проблема с SSL сертификатом GigaChat. Используйте режим без ИИ или настройте сертификаты.")
                self.use_ai_generation = False  # Отключаем ИИ до перезапуска
            else:
                self.logger.error(f"Ошибка при генерации сообщения с помощью ИИ: {e}")
            return self._select_message_from_examples()

    def _select_message_from_examples(self):
        """Выбирает сообщение из примеров, применяя случайные изменения для уникальности"""
        # Выбираем случайное сообщение из списка
        message = random.choice(self.texts)

        # Случайные вариации призыва к действию с жирным форматированием
        cta_variations = [
            "\n\n**По всем вопросам пишите мне в личные сообщения!**",
            "\n\n**Пишите мне в личные сообщения!**",
            "\n\n**По вопросам о работе писать мне в личные сообщения!**",
            "\n\n**Для того чтобы начать работать, пишите мне в личные сообщения!**",
            "\n\n**Пишите мне в личные сообщения в Телеграм!**",
            "\n\n**Если заинтересовало — обращайтесь в ЛС!**",
            "\n\n**За подробностями обращайтесь в личные сообщения!**",
            "\n\n**Все вопросы в личные сообщения!**",
        ]

        # Удаляем существующий призыв к действию и маркеры заголовков
        cta_patterns = [
            "По всем вопросам пишите мне в личные сообщения!",
            "Пишите мне в личные сообщения!",
            "По вопросам о работе писать мне в личные сообщения!",
            "Для того чтобы начать работать, пишите мне в личные сообщения!",
            "Пишите мне в личные сообщения в Телеграм!",
            "пишите мне в личные сообщения",
            "ПИШИТЕ МНЕ В ЛИЧНЫЕ СООБЩЕНИЯ",
            "обращайтесь в ЛС",
            "пишите в ЛС",
        ]

        cleaned_message = message
        for pattern in cta_patterns:
            cleaned_message = cleaned_message.replace(pattern, "").strip()
        
        # Удаляем маркеры заголовков из начала сообщения
        cleaned_message = cleaned_message.lstrip('#').strip()

        # Добавляем случайный призыв к действию
        random_cta = random.choice(cta_variations)

        return cleaned_message + random_cta

    def _select_message(self):
        # Use AI generation if available, otherwise use original method
        if self.use_ai_generation:
            return self._generate_ai_message()
        else:
            return self._select_message_from_examples()

    async def _safe_send(self, entity, name):
        attempt_count = 0

        while attempt_count < 3:
            try:
                if self.dry_run:
                    self.logger.info(f"[DRY] {name}")
                    return True

                # Check if tagging is enabled and this is a group/channel
                if self.tagging_enabled and isinstance(entity, (Chat, Channel)):
                    # Get group participants
                    participants = await self._get_group_participants(entity)
                    
                    if participants:
                        # Select random participants to tag
                        selected_participants = self._select_random_participants(participants)
                        
                        if selected_participants:
                            # Create message with mentions
                            tagged_message = self._create_mention_message(self.current_message, selected_participants)
                            
                            # Send the message with mentions
                            sent_message = await self.client.send_message(entity, tagged_message)
                            self.logger.info(f"[SENT_WITH_TAGS] {name} (tagged {len(selected_participants)} participants)")
                            
                            # Wait for the specified duration
                            await asyncio.sleep(self.tag_duration)
                            
                            # Edit the message to remove mentions (keeping only the original message)
                            await self.client.edit_message(entity, sent_message.id, self.current_message)
                            self.logger.info(f"[EDITED] {name} (removed tags after {self.tag_duration}s)")
                        else:
                            # No participants to tag, send original message
                            await self.client.send_message(entity, self.current_message)
                            self.logger.info(f"[SENT] {name}")
                    else:
                        # No participants found, send original message
                        await self.client.send_message(entity, self.current_message)
                        self.logger.info(f"[SENT] {name}")
                else:
                    # Tagging disabled or not a group/channel, send original message
                    await self.client.send_message(entity, self.current_message)
                    self.logger.info(f"[SENT] {name}")
                return True

            except errors.FloodWaitError as e:
                wait = e.seconds + random.randint(10, 30)
                self.logger.warning(f"[FLOOD] {name} → ждём {wait} сек")
                await asyncio.sleep(wait)
                return await self._safe_send(entity, name)

            except errors.UserBotError as e:
                self.logger.error(f"[USER_BOT_ERROR] {name}: {e}")
                # Ошибка, связанная с пользовательским ботом, возможно бан
                return False
            except errors.ChatAdminRequiredError as e:
                self.logger.error(f"[ADMIN_REQUIRED] {name}: Требуются права администратора для отправки сообщений {e}")
                return False
            except errors.MessageNotModifiedError as e:
                self.logger.error(f"[MSG_NOT_MODIFIED] {name}: Сообщение не было изменено {e}")
                return False
            except errors.SlowModeWaitError as e:
                self.logger.error(f"[SLOW_MODE] {name}: Необходимо подождать перед следующей отправкой {e}")
                await asyncio.sleep(e.seconds)
                return False
            except errors.ChatWriteForbiddenError as e:
                self.logger.error(f"[WRITE_FORBIDDEN] {name}: Запрещено писать в этот чат {e}")
                return False
            except errors.ChannelPrivateError as e:
                self.logger.error(f"[CHANNEL_PRIVATE] {name}: Канал/чат является приватным {e}")
                return False
            except errors.UserBannedInChannelError as e:
                self.logger.error(f"[BANNED_RIGHTS] {name}: У аккаунта есть запреты на отправку сообщений {e}")
                # При получении этой ошибки прекращаем попытки отправки в этот чат
                return False
            except errors.YouBlockedUserError as e:
                self.logger.error(f"[YOU_BLOCKED_USER] {name}: Вы заблокировали этого пользователя {e}")
                return False
            except Exception as e:
                # Проверяем, содержит ли ошибка указание на бан
                error_str = str(e).lower()
                if 'banned' in error_str or 'ban' in error_str or 'restricted' in error_str:
                    self.logger.error(f"[BANNED] {name}: Аккаунт заблокирован от отправки сообщений в этот чат {e}")
                    # При получении бана, возможно, стоит прекратить рассылку
                    raise e
                else:
                    self.logger.error(f"[ERROR] {name}: {e}")
                    # Увеличиваем счетчик попыток и делаем задержку перед повторной попыткой
                    attempt_count += 1
                    if attempt_count < 3:
                        # Экспоненциальная задержка: 2^attempt_count * 10 секунд
                        delay = (2 ** attempt_count) * 10 + random.randint(1, 10)
                        self.logger.info(f"[RETRY] {name} → попытка {attempt_count}/3, ждём {delay} сек")
                        await asyncio.sleep(delay)
                    else:
                        self.logger.error(f"[FAILED] {name} → все попытки исчерпаны")
                        return False

    async def _night_pause(self):
        """Пауза до начала разрешённого времени (7 утра)"""
        self.logger.info("[PAUSE] Ожидание до 7:00")
        await asyncio.sleep(3600)  # проверяем каждый час

    async def _connect_with_2fa(self):
        """Подключение к Telegram с поддержкой 2FA (облачный пароль)"""
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            self.logger.info("📱 Требуется авторизация")
            
            # Запрос номера телефона
            phone = input("Введите номер телефона в международном формате: ")
            await self.client.send_code_request(phone)
            
            # Запрос кода из Telegram
            code = input("Введите код из Telegram: ")
            
            try:
                await self.client.sign_in(phone, code)
            except errors.SessionPasswordNeededError:
                # Запрос облачного пароля (2FA)
                self.logger.info("🔐 Включена двухфакторная аутентификация")
                password = input("Введите облачный пароль: ")
                await self.client.sign_in(password=password)
            
            self.logger.info("✅ Успешная авторизация")

    async def _get_group_participants_count(self, entity):
        """Получает количество участников в группе"""
        try:
            # Для каналов и чатов используем разные атрибуты
            if hasattr(entity, 'participants_count'):
                return entity.participants_count
            elif hasattr(entity, 'count'):
                return entity.count
            return 0
        except Exception:
            return 0

    async def _check_participants_filter(self, entity):
        """Проверяет, подходит ли группа по количеству участников"""
        count = await self._get_group_participants_count(entity)
        return self.min_group_participants <= count <= self.max_group_participants, count

    async def run_forever(self, only_groups=True):
        """Основной цикл: отправляет сообщения по очереди во все группы, затем начинает заново"""
        # Подключение с поддержкой 2FA
        await self._connect_with_2fa()

        while True:
            # Проверяем разрешённое время (7:00 - 0:30)
            if not self._is_time_allowed():
                self.logger.info("⏰ Вне рабочего времени (7:00 - 0:30). Ожидание...")
                await asyncio.sleep(60)
                continue

            self.logger.info("🚀 Сбор групп для рассылки")

            dialogs = await self.client.get_dialogs()
            self.logger.info(f"[INFO] Найдено {len(dialogs)} диалогов")

            # Собираем все подходящие группы
            eligible_groups = []
            skipped_filters = 0
            skipped_participants = 0
            non_groups = 0

            for dialog in dialogs:
                entity = dialog.entity
                name = dialog.name or "Без имени"

                if only_groups:
                    if not isinstance(entity, (Chat, Channel)):
                        non_groups += 1
                        continue
                    if isinstance(entity, Channel) and not getattr(entity, "megagroup", False):
                        non_groups += 1
                        continue

                if not self._chat_allowed(name):
                    skipped_filters += 1
                    continue

                # Проверка по количеству участников
                is_ok, count = await self._check_participants_filter(entity)
                if not is_ok:
                    skipped_participants += 1
                    self.logger.debug(f"[PARTICIPANTS-SKIP] {name}: {count} участников (требуется {self.min_group_participants}-{self.max_group_participants})")
                    continue

                eligible_groups.append(dialog)

            # Перемешиваем группы для разнообразия порядка отправки
            if eligible_groups:
                random.shuffle(eligible_groups)
                self.logger.info(f"[SHUFFLE] Группы перемешаны")
            else:
                self.logger.warning("[WARNING] Нет подходящих групп!")
                await self._night_pause()
                continue

            self.logger.info(f"[INFO] Пропущено не-групп: {non_groups}")
            self.logger.info(f"[INFO] Пропущено по фильтрам: {skipped_filters}")
            self.logger.info(f"[INFO] Пропущено по участникам: {skipped_participants}")
            self.logger.info(f"[INFO] Найдено {len(eligible_groups)} групп для рассылки")

            # Отправляем по очереди в каждую группу
            for i, dialog in enumerate(eligible_groups, 1):
                # Снова проверяем время перед каждой отправкой
                if not self._is_time_allowed():
                    self.logger.info("⏰ Время вышло. Продолжим в 7:00")
                    await self._night_pause()

                entity = dialog.entity
                name = dialog.name or "Без имени"

                self.current_message = self._select_message()
                self.logger.info(f"[{i}/{len(eligible_groups)}] Отправка в {name}")

                ok = await self._safe_send(entity, name)
                if not ok:
                    self.logger.warning(f"[FAILED] Не удалось отправить в {name}")

                # Пауза 10 минут между сообщениями
                delay = random.uniform(self.msg_delay_min, self.msg_delay_max)
                self.logger.info(f"[DELAY] Ожидание {delay:.0f} сек до следующего сообщения")
                await asyncio.sleep(delay)

            self.logger.info("✅ Все группы пройдены. Начинаем заново")
