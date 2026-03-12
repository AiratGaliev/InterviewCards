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
)

__all__ = [
    # utils — парсинг
    'load_markdown_topics',
    'parse_cards_from_markdown',
    'parse_all_markdown_files',
    # utils — генерация
    'generate_anki_import_file',
    'generate_all_formats',
    'clean_up_duplicates',
    'process_cards_batch',
    # utils — форматирование
    'extract_frontmatter',
    'format_markdown_to_html',
    'format_markdown_to_anki_html',
    'remove_obsidian_links',
    'remove_spaced_repetition_tags',
    'validate_markdown_structure',
    # utils — Anki конвертация
    'convert_cloze_to_anki_format',
    'generate_card_guid',
    'escape_for_tsv',
    # core
    'CardGenerator',
    'GenerationResult',
]
