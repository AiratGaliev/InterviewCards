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
from pygments.lexers import get_lexer_by_name, TexLexer
from pygments.formatters.html import HtmlFormatter

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


def _strip_formatting_for_separator_check(text: str) -> str:
    """Убирает содержимое inline code, bold и italic
    для проверки разделителей.

    `ClassName::method`   → \x00
    **Class::method**     → \x00
    *Class::method*       → \x00
    ~~Class::method~~     → \x00

    Разделитель ВНЕ форматирования сохраняется:
    `code`::answer → \x00::answer
    """
    # Порядок важен: сначала более специфичные
    result = re.sub(r'`[^`]*`', '\x00', text)
    result = re.sub(r'\*\*[^*]+\*\*', '\x00', result)
    result = re.sub(r'(?<!\*)\*(?!\*)[^*]+\*(?!\*)', '\x00', result)
    result = re.sub(r'~~[^~]+~~', '\x00', result)
    return result


def _find_separator_position(line: str, separator: str) -> int:
    """Находит позицию первого separator вне inline code,
    bold, italic и strikethrough.

    Для '::' пропускает позиции, являющиеся частью ':::'.
    Возвращает -1, если не найден.
    """
    protected_ranges: List[Tuple[int, int]] = []

    # inline code
    for m in re.finditer(r'`[^`]*`', line):
        protected_ranges.append((m.start(), m.end()))
    # bold **...**
    for m in re.finditer(r'\*\*[^*]+\*\*', line):
        protected_ranges.append((m.start(), m.end()))
    # italic *...* (не bold)
    for m in re.finditer(r'(?<!\*)\*(?!\*)[^*]+\*(?!\*)', line):
        protected_ranges.append((m.start(), m.end()))
    # strikethrough ~~...~~
    for m in re.finditer(r'~~[^~]+~~', line):
        protected_ranges.append((m.start(), m.end()))

    # Сортируем для корректного перепрыгивания
    protected_ranges.sort(key=lambda r: r[0])

    def _is_protected(pos: int) -> bool:
        return any(s <= pos < e for s, e in protected_ranges)

    sep_len = len(separator)
    idx = 0

    while idx <= len(line) - sep_len:
        if _is_protected(idx):
            for s, e in protected_ranges:
                if s <= idx < e:
                    idx = e
                    break
            continue

        if line[idx:idx + sep_len] != separator:
            idx += 1
            continue

        if separator == '::' and sep_len == 2:
            before_is_colon = (
                    idx > 0
                    and line[idx - 1] == ':'
                    and not _is_protected(idx - 1)
            )
            after_is_colon = (
                    idx + 2 < len(line)
                    and line[idx + 2] == ':'
                    and not _is_protected(idx + 2)
            )
            if before_is_colon or after_is_colon:
                idx += 1
                continue

        return idx

    return -1


def _strip_all_formatting(text: str) -> str:
    """Убирает содержимое inline code для проверки cloze.

    `a == b`  → убрано (== — оператор, не cloze)
    ==text==  → сохранено (настоящий cloze)
    """
    return re.sub(r'`[^`]*`', '', text)


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
        lines.append(
            f"Смешение форматов: "
            f"{'Да ⚠️' if self.has_mixed_formats else 'Нет ✅'}"
        )

        if self.detected_formats:
            lines.append("Обнаруженные форматы:")
            for fmt, line_nums in self.detected_formats.items():
                preview = ', '.join(map(str, line_nums[:10]))
                extra = (
                    f" ...и ещё {len(line_nums) - 10}"
                    if len(line_nums) > 10 else ""
                )
                lines.append(f"  - {fmt}: строки {preview}{extra}")

        if self.issues:
            lines.append(f"\nПроблемы ({len(self.issues)}):")
            for issue in self.issues:
                icon = {
                    "error": "❌", "warning": "⚠️", "info": "ℹ️"
                }.get(issue.severity, "•")
                lines.append(
                    f"  {icon} Строка {issue.line_number}: {issue.message}"
                )
                if issue.line_text:
                    display = issue.line_text[:120]
                    if len(issue.line_text) > 120:
                        display += "…"
                    lines.append(f"     │ {display}")
                if issue.suggestion:
                    lines.append(f"     └ Совет: {issue.suggestion}")
        else:
            lines.append("✅ Проблем не обнаружено")

        return "\n".join(lines)


@dataclass
class DuplicateInfo:
    """Информация о найденном дубликате карточки."""
    question_preview: str  # Начало текста вопроса (до 120 символов)
    answer_preview: str = ""  # Начало ответа (для контекста, до 80 символов)
    original_source: str = ""  # Путь к файлу первого вхождения
    original_topic: str = ""  # Имя темы первого вхождения
    duplicate_source: str = ""  # Путь к файлу дубликата
    duplicate_topic: str = ""  # Имя темы дубликата
    card_type: str = ""  # Тип карточки (значение CardType)
    is_cross_file: bool = False  # True — дубликат в другом файле


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
    """Полная валидация одного Markdown-файла."""

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
            severity='error',
            line_number=0,
            line_text='',
            message=f"Не удалось прочитать файл: {e}",
        ))
        report.is_valid = False
        return report

    lines = content.splitlines()

    # --- 1. Frontmatter ---
    _validate_frontmatter(content, lines, report)

    # --- 2. Построчный анализ ---
    in_code_block = False
    in_frontmatter = False
    frontmatter_closed = False
    all_formats: Dict[str, List[int]] = {}

    for line_idx, line in enumerate(lines):
        line_num = line_idx + 1
        stripped = line.strip()

        # Отслеживание frontmatter
        if line_idx == 0 and stripped == '---':
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == '---':
                in_frontmatter = False
                frontmatter_closed = True
            continue

        # Отслеживание блоков кода
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Определение формата строки
        fmt = _detect_line_format_for_validation(stripped, False)

        if fmt:
            if fmt not in all_formats:
                all_formats[fmt] = []
            all_formats[fmt].append(line_num)

        # --- Проверки конкретных форматов ---

        if fmt == 'single_line_basic':
            _check_single_line_basic(stripped, line_num, report)

        elif fmt == 'single_line_bidirectional':
            _check_single_line_bidirectional(stripped, line_num, report)

        elif fmt in ('multi_line_basic_sep', 'multi_line_bidirectional_sep'):
            _check_multiline_separator(
                stripped, line_num, lines, line_idx, report,
            )

        # Проверка незакрытых cloze (для любой строки с ==)
        _check_unclosed_cloze(stripped, line_num, report)

    # Проверка незакрытого блока кода
    if in_code_block:
        report.issues.append(FormatIssue(
            severity='error',
            line_number=len(lines),
            line_text='',
            message=(
                "Незакрытый блок кода "
                "(``` без парного закрывающего ```)"
            ),
            suggestion="Добавьте закрывающий ``` в конце блока кода",
        ))

    report.detected_formats = all_formats

    # --- 3. Смешение форматов на уровне файла ---
    _check_format_mixing(all_formats, report)

    # --- 3b. Все конфликты на уровне блоков ---
    _check_block_level_conflicts(lines, report)

    # --- 4. Подсчёт карточек ---
    try:
        cards = parse_cards_from_markdown(file_path)
        report.total_cards_found = len(cards)
    except Exception:
        report.total_cards_found = 0

    # --- 5. Дополнительные проверки ---
    _check_empty_content(content, report)
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
            severity='info',
            line_number=1,
            line_text='',
            message="Отсутствует frontmatter (---...---)",
            suggestion=(
                "Добавьте frontmatter с тегами и категорией: "
                "tags: [flashcards/...], category: ..."
            ),
        ))
        return

    if 'tags' not in fm:
        report.issues.append(FormatIssue(
            severity='warning',
            line_number=1,
            line_text='',
            message="Frontmatter без поля 'tags'",
            suggestion="Добавьте tags: [flashcards/category]",
        ))

    if 'category' not in fm and 'deck' not in fm:
        report.issues.append(FormatIssue(
            severity='info',
            line_number=1,
            line_text='',
            message=(
                "Frontmatter без 'category' и 'deck' — "
                "будет использована категория из имени папки"
            ),
        ))

    tags = normalize_tags(fm.get('tags', []))
    has_flashcards_tag = any(t.startswith('flashcards') for t in tags)
    if tags and not has_flashcards_tag:
        report.issues.append(FormatIssue(
            severity='info',
            line_number=1,
            line_text=f"tags: {fm.get('tags')}",
            message=(
                "Нет тега flashcards/... — "
                "колода будет определена автоматически"
            ),
            suggestion=(
                "Добавьте тег flashcards/<category> "
                "для явного указания колоды"
            ),
        ))


