"""
Основной модуль логики приложения InterviewCards.
Содержит функции для парсинга Markdown и генерации карточек.
Полностью совместим с Obsidian Spaced Repetition.

Поддерживаемые форматы:
- Single-line Basic: question::answer
- Single-line Bidirectional: info1:::info2
- Multi-line Basic: question\n?\nanswer
- Multi-line Bidirectional: info1\n??\ninfo2
- Cloze: text with ==hidden parts==
"""

import glob
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, TextLexer

from models.InterviewCard import (
    InterviewCard, CardType, ClozeDeletion, SchedulingData
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SEPARATORS = {
    'single_line_basic': '::',
    'single_line_bidirectional': ':::',
    'multi_line_basic': '?',
    'multi_line_bidirectional': '??',
}

PATTERNS = {
    'question_answer_v1': r'###\s*Вопрос:\s*(.+?)\n+(?:Ответ:)\s*(.+?)(?=\n###\s*Вопрос:|\Z)',
    'question_answer_v2': r'###\s*Вопрос:\s*(.+?)\n+(?:Ответ:)\s*(.+?)(?=\n###\s*Вопрос:|\Z)',

    'single_line_basic': r'^(.+?)::(.+?)$',
    'single_line_bidirectional': r'^(.+?):::(.+?)$',

    'scheduling_comment': r'<!--SR:(\d{4}-\d{2}-\d{2}),(\d+),(\d+)-->',

    'frontmatter': r'^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)',

    'header': r'^\s*#\s+(.+?)$',

    'code_block': r'```(?:python|javascript|java|sql|json|bash|typescript|go|rust)?\r?\n(.*?)\r?\n```',

    'cloze_simple': r'==(.+?)==',
    'cloze_with_hint': r'==(.+?)==\^\[([^\]]*)\]',
    'cloze_with_sequence': r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]',
    'cloze_generalized': r'==(.+?)==\[\^([ahs]+)\]',
}

SUPPORTED_LANGUAGES = [
    'python', 'javascript', 'java', 'sql', 'json',
    'bash', 'typescript', 'go', 'rust',
]

ANKI_NOTE_TYPES = {
    'basic': 'Basic',
    'reversed': 'Basic (and reversed card)',
    'cloze': 'Cloze',
}

BASIC_CARD_TYPES = (
    CardType.SINGLE_LINE_BASIC,
    CardType.MULTI_LINE_BASIC,
)

BIDIRECTIONAL_CARD_TYPES = (
    CardType.SINGLE_LINE_BIDIRECTIONAL,
    CardType.MULTI_LINE_BIDIRECTIONAL,
)


# ──────────────────────────────────────────────────────
#  ВАЛИДАЦИЯ И ДИАГНОСТИКА ФОРМАТОВ
# ──────────────────────────────────────────────────────

@dataclass
class FormatIssue:
    """Одна проблема, обнаруженная при валидации файла."""
    severity: str  # 'error', 'warning', 'info'
    line_number: int  # 1-based
    line_text: str  # текст проблемной строки
    message: str  # описание проблемы
    suggestion: str = ""  # предлагаемое исправление

    def __repr__(self):
        return (
            f"[{self.severity.upper()}] line {self.line_number}: "
            f"{self.message}"
        )


@dataclass
class FileValidationReport:
    """Отчёт валидации одного файла."""
    file_path: str
    topic_name: str
    issues: List[FormatIssue] = field(default_factory=list)
    detected_formats: Dict[str, List[int]] = field(default_factory=dict)
    has_mixed_formats: bool = False
    total_cards_found: int = 0
    is_valid: bool = True

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == 'error')

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == 'warning')

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == 'info')

    def to_text(self) -> str:
        lines = [f"=== {self.topic_name} ==="]
        lines.append(f"Файл: {self.file_path}")
        lines.append(f"Карточек найдено: {self.total_cards_found}")
        lines.append(f"Смешение форматов: {'Да ⚠️' if self.has_mixed_formats else 'Нет ✅'}")

        if self.detected_formats:
            lines.append("Обнаруженные форматы:")
            for fmt, line_nums in self.detected_formats.items():
                lines.append(f"  - {fmt}: строки {', '.join(map(str, line_nums[:10]))}"
                             + (f" ...и ещё {len(line_nums) - 10}" if len(line_nums) > 10 else ""))

        if self.issues:
            lines.append(f"\nПроблемы ({len(self.issues)}):")
            for issue in self.issues:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue.severity, "•")
                lines.append(f"  {icon} Строка {issue.line_number}: {issue.message}")
                if issue.line_text:
                    display_text = issue.line_text[:120]
                    if len(issue.line_text) > 120:
                        display_text += "…"
                    lines.append(f"     │ {display_text}")
                if issue.suggestion:
                    lines.append(f"     └ Совет: {issue.suggestion}")
        else:
            lines.append("✅ Проблем не обнаружено")

        return "\n".join(lines)


def detect_line_format(line: str) -> Optional[str]:
    """Определяет формат карточки в одной строке.
    Возвращает строковое описание формата или None."""

    stripped = line.strip()

    if not stripped or stripped.startswith('#') or stripped.startswith('```'):
        return None
    if stripped == '---':
        return None

    # Порядок важен: сначала более специфичные паттерны
    if re.match(r'^###\s*Вопрос:', stripped):
        return 'legacy_question'

    if stripped == '??':
        return 'multi_line_bidirectional_sep'
    if stripped == '?':
        return 'multi_line_basic_sep'

    if ':::' in stripped and not stripped.startswith('```'):
        parts = stripped.split(':::')
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return 'single_line_bidirectional'

    if '::' in stripped and ':::' not in stripped and not stripped.startswith('```'):
        parts = stripped.split('::')
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return 'single_line_basic'

    if re.search(r'==.+?==', stripped):
        return 'cloze'

    return None


def _check_mixed_formats_in_block(
        lines: List[str],
        start_line_num: int,
        issues: List[FormatIssue],
        detected_formats: Dict[str, List[int]],
) -> None:
    """Проверяет блок строк на смешение форматов карточек."""

    block_formats: Dict[str, List[int]] = {}
    in_code_block = False

    for offset, line in enumerate(lines):
        line_num = start_line_num + offset
        stripped = line.strip()

        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        fmt = detect_line_format(stripped)
        if fmt:
            if fmt not in block_formats:
                block_formats[fmt] = []
            block_formats[fmt].append(line_num)

            if fmt not in detected_formats:
                detected_formats[fmt] = []
            detected_formats[fmt].append(line_num)

    return block_formats


FORMAT_COMPATIBILITY_GROUPS = {
    'qa_formats': {'single_line_basic', 'single_line_bidirectional',
                   'multi_line_basic_sep', 'multi_line_bidirectional_sep',
                   'legacy_question'},
    'cloze_formats': {'cloze'},
}

# Форматы, которые НЕ должны смешиваться в одном блоке-карточке
INCOMPATIBLE_PAIRS = [
    ({'single_line_basic', 'single_line_bidirectional'},
     "Basic (::) и Bidirectional (:::) в одном блоке — "
     "используйте один формат"),

    ({'multi_line_basic_sep', 'multi_line_bidirectional_sep'},
     "Разделители ? и ?? в одном блоке — "
     "используйте один тип разделителя"),

    ({'single_line_basic', 'multi_line_basic_sep'},
     "Однострочный (::) и многострочный (?) в одном блоке — "
     "разделите на отдельные блоки"),

    ({'single_line_bidirectional', 'multi_line_bidirectional_sep'},
     "Однострочный (:::) и многострочный (??) в одном блоке — "
     "разделите на отдельные блоки"),
]


