"""
Основной модуль с общей логикой генерации карточек.
Используется как Streamlit, так и CLI версиями.
Полностью совместим с Obsidian Spaced Repetition.
"""

import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config.Config import AppConfig
from logic.utils import (
    load_markdown_topics,
    parse_cards_from_markdown,
    generate_all_formats,
    clean_up_duplicates
)
from models.InterviewCard import InterviewCard

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Результат генерации карточек"""
    success: bool
    total_cards: int = 0
    output_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    topics_processed: int = 0


class CardGenerator:
    """
    Класс для генерации карточек из Markdown файлов.
    Инкапсулирует общую логику для Streamlit и CLI версий.

    Совместим с Obsidian Spaced Repetition форматами:
    - Single-line Basic: question::answer
    - Single-line Bidirectional: info1:::info2
    - Multi-line Basic: question\n?\nanswer
    - Multi-line Bidirectional: info1\n??\ninfo2
    - Cloze: text with ==hidden parts==
    """

    def __init__(self, config: AppConfig, use_reverse_cards: bool = True):
        """
        Инициализация генератора.

        Args:
            config: Конфигурация приложения
            use_reverse_cards: Использовать двухсторонние карточки
        """
        self.config = config
        self.use_reverse_cards = use_reverse_cards

    def validate_input_directory(self, input_dir: str) -> Tuple[bool, str]:
        """
        Проверяет существование и содержимое директории.

        Args:
            input_dir: Путь к директории

        Returns:
            Tuple[bool, str]: (валидность, сообщение)
        """
        if not os.path.exists(input_dir):
            return False, f"Папка не найдена: {input_dir}"

        topics = load_markdown_topics(input_dir)
        if not topics:
            return False, "Нет Markdown файлов в папке"

        return True, f"Найдено тем: {len(topics)}"

    def create_example_file(self, input_dir: str, template_path: Optional[str] = None) -> str:
        """
        Создаёт пример Markdown файла в директории.

        Args:
            input_dir: Путь к директории
            template_path: Путь к шаблону (опционально)

        Returns:
            str: Путь к созданному файлу
        """
        os.makedirs(input_dir, exist_ok=True)
        example_path = os.path.join(input_dir, "example_topic.md")

        # Пытаемся загрузить контент из файла шаблона
        content = ""
        if template_path and os.path.exists(template_path):
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                logger.error(f"Не удалось прочитать файл шаблона {template_path}: {e}")

        # Если контент не удалось загрузить - читаем из templates/example_topic.md
        if not content:
            content = self._get_example_content()

        if content:
            with open(example_path, 'w', encoding='utf-8') as f:
                f.write(content)

        return example_path

    def _get_example_content(self) -> str:
        """
        Загружает пример контента из файла шаблона templates/example_topic.md.

        Returns:
            str: Пример Markdown контента или пустая строка
        """
        # Определяем путь к файлу шаблона относительно текущего файла
        current_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(current_dir, "..", "templates", "example_topic.md")

        # Пытаемся загрузить контент из шаблона
        if os.path.exists(template_path):
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content:
                        return content
            except Exception as e:
                logger.error(f"Не удалось прочитать файл шаблона {template_path}: {e}")

        # Fallback: минимальный пример если шаблон не найден
        # Соответствует формату templates/example_topic.md
        logger.warning(f"Файл шаблона не найден: {template_path}, используется минимальный пример")
        return '''---
        tags: [flashcards/example]
        category: general
        ---
        
        # Пример карточки
        
        Что такое Spaced Repetition?::Метод обучения с повторением через увеличивающиеся интервалы
        
        #flashcards
        '''

    def copy_material_to_vault(
            self,
            source_path: str,
            topic_name: str,
            category: str
    ) -> Optional[str]:
        """
        Копирует файл материала в Obsidian Vault.

        Args:
            source_path: Путь к исходному файлу
            topic_name: Имя темы
            category: Категория

        Returns:
            Optional[str]: Путь к скопированному файлу или None
        """
        materials_path = self.config.get_materials_path(category)
        target_path = os.path.join(materials_path, f"{topic_name}.md")

        # Нормализация путей для сравнения
        source_abs = os.path.abspath(source_path)
        target_abs = os.path.abspath(target_path)
        vault_materials_root_abs = os.path.abspath(self.config.materials_source)

        # 1. Если исходный файл уже находится в целевой папке
        if source_abs == target_abs:
            return target_path

        # 2. Если файл уже внутри папки Materials
        if source_abs.startswith(vault_materials_root_abs + os.sep):
            return source_path

        # 3. Копируем файл
        try:
            os.makedirs(materials_path, exist_ok=True)
            shutil.copy2(source_path, target_path)
            logger.debug(f"Скопирован материал: {target_path}")
            return target_path
        except PermissionError:
            logger.warning(f"Не удалось скопировать {topic_name}: файл занят или нет прав")
            return None
        except Exception as e:
            logger.error(f"Ошибка копирования {topic_name}: {e}")
            return None

    def generate(
            self,
            input_dir: str,
            selected_categories: Optional[List[str]] = None,
            clean_duplicates: bool = False
    ) -> GenerationResult:
        """
        Выполняет генерацию карточек.

        Args:
            input_dir: Путь к папке с Markdown файлами
            selected_categories: Выбранные категории (None = все)
            clean_duplicates: Удалить дубликаты

        Returns:
            GenerationResult: Результат генерации
        """
        result = GenerationResult(success=False)

        # Валидация
        is_valid, message = self.validate_input_directory(input_dir)
        if not is_valid:
            result.errors.append(message)
            return result

        # Загрузка тем
        topics = load_markdown_topics(input_dir)
        if not topics:
            result.errors.append("Нет тем для обработки")
            return result

        # Подготовка категорий
        if selected_categories is None:
            selected_categories = self.config.categories

        all_cards: List[InterviewCard] = []
        card_id = 1

        # Обработка каждой темы
        for topic_name, topic_data in topics.items():
            topic_category = topic_data.get('category', 'general')

            # Проверка категории
            if selected_categories and topic_category not in selected_categories:
                result.warnings.append(f"Категория '{topic_category}' не выбрана: {topic_name}")
                continue

            # Парсинг карточек
            cards = parse_cards_from_markdown(topic_data['path'], card_id)

            if not cards:
                result.warnings.append(f"Нет валидных карточек в теме '{topic_name}'")
                continue

            card_id += len(cards)

            # Получаем путь к файлу материала
            material_file_path = self.copy_material_to_vault(
                topic_data['path'],
                topic_name,
                topic_category
            )

            # Вычисляем ссылку для Obsidian
            if material_file_path:
                try:
                    link_path = os.path.relpath(material_file_path, self.config.obsidian_vault)
                    link_path = link_path.replace('\\', '/')
                    if link_path.endswith('.md'):
                        link_path = link_path[:-3]
                except ValueError:
                    link_path = topic_name
            else:
                link_path = topic_name

            # Проставляем ссылку во все карточки
            for card in cards:
                card.source_note = link_path

            cards_path = self.config.get_cards_path(topic_category)
            anki_path = os.path.join(self.config.output, "anki")

            # Генерация файлов
            try:
                files = generate_all_formats(
                    cards,
                    topic_category,
                    cards_path,
                    anki_path,
                    use_reverse_cards=self.use_reverse_cards
                )
                result.output_files.extend(files)
                all_cards.extend(cards)
                result.topics_processed += 1
                logger.info(f"Тема '{topic_name}': {len(cards)} карточек")
            except Exception as e:
                result.errors.append(f"Ошибка генерации '{topic_name}': {e}")
                logger.error(f"Ошибка генерации '{topic_name}': {e}")

        # Удаление дубликатов
        if clean_duplicates and result.output_files:
            txt_files = [f for f in result.output_files if f.endswith('.txt')]
            if txt_files:
                removed = clean_up_duplicates(txt_files)
                if removed > 0:
                    result.warnings.append(f"Удалено дубликатов: {removed}")

        result.total_cards = len(all_cards)
        result.success = result.total_cards > 0

        return result

    def get_output_paths(self) -> Dict[str, str]:
        """
        Возвращает пути вывода для отображения.

        Returns:
            Dict[str, str]: Словарь путей
        """
        return {
            'obsidian_vault': self.config.obsidian_vault,
            'cards': os.path.join(self.config.obsidian_vault, 'Interview', 'Cards'),
            'materials': os.path.join(self.config.obsidian_vault, 'Interview', 'Materials'),
            'anki': os.path.join(self.config.output, 'anki'),
        }
