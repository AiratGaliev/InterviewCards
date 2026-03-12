"""
Модуль сохранения пользовательских настроек между сессиями.
Хранит настройки в JSON-файле в директории проекта.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SETTINGS_FILE = ".interview_cards_settings.json"


@dataclass
class UserSettings:
    """
    Пользовательские настройки, сохраняемые между сессиями.

    Attributes:
        input_dir: Путь к папке с исходными материалами
        selected_categories: Выбранные категории для генерации
        export_format: Формат экспорта ('both', 'obsidian', 'anki')
        use_reverse_cards: Генерировать обратные карточки
        clean_duplicates: Удалять дубликаты при генерации
        preview_length: Длина предпросмотра контента (символы)
        auto_save: Автосохранение настроек при генерации
        last_input_dir: Последняя использованная папка
        last_saved: Время последнего сохранения (ISO)
    """
    input_dir: str = ""
    selected_categories: List[str] = field(default_factory=list)
    export_format: str = "both"
    use_reverse_cards: bool = True
    clean_duplicates: bool = False
    preview_length: int = 1500
    auto_save: bool = True
    last_input_dir: str = ""
    last_saved: str = ""

    def save(self, path: str = SETTINGS_FILE) -> bool:
        """
        Сохраняет настройки в JSON-файл.

        Args:
            path: Путь к файлу настроек

        Returns:
            bool: True если сохранение успешно
        """
        try:
            self.last_saved = datetime.now().isoformat(timespec='seconds')
            data = asdict(self)

            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"Настройки сохранены: {path}")
            return True

        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")
            return False

    @classmethod
    def load(cls, path: str = SETTINGS_FILE) -> 'UserSettings':
        """
        Загружает настройки из JSON-файла.
        Возвращает значения по умолчанию если файл не найден.

        Args:
            path: Путь к файлу настроек

        Returns:
            UserSettings: Загруженные или дефолтные настройки
        """
        if not os.path.exists(path):
            logger.debug(f"Файл настроек не найден: {path}")
            return cls()

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Фильтруем только известные поля (для обратной совместимости)
            valid_fields = set(cls.__dataclass_fields__.keys())
            filtered = {k: v for k, v in data.items() if k in valid_fields}

            settings = cls(**filtered)
            logger.info(f"Настройки загружены: {path}")
            return settings

        except json.JSONDecodeError as e:
            logger.warning(f"Ошибка парсинга настроек: {e}")
            return cls()
        except Exception as e:
            logger.warning(f"Ошибка загрузки настроек: {e}")
            return cls()

    def to_dict(self) -> Dict[str, Any]:
        """Конвертирует настройки в словарь."""
        return asdict(self)

    def merge_with_config(self, config) -> 'UserSettings':
        """
        Объединяет сохранённые настройки с текущим конфигом.
        Валидирует категории и пути.

        Args:
            config: AppConfig

        Returns:
            UserSettings: Настройки с валидированными значениями
        """
        # Валидация категорий — оставляем только существующие
        if self.selected_categories and config.categories:
            self.selected_categories = [
                c for c in self.selected_categories
                if c in config.categories
            ]

        # Путь по умолчанию из конфига
        if not self.input_dir:
            self.input_dir = config.materials_source

        # Валидация формата экспорта
        if self.export_format not in ('both', 'obsidian', 'anki'):
            self.export_format = 'both'

        return self

    def get_display_info(self) -> str:
        """Возвращает краткое описание настроек для отображения."""
        parts = []
        if self.export_format == 'both':
            parts.append("Obsidian + Anki")
        elif self.export_format == 'obsidian':
            parts.append("Obsidian")
        else:
            parts.append("Anki")

        if self.use_reverse_cards:
            parts.append("+ reverse")
        if self.clean_duplicates:
            parts.append("+ дедупликация")

        return " | ".join(parts)

    @staticmethod
    def get_settings_path() -> str:
        """Возвращает путь к файлу настроек."""
        return os.path.abspath(SETTINGS_FILE)