def validate_file_formats(file_path: str) -> FileValidationReport:
    """Полная валидация одного Markdown-файла.
    Проверяет смешение форматов, проблемные строки и т.д."""

    topic_name = Path(file_path).stem
    report = FileValidationReport(
        file_path=file_path,
        topic_name=topic_name,
    )

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        report.issues.append(FormatIssue(
            severity='error', line_number=0, line_text='',
            message=f"Не удалось прочитать файл: {e}",
        ))
        report.is_valid = False
        return report

    lines = content.splitlines()

    # --- 1. Проверка frontmatter ---
    _validate_frontmatter(content, lines, report)

    # --- 2. Построчный анализ форматов ---
    in_code_block = False
    in_frontmatter = False
    frontmatter_ended = False
    all_formats_with_lines: Dict[str, List[int]] = {}

    for line_num_0, line in enumerate(lines):
        line_num = line_num_0 + 1
        stripped = line.strip()

        # Отслеживание frontmatter
        if line_num_0 == 0 and stripped == '---':
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == '---':
                in_frontmatter = False
                frontmatter_ended = True
            continue

        # Отслеживание блоков кода
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            # Проверка незакрытого блока кода в конце файла
            continue
        if in_code_block:
            continue

        # Определение формата строки
        fmt = detect_line_format(stripped)
        if fmt:
            if fmt not in all_formats_with_lines:
                all_formats_with_lines[fmt] = []
            all_formats_with_lines[fmt].append(line_num)

        # --- Проверка проблемных строк ---

        # :: в начале или конце строки (пустой вопрос/ответ)
        if fmt == 'single_line_basic':
            parts = stripped.split('::', 1)
            if not parts[0].strip():
                report.issues.append(FormatIssue(
                    severity='error', line_number=line_num,
                    line_text=stripped,
                    message="Пустой вопрос перед разделителем '::'",
                    suggestion="Добавьте текст вопроса перед '::'",
                ))
            if len(parts) > 1 and not parts[1].strip():
                report.issues.append(FormatIssue(
                    severity='warning', line_number=line_num,
                    line_text=stripped,
                    message="Пустой ответ после разделителя '::'",
                    suggestion="Добавьте текст ответа после '::'",
                ))

        if fmt == 'single_line_bidirectional':
            parts = stripped.split(':::', 1)
            if not parts[0].strip():
                report.issues.append(FormatIssue(
                    severity='error', line_number=line_num,
                    line_text=stripped,
                    message="Пустая сторона перед разделителем ':::'",
                    suggestion="Добавьте текст перед ':::'",
                ))
            if len(parts) > 1 and not parts[1].strip():
                report.issues.append(FormatIssue(
                    severity='error', line_number=line_num,
                    line_text=stripped,
                    message="Пустая сторона после разделителя ':::'",
                    suggestion="Добавьте текст после ':::'",
                ))

        # Проверка незакрытых cloze
        if '==' in stripped:
            count = stripped.count('==')
            if count % 2 != 0:
                report.issues.append(FormatIssue(
                    severity='warning', line_number=line_num,
                    line_text=stripped,
                    message="Нечётное количество '==' — возможно незакрытый cloze",
                    suggestion="Проверьте, что каждый ==текст== имеет закрывающий ==",
                ))

        # Одиночные ? и ?? — предупреждение, если следующая строка пуста
        if stripped in ('?', '??'):
            next_idx = line_num_0 + 1
            if next_idx >= len(lines) or not lines[next_idx].strip():
                report.issues.append(FormatIssue(
                    severity='warning', line_number=line_num,
                    line_text=stripped,
                    message=f"Разделитель '{stripped}' без ответа на следующей строке",
                    suggestion="Добавьте ответ после разделителя",
                ))

            # Проверка: есть ли вопрос ПЕРЕД разделителем?
            prev_idx = line_num_0 - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1
            if prev_idx < 0 or lines[prev_idx].strip().startswith('#') or lines[prev_idx].strip() == '---':
                report.issues.append(FormatIssue(
                    severity='warning', line_number=line_num,
                    line_text=stripped,
                    message=f"Разделитель '{stripped}' без вопроса перед ним",
                    suggestion="Добавьте текст вопроса перед разделителем",
                ))

        # Множественные :: в одной строке (возможно ошибка)
        if '::' in stripped and not stripped.startswith('#'):
            double_colon_count = len(re.findall(r'(?<!:)::(?!:)', stripped))
            triple_colon_count = len(re.findall(r':::', stripped))
            if double_colon_count > 1 and triple_colon_count == 0:
                report.issues.append(FormatIssue(
                    severity='warning', line_number=line_num,
                    line_text=stripped,
                    message=f"Множественные '::' в одной строке ({double_colon_count} шт.) — "
                            f"возможно нужно разбить на несколько карточек",
                    suggestion="Каждая карточка должна быть на отдельной строке",
                ))

    # Проверка незакрытого блока кода
    if in_code_block:
        report.issues.append(FormatIssue(
            severity='error', line_number=len(lines),
            line_text='',
            message="Незакрытый блок кода (``` без парного закрывающего ```)",
            suggestion="Добавьте закрывающий ``` в конце блока кода",
        ))

    report.detected_formats = all_formats_with_lines

    # --- 3. Проверка смешения форматов ---
    _check_format_mixing(all_formats_with_lines, report)

    # --- 4. Подсчёт карточек ---
    try:
        cards = parse_cards_from_markdown(file_path)
        report.total_cards_found = len(cards)
    except Exception:
        report.total_cards_found = 0

    # --- 5. Дополнительные проверки ---
    _check_empty_content(content, lines, report)
    _check_very_long_lines(lines, report)
    _check_duplicate_questions(file_path, report)

    report.is_valid = report.error_count == 0

    return report


