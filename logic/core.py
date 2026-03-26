"""
Основной модуль с общей логикой генерации карточек.
Используется как Streamlit, так и CLI версиями.
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
    clean_up_duplicates, normalize_text_key, DuplicateInfo
)
from models.InterviewCard import InterviewCard

logger = logging.getLogger(__name__)


def _category_matches(
        topic_category: str,
        selected_categories: List[str],
) -> bool:
    """Иерархическое сравнение категорий.

    - 'java/core' совпадает с выбранной 'java/core' (точно)
    - 'java/core' совпадает с выбранной 'java' (родитель)
    - 'java' НЕ совпадает с выбранной 'java/core' (ребёнок)
    """
    for sel_cat in selected_categories:
        if topic_category == sel_cat:
            return True
        if topic_category.startswith(sel_cat + '/'):
            return True
    return False


@dataclass
class GenerationResult:
    """Результат генерации карточек"""
    success: bool
    total_cards: int = 0
    output_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    topics_processed: int = 0
    cards_by_type: Dict[str, int] = field(default_factory=dict)
    cards_by_category: Dict[str, int] = field(default_factory=dict)
    duplicates_found: List = field(default_factory=list)
    duplicates_removed: List = field(default_factory=list)


class CardGenerator:
    """
    Класс для генерации карточек из Markdown файлов.
    Инкапсулирует общую логику для Streamlit и CLI версий.
    """

    def __init__(self, config: AppConfig, use_reverse_cards: bool = True):
        self.config = config
        self.use_reverse_cards = use_reverse_cards

    def validate_input_directory(self, input_dir: str) -> Tuple[bool, str]:
        """Проверяет существование и содержимое директории."""
        if not os.path.exists(input_dir):
            return False, f"Папка не найдена: {input_dir}"

        topics = load_markdown_topics(input_dir)
        if not topics:
            return False, "Нет Markdown файлов в папке"

        return True, f"Найдено тем: {len(topics)}"

    def get_topics_summary(
            self,
            input_dir: str,
            selected_categories: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Возвращает сводку по темам с учётом фильтра категорий.

        Args:
            input_dir: Путь к директории
            selected_categories: Фильтр категорий (None = все)

        Returns:
            List[Dict]: Список словарей с метаданными тем
        """
        topics = load_markdown_topics(input_dir)
        summary = []

        for name, data in topics.items():
            category = data.get('category', 'general')

            # Фильтрация по категории
            if (selected_categories
                    and category not in selected_categories):
                continue

            cards = parse_cards_from_markdown(data['path'])

            type_counts = {}
            for card in cards:
                type_name = card.card_type.value
                type_counts[type_name] = type_counts.get(type_name, 0) + 1

            summary.append({
                'name': name,
                'path': data['path'],
                'category': category,
                'deck_name': data.get('deck_name', ''),
                'total_cards': len(cards),
                'cards_by_type': type_counts,
                'has_cloze': 'cloze' in type_counts,
                'has_code': any(c.has_code() for c in cards),
            })

        return summary

    def preview_cards(self, file_path: str) -> List[InterviewCard]:
        """
        Парсит и возвращает карточки из файла для предпросмотра.

        Args:
            file_path: Путь к Markdown файлу

        Returns:
            List[InterviewCard]: Список карточек
        """
        return parse_cards_from_markdown(file_path)

    def create_example_file(
            self,
            input_dir: str,
            template_path: Optional[str] = None
    ) -> str:
        """Создаёт пример Markdown файла в директории."""
        os.makedirs(input_dir, exist_ok=True)
        example_path = os.path.join(input_dir, "example_topic.md")

        content = ""
        if template_path and os.path.exists(template_path):
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                logger.error(
                    f"Не удалось прочитать шаблон {template_path}: {e}"
                )

        if not content:
            content = self._get_example_content()

        if content:
            with open(example_path, 'w', encoding='utf-8') as f:
                f.write(content)

        return example_path

    def _get_example_content(self) -> str:
        """Загружает пример контента из файла шаблона."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(
            current_dir, "..", "templates", "example_topic.md"
        )

        if os.path.exists(template_path):
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content:
                        return content
            except Exception as e:
                logger.error(f"Не удалось прочитать шаблон: {e}")

        logger.warning("Шаблон не найден, используется минимальный пример")
        return (
            '---\n'
            'tags: [flashcards/example]\n'
            'category: general\n'
            '---\n\n'
            '# Пример карточки\n\n'
            'Что такое Spaced Repetition?::'
            'Метод обучения с повторением через '
            'увеличивающиеся интервалы\n'
        )

    def copy_material_to_vault(
            self,
            source_path: str,
            topic_name: str,
            category: str
    ) -> Optional[str]:
        """Копирует файл материала в Obsidian Vault."""
        materials_path = self.config.get_materials_path(category)
        target_path = os.path.join(materials_path, f"{topic_name}.md")

        source_abs = os.path.abspath(source_path)
        target_abs = os.path.abspath(target_path)
        vault_root_abs = os.path.abspath(self.config.materials_source)

        if source_abs == target_abs:
            return target_path

        if source_abs.startswith(vault_root_abs + os.sep):
            return source_path

        try:
            os.makedirs(materials_path, exist_ok=True)
            shutil.copy2(source_path, target_path)
            logger.debug(f"Скопирован материал: {target_path}")
            return target_path
        except PermissionError:
            logger.warning(
                f"Не удалось скопировать {topic_name}: нет прав"
            )
            return None
        except Exception as e:
            logger.error(f"Ошибка копирования {topic_name}: {e}")
            return None

    def generate(
            self,
            input_dir: str,
            selected_categories: Optional[List[str]] = None,
            clean_duplicates: bool = False,
            export_format: str = "both",
            use_reverse_cards: Optional[bool] = None,
            progress_callback=None,
    ) -> GenerationResult:
        """
        Выполняет генерацию карточек.

        Args:
            input_dir: Путь к папке с Markdown файлами
            selected_categories: Выбранные категории (None = все)
            clean_duplicates: Удалить дубликаты
            export_format: Формат ('both', 'obsidian', 'anki')
            use_reverse_cards: Обратные карточки (None = из __init__)
            progress_callback: Функция обратного вызова для прогресса
                               callback(current, total, topic_name)

        Returns:
            GenerationResult: Результат генерации
        """
        result = GenerationResult(success=False)

        # Определяем параметр reverse
        reverse = (
            use_reverse_cards
            if use_reverse_cards is not None
            else self.use_reverse_cards
        )

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
        # selected_categories=None → обработать все темы
        # selected_categories=[] → тоже все темы
        # selected_categories=['java'] → только java и java/*

        all_cards: List[InterviewCard] = []
        card_id = 1
        total_topics = len(topics)

        # Обработка каждой темы
        for idx, (topic_name, topic_data) in enumerate(topics.items()):
            topic_category = topic_data.get('category', 'general')

            # Прогресс
            if progress_callback:
                progress_callback(idx, total_topics, topic_name)

            # Проверка категории
            if (selected_categories
                    and not _category_matches(
                        topic_category, selected_categories
                    )):
                result.warnings.append(
                    f"Категория '{topic_category}' "
                    f"не выбрана: {topic_name}"
                )
                continue

            # Парсинг карточек
            cards = parse_cards_from_markdown(topic_data['path'], card_id)

            if not cards:
                result.warnings.append(
                    f"Нет валидных карточек в теме '{topic_name}'"
                )
                continue

            card_id += len(cards)

            # Статистика по типам
            for card in cards:
                type_name = card.card_type.value
                result.cards_by_type[type_name] = (
                        result.cards_by_type.get(type_name, 0) + 1
                )
                result.cards_by_category[topic_category] = (
                        result.cards_by_category.get(topic_category, 0) + 1
                )

            # Копирование материала в Vault
            material_file_path = self.copy_material_to_vault(
                topic_data['path'],
                topic_name,
                topic_category
            )

            # Вычисляем ссылку для Obsidian
            if material_file_path:
                try:
                    link_path = os.path.relpath(
                        material_file_path, self.config.obsidian_vault
                    )
                    link_path = link_path.replace('\\', '/')
                    if link_path.endswith('.md'):
                        link_path = link_path[:-3]
                except ValueError:
                    link_path = topic_name
            else:
                link_path = topic_name

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
                    use_reverse_cards=reverse,
                    formats=export_format,
                )
                result.output_files.extend(files)
                all_cards.extend(cards)
                result.topics_processed += 1
                logger.info(
                    f"Тема '{topic_name}': {len(cards)} карточек"
                )
            except Exception as e:
                result.errors.append(
                    f"Ошибка генерации '{topic_name}': {e}"
                )
                logger.error(f"Ошибка генерации '{topic_name}': {e}")

        # Финальный прогресс
        if progress_callback:
            progress_callback(total_topics, total_topics, "Завершено")

        # ── Детекция дубликатов на уровне исходных материалов ──
        seen_questions: Dict[str, tuple] = {}
        for card in all_cards:
            if card.is_reverse:
                continue
            key = normalize_text_key(card.question)
            if not key or len(key) < 5:
                continue

            if key in seen_questions:
                orig_topic, orig_q, orig_a = seen_questions[key]
                if card.topic != orig_topic:
                    result.duplicates_found.append(DuplicateInfo(
                        question_preview=card.question[:120],
                        answer_preview=card.answer[:80],
                        original_source="",
                        original_topic=orig_topic,
                        duplicate_source="",
                        duplicate_topic=card.topic,
                        card_type=card.card_type.value,
                        is_cross_file=True,
                    ))
                else:
                    result.duplicates_found.append(DuplicateInfo(
                        question_preview=card.question[:120],
                        answer_preview=card.answer[:80],
                        original_source="",
                        original_topic=orig_topic,
                        duplicate_source="",
                        duplicate_topic=card.topic,
                        card_type=card.card_type.value,
                        is_cross_file=False,
                    ))
            else:
                seen_questions[key] = (
                    card.topic, card.question, card.answer
                )

        if result.duplicates_found:
            cross = sum(
                1 for d in result.duplicates_found if d.is_cross_file
            )
            same = len(result.duplicates_found) - cross
            parts = []
            if cross:
                parts.append(f"{cross} между файлами")
            if same:
                parts.append(f"{same} внутри файлов")
            result.warnings.append(
                f"Найдено дубликатов: {', '.join(parts)}"
            )

            # ── Дедупликация выходных файлов ──
        if clean_duplicates and result.output_files:
            txt_files = [
                f for f in result.output_files if f.endswith('.txt')
            ]
            if txt_files:
                removed, dup_details = clean_up_duplicates(txt_files)
                result.duplicates_removed.extend(dup_details)
                if removed > 0:
                    result.warnings.append(
                        f"Удалено дубликатов из выходных файлов: "
                        f"{removed}"
                    )

        result.total_cards = len(all_cards)
        result.success = result.total_cards > 0

        return result

    def get_output_paths(self) -> Dict[str, str]:
        """Возвращает пути вывода для отображения."""
        return {
            'obsidian_vault': self.config.obsidian_vault,
            'cards': os.path.join(
                self.config.obsidian_vault, 'Interview', 'Cards'
            ),
            'materials': os.path.join(
                self.config.obsidian_vault, 'Interview', 'Materials'
            ),
            'anki': os.path.join(self.config.output, 'anki'),
        }

    def clean_output_files(
            self, file_paths: List[str]
    ) -> Tuple[int, List[str]]:
        """
        Удаляет указанные сгенерированные файлы.

        Args:
            file_paths: Список путей к файлам

        Returns:
            Tuple[int, List[str]]: (удалено, ошибки)
        """
        deleted = 0
        errors = []

        for fp in file_paths:
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    deleted += 1
                    logger.info(f"Удалён: {fp}")
            except Exception as e:
                errors.append(f"Не удалось удалить {fp}: {e}")

        return deleted, errors
