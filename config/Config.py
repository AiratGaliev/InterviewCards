"""
Модуль конфигурации приложения InterviewCards.
Использует dataclass для типизации и читаемости.
Полностью совместим с Obsidian Spaced Repetition.
"""

import configparser
import os
import platform
import re
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class AppConfig:
    """
    Класс конфигурации приложения.
    Загружает настройки из config.ini и предоставляет удобный доступ.

    Совместимость с Obsidian Spaced Repetition:
    - Deck теги: #flashcards/category
    - Форматы: ::, :::, ?, ??, Cloze
    """
    _config_path: str = "config.ini"
    _config: configparser.ConfigParser = field(default_factory=configparser.ConfigParser, repr=False)

    # Основные настройки
    categories: List[str] = field(default_factory=list)
    category_id: int = 0
    documents: str = ""
    output: str = ""

    # Пути Obsidian и Anki
    obsidian_vault: str = ""
    anki_collection_media: str = ""
    materials_source: str = ""

    # Лимиты
    max_questions_per_deck: int = 500
    max_question_length: int = 2000
    max_answer_length: int = 5000

    # Теги Spaced Repetition
    card_tag: str = "#flashcards"  # Основной тег колоды

    # Разделители карточек (по умолчанию в SR)
    single_line_basic_sep: str = "::"
    single_line_bidirectional_sep: str = ":::"
    multi_line_basic_sep: str = "?"
    multi_line_bidirectional_sep: str = "??"

    # Cloze настройки
    cloze_start: str = "=="
    cloze_end: str = "=="

    def __post_init__(self):
        """Загрузка конфигурации после инициализации"""
        self._load_config()

    def _load_config(self) -> None:
        """Загружает конфигурацию из файла config.ini"""
        self._config.read(self._config_path, encoding='utf-8')

        if not self._config.sections():
            print(f"⚠️ Конфигурационный файл не найден или пуст: {self._config_path}")
            return

        # Основные настройки
        self.categories = self._parse_categories(
            self._config.get('main', 'categories', fallback='general')
        )
        self.category_id = self._config.getint('main', 'category_id', fallback=0)
        self.documents = self._expand_path(
            self._config.get('main', 'documents', fallback='~/Documents/InterviewCards/')
        )
        self.output = self._expand_path(
            self._config.get('main', 'output', fallback='~/Documents/InterviewCards/output/')
        )

        # Пути по платформе
        platform_section = self._get_platform_section()
        self.obsidian_vault = self._expand_path(
            self._config.get(platform_section, 'obsidian_vault', fallback='~/Documents/ObsidianVault/')
        )
        self.anki_collection_media = self._expand_path(
            self._config.get(platform_section, 'anki_collection_media', fallback='')
        )

        # Materials source внутри Obsidian Vault
        self.materials_source = os.path.join(self.obsidian_vault, "Interview", "Materials")

        # Лимиты
        self.max_questions_per_deck = self._config.getint(
            'limits', 'max_questions_per_deck', fallback=500
        )
        self.max_question_length = self._config.getint(
            'limits', 'max_question_length', fallback=2000
        )
        self.max_answer_length = self._config.getint(
            'limits', 'max_answer_length', fallback=5000
        )

        # Теги Spaced Repetition
        self.card_tag = self._config.get('spaced_repetition', 'card_tag', fallback='#flashcards')

        # Разделители
        self.single_line_basic_sep = self._config.get(
            'separators', 'single_line_basic', fallback='::'
        )
        self.single_line_bidirectional_sep = self._config.get(
            'separators', 'single_line_bidirectional', fallback=':::'
        )
        self.multi_line_basic_sep = self._config.get(
            'separators', 'multi_line_basic', fallback='?'
        )
        self.multi_line_bidirectional_sep = self._config.get(
            'separators', 'multi_line_bidirectional', fallback='??'
        )

        # Cloze настройки
        self.cloze_start = self._config.get('cloze', 'start', fallback='==')
        self.cloze_end = self._config.get('cloze', 'end', fallback='==')

    @staticmethod
    def _parse_categories(categories_str: str) -> List[str]:
        """Парсит строку категорий в список"""
        return [c.strip() for c in re.split(r'\s*,\s*', categories_str) if c.strip()]

    @staticmethod
    def _expand_path(path: str) -> str:
        """Расширяет путь с учётом домашней директории"""
        if path.startswith('~'):
            return os.path.expanduser(path)
        return path

    @staticmethod
    def _get_platform_section() -> str:
        """Определяет секцию конфигурации по платформе"""
        system = platform.system()
        if system == 'Windows':
            return 'directories-windows'
        return 'directories-linux'

    def create_directories(self) -> None:
        """Создаёт все необходимые директории"""
        directories = [
            self.documents,
            self.output,
            os.path.join(self.output, 'anki'),
            self.obsidian_vault,
            os.path.join(self.obsidian_vault, 'Interview', 'Materials'),
            os.path.join(self.obsidian_vault, 'Interview', 'Cards'),
            self.materials_source
        ]

        for dir_path in directories:
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
                print(f"✅ Директория создана: {dir_path}")

    def validate(self) -> bool:
        """Проверяет корректность конфигурации"""
        if not self.obsidian_vault:
            print("❌ Не указан путь к Obsidian Vault")
            return False
        if not self.categories:
            print("❌ Не указаны категории")
            return False
        return True

    def get_anki_deck_name(self, category: str) -> str:
        """
        Формирует имя колоды Anki для категории.

        Args:
            category: Категория карточек

        Returns:
            str: Имя колоды Anki (например, "Interview::Python")
        """
        formatted_category = category.replace('_', ' ').title()
        return f"Interview::{formatted_category}"

    def get_deck_tag(self, category: str) -> str:
        """
        Генерирует тег колоды для Spaced Repetition.
        Используется как fallback если deck не указан в frontmatter.

        Args:
            category: Категория карточек

        Returns:
            str: Тег колоды (например, "#flashcards/python")
        """
        if not category or category == 'general':
            return "#flashcards"
        return f"#flashcards/{category}"

    def get_cards_path(self, category: str) -> str:
        """
        Возвращает путь к папке карточек для категории.

        Args:
            category: Категория карточек

        Returns:
            str: Путь к папке карточек
        """
        return os.path.join(self.obsidian_vault, "Interview", "Cards", category)

    def get_materials_path(self, category: str) -> str:
        """
        Возвращает путь к папке материалов для категории.

        Args:
            category: Категория карточек

        Returns:
            str: Путь к папке материалов
        """
        return os.path.join(self.obsidian_vault, "Interview", "Materials", category)

    def get_separators(self) -> dict:
        """
        Возвращает словарь разделителей карточек.

        Returns:
            dict: Словарь с разделителями для всех типов карточек
        """
        return {
            'single_line_basic': self.single_line_basic_sep,
            'single_line_bidirectional': self.single_line_bidirectional_sep,
            'multi_line_basic': self.multi_line_basic_sep,
            'multi_line_bidirectional': self.multi_line_bidirectional_sep,
        }


# Глобальный экземпляр конфигурации (singleton pattern)
_config_instance: Optional[AppConfig] = None


def get_config(config_path: str = "config.ini") -> AppConfig:
    """
    Возвращает глобальный экземпляр конфигурации.
    Создаёт новый при первом вызове.

    Args:
        config_path: Путь к файлу конфигурации

    Returns:
        AppConfig: Экземпляр конфигурации
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = AppConfig(_config_path=config_path)
    return _config_instance


# Для обратной совместимости
Config = get_config()