def _validate_frontmatter(
        content: str,
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Проверка frontmatter."""
    fm = extract_frontmatter(content)

    if not fm:
        report.issues.append(FormatIssue(
            severity='info', line_number=1,
            line_text='',
            message="Отсутствует frontmatter (---...---)",
            suggestion="Добавьте frontmatter с тегами и категорией: "
                       "tags: [flashcards/...], category: ...",
        ))
        return

    if 'tags' not in fm:
        report.issues.append(FormatIssue(
            severity='warning', line_number=1,
            line_text='',
            message="Frontmatter без поля 'tags'",
            suggestion="Добавьте tags: [flashcards/category]",
        ))

    if 'category' not in fm and 'deck' not in fm:
        report.issues.append(FormatIssue(
            severity='info', line_number=1,
            line_text='',
            message="Frontmatter без 'category' и 'deck' — "
                    "будет использована категория из имени папки",
        ))

    tags = normalize_tags(fm.get('tags', []))
    has_flashcards_tag = any(
        t.startswith('flashcards') for t in tags
    )
    if tags and not has_flashcards_tag:
        report.issues.append(FormatIssue(
            severity='info', line_number=1,
            line_text=f"tags: {fm.get('tags')}",
            message="Нет тега flashcards/... — колода будет определена автоматически",
            suggestion="Добавьте тег flashcards/<category> для явного указания колоды",
        ))


def _check_format_mixing(
        all_formats: Dict[str, List[int]],
        report: FileValidationReport,
) -> None:
    """Проверяет смешение несовместимых форматов в файле."""

    format_names = set(all_formats.keys())

    # Исключаем cloze — он может сосуществовать с другими
    qa_formats = format_names - {'cloze'}

    # Проверка несовместимых пар
    for incompatible_set, message in INCOMPATIBLE_PAIRS:
        found = incompatible_set & format_names
        if len(found) >= 2:
            report.has_mixed_formats = True

            # Собираем строки каждого формата для отчёта
            details_parts = []
            for fmt in sorted(found):
                fmt_lines = all_formats.get(fmt, [])
                if fmt_lines:
                    lines_str = ", ".join(str(ln) for ln in fmt_lines[:5])
                    if len(fmt_lines) > 5:
                        lines_str += f" ...+{len(fmt_lines) - 5}"
                    details_parts.append(f"  '{fmt}' на строках: {lines_str}")

            report.issues.append(FormatIssue(
                severity='warning',
                line_number=min(
                    ln for fmt in found for ln in all_formats.get(fmt, [0])
                ),
                line_text='',
                message=f"Смешение форматов: {message}",
                suggestion="Используйте один формат карточек в файле или "
                           "разделите на отдельные файлы.\n" +
                           "\n".join(details_parts),
            ))

    # Общее предупреждение если > 2 разных QA-форматов
    if len(qa_formats) > 2:
        report.has_mixed_formats = True
        report.issues.append(FormatIssue(
            severity='warning', line_number=1,
            line_text='',
            message=f"Используется {len(qa_formats)} различных форматов "
                    f"карточек: {', '.join(sorted(qa_formats))}",
            suggestion="Рекомендуется использовать 1-2 формата в одном файле "
                       "для предсказуемого парсинга",
        ))


def _check_empty_content(
        content: str,
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Проверка на пустое содержимое."""
    content_without_fm = strip_frontmatter(content).strip()
    if not content_without_fm:
        report.issues.append(FormatIssue(
            severity='error', line_number=1,
            line_text='',
            message="Файл не содержит контента (только frontmatter)",
            suggestion="Добавьте карточки после frontmatter",
        ))

    if not validate_markdown_structure(content):
        report.issues.append(FormatIssue(
            severity='warning', line_number=1,
            line_text='',
            message="Не найдено распознаваемых форматов карточек",
            suggestion="Используйте :: для Basic, ::: для Bidirectional, "
                       "? / ?? для многострочных, == для Cloze",
        ))


def _check_very_long_lines(
        lines: List[str],
        report: FileValidationReport,
        max_line_length: int = 1000,
) -> None:
    """Предупреждение о слишком длинных строках."""
    in_code_block = False
    for i, line in enumerate(lines):
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if len(line) > max_line_length:
            report.issues.append(FormatIssue(
                severity='info',
                line_number=i + 1,
                line_text=line[:100] + '…',
                message=f"Очень длинная строка ({len(line)} символов) — "
                        f"может быть сложно редактировать",
                suggestion="Рассмотрите разбивку на многострочный формат (? или ??)",
            ))


def _check_duplicate_questions(
        file_path: str,
        report: FileValidationReport,
) -> None:
    """Проверяет дублирование вопросов внутри файла."""
    try:
        cards = parse_cards_from_markdown(file_path)
    except Exception:
        return

    seen: Dict[str, int] = {}
    for card in cards:
        key = normalize_text_key(card.question)
        if key in seen:
            report.issues.append(FormatIssue(
                severity='warning',
                line_number=0,
                line_text=card.question[:100],
                message=f"Дубликат вопроса: '{card.question[:60]}…' "
                        f"(уже встречался как карточка #{seen[key]})",
                suggestion="Удалите дублирующуюся карточку",
            ))
        else:
            seen[key] = card.id


def validate_all_files(input_dir: str) -> List[FileValidationReport]:
    """Валидация всех MD-файлов в директории."""
    reports = []

    if not os.path.exists(input_dir):
        return reports

    input_path = Path(input_dir)
    for file_path in sorted(input_path.rglob('*.md')):
        report = validate_file_formats(str(file_path))
        reports.append(report)

    return reports


def generate_validation_report_text(reports: List[FileValidationReport]) -> str:
    """Генерирует текстовый отчёт валидации."""
    lines = [
        "=" * 60,
        "ОТЧЁТ ВАЛИДАЦИИ МАТЕРИАЛОВ",
        "=" * 60,
        "",
    ]

    total_errors = sum(r.error_count for r in reports)
    total_warnings = sum(r.warning_count for r in reports)
    total_info = sum(r.info_count for r in reports)
    mixed_count = sum(1 for r in reports if r.has_mixed_formats)

    lines.append(f"Файлов проверено: {len(reports)}")
    lines.append(f"Ошибок: {total_errors}")
    lines.append(f"Предупреждений: {total_warnings}")
    lines.append(f"Информация: {total_info}")
    lines.append(f"Файлов со смешением форматов: {mixed_count}")
    lines.append("")

    # Сначала файлы с проблемами
    problem_reports = [r for r in reports if r.issues]
    clean_reports = [r for r in reports if not r.issues]

    if problem_reports:
        lines.append("-" * 40)
        lines.append("ФАЙЛЫ С ПРОБЛЕМАМИ")
        lines.append("-" * 40)
        for report in problem_reports:
            lines.append("")
            lines.append(report.to_text())

    if clean_reports:
        lines.append("")
        lines.append("-" * 40)
        lines.append("ФАЙЛЫ БЕЗ ПРОБЛЕМ")
        lines.append("-" * 40)
        for report in clean_reports:
            lines.append(f"  ✅ {report.topic_name} ({report.total_cards_found} карточек)")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────
#  ПОИСК ПО КАРТОЧКАМ
# ──────────────────────────────────────────────────────

def search_cards(
        cards: List[InterviewCard],
        query: str,
        search_in_answers: bool = True,
        search_in_questions: bool = True,
        case_sensitive: bool = False,
) -> List[InterviewCard]:
    """Поиск карточек по тексту."""
    if not query or not query.strip():
        return cards

    q = query.strip()
    if not case_sensitive:
        q = q.lower()

    results = []
    for card in cards:
        question = card.question if case_sensitive else card.question.lower()
        answer = card.answer if case_sensitive else card.answer.lower()

        matched = False
        if search_in_questions and q in question:
            matched = True
        if search_in_answers and q in answer:
            matched = True

        if matched:
            results.append(card)

    return results


# ──────────────────────────────────────────────────────
#  СТАТИСТИКА
# ──────────────────────────────────────────────────────

def compute_deck_statistics(input_dir: str) -> Dict[str, Any]:
    """Подробная статистика по колодам и категориям."""
    topics = load_markdown_topics(input_dir)

    stats = {
        'total_files': len(topics),
        'total_cards': 0,
        'by_deck': {},  # deck_name -> count
        'by_category': {},  # category -> count
        'by_type': {},  # card_type -> count
        'by_file': {},  # topic_name -> {cards, types, ...}
        'cloze_count': 0,
        'code_count': 0,
        'reverse_count': 0,
        'avg_question_length': 0,
        'avg_answer_length': 0,
        'longest_question': ('', 0),
        'longest_answer': ('', 0),
        'files_with_issues': 0,
    }

    all_q_lengths = []
    all_a_lengths = []

    for topic_name, topic_data in topics.items():
        cards = parse_cards_from_markdown(topic_data['path'])
        deck_name = topic_data.get('deck_name', 'flashcards')
        category = topic_data.get('category', 'general')

        stats['total_cards'] += len(cards)
        stats['by_deck'][deck_name] = stats['by_deck'].get(deck_name, 0) + len(cards)
        stats['by_category'][category] = stats['by_category'].get(category, 0) + len(cards)

        type_counts = {}
        for card in cards:
            type_name = card.card_type.value
            stats['by_type'][type_name] = stats['by_type'].get(type_name, 0) + 1
            type_counts[type_name] = type_counts.get(type_name, 0) + 1

            if card.card_type == CardType.CLOZE:
                stats['cloze_count'] += 1
            if card.has_code():
                stats['code_count'] += 1
            if card.is_reverse:
                stats['reverse_count'] += 1

            all_q_lengths.append(len(card.question))
            all_a_lengths.append(len(card.answer))

            if len(card.question) > stats['longest_question'][1]:
                stats['longest_question'] = (card.question[:80], len(card.question))
            if len(card.answer) > stats['longest_answer'][1]:
                stats['longest_answer'] = (card.answer[:80], len(card.answer))

        stats['by_file'][topic_name] = {
            'cards': len(cards),
            'types': type_counts,
            'category': category,
            'deck': deck_name,
        }

    if all_q_lengths:
        stats['avg_question_length'] = sum(all_q_lengths) / len(all_q_lengths)
    if all_a_lengths:
        stats['avg_answer_length'] = sum(all_a_lengths) / len(all_a_lengths)

    return stats


# ──────────────────────────────────────────────────────
#  Остальные функции (без изменений, но с добавлением import)
# ──────────────────────────────────────────────────────

def normalize_tags(tags: Any) -> List[str]:
    if tags is None:
        return []

    if isinstance(tags, str):
        if ',' in tags:
            parts = [part.strip() for part in tags.split(',')]
            return [part for part in parts if part]
        return [tags.strip()] if tags.strip() else []

    if isinstance(tags, (list, tuple, set)):
        result = []
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                result.append(tag.strip())
        return result

    return []


def strip_frontmatter(content: str) -> str:
    return re.sub(
        PATTERNS['frontmatter'],
        '',
        content,
        count=1,
        flags=re.DOTALL | re.MULTILINE
    )


def normalize_text_key(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def remove_scheduling_comment(text: str) -> str:
    if not text:
        return ""
    return re.sub(PATTERNS['scheduling_comment'], '', text).strip()


def build_cloze_question(text: str) -> str:
    if not text:
        return "Cloze"

    masked = text

    masked = re.sub(
        r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]',
        lambda m: f"[{m.group(2).strip() or '...'}]",
        masked
    )
    masked = re.sub(
        r'==(.+?)==\^\[([^\]]*)\](?!\[\^)',
        lambda m: f"[{m.group(2).strip() or '...'}]",
        masked
    )
    masked = re.sub(r'==(.+?)==\[\^([ahs]+)\]', '[...]', masked)
    masked = re.sub(r'==(.+?)==', '[...]', masked)

    masked = re.sub(r'[ \t]+', ' ', masked)
    masked = re.sub(r'\n{3,}', '\n\n', masked)
    masked = masked.strip()

    return masked or "Cloze"


def reveal_cloze_text(text: str) -> str:
    if not text:
        return ""

    revealed = text
    revealed = re.sub(r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==\^\[([^\]]*)\](?!\[\^)', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==\[\^([ahs]+)\]', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==', r'**\1**', revealed)

    return revealed


def should_skip_line_for_single_pass(line: str) -> bool:
    if not line:
        return True

    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith('#'):
        return True
    if stripped.startswith('```'):
        return True
    if stripped == '---':
        return True

    return False


def contains_single_line_card_syntax(section: str) -> bool:
    in_code_block = False

    for raw_line in section.splitlines():
        line = raw_line.strip()

        if line.startswith('```'):
            in_code_block = not in_code_block
            continue

        if in_code_block or should_skip_line_for_single_pass(line):
            continue

        if detect_card_format(line):
            return True

    return False


def split_markdown_blocks(content: str) -> List[str]:
    blocks: List[str] = []
    current: List[str] = []
    in_code_block = False

    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith('```'):
            current.append(line)
            in_code_block = not in_code_block
            continue

        if not in_code_block and not stripped:
            if current:
                blocks.append('\n'.join(current).strip())
                current = []
            continue

        current.append(line)

    if current:
        blocks.append('\n'.join(current).strip())

    return [block for block in blocks if block.strip()]


def get_first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def is_legacy_question_block(block: str) -> bool:
    first_line = get_first_nonempty_line(block)
    return bool(re.match(r'^###\s*Вопрос:', first_line))


def is_heading_block(block: str) -> bool:
    first_line = get_first_nonempty_line(block)
    return first_line.startswith('#') and not is_legacy_question_block(block)


def is_horizontal_rule_block(block: str) -> bool:
    return block.strip() == '---'


def split_multiline_block(block: str) -> Optional[Tuple[str, str, str]]:
    lines = block.splitlines()
    in_code_block = False

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()

        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        if stripped in {SEPARATORS['multi_line_basic'], SEPARATORS['multi_line_bidirectional']}:
            question = '\n'.join(lines[:idx]).strip()
            answer = '\n'.join(lines[idx + 1:]).strip()

            if not question:
                return None

            return stripped, question, answer

    return None


def is_standalone_cloze_block(block: str) -> bool:
    stripped = block.strip()

    if not stripped:
        return False
    if is_horizontal_rule_block(stripped):
        return False
    if is_heading_block(stripped):
        return False
    if is_legacy_question_block(stripped):
        return False
    if contains_single_line_card_syntax(stripped):
        return False
    if split_multiline_block(stripped):
        return False

    return has_cloze_deletions(stripped)


def is_card_boundary_block(block: str) -> bool:
    stripped = block.strip()

    if not stripped:
        return True
    if is_horizontal_rule_block(stripped):
        return True
    if is_heading_block(stripped):
        return True
    if is_legacy_question_block(stripped):
        return True
    if contains_single_line_card_syntax(stripped):
        return True
    if split_multiline_block(stripped):
        return True
    if is_standalone_cloze_block(stripped):
        return True

    return False


def extract_code_snippets_from_text(text: str) -> List[str]:
    return re.findall(PATTERNS['code_block'], text, re.DOTALL)


def merge_multiline_sections(content: str) -> List[str]:
    raw_sections = re.split(r'\n\s*\n', content)
    merged_sections: List[str] = []

    i = 0
    while i < len(raw_sections):
        section = raw_sections[i]
        has_multi_sep = ('\n?\n' in section or '\n??\n' in section)

        if has_multi_sep:
            current_merged = section
            j = i + 1

            while j < len(raw_sections):
                next_section = raw_sections[j]
                stripped = next_section.strip()
                first_line = stripped.split('\n')[0] if stripped else ""

                stop = False
                if not stripped:
                    j += 1
                    continue
                if first_line.startswith('#'):
                    stop = True
                elif first_line == '---':
                    stop = True
                elif first_line.startswith('### Вопрос:'):
                    stop = True
                elif contains_single_line_card_syntax(next_section):
                    stop = True

                if stop:
                    break

                current_merged += "\n\n" + next_section
                j += 1

            merged_sections.append(current_merged)
            i = j
        else:
            merged_sections.append(section)
            i += 1

    return merged_sections


def create_cloze_card(
        card_id: int,
        topic_name: str,
        category: str,
        text: str,
        tags: List[str],
        source_note: str,
        deck_name: str,
        frontmatter: Dict,
        scheduling: Optional[SchedulingData] = None
) -> Optional[InterviewCard]:
    cleaned_text = remove_scheduling_comment(text).strip()
    deletions = parse_cloze_deletions(cleaned_text)

    if not cleaned_text or not deletions:
        return None

    return InterviewCard(
        id=card_id,
        topic=topic_name,
        category=category,
        question=build_cloze_question(cleaned_text),
        answer=cleaned_text,
        card_type=CardType.CLOZE,
        cloze_deletions=deletions,
        tags=tags,
        source_note=source_note,
        deck_name=deck_name,
        frontmatter=frontmatter,
        scheduling=scheduling,
    )


def load_markdown_topics(input_dir: str) -> Dict[str, Dict]:
    topics = {}

    if not os.path.exists(input_dir):
        logger.warning(f"Папка не найдена: {input_dir}")
        return topics

    input_path = Path(input_dir)

    for file_path in input_path.rglob('*.md'):
        try:
            topic_name = file_path.stem

            rel_path = file_path.relative_to(input_path)
            if len(rel_path.parts) > 1:
                category = rel_path.parts[0]
            else:
                category = 'general'

            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            frontmatter = extract_frontmatter(content)
            deck_name = determine_deck_name(frontmatter, category)

            topics[topic_name] = {
                'content': content,
                'path': str(file_path),
                'category': frontmatter.get('category', category),
                'frontmatter': frontmatter,
                'deck_name': deck_name,
            }

        except Exception as e:
            logger.error(f"Ошибка при чтении {file_path}: {e}")
            continue

    logger.info(f"Загружено тем: {len(topics)}")
    return topics


def determine_deck_name(frontmatter: Dict, category: str) -> str:
    if 'deck' in frontmatter:
        deck = frontmatter['deck']
        if isinstance(deck, str) and deck.strip():
            deck = deck.strip()
            if not deck.startswith('flashcards'):
                deck = f"flashcards/{deck}"
            return deck

    tags = normalize_tags(frontmatter.get('tags', []))
    for tag in tags:
        if tag.startswith('flashcards/'):
            return tag

    return f"flashcards/{category}"


def extract_frontmatter(content: str) -> Dict:
    match = re.match(PATTERNS['frontmatter'], content, re.DOTALL | re.MULTILINE)
    if not match:
        return {}

    try:
        frontmatter = yaml.safe_load(match.group(1))
        return frontmatter if isinstance(frontmatter, dict) else {}
    except yaml.YAMLError as e:
        logger.warning(f"Ошибка парсинга frontmatter: {e}")
        return {}


def extract_scheduling_data(content: str) -> Optional[SchedulingData]:
    match = re.search(PATTERNS['scheduling_comment'], content)
    if match:
        return SchedulingData(
            next_review=match.group(1),
            interval=int(match.group(2)),
            ease=int(match.group(3))
        )
    return None


def extract_card_metadata(content: str) -> Tuple[str, Dict]:
    frontmatter = extract_frontmatter(content)

    category = frontmatter.get('category', 'general')
    if category == 'general':
        match = re.search(PATTERNS['header'], content, re.MULTILINE)
        if match:
            category = match.group(1).lower().replace(' ', '_')

    return category, frontmatter


def detect_card_format(line: str) -> Optional[CardType]:
    line = line.strip()

    if re.match(PATTERNS['single_line_bidirectional'], line):
        return CardType.SINGLE_LINE_BIDIRECTIONAL

    if ':::' not in line and re.match(PATTERNS['single_line_basic'], line):
        return CardType.SINGLE_LINE_BASIC

    return None


def parse_cloze_deletions(text: str) -> List[ClozeDeletion]:
    deletions: List[ClozeDeletion] = []

    for match in re.finditer(r'==(.+?)==\[\^([ahs]+)\]', text):
        deletions.append(ClozeDeletion(
            text=match.group(1),
            position=match.start(),
            actions=match.group(2),
        ))

    for match in re.finditer(r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]', text):
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
                hint=match.group(2) if match.group(2) else None,
                sequence=int(match.group(3)),
            ))

    for match in re.finditer(r'==(.+?)==\^\[([^\]]*)\](?!\[\^)', text):
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
                hint=match.group(2) if match.group(2) else None,
            ))

    for match in re.finditer(r'==(.+?)==', text):
        end_pos = match.end()
        if end_pos < len(text) and text[end_pos:end_pos + 2] in ['^[', '[^']:
            continue
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
            ))

    deletions.sort(key=lambda d: d.position)
    return deletions