def _detect_line_format_for_validation(
        line: str,
        in_code_block: bool,
) -> Optional[str]:
    """Определяет формат карточки в строке для целей валидации.

    Возвращает строковый идентификатор формата или None.
    Вызывается ТОЛЬКО для строк вне блоков кода и frontmatter.
    """
    stripped = line.strip()

    if not stripped or in_code_block:
        return None

    # Пропускаем служебные строки
    if stripped.startswith('```'):
        return None
    if stripped == '---':
        return None

    # Legacy формат — самый специфичный, проверяем первым
    if re.match(r'^###\s*Вопрос:', stripped):
        return 'legacy_question'

    # Заголовки — не карточки
    if stripped.startswith('#'):
        return None

    # Многострочные разделители — ТОЛЬКО если строка состоит
    # ЦЕЛИКОМ из ? или ?? (с возможными пробелами)
    if stripped == '??':
        return 'multi_line_bidirectional_sep'
    if stripped == '?':
        return 'multi_line_basic_sep'

    # Убираем inline-код, bold, italic, strikethrough
    text_no_inline_code = re.sub(r'`[^`]*`', '___CODE___', stripped)
    text_no_inline_code = re.sub(r'\*\*[^*]+\*\*', '___CODE___', text_no_inline_code)
    text_no_inline_code = re.sub(r'(?<!\*)\*(?!\*)[^*]+\*(?!\*)', '___CODE___', text_no_inline_code)
    text_no_inline_code = re.sub(r'~~[^~]+~~', '___CODE___', text_no_inline_code)

    # Bidirectional ::: — проверяем ДО basic ::
    # Условие: ровно одно ::: в строке, обе стороны непустые
    if ':::' in text_no_inline_code:
        parts = text_no_inline_code.split(':::', 1)
        if (len(parts) == 2
                and parts[0].strip()
                and parts[1].strip()
                # Исключаем URL-подобные паттерны
                and not re.search(r'https?://', stripped)):
            return 'single_line_bidirectional'

    # Basic :: — ровно одно :: (не :::), обе стороны непустые
    if '::' in text_no_inline_code and ':::' not in text_no_inline_code:
        parts = text_no_inline_code.split('::', 1)
        if (len(parts) == 2
                and parts[0].strip()
                and parts[1].strip()
                # Исключаем URL-порты (localhost::8080 и т.д.)
                and not re.search(r'https?://', stripped)
                and not re.search(r':\d+', parts[1].strip()[:5])):
            return 'single_line_basic'

    # Cloze — парные ==текст== (не оператор сравнения)
    # Сначала убираем inline-код, потом ищем cloze
    text_for_cloze = _strip_all_formatting(stripped)
    if re.search(r'==\S.*?\S==|==\S==', text_for_cloze):
        return 'cloze'

    return None


# ──────────────────────────────────────
#  Группы совместимости форматов
# ──────────────────────────────────────

# Форматы одного «направления» — basic (одностороннее)
_BASIC_FORMATS = {'single_line_basic', 'multi_line_basic_sep', 'legacy_question'}

# Форматы двустороннего типа
_BIDI_FORMATS = {'single_line_bidirectional', 'multi_line_bidirectional_sep'}

# Cloze — может сосуществовать с чем угодно
_CLOZE_FORMATS = {'cloze'}

# Пары, которые ДЕЙСТВИТЕЛЬНО несовместимы в одном файле
_INCOMPATIBLE_PAIRS = [
    # Basic и Bidirectional в одном файле — разные note types в Anki,
    # но в Obsidian SR это допустимо. Предупреждаем мягко.
    (
        _BASIC_FORMATS, _BIDI_FORMATS,
        "warning",
        "Файл содержит и Basic (::/?), и Bidirectional (:::/??) карточки. "
        "При экспорте в Anki они попадут в разные note types.",
        "Это допустимо, но для порядка лучше разделить "
        "на отдельные файлы по типу.",
    ),
]


def _check_format_mixing(
        all_formats: Dict[str, List[int]],
        report: FileValidationReport,
) -> None:
    """Проверяет смешение несовместимых форматов в файле.

    Считает по ГРУППАМ форматов (basic, bidirectional),
    а не по индивидуальным синтаксическим вариантам.
    """

    found_formats = set(all_formats.keys())

    # Убираем cloze из анализа — он всегда совместим
    qa_formats = found_formats - _CLOZE_FORMATS

    if not qa_formats:
        return

    # ── Группируем по смысловым типам ──
    basic_found = qa_formats & _BASIC_FORMATS
    bidi_found = qa_formats & _BIDI_FORMATS

    # Считаем количество ГРУПП, а не вариантов синтаксиса
    active_groups = [g for g in [basic_found, bidi_found] if g]

    if len(active_groups) <= 1:
        # Все QA-форматы одного типа
        # (например :: и ? — оба basic, это нормально)
        return

    # ── Проверяем несовместимые пары ──
    for group_a, group_b, severity, message, suggestion in _INCOMPATIBLE_PAIRS:
        formats_in_a = qa_formats & group_a
        formats_in_b = qa_formats & group_b

        if not (formats_in_a and formats_in_b):
            continue

        # Считаем карточки в каждой группе
        count_a = sum(len(all_formats.get(f, [])) for f in formats_in_a)
        count_b = sum(len(all_formats.get(f, [])) for f in formats_in_b)
        min_count = min(count_a, count_b)

        # Единичные случаи — info, не warning
        effective_severity = 'info' if min_count <= 3 else severity

        # has_mixed_formats только для warning/error
        if effective_severity in ('warning', 'error'):
            report.has_mixed_formats = True

        # ── Детали для отчёта ──
        detail_lines = []
        for fmt in sorted(formats_in_a | formats_in_b):
            fmt_line_nums = all_formats.get(fmt, [])
            if fmt_line_nums:
                nums_str = ", ".join(str(n) for n in fmt_line_nums[:5])
                extra = (
                    f" ...+{len(fmt_line_nums) - 5}"
                    if len(fmt_line_nums) > 5 else ""
                )
                detail_lines.append(
                    f"  '{fmt}' → строки: {nums_str}{extra}"
                )

        all_line_nums = []
        for fmt in formats_in_a | formats_in_b:
            all_line_nums.extend(all_formats.get(fmt, []))

        first_line = min(all_line_nums) if all_line_nums else 1

        report.issues.append(FormatIssue(
            severity=effective_severity,
            line_number=first_line,
            line_text='',
            message=message,
            suggestion=suggestion + "\n" + "\n".join(detail_lines),
        ))


