"""
Модуль логики InterviewCards.
"""

from logic.core import CardGenerator, GenerationResult
from logic.utils import (
    load_markdown_topics,
    parse_cards_from_markdown,
    parse_all_markdown_files,
    generate_anki_import_file,
    generate_all_formats,
    clean_up_duplicates,
    extract_frontmatter,
    format_markdown_to_html,
    format_markdown_to_anki_html,
    remove_obsidian_links,
    remove_spaced_repetition_tags,
    validate_markdown_structure,
    process_cards_batch,
    convert_cloze_to_anki_format,
    generate_card_guid,
    escape_for_tsv,
    ANKI_NOTE_TYPES,
    BASIC_CARD_TYPES,
    BIDIRECTIONAL_CARD_TYPES,
)

__all__ = [
    # Константы
    'ANKI_NOTE_TYPES',
    'BASIC_CARD_TYPES',
    'BIDIRECTIONAL_CARD_TYPES',
    # Парсинг
    'load_markdown_topics',
    'parse_cards_from_markdown',
    'parse_all_markdown_files',
    # Генерация
    'generate_anki_import_file',
    'generate_all_formats',
    'clean_up_duplicates',
    'process_cards_batch',
    # Форматирование
    'extract_frontmatter',
    'format_markdown_to_html',
    'format_markdown_to_anki_html',
    'remove_obsidian_links',
    'remove_spaced_repetition_tags',
    'validate_markdown_structure',
    # Anki конвертация
    'convert_cloze_to_anki_format',
    'generate_card_guid',
    'escape_for_tsv',
    # Core
    'CardGenerator',
    'GenerationResult',
]