def has_cloze_deletions(text: str) -> bool:
    return bool(re.search(r'==.+?==', text, re.DOTALL))


def parse_cards_from_markdown(file_path: str, start_id: int = 1) -> List[InterviewCard]:
    cards: List[InterviewCard] = []
    card_id = start_id

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"Ошибка чтения файла {file_path}: {e}")
        return cards

    topic_name = Path(file_path).stem
    category, frontmatter = extract_card_metadata(content)
    deck_name = determine_deck_name(frontmatter, category)
    tags = normalize_tags(frontmatter.get('tags', []))

    content_without_fm = strip_frontmatter(content)
    blocks = split_markdown_blocks(content_without_fm)

    all_cards: List[InterviewCard] = []
    cloze_keys: Set[str] = set()

    def add_cloze_card(text: str, scheduling: Optional[SchedulingData] = None) -> None:
        nonlocal card_id

        cloze_card = create_cloze_card(
            card_id=card_id,
            topic_name=topic_name,
            category=category,
            text=text,
            tags=tags,
            source_note=topic_name,
            deck_name=deck_name,
            frontmatter=frontmatter,
            scheduling=scheduling,
        )

        if not cloze_card:
            return

        cloze_key = normalize_text_key(cloze_card.answer)
        if cloze_key in cloze_keys:
            return

        all_cards.append(cloze_card)
        cloze_keys.add(cloze_key)
        card_id += 1

    i = 0
    while i < len(blocks):
        block = blocks[i].strip()

        if not block or is_horizontal_rule_block(block) or is_heading_block(block):
            i += 1
            continue

        if is_legacy_question_block(block):
            full_block_parts = [block]
            j = i + 1

            while j < len(blocks) and not is_card_boundary_block(blocks[j]):
                full_block_parts.append(blocks[j].strip())
                j += 1

            full_block = '\n\n'.join(part for part in full_block_parts if part).strip()
            legacy_questions = re.findall(
                PATTERNS['question_answer_v1'],
                full_block,
                re.DOTALL | re.MULTILINE
            )

            if legacy_questions:
                q_text, a_text = legacy_questions[0]
                answer_clean = remove_scheduling_comment(a_text.strip())
                scheduling = extract_scheduling_data(full_block)

                if has_cloze_deletions(answer_clean):
                    add_cloze_card(answer_clean, scheduling)
                else:
                    card = InterviewCard(
                        id=card_id,
                        topic=topic_name,
                        category=category,
                        question=q_text.strip(),
                        answer=answer_clean,
                        card_type=CardType.MULTI_LINE_BASIC,
                        code_snippets=extract_code_snippets_from_text(answer_clean),
                        tags=tags,
                        source_note=topic_name,
                        deck_name=deck_name,
                        frontmatter=frontmatter,
                        scheduling=scheduling,
                    )
                    all_cards.append(card)
                    card_id += 1

            i = j
            continue

        if contains_single_line_card_syntax(block):
            in_code_block = False

            for raw_line in block.splitlines():
                line = raw_line.strip()

                if line.startswith('```'):
                    in_code_block = not in_code_block
                    continue

                if in_code_block or should_skip_line_for_single_pass(line):
                    continue

                card_type = detect_card_format(line)
                if not card_type:
                    continue

                if card_type == CardType.SINGLE_LINE_BASIC:
                    question, answer = line.split('::', 1)
                    question = question.strip()
                    answer = remove_scheduling_comment(answer.strip())
                    scheduling = extract_scheduling_data(line)

                    if has_cloze_deletions(answer):
                        add_cloze_card(answer, scheduling)
                    else:
                        card = InterviewCard(
                            id=card_id,
                            topic=topic_name,
                            category=category,
                            question=question,
                            answer=answer,
                            card_type=CardType.SINGLE_LINE_BASIC,
                            tags=tags,
                            source_note=topic_name,
                            deck_name=deck_name,
                            frontmatter=frontmatter,
                            scheduling=scheduling,
                        )
                        all_cards.append(card)
                        card_id += 1

                elif card_type == CardType.SINGLE_LINE_BIDIRECTIONAL:
                    info1, info2 = line.split(':::', 1)
                    info1 = remove_scheduling_comment(info1.strip())
                    info2 = remove_scheduling_comment(info2.strip())
                    scheduling = extract_scheduling_data(line)

                    card1 = InterviewCard(
                        id=card_id,
                        topic=topic_name,
                        category=category,
                        question=info1,
                        answer=info2,
                        card_type=CardType.SINGLE_LINE_BIDIRECTIONAL,
                        tags=tags,
                        source_note=topic_name,
                        deck_name=deck_name,
                        frontmatter=frontmatter,
                        scheduling=scheduling,
                        is_reverse=False,
                    )
                    all_cards.append(card1)
                    card_id += 1

                    card2 = InterviewCard(
                        id=card_id,
                        topic=topic_name,
                        category=category,
                        question=info2,
                        answer=info1,
                        card_type=CardType.SINGLE_LINE_BIDIRECTIONAL,
                        tags=tags,
                        source_note=topic_name,
                        deck_name=deck_name,
                        frontmatter=frontmatter,
                        scheduling=scheduling,
                        is_reverse=True,
                        sibling_id=card1.id,
                    )
                    all_cards.append(card2)
                    card_id += 1

            i += 1
            continue

        multiline_parts = split_multiline_block(block)
        if multiline_parts:
            separator, part1, part2 = multiline_parts
            full_block_parts = [block]
            merged_answer = part2
            j = i + 1

            while j < len(blocks) and not is_card_boundary_block(blocks[j]):
                continuation = blocks[j].strip()
                full_block_parts.append(continuation)
                merged_answer = (
                    f"{merged_answer}\n\n{continuation}".strip()
                    if merged_answer else continuation
                )
                j += 1

            full_block = '\n\n'.join(
                part for part in full_block_parts if part
            ).strip()
            scheduling = extract_scheduling_data(full_block)

            if separator == SEPARATORS['multi_line_bidirectional']:
                info1 = remove_scheduling_comment(part1.strip())
                info2 = remove_scheduling_comment(merged_answer.strip())
                code_snippets = extract_code_snippets_from_text(
                    f"{info1}\n\n{info2}"
                )

                card1 = InterviewCard(
                    id=card_id,
                    topic=topic_name,
                    category=category,
                    question=info1,
                    answer=info2,
                    card_type=CardType.MULTI_LINE_BIDIRECTIONAL,
                    code_snippets=code_snippets,
                    tags=tags,
                    source_note=topic_name,
                    deck_name=deck_name,
                    frontmatter=frontmatter,
                    scheduling=scheduling,
                    is_reverse=False,
                )
                all_cards.append(card1)
                card_id += 1

                card2 = InterviewCard(
                    id=card_id,
                    topic=topic_name,
                    category=category,
                    question=info2,
                    answer=info1,
                    card_type=CardType.MULTI_LINE_BIDIRECTIONAL,
                    code_snippets=code_snippets,
                    tags=tags,
                    source_note=topic_name,
                    deck_name=deck_name,
                    frontmatter=frontmatter,
                    scheduling=scheduling,
                    is_reverse=True,
                    sibling_id=card1.id,
                )
                all_cards.append(card2)
                card_id += 1

            else:
                question = remove_scheduling_comment(part1.strip())
                answer = remove_scheduling_comment(merged_answer.strip())

                if has_cloze_deletions(answer):
                    add_cloze_card(answer, scheduling)
                else:
                    card = InterviewCard(
                        id=card_id,
                        topic=topic_name,
                        category=category,
                        question=question,
                        answer=answer,
                        card_type=CardType.MULTI_LINE_BASIC,
                        code_snippets=extract_code_snippets_from_text(answer),
                        tags=tags,
                        source_note=topic_name,
                        deck_name=deck_name,
                        frontmatter=frontmatter,
                        scheduling=scheduling,
                    )
                    all_cards.append(card)
                    card_id += 1

            i = j
            continue

        if is_standalone_cloze_block(block):
            add_cloze_card(block, extract_scheduling_data(block))
            i += 1
            continue

        i += 1

    for card in all_cards:
        try:
            if card.validate():
                cards.append(card)
        except Exception as e:
            logger.warning(
                f"Карточка {card.id} из {topic_name} "
                f"не прошла валидацию: {e}"
            )

    if not cards:
        logger.info(
            f"Структурированные вопросы не найдены в {topic_name}, "
            f"создаём карточку из контента"
        )

        fallback_answer = content_without_fm.strip()
        fallback_card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=f"Расскажите о: {topic_name}",
            answer=fallback_answer,
            card_type=CardType.MULTI_LINE_BASIC,
            code_snippets=re.findall(
                PATTERNS['code_block'], content_without_fm, re.DOTALL
            ),
            tags=tags or [category],
            source_note=topic_name,
            deck_name=deck_name,
            frontmatter=frontmatter,
        )
        try:
            if fallback_card.validate():
                cards.append(fallback_card)
        except Exception as e:
            logger.warning(
                f"Fallback-карточка для {topic_name} "
                f"не прошла валидацию: {e}"
            )

    logger.info(f"Парсинг {topic_name}: найдено {len(cards)} карточек")
    return cards