def _check_block_level_conflicts(
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Комплексная проверка конфликтов форматов внутри блоков.

    Проверяет все комбинации смешения:
    - cloze (==...==) внутри :: / ::: / ? / ?? / legacy блоков
    - :: / ::: внутри ? / ?? блоков (вопрос и ответ)
    - :: / ::: на одной строке
    - legacy + другие форматы
    - несколько разделителей без разграничения блоков
    """
    in_code_block = False
    in_frontmatter = False

    # --- Фаза 1: Проверка конфликтов вокруг ? и ?? ---
    _check_separator_block_conflicts(lines, report)

    # --- Фаза 2: Проверка конфликтов внутри однострочных :: и ::: ---
    _check_single_line_internal_conflicts(lines, report)

    # --- Фаза 3: Проверка конфликтов в legacy блоках ---
    _check_legacy_block_conflicts(lines, report)

    # --- Фаза 4: Проверка смежных разделителей без разграничения ---
    _check_adjacent_separators(lines, report)

    # --- Фаза 5: Проверка :: и ::: на одной строке ---
    _check_double_separator_on_line(lines, report)


def _iter_content_lines(lines: List[str]):
    """Итерирует строки контента, пропуская frontmatter и блоки кода.

    Yields: (line_idx, line_num, stripped_line, is_in_code_block)
    """
    in_code_block = False
    in_frontmatter = False

    for line_idx, line in enumerate(lines):
        line_num = line_idx + 1
        stripped = line.strip()

        if line_idx == 0 and stripped == '---':
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == '---':
                in_frontmatter = False
            continue

        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        yield line_idx, line_num, stripped


def _strip_inline_code(text: str) -> str:
    """Убирает inline-код из строки для анализа разметки."""
    return re.sub(r'`[^`]*`', '', text)


def _has_cloze(text: str) -> bool:
    """Проверяет наличие cloze-разметки (без inline-кода)."""
    clean = _strip_inline_code(text)
    return bool(re.search(r'==(?=\S).+?(?<=\S)==', clean))


def _has_basic_sep(text: str) -> bool:
    """Проверяет наличие :: (не :::) разделителя (без inline-кода)."""
    clean = _strip_inline_code(text)
    if ':::' in clean:
        return False
    if '::' not in clean:
        return False
    parts = clean.split('::', 1)
    return len(parts) == 2 and bool(parts[0].strip()) and bool(parts[1].strip())


def _has_bidi_sep(text: str) -> bool:
    """Проверяет наличие ::: разделителя (без inline-кода)."""
    clean = _strip_inline_code(text)
    if ':::' not in clean:
        return False
    parts = clean.split(':::', 1)
    return len(parts) == 2 and bool(parts[0].strip()) and bool(parts[1].strip())


def _collect_block_around_separator(
        lines: List[str],
        sep_line_idx: int,
) -> tuple:
    """Собирает строки вопроса (выше) и ответа (ниже) вокруг разделителя ? или ??.

    Returns:
        (question_lines, answer_lines)
        Каждый элемент — список кортежей (line_num, stripped_text)
    """
    question_lines = []
    check_idx = sep_line_idx - 1
    in_code = False

    while check_idx >= 0:
        prev = lines[check_idx].strip()
        if prev.startswith('```'):
            in_code = not in_code
            check_idx -= 1
            continue
        if in_code:
            check_idx -= 1
            continue
        if not prev:
            break
        if prev == '---':
            break
        if prev.startswith('#'):
            break
        question_lines.insert(0, (check_idx + 1, prev))
        check_idx -= 1

    answer_lines = []
    check_idx = sep_line_idx + 1
    in_code = False

    while check_idx < len(lines):
        nxt = lines[check_idx].strip()
        if nxt.startswith('```'):
            in_code = not in_code
            answer_lines.append((check_idx + 1, nxt))
            check_idx += 1
            continue
        if in_code:
            answer_lines.append((check_idx + 1, nxt))
            check_idx += 1
            continue
        if not nxt:
            break
        if nxt == '---':
            break
        if nxt.startswith('#'):
            break
        answer_lines.append((check_idx + 1, nxt))
        check_idx += 1

    return question_lines, answer_lines


def _check_separator_block_conflicts(
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Проверяет конфликты вокруг разделителей ? и ??:
    - cloze в вопросе или ответе
    - :: в вопросе или ответе
    - ::: в вопросе или ответе
    """
    for line_idx, line_num, stripped in _iter_content_lines(lines):
        if stripped not in ('?', '??'):
            continue

        separator = stripped
        question_lines, answer_lines = _collect_block_around_separator(
            lines, line_idx,
        )

        all_block_lines = question_lines + answer_lines

        for block_ln, block_text in all_block_lines:
            is_question = any(ln == block_ln for ln, _ in question_lines)
            side = "вопросе" if is_question else "ответе"

            # --- Cloze внутри ? / ?? блока ---
            if _has_cloze(block_text):
                report.issues.append(FormatIssue(
                    severity='warning',
                    line_number=block_ln,
                    line_text=block_text,
                    message=(
                        f"Cloze-разметка (==...==) в {side} "
                        f"блока с разделителем '{separator}' "
                        f"(строка {line_num}). "
                        f"Парсер обработает это как multi-line карточку, "
                        f"cloze-пропуски могут быть потеряны."
                    ),
                    suggestion=(
                        f"Если нужна Cloze — уберите '{separator}' "
                        f"и оставьте текст с ==...== как отдельный блок. "
                        f"Если нужна Q&A — уберите == из текста."
                    ),
                ))

            # --- :: внутри вопроса ? / ?? блока ---
            if is_question and _has_basic_sep(block_text):
                report.issues.append(FormatIssue(
                    severity='warning',
                    line_number=block_ln,
                    line_text=block_text,
                    message=(
                        f"Basic-разделитель (::) в {side} блока "
                        f"с '{separator}' (строка {line_num}). "
                        f"Парсер может обработать строку как "
                        f"single-line Basic вместо multi-line."
                    ),
                    suggestion=(
                        f"Используйте один формат: "
                        f"либо :: (однострочный), "
                        f"либо '{separator}' (многострочный). "
                        f"Если :: — часть текста, оберните в `inline code`."
                    ),
                ))

            # --- ::: внутри вопроса ? / ?? блока ---
            if is_question and _has_bidi_sep(block_text):
                report.issues.append(FormatIssue(
                    severity='warning',
                    line_number=block_ln,
                    line_text=block_text,
                    message=(
                        f"Bidirectional-разделитель (:::) в {side} блока "
                        f"с '{separator}' (строка {line_num}). "
                        f"Парсер может обработать строку как "
                        f"single-line Bidirectional вместо multi-line."
                    ),
                    suggestion=(
                        f"Используйте один формат: "
                        f"либо ::: (однострочный), "
                        f"либо '{separator}' (многострочный). "
                        f"Если ::: — часть текста, оберните в `inline code`."
                    ),
                ))

            # --- :: в ответе ? / ?? блока ---
            #     (менее критично, но может запутать при редактировании)
            if not is_question and _has_basic_sep(block_text):
                report.issues.append(FormatIssue(
                    severity='info',
                    line_number=block_ln,
                    line_text=block_text,
                    message=(
                        f"Разделитель '::' в ответе блока "
                        f"с '{separator}' (строка {line_num}). "
                        f"Сейчас обрабатывается корректно, но может "
                        f"запутать при редактировании."
                    ),
                    suggestion=(
                        f"Если :: — часть текста (не разделитель карточки), "
                        f"оберните в `inline code` для ясности."
                    ),
                ))

            # --- ::: в ответе ? / ?? блока ---
            if not is_question and _has_bidi_sep(block_text):
                report.issues.append(FormatIssue(
                    severity='info',
                    line_number=block_ln,
                    line_text=block_text,
                    message=(
                        f"Разделитель ':::' в ответе блока "
                        f"с '{separator}' (строка {line_num}). "
                        f"Сейчас обрабатывается корректно, но может "
                        f"запутать при редактировании."
                    ),
                    suggestion=(
                        f"Если ::: — часть текста, "
                        f"оберните в `inline code` для ясности."
                    ),
                ))


def _check_single_line_internal_conflicts(
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Проверяет конфликты внутри однострочных :: и ::: карточек:
    - cloze в вопросе или ответе
    """
    for line_idx, line_num, stripped in _iter_content_lines(lines):
        if not stripped or stripped.startswith('#') or stripped == '---':
            continue

        text_no_code = _strip_inline_code(stripped)

        # --- ::: с cloze ---
        if ':::' in text_no_code:
            parts = text_no_code.split(':::', 1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                for side_idx, side in enumerate(parts):
                    if re.search(r'==(?=\S).+?(?<=\S)==', side):
                        side_name = "первой" if side_idx == 0 else "второй"
                        report.issues.append(FormatIssue(
                            severity='warning',
                            line_number=line_num,
                            line_text=stripped,
                            message=(
                                f"Cloze-разметка (==...==) в {side_name} "
                                f"части Bidirectional-карточки (:::). "
                                f"Cloze-пропуски не будут обработаны "
                                f"как отдельный Cloze-тип."
                            ),
                            suggestion=(
                                "Если нужна Cloze — уберите ::: "
                                "и оставьте текст с ==...== отдельным блоком. "
                                "Если нужна Bidirectional — уберите ==."
                            ),
                        ))
                        break
                continue

        # --- :: с cloze ---
        if '::' in text_no_code and ':::' not in text_no_code:
            parts = text_no_code.split('::', 1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                q_part, a_part = parts

                if re.search(r'==(?=\S).+?(?<=\S)==', a_part):
                    report.issues.append(FormatIssue(
                        severity='warning',
                        line_number=line_num,
                        line_text=stripped,
                        message=(
                            "Cloze-разметка (==...==) в ответе "
                            "Basic-карточки (::). "
                            "Парсер может обработать как Basic, "
                            "cloze-пропуски будут потеряны."
                        ),
                        suggestion=(
                            "Если нужна Cloze — уберите :: "
                            "и оставьте текст с ==...== отдельным блоком. "
                            "Если нужна Q&A — уберите == из ответа."
                        ),
                    ))
                elif re.search(r'==(?=\S).+?(?<=\S)==', q_part):
                    report.issues.append(FormatIssue(
                        severity='warning',
                        line_number=line_num,
                        line_text=stripped,
                        message=(
                            "Cloze-разметка (==...==) в вопросе "
                            "Basic-карточки (::). "
                            "Неоднозначность формата."
                        ),
                        suggestion=(
                            "Если нужна Cloze — уберите :: "
                            "и оставьте текст с ==...== отдельным блоком. "
                            "Если нужна Q&A — уберите == из вопроса."
                        ),
                    ))


def _check_legacy_block_conflicts(
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Проверяет конфликты в legacy-блоках (### Вопрос: ... Ответ: ...):
    - :: или ::: в строке заголовка вопроса
    - cloze в теле ответа (info — парсер это поддерживает)
    - ? или ?? внутри legacy-блока
    """
    for line_idx, line_num, stripped in _iter_content_lines(lines):
        if not re.match(r'^###\s*Вопрос:', stripped):
            continue

        # Проверяем :: / ::: в самой строке ### Вопрос:
        question_text = re.sub(r'^###\s*Вопрос:\s*', '', stripped)
        question_no_code = _strip_inline_code(question_text)

        if _has_bidi_sep(question_no_code):
            report.issues.append(FormatIssue(
                severity='warning',
                line_number=line_num,
                line_text=stripped,
                message=(
                    "Bidirectional-разделитель (:::) в строке "
                    "legacy-формата (### Вопрос:). "
                    "Неоднозначность: legacy или :::?"
                ),
                suggestion=(
                    "Используйте один формат: "
                    "либо ### Вопрос:/Ответ:, либо :::. "
                    "Если ::: — часть текста, оберните в `inline code`."
                ),
            ))

        elif _has_basic_sep(question_no_code):
            report.issues.append(FormatIssue(
                severity='warning',
                line_number=line_num,
                line_text=stripped,
                message=(
                    "Basic-разделитель (::) в строке "
                    "legacy-формата (### Вопрос:). "
                    "Неоднозначность: legacy или ::?"
                ),
                suggestion=(
                    "Используйте один формат: "
                    "либо ### Вопрос:/Ответ:, либо ::. "
                    "Если :: — часть текста, оберните в `inline code`."
                ),
            ))

        # Собираем тело legacy-блока (до следующего ### Вопрос: или конца)
        body_lines = []
        check_idx = line_idx + 1

        # Пропуск через итератор не работает, идём по raw lines
        in_code = False
        while check_idx < len(lines):
            body_stripped = lines[check_idx].strip()

            if body_stripped.startswith('```'):
                in_code = not in_code
                check_idx += 1
                continue
            if in_code:
                check_idx += 1
                continue

            if re.match(r'^###\s*Вопрос:', body_stripped):
                break
            if body_stripped.startswith('# ') or body_stripped.startswith('## '):
                break

            if body_stripped:
                body_lines.append((check_idx + 1, body_stripped))
            check_idx += 1

        # Проверяем тело на конфликты
        for body_ln, body_text in body_lines:
            # ? или ?? как отдельная строка внутри legacy
            if body_text in ('?', '??'):
                report.issues.append(FormatIssue(
                    severity='warning',
                    line_number=body_ln,
                    line_text=body_text,
                    message=(
                        f"Разделитель '{body_text}' внутри "
                        f"legacy-блока (### Вопрос: на строке {line_num}). "
                        f"Конфликт форматов."
                    ),
                    suggestion=(
                        "Используйте один формат: "
                        f"либо ### Вопрос:/Ответ:, либо '{body_text}'. "
                        f"Если '{body_text}' — часть текста, "
                        f"добавьте текст на ту же строку."
                    ),
                ))

            # cloze в ответе legacy — поддерживается парсером, но информируем
            if body_text.startswith('Ответ:') or body_text.startswith('ответ:'):
                answer_text = re.sub(r'^[Оо]твет:\s*', '', body_text)
                if _has_cloze(answer_text):
                    report.issues.append(FormatIssue(
                        severity='info',
                        line_number=body_ln,
                        line_text=body_text,
                        message=(
                            "Cloze-разметка (==...==) в ответе "
                            "legacy-блока. Парсер обработает как "
                            "Cloze-карточку (вопрос будет проигнорирован)."
                        ),
                        suggestion=(
                            "Если нужна Q&A — уберите ==. "
                            "Если нужна Cloze — можно вынести текст "
                            "в отдельный блок без ### Вопрос:."
                        ),
                    ))


def _check_adjacent_separators(
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Проверяет несколько разделителей ? / ?? подряд без чёткого разграничения.

    Ситуация:
        Q1
        ?
        A1
        Q2       <-- нет пустой строки / --- между блоками
        ?
        A2
    Парсер может слить A1+Q2 в один ответ.
    """
    separator_positions = []

    for line_idx, line_num, stripped in _iter_content_lines(lines):
        if stripped in ('?', '??'):
            separator_positions.append((line_idx, line_num, stripped))

    for i in range(1, len(separator_positions)):
        prev_idx, prev_num, prev_sep = separator_positions[i - 1]
        curr_idx, curr_num, curr_sep = separator_positions[i]

        # Проверяем: есть ли пустая строка или --- между ними
        has_boundary = False
        for between_idx in range(prev_idx + 1, curr_idx):
            between_line = lines[between_idx].strip()
            if not between_line:
                has_boundary = True
                break
            if between_line == '---':
                has_boundary = True
                break
            if between_line.startswith('#'):
                has_boundary = True
                break

        if not has_boundary:
            report.issues.append(FormatIssue(
                severity='warning',
                line_number=curr_num,
                line_text=curr_sep,
                message=(
                    f"Разделитель '{curr_sep}' следует за "
                    f"'{prev_sep}' (строка {prev_num}) "
                    f"без пустой строки или --- между блоками. "
                    f"Парсер может неправильно определить границы "
                    f"вопроса и ответа."
                ),
                suggestion=(
                    "Добавьте пустую строку или --- между "
                    "блоками карточек для чёткого разграничения."
                ),
            ))

        # Смешение ? и ?? рядом
        if prev_sep != curr_sep:
            report.issues.append(FormatIssue(
                severity='info',
                line_number=curr_num,
                line_text=curr_sep,
                message=(
                    f"Смежные разделители разных типов: "
                    f"'{prev_sep}' (строка {prev_num}) и "
                    f"'{curr_sep}' (строка {curr_num}). "
                    f"Карточки попадут в разные note types при "
                    f"экспорте в Anki."
                ),
                suggestion=(
                    "Рассмотрите использование одного типа "
                    "разделителя в файле."
                ),
            ))


def _check_double_separator_on_line(
        lines: List[str],
        report: FileValidationReport,
) -> None:
    """Проверяет наличие и :: и ::: на одной строке, или другие неоднозначности."""
    for line_idx, line_num, stripped in _iter_content_lines(lines):
        if not stripped or stripped.startswith('#') or stripped == '---':
            continue

        text_no_code = _strip_inline_code(stripped)

        # Строка содержит и :: и ::: одновременно
        # (например a::b:::c или a:::b::c)
        if ':::' in text_no_code and '::' in text_no_code.replace(':::', ''):
            # Есть :: отдельно от :::
            report.issues.append(FormatIssue(
                severity='warning',
                line_number=line_num,
                line_text=stripped,
                message=(
                    "Строка содержит и '::' и ':::' одновременно. "
                    "Неоднозначный парсинг: парсер выберет только "
                    "один формат."
                ),
                suggestion=(
                    "Разделите на отдельные карточки или "
                    "оберните :: / ::: в `inline code` "
                    "если это часть текста."
                ),
            ))


def _check_unclosed_cloze(
        stripped: str,
        line_num: int,
        report: FileValidationReport,
) -> None:
    """Проверяет строку на незакрытые cloze-пропуски.

    Не срабатывает на операторы == в коде или тексте.
    """
    if '==' not in stripped:
        return

    # 1. Убираем inline-код — внутри него == это оператор
    text = re.sub(r'`[^`]*`', '', stripped)

    if '==' not in text:
        return

    # 2. Убираем тройные === (оператор строгого равенства JS/TS)
    text = text.replace('===', ' ')
    # Убираем !== и подобные
    text = re.sub(r'!==?', ' ', text)

    # 3. Убираем == окружённые пробелами (оператор сравнения)
    #    Включая начало/конец строки
    text = re.sub(r'(?:^|(?<=\s))==(?=\s|$)', ' ', text)
    text = re.sub(r'(?:^|(?<=\s))==(?=[,;)\]?!.])', ' ', text)

    if '==' not in text:
        return

    # 4. Убираем все корректные парные cloze ==текст==
    #    Ключевой паттерн: == + любые символы (не жадно) + ==
    #    Условие: первый символ после == не пробел,
    #             последний символ перед == не пробел
    #    Обрабатываем также однобуквенные ==x==
    text_cleaned = re.sub(r'==(?=\S)(.+?)(?<=\S)==', '', text)

    # 5. Если остались == — потенциальная проблема
    if '==' in text_cleaned:
        report.issues.append(FormatIssue(
            severity='warning',
            line_number=line_num,
            line_text=stripped,
            message="Возможно незакрытый cloze-пропуск",
            suggestion=(
                "Проверьте парность ==текст==. "
                "Если == — оператор сравнения, "
                "оберните выражение в `inline code`."
            ),
        ))


def _check_single_line_basic(
        stripped: str,
        line_num: int,
        report: FileValidationReport,
) -> None:
    """Проверяет корректность single-line basic карточки (::)."""

    # Убираем inline-код для корректного split
    text_no_code = re.sub(r'`[^`]*`', '___CODE___', stripped)

    if ':::' in text_no_code:
        return  # Это bidirectional, не basic

    parts = text_no_code.split('::', 1)
    if len(parts) != 2:
        return

    question_part = parts[0].strip()
    answer_part = parts[1].strip()

    if not question_part:
        report.issues.append(FormatIssue(
            severity='error',
            line_number=line_num,
            line_text=stripped,
            message="Пустой вопрос перед разделителем '::'",
            suggestion="Добавьте текст вопроса перед '::'",
        ))

    if not answer_part:
        report.issues.append(FormatIssue(
            severity='warning',
            line_number=line_num,
            line_text=stripped,
            message="Пустой ответ после разделителя '::'",
            suggestion="Добавьте текст ответа после '::'",
        ))

    # Множественные :: в одной строке (возможно нужно разбить)
    double_colon_count = len(re.findall(r'(?<!:)::(?!:)', text_no_code))
    if double_colon_count > 1:
        report.issues.append(FormatIssue(
            severity='warning',
            line_number=line_num,
            line_text=stripped,
            message=(
                f"Множественные '::' в одной строке "
                f"({double_colon_count} шт.)"
            ),
            suggestion="Каждая карточка должна быть на отдельной строке",
        ))


def _check_single_line_bidirectional(
        stripped: str,
        line_num: int,
        report: FileValidationReport,
) -> None:
    """Проверяет корректность single-line bidirectional карточки (:::)."""

    text_no_code = re.sub(r'`[^`]*`', '___CODE___', stripped)
    parts = text_no_code.split(':::', 1)

    if len(parts) != 2:
        return

    if not parts[0].strip():
        report.issues.append(FormatIssue(
            severity='error',
            line_number=line_num,
            line_text=stripped,
            message="Пустая сторона перед разделителем ':::'",
            suggestion="Добавьте текст перед ':::'",
        ))

    if not parts[1].strip():
        report.issues.append(FormatIssue(
            severity='error',
            line_number=line_num,
            line_text=stripped,
            message="Пустая сторона после разделителя ':::'",
            suggestion="Добавьте текст после ':::'",
        ))


def _check_multiline_separator(
        stripped: str,
        line_num: int,
        lines: List[str],
        line_idx: int,
        report: FileValidationReport,
) -> None:
    """Проверяет корректность разделителя ? или ??."""

    # Проверяем ответ после разделителя
    next_idx = line_idx + 1
    has_answer = False
    while next_idx < len(lines):
        next_line = lines[next_idx].strip()
        if next_line:
            has_answer = True
            break
        next_idx += 1

    if not has_answer:
        report.issues.append(FormatIssue(
            severity='warning',
            line_number=line_num,
            line_text=stripped,
            message=f"Разделитель '{stripped}' без ответа после него",
            suggestion="Добавьте ответ после разделителя",
        ))

    # Проверяем вопрос перед разделителем
    prev_idx = line_idx - 1
    has_question = False
    while prev_idx >= 0:
        prev_line = lines[prev_idx].strip()
        if not prev_line:
            prev_idx -= 1
            continue
        # Если предыдущая непустая строка — заголовок или ---,
        # то вопроса нет
        if prev_line.startswith('#') or prev_line == '---':
            break
        has_question = True
        break

    if not has_question:
        report.issues.append(FormatIssue(
            severity='warning',
            line_number=line_num,
            line_text=stripped,
            message=f"Разделитель '{stripped}' без вопроса перед ним",
            suggestion="Добавьте текст вопроса перед разделителем",
        ))


def _check_empty_content(
        content: str,
        report: FileValidationReport,
) -> None:
    """Проверка на пустое содержимое."""
    content_without_fm = strip_frontmatter(content).strip()
    if not content_without_fm:
        report.issues.append(FormatIssue(
            severity='error',
            line_number=1,
            line_text='',
            message="Файл не содержит контента (только frontmatter)",
            suggestion="Добавьте карточки после frontmatter",
        ))

    if not validate_markdown_structure(content):
        report.issues.append(FormatIssue(
            severity='warning',
            line_number=1,
            line_text='',
            message="Не найдено распознаваемых форматов карточек",
            suggestion=(
                "Используйте :: для Basic, ::: для Bidirectional, "
                "? / ?? для многострочных, == для Cloze"
            ),
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
                message=(
                    f"Очень длинная строка ({len(line)} символов)"
                ),
                suggestion=(
                    "Рассмотрите разбивку "
                    "на многострочный формат (? или ??)"
                ),
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

    # key -> (card_id, question_text, card_type, answer_preview)
    seen: Dict[str, Tuple[int, str, str, str]] = {}

    for card in cards:
        if card.is_reverse:
            continue

        key = normalize_text_key(card.question)
        if not key or len(key) < 5:
            continue

        if key in seen:
            orig_id, orig_q, orig_type, orig_a = seen[key]
            q_preview = card.question[:60]
            ellipsis = "…" if len(card.question) > 60 else ""

            report.issues.append(FormatIssue(
                severity='warning',
                line_number=0,
                line_text=card.question[:100],
                message=(
                    f"Дубликат вопроса: "
                    f"'{q_preview}{ellipsis}' "
                    f"(совпадает с карточкой #{orig_id}, "
                    f"тип: {orig_type})"
                ),
                suggestion=(
                    f"Оригинал: '{orig_q[:60]}…'\n"
                    f"Ответ оригинала: '{orig_a[:50]}…'\n"
                    f"Удалите одну из дублирующихся карточек "
                    f"из исходного файла."
                ),
            ))
        else:
            seen[key] = (
                card.id,
                card.question,
                card.card_type.value,
                card.answer,
            )


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


def generate_validation_report_text(
        reports: List[FileValidationReport],
) -> str:
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
    # Удаляем только SR-комментарии в конце строки, чтобы не задеть
    # случайный <!--...--> в середине текста.
    # Используем PATTERNS['scheduling_comment'] (без дублирования regex)
    return re.sub(
        PATTERNS['scheduling_comment'] + r'\s*$',
        '', text, flags=re.MULTILINE
    ).strip()


def build_cloze_question(text: str) -> str:
    """Строит вопрос из cloze-текста, маскируя пропуски.
    Игнорирует == внутри inline code.
    """
    if not text:
        return "Cloze"

    # Защищаем inline code
    code_blocks: List[str] = []

    def _save_code(m):
        idx = len(code_blocks)
        code_blocks.append(m.group(0))
        return f"\x02CODE{idx}\x02"

    masked = re.sub(r'`[^`]*`', _save_code, text)

    # Маскируем cloze-пропуски
    masked = re.sub(
        r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]',
        lambda m: f"[{m.group(2).strip() or '...'}]",
        masked,
    )
    masked = re.sub(
        r'==(.+?)==\^\[([^\]]*)\](?!\[\^)',
        lambda m: f"[{m.group(2).strip() or '...'}]",
        masked,
    )
    masked = re.sub(r'==(.+?)==\[\^([ahs]+)\]', '[...]', masked)
    masked = re.sub(r'==(.+?)==', '[...]', masked)

    # Восстанавливаем inline code
    for idx, code in enumerate(code_blocks):
        masked = masked.replace(f"\x02CODE{idx}\x02", code)

    masked = re.sub(r'[ \t]+', ' ', masked)
    masked = re.sub(r'\n{3,}', '\n\n', masked)
    masked = masked.strip()

    return masked or "Cloze"


def reveal_cloze_text(text: str) -> str:
    """Раскрывает cloze, оборачивая в bold.
    Игнорирует == внутри inline code.
    """
    if not text:
        return ""

    code_blocks: List[str] = []

    def _save_code(m):
        idx = len(code_blocks)
        code_blocks.append(m.group(0))
        return f"\x02CODE{idx}\x02"

    revealed = re.sub(r'`[^`]*`', _save_code, text)

    revealed = re.sub(r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==\^\[([^\]]*)\](?!\[\^)', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==\[\^([ahs]+)\]', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==', r'**\1**', revealed)

    for idx, code in enumerate(code_blocks):
        revealed = revealed.replace(f"\x02CODE{idx}\x02", code)

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

    # Standalone separator или блок, начинающийся с разделителя
    if stripped in ('?', '??'):
        return True
    first_line = get_first_nonempty_line(stripped)
    if first_line in ('?', '??'):
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
                # Все директории (кроме имени файла) формируют категорию
                # java/core/file.md → category = 'java/core'
                category = '/'.join(rel_path.parts[:-1])
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

    # Убираем содержимое inline code и bold —
    # внутри них :: и ::: не являются разделителями карточек
    text_clean = _strip_formatting_for_separator_check(line)

    if re.match(PATTERNS['single_line_bidirectional'], text_clean):
        return CardType.SINGLE_LINE_BIDIRECTIONAL

    if ':::' not in text_clean and re.match(
            PATTERNS['single_line_basic'], text_clean
    ):
        return CardType.SINGLE_LINE_BASIC

    return None


def parse_cloze_deletions(text: str) -> List[ClozeDeletion]:
    """Парсит cloze-пропуски, игнорируя == внутри inline code."""
    deletions: List[ClozeDeletion] = []

    # Собираем защищённые диапазоны (inline code)
    protected_ranges: List[Tuple[int, int]] = []
    for m in re.finditer(r'`[^`]*`', text):
        protected_ranges.append((m.start(), m.end()))

    def _is_protected(pos: int) -> bool:
        return any(s <= pos < e for s, e in protected_ranges)

    # 1. ==text==[^actions]
    for match in re.finditer(r'==(.+?)==\[\^([ahs]+)\]', text):
        if _is_protected(match.start()):
            continue
        deletions.append(ClozeDeletion(
            text=match.group(1),
            position=match.start(),
            actions=match.group(2),
        ))

    # 2. ==text==^[hint][^seq]
    for match in re.finditer(
            r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]', text
    ):
        if _is_protected(match.start()):
            continue
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
                hint=match.group(2) if match.group(2) else None,
                sequence=int(match.group(3)),
            ))

    # 3. ==text==^[hint]
    for match in re.finditer(
            r'==(.+?)==\^\[([^\]]*)\](?!\[\^)', text
    ):
        if _is_protected(match.start()):
            continue
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
                hint=match.group(2) if match.group(2) else None,
            ))

    # 4. ==text== (простой)
    for match in re.finditer(r'==(.+?)==', text):
        if _is_protected(match.start()):
            continue
        end_pos = match.end()
        if end_pos < len(text) and text[end_pos:end_pos + 2] in [
            '^[', '[^'
        ]:
            continue
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
            ))

    deletions.sort(key=lambda d: d.position)
    return deletions


def has_cloze_deletions(text: str) -> bool:
    """Проверяет наличие cloze-разметки ==...== вне inline code.
    Не использует re.DOTALL, чтобы == из операторов сравнения
    (например, x == true) не детектились как cloze на разных строках."""
    cleaned = _strip_all_formatting(text)
    return bool(re.search(r'==.+?==', cleaned))


def _is_hard_boundary(block_text: str) -> bool:
    """Проверяет, является ли блок жёсткой границей карточки.

    Используется при препроцессинге standalone-разделителей ?/??
    для определения, где остановить сбор вопроса/ответа.
    """
    stripped = block_text.strip()
    if not stripped:
        return True
    if stripped == '---':
        return True
    if stripped.startswith('#') and not stripped.startswith('```'):
        return True
    if stripped in ('?', '??'):
        return True
    first_line = get_first_nonempty_line(stripped)
    if first_line in ('?', '??'):
        return True
    if split_multiline_block(stripped) is not None:
        return True
    if contains_single_line_card_syntax(stripped):
        return True
    if is_legacy_question_block(stripped):
        return True
    if is_standalone_cloze_block(stripped):
        return True
    return False


def _preprocess_standalone_separators(
        blocks: List[str],
) -> List[str]:
    """Мержит standalone/leading ? и ?? с окружающими блоками.

    Обрабатывает случаи, когда ? или ?? находится в отдельном блоке
    (отделён от вопроса и ответа пустыми строками), или когда
    блок начинается с ? с ответом на следующих строках.

    Эвристика для вопроса (назад):
      Берём code-блоки + первый text-блок перед ними.
      Останавливаемся на втором text-блоке или жёсткой границе.

    Эвристика для ответа (вперёд, только standalone):
      Берём code-блоки + первый text-блок после них.
      Останавливаемся на втором text-блоке, следующем
      разделителе или жёсткой границе.
    """
    # ── Находим позиции разделителей ──
    sep_positions: Dict[int, str] = {}
    for idx, block in enumerate(blocks):
        stripped = block.strip()
        if stripped in ('?', '??'):
            sep_positions[idx] = stripped
        else:
            first_line = get_first_nonempty_line(stripped)
            if first_line in ('?', '??'):
                sep_positions[idx] = first_line
            else:
                _lines = stripped.splitlines()
                _in_code = False
                for _li, _line in enumerate(_lines):
                    if _line.strip().startswith('```'):
                        _in_code = not _in_code
                        continue
                    if _in_code:
                        continue
                    if _line.strip() in ('?', '??') and _li > 0:
                        _before = '\n'.join(
                            _lines[:_li]
                        ).strip()
                        _after = '\n'.join(
                            _lines[_li + 1:]
                        ).strip()
                        if _before and _after:
                            sep_positions[idx] = _line.strip()
                        break

    if not sep_positions:
        return blocks

    result: List[str] = []
    i = 0

    while i < len(blocks):
        if i not in sep_positions:
            result.append(blocks[i])
            i += 1
            continue

        separator = sep_positions[i]
        block_text = blocks[i].strip()
        is_standalone = (block_text == separator)

        # Detect embedded separator (on a non-first line within the block)
        embedded_sep_idx = -1
        if not is_standalone:
            _bl = block_text.splitlines()
            _ic = False
            for _li, _ln in enumerate(_bl):
                if _ln.strip().startswith('```'):
                    _ic = not _ic
                    continue
                if _ic:
                    continue
                if _ln.strip() == separator:
                    embedded_sep_idx = _li
                    break

        # Встроенный ответ (блок вида "?\nответ...")
        if is_standalone:
            embedded_answer = ''
            embedded_question = ''
        elif embedded_sep_idx > 0:
            _bl = block_text.splitlines()
            embedded_question = '\n'.join(
                _bl[:embedded_sep_idx]
            ).strip()
            embedded_answer = '\n'.join(
                _bl[embedded_sep_idx + 1:]
            ).strip()
        else:
            block_lines = block_text.splitlines()
            embedded_answer = '\n'.join(block_lines[1:]).strip()
            embedded_question = ''

        # ── Назад: собираем вопрос ──
        question_parts: List[str] = []
        found_text = False

        if embedded_question:
            # Embedded separator: question is already extracted,
            # no backward scan needed
            question_parts = [embedded_question]
        else:
            while result:
                candidate = result[-1].strip()
                if _is_hard_boundary(candidate):
                    break

                is_code = candidate.startswith('```')

                if is_code:
                    if found_text:
                        question_parts.insert(0, result.pop())
                        found_text = False
                    else:
                        question_parts.insert(0, result.pop())
                elif not found_text:

                    question_parts.insert(0, result.pop())
                    found_text = True
                else:

                    break

        # ── Вперёд: собираем ответ (только standalone) ──
        answer_parts: List[str] = []
        j = i + 1

        if is_standalone or embedded_sep_idx > 0:
            fwd_found_text = False
            while j < len(blocks):
                if j in sep_positions:
                    break
                candidate = blocks[j].strip()
                if _is_hard_boundary(candidate):
                    break

                is_code = candidate.startswith('```')

                if is_code:
                    answer_parts.append(blocks[j])
                    fwd_found_text = False
                    j += 1
                elif not fwd_found_text:
                    answer_parts.append(blocks[j])
                    fwd_found_text = True
                    j += 1
                else:
                    # Второй text-блок — может быть
                    # началом следующего вопроса
                    break

        # ── Собираем итоговый блок ──
        if question_parts:
            question_text = '\n\n'.join(
                p.strip() for p in question_parts if p.strip()
            )

            all_answer: List[str] = []
            if embedded_answer:
                all_answer.append(embedded_answer)
            for ap in answer_parts:
                s = ap.strip()
                if s:
                    all_answer.append(s)
            answer_text = '\n\n'.join(all_answer)

            if answer_text:
                merged = f"{question_text}\n{separator}\n{answer_text}"
            else:
                merged = f"{question_text}\n{separator}"

            result.append(merged.strip())
        else:
            # Нет вопроса — оставляем как есть
            result.append(blocks[i])
            j = i + 1

        i = j

    return result


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
    blocks = _preprocess_standalone_separators(blocks)

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
                    sep_pos = _find_separator_position(line, '::')
                    if sep_pos < 0:
                        continue
                    question = line[:sep_pos].strip()
                    answer = remove_scheduling_comment(
                        line[sep_pos + 2:].strip()
                    )
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
                    sep_pos = _find_separator_position(line, ':::')
                    if sep_pos < 0:
                        continue
                    info1 = remove_scheduling_comment(
                        line[:sep_pos].strip()
                    )
                    info2 = remove_scheduling_comment(
                        line[sep_pos + 3:].strip()
                    )
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
        lexer = TexLexer(stripall=True)

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
        f'<div style="text-align: left;margin:8px 0;">'
        f'<div style="background:{accent};color:#fff;'
        f'padding:2px 8px;border-radius:4px 4px 0 0;'
        f'font-size:0.75em;display:inline-block;">'
        f'{lang_label}</div>'
        f'<pre style="text-align: left;background:#272822;color:#f8f8f2;'
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


def _convert_markdown_table_to_html(table_text: str) -> str:
    """Convert a markdown table to a styled HTML table for Anki."""
    lines = table_text.strip().split('\n')
    if len(lines) < 2:
        return table_text

    rows = []
    alignments = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue

        raw = stripped
        if raw.startswith('|'):
            raw = raw[1:]
        if raw.endswith('|'):
            raw = raw[:-1]

        cells = [cell.strip() for cell in raw.split('|')]

        is_separator = all(
            re.match(r'^:?-+:?$', cell.strip()) for cell in cells
            if cell.strip()
        )

        if is_separator and i >= 1:
            for cell in cells:
                cell_stripped = cell.strip()
                if not cell_stripped:
                    continue
                if (cell_stripped.startswith(':')
                        and cell_stripped.endswith(':')
                        and len(cell_stripped) >= 3):
                    alignments.append('center')
                elif (cell_stripped.endswith(':')
                      and len(cell_stripped) >= 3):
                    alignments.append('right')
                elif (cell_stripped.startswith(':')
                      and len(cell_stripped) >= 3):
                    alignments.append('left')
                else:
                    alignments.append('left')
            continue

        rows.append(cells)

    if not rows:
        return table_text

    max_cols = max(len(r) for r in rows)
    while len(alignments) < max_cols:
        alignments.append('left')

    parts = []
    parts.append(
        '<table style="border-collapse:collapse;width:100%;'
        'margin:8px 0;font-size:0.9em;'
        'font-family:&#39;Segoe UI&#39;,Arial,sans-serif;">'
    )

    if rows:
        parts.append(
            '<thead><tr style="background:#282828;'
            'border-bottom:2px solid #ccc;">'
        )
        for j, cell in enumerate(rows[0]):
            align = alignments[j] if j < len(alignments) else 'left'
            parts.append(
                f'<th style="padding:6px 10px;text-align:{align};'
                f'border:1px solid #ddd;font-weight:bold;'
                f'white-space:nowrap;">{cell}</th>'
            )
        parts.append('</tr></thead>')

    parts.append('<tbody>')
    for ri, row in enumerate(rows[1:]):
        bg = '#3b4247' if ri % 2 == 0 else '#43453b'
        parts.append(f'<tr style="background:{bg};">')
        for j, cell in enumerate(row):
            align = alignments[j] if j < len(alignments) else 'left'
            parts.append(
                f'<td style="padding:6px 10px;text-align:{align};'
                f'border:1px solid #ddd;">{cell}</td>'
            )
        parts.append('</tr>')
    parts.append('</tbody></table>')

    return ''.join(parts)


def _extract_markdown_tables(text: str) -> Tuple[str, List[str]]:
    """Extract markdown tables from text, replace with placeholders."""
    table_blocks: List[str] = []
    lines = text.split('\n')
    result_lines: List[str] = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith('|') and i + 1 < len(lines):
            next_stripped = lines[i + 1].strip()
            if (next_stripped.startswith('|')
                    and re.match(r'^\|[\s:|\-]+\|$', next_stripped)):
                table_lines = [lines[i]]
                j = i + 1
                table_lines.append(lines[j])
                j += 1
                while j < len(lines):
                    dl = lines[j].strip()
                    if dl.startswith('|') and dl.endswith('|'):
                        table_lines.append(lines[j])
                        j += 1
                    elif dl.startswith('|'):
                        table_lines.append(lines[j])
                        j += 1
                    else:
                        break
                table_text = '\n'.join(table_lines)
                idx = len(table_blocks)
                table_blocks.append(table_text)
                result_lines.append(f"\x00TB{idx}\x00")
                i = j
                continue
        result_lines.append(lines[i])
        i += 1

    return '\n'.join(result_lines), table_blocks


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
    text, _html_table_blocks = _extract_markdown_tables(text)
    text = re.sub(
        r'^\s*[-*]\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE
    )
    text = re.sub(r'(<li>.+</li>\n?)+', r'<ul>\g<0></ul>', text)
    text = re.sub(
        r'^\s*\d+\.\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE
    )
    text = text.replace('\n\n', '<br><br>')
    text = text.replace('\n', '<br>')

    for idx, tbl in enumerate(_html_table_blocks):
        html_table = _convert_markdown_table_to_html(tbl)
        text = text.replace(f"\x00TB{idx}\x00", html_table)

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

    # --- Extract markdown tables before newline conversion ---
    result, _anki_table_blocks = _extract_markdown_tables(result)
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

    # --- Restore markdown tables as styled HTML ---
    for idx, table_text in enumerate(_anki_table_blocks):
        html_table = _convert_markdown_table_to_html(table_text)
        # Also restore inline code placeholders inside table cells
        for ic_idx, code_text in enumerate(inline_codes):
            escaped = escape_html(code_text)
            styled = (
                f'<code style="background:#272822;padding:2px 6px;'
                f'border-radius:4px;'
                f'font-family:Consolas,monospace;'
                f'font-size:0.9em;">{escaped}</code>'
            )
            html_table = html_table.replace(
                f"\x00IC{ic_idx}\x00", styled
            )
        result = result.replace(f"\x00TB{idx}\x00", html_table)

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
        deck_prefix: str = "Interview Cards",
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

    category = cards[0].category or 'general'
    # category/subcategory → Deck Prefix::Category::Subcategory
    category_parts = [
        p for p in category.split('/') if p
    ]
    deck_name = f"{deck_prefix}::{'::'.join(category_parts)}"

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
        # Без reverse: оба направления bidirectional карточек
        # идут в Basic (по отдельным записям)
        extra_basic = [
            c for c in cards
            if c.card_type in BIDIRECTIONAL_CARD_TYPES
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


def find_all_duplicates(input_dir: str) -> List[DuplicateInfo]:
    """Находит дубликаты карточек между всеми файлами в директории.
    Возвращает список DuplicateInfo для каждого дубликата.
    Первое вхождение считается оригиналом, остальные — дубликатами.
    Reverse-карточки (is_reverse=True) пропускаются.
    """
    topics = load_markdown_topics(input_dir)

    # normalized_question -> list of (card, topic_name, file_path)
    question_map: Dict[str, List[Tuple]] = {}

    for topic_name, topic_data in sorted(topics.items()):
        cards = parse_cards_from_markdown(topic_data['path'])
        for card in cards:
            if card.is_reverse:
                continue
            key = normalize_text_key(card.question)
            if not key or len(key) < 5:
                continue
            if key not in question_map:
                question_map[key] = []
            question_map[key].append(
                (card, topic_name, topic_data['path'])
            )

    duplicates: List[DuplicateInfo] = []

    for key, occurrences in question_map.items():
        if len(occurrences) <= 1:
            continue

        orig_card, orig_topic, orig_path = occurrences[0]

        for card, topic_name, file_path in occurrences[1:]:
            is_cross = (orig_path != file_path)
            duplicates.append(DuplicateInfo(
                question_preview=card.question[:120],
                answer_preview=card.answer[:80],
                original_source=orig_path,
                original_topic=orig_topic,
                duplicate_source=file_path,
                duplicate_topic=topic_name,
                card_type=card.card_type.value,
                is_cross_file=is_cross,
            ))

    # Сортировка: сначала между файлами, потом внутри файлов
    duplicates.sort(key=lambda d: (not d.is_cross_file, d.original_topic))
    return duplicates


def generate_duplicate_report_text(
        duplicates: List[DuplicateInfo],
) -> str:
    """Генерирует текстовый отчёт о найденных дубликатах."""
    if not duplicates:
        return "✅ Дубликатов не найдено."

    cross_file = [d for d in duplicates if d.is_cross_file]
    same_file = [d for d in duplicates if not d.is_cross_file]

    lines = [
        "=" * 60,
        "ОТЧЁТ О ДУБЛИКАТАХ КАРТОЧЕК",
        "=" * 60,
        "",
        f"Всего дубликатов: {len(duplicates)}",
        f"  Между файлами: {len(cross_file)}",
        f"  Внутри файлов: {len(same_file)}",
        "",
    ]

    if cross_file:
        lines.append("-" * 40)
        lines.append("ДУБЛИКАТЫ МЕЖДУ ФАЙЛАМИ")
        lines.append("-" * 40)
        for i, dup in enumerate(cross_file, 1):
            lines.append(f"\n  #{i}")
            lines.append(f"  Вопрос: {dup.question_preview}")
            if dup.answer_preview:
                lines.append(f"  Ответ:  {dup.answer_preview}…")
            lines.append(
                f"  Оригинал:  {dup.original_topic} "
                f"({Path(dup.original_source).name})"
            )
            lines.append(
                f"  Дубликат:  {dup.duplicate_topic} "
                f"({Path(dup.duplicate_source).name})"
            )
            if dup.card_type:
                lines.append(f"  Тип: {dup.card_type}")

    if same_file:
        lines.append("")
        lines.append("-" * 40)
        lines.append("ДУБЛИКАТЫ ВНУТРИ ФАЙЛОВ")
        lines.append("-" * 40)
        for i, dup in enumerate(same_file, 1):
            lines.append(f"\n  #{i}")
            lines.append(f"  Вопрос: {dup.question_preview}")
            lines.append(f"  Файл:   {dup.duplicate_topic}")
            if dup.card_type:
                lines.append(f"  Тип: {dup.card_type}")

    return "\n".join(lines)


def clean_up_duplicates(
        file_paths: List[str],
) -> Tuple[int, List[DuplicateInfo]]:
    """Удаляет дубликаты из сгенерированных файлов.

    Returns:
        (total_removed, removed_details) — количество удалённых
        и список DuplicateInfo для каждого удалённого дубликата.
    """
    if not file_paths:
        return 0, []

    seen_questions: Set[str] = set()
    total_removed = 0
    removed_details: List[DuplicateInfo] = []

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
                question_key = (
                    normalize_text_key(parts[0]) if parts else ""
                )

                if question_key and question_key not in seen_questions:
                    seen_questions.add(question_key)
                    unique_data.append(line)
                elif question_key and question_key in seen_questions:
                    # ── Дубликат — записываем детали ──
                    q_raw = parts[0] if parts else ""
                    a_raw = parts[1] if len(parts) > 1 else ""
                    # Убираем HTML для читаемости
                    q_clean = re.sub(
                        r'<[^>]+>', '', q_raw
                    ).strip()[:120]
                    a_clean = re.sub(
                        r'<[^>]+>', '', a_raw
                    ).strip()[:80]

                    removed_details.append(DuplicateInfo(
                        question_preview=q_clean,
                        answer_preview=a_clean,
                        duplicate_source=os.path.basename(file_path),
                        duplicate_topic=os.path.basename(file_path),
                        is_cross_file=True,
                    ))
                elif not question_key:
                    unique_data.append(line)

            removed = len(data_lines) - len(unique_data)
            total_removed += removed

            if removed > 0:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(header_lines)
                    f.writelines(unique_data)
                logger.info(
                    f"Удалено {removed} дубл. "
                    f"из {os.path.basename(file_path)}"
                )

        except Exception as e:
            logger.warning(f"Ошибка обработки {file_path}: {e}")
            continue

    logger.info(f"Всего удалено дубликатов: {total_removed}")
    return total_removed, removed_details


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
