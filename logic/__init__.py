# logic/__init__.py

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
    # Новые экспорты
    validate_file_formats,
    validate_all_files,
    generate_validation_report_text,
    search_cards,
    compute_deck_statistics,
    FormatIssue,
    FileValidationReport,
)

__all__ = [
    'ANKI_NOTE_TYPES',
    'BASIC_CARD_TYPES',
    'BIDIRECTIONAL_CARD_TYPES',

    'load_markdown_topics',
    'parse_cards_from_markdown',
    'parse_all_markdown_files',

    'generate_anki_import_file',
    'generate_all_formats',
    'clean_up_duplicates',
    'process_cards_batch',

    'extract_frontmatter',
    'format_markdown_to_html',
    'format_markdown_to_anki_html',
    'remove_obsidian_links',
    'remove_spaced_repetition_tags',
    'validate_markdown_structure',

    'convert_cloze_to_anki_format',
    'generate_card_guid',
    'escape_for_tsv',

    'CardGenerator',
    'GenerationResult',

    # Новое
    'validate_file_formats',
    'validate_all_files',
    'generate_validation_report_text',
    'search_cards',
    'compute_deck_statistics',
    'FormatIssue',
    'FileValidationReport',
]