def parse_all_markdown_files(input_dir: str) -> List[InterviewCard]:
    all_cards: List[InterviewCard] = []
    card_id = 1

    if not os.path.exists(input_dir):
        logger.warning(f"Директория не найдена: {input_dir}")
        return all_cards

    input_path = Path(input_dir)

    for file_path in sorted(input_path.rglob('*.md')):
        cards = parse_cards_from_markdown(str(file_path), card_id)
        all_cards.extend(cards)
        card_id += len(cards)

    logger.info(f"Всего карточек: {len(all_cards)}")
    return all_cards


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def escape_html(text: str) -> str:
    if not text:
        return ""
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    return text


def escape_for_tsv(text: str) -> str:
    if not text:
        return ""

    text = text.replace('\t', '    ')

    text = text.replace('\r\n', '<br>')
    text = text.replace('\r', '<br>')
    text = text.replace('\n', '<br>')

    while '<br><br><br>' in text:
        text = text.replace('<br><br><br>', '<br><br>')
    return text


def format_code_for_anki(code: str, language: str = 'text') -> str:
    if not code:
        return ""

    code = code.strip()
    language = language.strip().lower() if language else 'text'

    try:
        lexer = get_lexer_by_name(language, stripall=True)
    except Exception:
        lexer = TextLexer(stripall=True)

    formatter = HtmlFormatter(
        noclasses=True,
        nowrap=True,
        style='monokai',
    )

    highlighted = pygments_highlight(code, lexer, formatter)
    highlighted = highlighted.strip()
    highlighted = highlighted.replace('\n', '<br>')

    lang_colors = {
        'python': '#306998', 'javascript': '#f7df1e',
        'typescript': '#3178c6', 'java': '#ed8b00',
        'sql': '#e38c00', 'bash': '#4eaa25',
        'shell': '#4eaa25', 'json': '#292929',
        'go': '#00add8', 'rust': '#ce412b',
        'xml': '#e44d26', 'html': '#e44d26',
        'css': '#264de4', 'kotlin': '#7f52ff',
        'swift': '#fa7343', 'c': '#555',
        'cpp': '#004482', 'csharp': '#239120',
        'ruby': '#cc342d', 'php': '#777bb4',
        'yaml': '#cb171e', 'text': '#666',
    }
    accent = lang_colors.get(language, '#666')
    lang_label = escape_html(language)

    result = (
        f'<div style="margin:8px 0;">'
        f'<div style="background:{accent};color:#fff;'
        f'padding:2px 8px;border-radius:4px 4px 0 0;'
        f'font-size:0.75em;display:inline-block;">'
        f'{lang_label}</div>'
        f'<pre style="background:#272822;color:#f8f8f2;'
        f'padding:12px;border-radius:0 4px 4px 4px;'
        f'overflow-x:auto;margin:0;font-size:0.85em;'
        f'line-height:1.5;'
        f'font-family:\'Fira Code\',Consolas,'
        f'\'Courier New\',monospace;">'
        f'{highlighted}</pre></div>'
    )

    result = result.replace('\n', '')
    result = result.replace('\r', '')

    return result


def format_markdown_to_html(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(
        r'`([^`]+)`',
        r'<code style="background:#f0f0f0;padding:2px 5px;'
        r'border-radius:3px;">\1</code>',
        text
    )
    text = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    text = re.sub(
        r'^\s*[-*]\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE
    )
    text = re.sub(r'(<li>.+</li>\n?)+', r'<ul>\g<0></ul>', text)
    text = re.sub(
        r'^\s*\d+\.\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE
    )
    text = text.replace('\n\n', '<br><br>')
    text = text.replace('\n', '<br>')

    return text


def format_markdown_to_anki_html(text: str) -> str:
    if not text:
        return ""

    result = text

    result = re.sub(
        r'^[ \t]+(```)', r'\1', result, flags=re.MULTILINE,
    )

    code_blocks: List[Tuple[str, str]] = []

    def _save_code_block(m):
        lang = (m.group(1) or 'text').strip().lower()
        code_content = m.group(2)
        idx = len(code_blocks)
        code_blocks.append((lang, code_content))
        return f"\x00CB{idx}\x00"

    result = re.sub(
        r'```\s*(\w*)\s*\r?\n(.*?)\r?\n\s*```',
        _save_code_block, result, flags=re.DOTALL,
    )
    result = re.sub(
        r'```\s*(\w*)\s*\r?\n(.*?)```',
        _save_code_block, result, flags=re.DOTALL,
    )
    result = re.sub(
        r'```\s*(\w*)\s*\r?\n(.*?)$',
        _save_code_block, result, flags=re.DOTALL,
    )
    result = re.sub(r'```\s*\w*\s*', '', result)

    inline_codes: List[str] = []

    def _save_inline(m):
        idx = len(inline_codes)
        inline_codes.append(m.group(1))
        return f"\x00IC{idx}\x00"

    result = re.sub(r'``([^`]+)``', _save_inline, result)
    result = re.sub(r'`([^`\n]+)`', _save_inline, result)
    result = re.sub(r'`+', '', result)

    result = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', result)
    result = re.sub(
        r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', result,
    )

    result = re.sub(
        r'^####\s+(.+)$', r'<h4>\1</h4>',
        result, flags=re.MULTILINE,
    )
    result = re.sub(
        r'^###\s+(.+)$', r'<h3>\1</h3>',
        result, flags=re.MULTILINE,
    )
    result = re.sub(
        r'^##\s+(.+)$', r'<h2>\1</h2>',
        result, flags=re.MULTILINE,
    )
    result = re.sub(
        r'^#\s+(.+)$', r'<h1>\1</h1>',
        result, flags=re.MULTILINE,
    )

    result = re.sub(
        r'^-{3,}\s*$', '<hr>', result, flags=re.MULTILINE,
    )

    lines = result.split('\n')
    processed: List[str] = []
    in_bq = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('>'):
            if not in_bq:
                processed.append(
                    '<blockquote style="border-left:3px solid #ccc;'
                    'padding-left:10px;color:#555;margin:8px 0;">'
                )
                in_bq = True
            processed.append(re.sub(r'^>\s*', '', stripped))
        else:
            if in_bq:
                processed.append('</blockquote>')
                in_bq = False
            processed.append(line)
    if in_bq:
        processed.append('</blockquote>')
    result = '\n'.join(processed)

    result = _convert_lists_to_html(result)

    result = re.sub(
        r'^\s*\d+\.\s+(.+)$', r'<li>\1</li>',
        result, flags=re.MULTILINE,
    )

    result = result.replace('\n\n', '<br><br>')
    result = result.replace('\n', '<br>')
    result = re.sub(r'(<br>){3,}', '<br><br>', result)

    block_tags = r'(?:ul|ol|li|h[1-4]|blockquote|hr|div|pre)'
    result = re.sub(
        rf'(</?{block_tags}[^>]*>)<br>', r'\1', result,
    )
    result = re.sub(
        rf'<br>(</?{block_tags}[^>]*>)', r'\1', result,
    )

    for idx, code_text in enumerate(inline_codes):
        escaped = escape_html(code_text)
        styled = (
            f'<code style="background:#272822;padding:2px 6px;'
            f'border-radius:4px;'
            f'font-family:Consolas,monospace;'
            f'font-size:0.9em;">{escaped}</code>'
        )
        result = result.replace(f"\x00IC{idx}\x00", styled)

    for idx, (lang, code_content) in enumerate(code_blocks):
        formatted = format_code_for_anki(code_content, lang)
        result = result.replace(f"\x00CB{idx}\x00", formatted)

    return result


def _convert_lists_to_html(text: str) -> str:
    lines = text.split('\n')
    result_lines: List[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        is_item = bool(re.match(r'^[-*]\s+', stripped))

        if is_item:
            if not in_list:
                result_lines.append('<ul>')
                in_list = True
            content = re.sub(r'^[-*]\s+', '', stripped)
            result_lines.append(f'<li>{content}</li>')
        elif in_list and not stripped:
            continue
        elif in_list:
            result_lines.append('</ul>')
            in_list = False
            result_lines.append(line)
        else:
            result_lines.append(line)

    if in_list:
        result_lines.append('</ul>')

    return '\n'.join(result_lines)


def remove_obsidian_links(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\[\[.*?\|(.+?)\]\]', r'\1', text)
    text = re.sub(r'\[\[(.+?)\]\]', r'\1', text)
    return text


def remove_spaced_repetition_tags(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'#interview\s*', '', text)
    text = re.sub(r'#flashcards(/[a-zA-Z0-9_/-]+)?\s*', '', text)
    text = re.sub(r'#difficulty/\w+\s*', '', text)
    return text.strip()


def convert_cloze_to_anki_format(text: str) -> str:
    if not text or '==' not in text:
        return text

    result = text
    cloze_counter = [0]
    seq_to_num: Dict[int, int] = {}

    code_blocks: List[str] = []

    def _save_code(m):
        idx = len(code_blocks)
        code_blocks.append(m.group(0))
        return f"\x02CODEPROTECT{idx}\x02"

    result = re.sub(r'```.*?```', _save_code, result, flags=re.DOTALL)
    result = re.sub(r'`[^`]+`', _save_code, result)

    def _replace_seq_hint(m):
        content = m.group(1)
        hint = m.group(2)
        seq = int(m.group(3))
        if seq not in seq_to_num:
            cloze_counter[0] += 1
            seq_to_num[seq] = cloze_counter[0]
        num = seq_to_num[seq]
        if hint and hint.strip():
            return f"{{{{c{num}::{content}::{hint.strip()}}}}}"
        return f"{{{{c{num}::{content}}}}}"

    result = re.sub(
        r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]', _replace_seq_hint, result
    )

    def _replace_hint(m):
        content = m.group(1)
        hint = m.group(2)
        cloze_counter[0] += 1
        if hint and hint.strip():
            return f"{{{{c{cloze_counter[0]}::{content}::{hint.strip()}}}}}"
        return f"{{{{c{cloze_counter[0]}::{content}}}}}"

    result = re.sub(
        r'==(.+?)==\^\[([^\]]*)\](?!\[\^)', _replace_hint, result
    )

    def _replace_actions(m):
        cloze_counter[0] += 1
        return f"{{{{c{cloze_counter[0]}::{m.group(1)}}}}}"

    result = re.sub(
        r'==(.+?)==\[\^([ahs]+)\]', _replace_actions, result
    )

    def _replace_simple(m):
        cloze_counter[0] += 1
        return f"{{{{c{cloze_counter[0]}::{m.group(1)}}}}}"

    result = re.sub(r'==(.+?)==', _replace_simple, result)

    for idx, block in enumerate(code_blocks):
        result = result.replace(f"\x02CODEPROTECT{idx}\x02", block)

    return result


def generate_card_guid(card: InterviewCard) -> str:
    key = (
        f"{card.topic}|{card.question[:200]}|"
        f"{card.card_type.value}|{card.is_reverse}"
    )
    return hashlib.md5(key.encode('utf-8')).hexdigest()[:12]


def generate_obsidian_topic_file(
        cards: List[InterviewCard],
        topic_name: str,
        output_dir: str,
        deck_name: str = None
) -> str:
    os.makedirs(output_dir, exist_ok=True)

    if not deck_name and cards:
        deck_name = cards[0].deck_name
    elif not deck_name:
        deck_name = "flashcards"

    category = cards[0].category if cards else 'general'
    tags = normalize_tags(cards[0].tags if cards else [])
    if not tags:
        tags = ['flashcards']

    frontmatter_data = {
        'tags': tags,
        'category': category,
        'deck': deck_name,
    }

    frontmatter_yaml = yaml.safe_dump(
        frontmatter_data,
        allow_unicode=True,
        sort_keys=False
    ).strip()

    content_parts = [
        "---",
        frontmatter_yaml,
        "---",
        "",
        f"# {topic_name}",
        "",
    ]

    for card in cards:
        content_parts.append("---")
        content_parts.append("")

        if card.card_type == CardType.CLOZE:
            content_parts.append(remove_scheduling_comment(card.answer))

        elif card.card_type == CardType.SINGLE_LINE_BASIC:
            question = remove_scheduling_comment(card.question)
            answer = remove_scheduling_comment(card.answer)
            content_parts.append(f"{question}::{answer}")

        elif card.card_type == CardType.SINGLE_LINE_BIDIRECTIONAL:
            if not card.is_reverse:
                question = remove_scheduling_comment(card.question)
                answer = remove_scheduling_comment(card.answer)
                content_parts.append(f"{question}:::{answer}")

        elif card.card_type == CardType.MULTI_LINE_BASIC:
            question = remove_scheduling_comment(card.question)
            answer = remove_scheduling_comment(card.answer)
            content_parts.append(question)
            content_parts.append("?")
            content_parts.append(answer)

        elif card.card_type == CardType.MULTI_LINE_BIDIRECTIONAL:
            if not card.is_reverse:
                question = remove_scheduling_comment(card.question)
                answer = remove_scheduling_comment(card.answer)
                content_parts.append(question)
                content_parts.append("??")
                content_parts.append(answer)

        if card.scheduling:
            scheduling_comment = card.scheduling.to_html_comment()
            if scheduling_comment:
                content_parts.append("")
                content_parts.append(scheduling_comment)

        content_parts.append("")

    content = '\n'.join(content_parts)
    file_path = os.path.join(output_dir, f"{topic_name}_obsidian_cards.md")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    logger.info(f"Создан файл темы: {file_path}")
    return file_path


def _clean_text_for_anki(text: str) -> str:
    if not text:
        return ""
    text = remove_scheduling_comment(text)
    text = remove_obsidian_links(text)
    text = remove_spaced_repetition_tags(text)
    return text.strip()


def _build_source_html(card: InterviewCard) -> str:
    if not card.source_note:
        return ""
    name = escape_html(card.source_note)
    return (
        f'<hr style="border:none;border-top:1px solid #ddd;margin:12px 0 6px 0;">'
        f'<div style="color:#999;font-size:0.8em;">📎 {name}</div>'
    )


def _format_tags_for_anki(card: InterviewCard) -> str:
    tag_values = normalize_tags(card.tags)
    result = []
    for tag in tag_values:
        clean = tag.lstrip('#').strip().replace(' ', '_')
        if clean:
            result.append(clean)

    if card.category and card.category not in result:
        result.append(card.category)
    return ' '.join(result)


def _write_anki_basic_file(
        cards: List[InterviewCard],
        file_path: str,
        deck_name: str,
) -> None:
    header_lines = [
        "#separator:tab",
        "#html:true",
        f"#notetype:{ANKI_NOTE_TYPES['basic']}",
        f"#deck:{deck_name}",
        "#tags column:3",
    ]

    card_lines: List[str] = []

    for card in cards:
        front_raw = _clean_text_for_anki(card.question or "")
        back_raw = _clean_text_for_anki(card.answer or "")

        front = format_markdown_to_anki_html(front_raw)
        back = format_markdown_to_anki_html(back_raw)

        if card.code_snippets:
            for snippet in card.code_snippets:
                norm_snippet = ' '.join(snippet.split())
                norm_back = ' '.join(back_raw.split())
                if norm_snippet not in norm_back:
                    back += format_code_for_anki(snippet)

        back += _build_source_html(card)

        front = escape_for_tsv(front)
        back = escape_for_tsv(back)
        tags = _format_tags_for_anki(card)

        line = f"{front}\t{back}\t{tags}"
        assert '\n' not in line, (
            f"Newline in card line! card_id={card.id}"
        )
        card_lines.append(line)

    content = '\n'.join(header_lines) + '\n' + '\n'.join(card_lines) + '\n'

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


def _write_anki_reversed_file(
        cards: List[InterviewCard],
        file_path: str,
        deck_name: str,
) -> None:
    header_lines = [
        "#separator:tab",
        "#html:true",
        f"#notetype:{ANKI_NOTE_TYPES['reversed']}",
        f"#deck:{deck_name}",
        "#tags column:3",
    ]

    card_lines: List[str] = []

    for card in cards:
        front_raw = _clean_text_for_anki(card.question or "")
        back_raw = _clean_text_for_anki(card.answer or "")

        front = format_markdown_to_anki_html(front_raw)
        back = format_markdown_to_anki_html(back_raw)

        if card.code_snippets:
            for snippet in card.code_snippets:
                norm_snippet = ' '.join(snippet.split())
                norm_back = ' '.join(back_raw.split())
                if norm_snippet not in norm_back:
                    back += format_code_for_anki(snippet)

        back += _build_source_html(card)

        front = escape_for_tsv(front)
        back = escape_for_tsv(back)
        tags = _format_tags_for_anki(card)

        line = f"{front}\t{back}\t{tags}"
        assert '\n' not in line, (
            f"Newline in reversed card! card_id={card.id}"
        )
        card_lines.append(line)

    content = '\n'.join(header_lines) + '\n' + '\n'.join(card_lines) + '\n'

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


def _write_anki_cloze_file(
        cards: List[InterviewCard],
        file_path: str,
        deck_name: str,
) -> None:
    header_lines = [
        "#separator:tab",
        "#html:true",
        f"#notetype:{ANKI_NOTE_TYPES['cloze']}",
        f"#deck:{deck_name}",
        "#tags column:3",
    ]

    card_lines: List[str] = []

    for card in cards:
        cloze_raw = card.answer or ""
        cloze_raw = remove_scheduling_comment(cloze_raw)
        cloze_raw = remove_obsidian_links(cloze_raw)
        cloze_raw = remove_spaced_repetition_tags(cloze_raw)

        anki_cloze = convert_cloze_to_anki_format(cloze_raw)

        cloze_markers: List[str] = []

        def _save_cloze(m, markers=cloze_markers):
            idx = len(markers)
            markers.append(m.group(0))
            return f"\x01CL{idx}\x01"

        protected = re.sub(
            r'\{\{c\d+::.*?\}\}',
            _save_cloze,
            anki_cloze,
            flags=re.DOTALL,
        )

        html_text = format_markdown_to_anki_html(protected)

        for idx, marker in enumerate(cloze_markers):
            html_text = html_text.replace(f"\x01CL{idx}\x01", marker)

        back_extra = _build_source_html(card)

        html_text = escape_for_tsv(html_text)
        back_extra = escape_for_tsv(back_extra)
        tags = _format_tags_for_anki(card)

        line = f"{html_text}\t{back_extra}\t{tags}"
        assert '\n' not in line, (
            f"Newline in cloze card! card_id={card.id}"
        )
        card_lines.append(line)

    content = '\n'.join(header_lines) + '\n' + '\n'.join(card_lines) + '\n'

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


def generate_anki_import_file(
        cards: List[InterviewCard],
        topic_name: str,
        output_path: str,
        deck_prefix: str = "Interview",
        use_reverse_cards: bool = True,
) -> List[str]:
    output_dir = (
        output_path
        if os.path.isdir(output_path)
        else os.path.dirname(output_path)
    )
    os.makedirs(output_dir, exist_ok=True)

    created_files: List[str] = []

    if not cards:
        return created_files

    if cards[0].deck_name:
        deck_name = cards[0].deck_name.replace('/', '::')
    else:
        category = cards[0].category
        deck_name = f"{deck_prefix}::{category.replace('_', ' ').title()}"

    if deck_name.startswith('flashcards'):
        deck_name = deck_name.replace('flashcards', 'Interview Cards', 1)

    basic_cards: List[InterviewCard] = [
        c for c in cards
        if c.card_type in BASIC_CARD_TYPES
    ]

    cloze_cards: List[InterviewCard] = [
        c for c in cards
        if c.card_type == CardType.CLOZE
    ]

    reversed_cards: List[InterviewCard] = []

    if use_reverse_cards:
        reversed_cards = [
            c for c in cards
            if c.card_type in BIDIRECTIONAL_CARD_TYPES
               and not c.is_reverse
        ]
    else:
        extra_basic = [
            c for c in cards
            if c.card_type in BIDIRECTIONAL_CARD_TYPES
               and not c.is_reverse
        ]
        basic_cards.extend(extra_basic)

    if basic_cards:
        basic_path = os.path.join(
            output_dir, f"{topic_name}_anki_basic.txt"
        )
        _write_anki_basic_file(basic_cards, basic_path, deck_name)
        created_files.append(basic_path)
        logger.info(
            f"Anki Basic: {basic_path} ({len(basic_cards)} карт.)"
        )

    if reversed_cards:
        reversed_path = os.path.join(
            output_dir, f"{topic_name}_anki_reversed.txt"
        )
        _write_anki_reversed_file(
            reversed_cards, reversed_path, deck_name
        )
        created_files.append(reversed_path)
        logger.info(
            f"Anki Reversed: {reversed_path} "
            f"({len(reversed_cards)} карт. × 2 направления)"
        )

    if cloze_cards:
        cloze_path = os.path.join(
            output_dir, f"{topic_name}_anki_cloze.txt"
        )
        _write_anki_cloze_file(cloze_cards, cloze_path, deck_name)
        created_files.append(cloze_path)
        logger.info(f"Anki Cloze: {cloze_path} ({len(cloze_cards)} карт.)")

    if not created_files:
        logger.warning(f"Нет карточек для Anki в теме '{topic_name}'")

    return created_files


def clean_up_duplicates(file_paths: List[str]) -> int:
    if not file_paths:
        return 0

    seen_questions: Set[str] = set()
    total_removed = 0

    for file_path in file_paths:
        if not os.path.exists(file_path):
            continue

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()

            header_lines: List[str] = []
            data_lines: List[str] = []

            for line in all_lines:
                stripped = line.strip()
                if stripped.startswith('#') or not stripped:
                    header_lines.append(line)
                else:
                    data_lines.append(line)

            unique_data: List[str] = []
            for line in data_lines:
                parts = line.split('\t')
                question_key = normalize_text_key(parts[0]) if parts else ""
                if question_key and question_key not in seen_questions:
                    seen_questions.add(question_key)
                    unique_data.append(line)
                elif not question_key:
                    unique_data.append(line)

            removed = len(data_lines) - len(unique_data)
            total_removed += removed

            if removed > 0:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(header_lines)
                    f.writelines(unique_data)
                logger.info(
                    f"Удалено {removed} дубл. из {os.path.basename(file_path)}"
                )

        except Exception as e:
            logger.warning(f"Ошибка обработки {file_path}: {e}")
            continue

    logger.info(f"Всего удалено дубликатов: {total_removed}")
    return total_removed


def natural_sort(file_paths: List[str]) -> List[str]:
    def natural_key(text: str) -> List:
        return [
            int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', text)
        ]

    return sorted(file_paths, key=natural_key)


def get_markdown_files(folder_paths: List[str]) -> List[str]:
    file_paths: List[str] = []

    for folder_path in folder_paths:
        if os.path.exists(folder_path):
            files = glob.glob(
                os.path.join(folder_path, '**/*.md'), recursive=True
            )
            file_paths.extend(files)

    return natural_sort(file_paths)


def validate_markdown_structure(content: str) -> bool:
    has_single_basic = bool(re.search(r'[^:]::[^:]', content))
    has_single_bidirectional = bool(re.search(r':::', content))
    has_multi_basic = bool(re.search(r'\n\?\n', content))
    has_multi_bidirectional = bool(re.search(r'\n\?\?\n', content))
    has_cloze = bool(re.search(r'==.+?==', content, re.DOTALL))
    has_question = bool(re.search(r'###\s*Вопрос:', content))

    return (
            has_single_basic or has_single_bidirectional or
            has_multi_basic or has_multi_bidirectional or
            has_cloze or has_question
    )


def process_cards_batch(
        cards: List[InterviewCard],
        category: str,
        output_dir: str,
        batch_size: int = 500,
        use_reverse_cards: bool = True,
) -> List[str]:
    output_files: List[str] = []

    for i in range(0, len(cards), batch_size):
        batch = cards[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(cards) + batch_size - 1) // batch_size

        category_name = (
            f"{category}_part{batch_num}"
            if total_batches > 1
            else category
        )

        anki_files = generate_anki_import_file(
            batch,
            category_name,
            output_dir,
            use_reverse_cards=use_reverse_cards,
        )
        output_files.extend(anki_files)

    return output_files


def generate_all_formats(
        cards: List[InterviewCard],
        category: str,
        cards_output: str,
        anki_output: str,
        use_reverse_cards: bool = True,
        formats: str = "both",
) -> List[str]:
    output_files: List[str] = []

    if not cards:
        return output_files

    topic_name = cards[0].topic
    deck_name = cards[0].deck_name

    gen_obsidian = formats in ("both", "obsidian")
    gen_anki = formats in ("both", "anki")

    logger.info(
        f"Генерация для темы: {topic_name} "
        f"(Категория: {category}, Deck: {deck_name}, "
        f"Форматы: {formats})"
    )

    if gen_obsidian:
        obsidian_cards = [c for c in cards if not c.is_reverse]

        if obsidian_cards:
            os.makedirs(cards_output, exist_ok=True)
            merged_file = generate_obsidian_topic_file(
                obsidian_cards,
                topic_name,
                cards_output,
                deck_name,
            )
            output_files.append(merged_file)

    if gen_anki:
        os.makedirs(anki_output, exist_ok=True)

        anki_files = generate_anki_import_file(
            cards,
            topic_name,
            anki_output,
            use_reverse_cards=use_reverse_cards,
        )
        output_files.extend(anki_files)

    logger.info(f"Сгенерировано файлов: {len(output_files)}")
    return output_files
