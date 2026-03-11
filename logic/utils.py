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
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
import yaml

from models.InterviewCard import (
    InterviewCard, CardType, ClozeDeletion, SchedulingData
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# Константы и паттерны для Spaced Repetition
# =============================================================================

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

SUPPORTED_LANGUAGES = ['python', 'javascript', 'java', 'sql', 'json', 'bash', 'typescript', 'go', 'rust']


# =============================================================================
# Вспомогательные функции
# =============================================================================

def normalize_tags(tags: Any) -> List[str]:
    """
    Нормализует теги к списку строк.
    """
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
    """
    Удаляет YAML frontmatter из контента.
    """
    return re.sub(
        PATTERNS['frontmatter'],
        '',
        content,
        count=1,
        flags=re.DOTALL | re.MULTILINE
    )


def normalize_text_key(text: str) -> str:
    """
    Нормализует текст для поиска дублей.
    """
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def remove_scheduling_comment(text: str) -> str:
    """
    Удаляет комментарий планирования из текста карточки.
    """
    if not text:
        return ""
    return re.sub(PATTERNS['scheduling_comment'], '', text).strip()


def build_cloze_question(text: str) -> str:
    """
    Строит маскированную версию cloze-текста.
    Используется как question для валидации и Anki-экспорта.
    """
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
    """
    Превращает Cloze-разметку в обычный текст с выделением скрытых частей.
    Полезно для Anki-экспорта.
    """
    if not text:
        return ""

    revealed = text
    revealed = re.sub(r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==\^\[([^\]]*)\](?!\[\^)', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==\[\^([ahs]+)\]', r'**\1**', revealed)
    revealed = re.sub(r'==(.+?)==', r'**\1**', revealed)

    return revealed


def should_skip_line_for_single_pass(line: str) -> bool:
    """
    Проверяет, надо ли пропустить строку в однострочном парсинге.
    """
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
    """
    Проверяет, содержит ли секция однострочную карточку.
    Игнорирует кодовые блоки.
    """
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
    """
    Делит Markdown на верхнеуровневые блоки, сохраняя code fences целиком.
    """
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
    """
    Возвращает первую непустую строку блока.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def is_legacy_question_block(block: str) -> bool:
    """
    Проверяет, начинается ли блок с legacy-вопроса.
    """
    first_line = get_first_nonempty_line(block)
    return bool(re.match(r'^###\s*Вопрос:', first_line))


def is_heading_block(block: str) -> bool:
    """
    Проверяет, является ли блок обычным заголовком.
    """
    first_line = get_first_nonempty_line(block)
    return first_line.startswith('#') and not is_legacy_question_block(block)


def is_horizontal_rule_block(block: str) -> bool:
    """
    Проверяет, является ли блок разделителем ---.
    """
    return block.strip() == '---'


def split_multiline_block(block: str) -> Optional[Tuple[str, str, str]]:
    """
    Ищет внутри блока multi-line карточку:
    question \\n ? \\n answer
    или
    info1 \\n ?? \\n info2
    """
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
    """
    Проверяет, является ли блок самостоятельной cloze-карточкой.
    """
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
    """
    Проверяет, начинается ли с этого блока новая карточка
    или структурная граница документа.
    """
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
    """
    Извлекает кодовые блоки из текста ответа.
    """
    return re.findall(PATTERNS['code_block'], text, re.DOTALL)


def merge_multiline_sections(content: str) -> List[str]:
    """
    Объединяет секции, которые относятся к одной multi-line карточке.
    """
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
    """
    Создаёт Cloze-карточку из текста.
    """
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


# =============================================================================
# Загрузка и парсинг Markdown файлов
# =============================================================================

def load_markdown_topics(input_dir: str) -> Dict[str, Dict]:
    """
    Рекурсивно загружает материалы из Markdown файлов,
    определяя категорию по имени папки.

    Args:
        input_dir: Путь к папке с Markdown файлами

    Returns:
        Dict[str, Dict]: Словарь тем с метаданными
    """
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
    """
    Определяет имя колоды из frontmatter.

    Приоритет:
    1. Поле deck
    2. Первый тег flashcards/...
    3. flashcards/<category>
    """
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
    """
    Извлекает YAML frontmatter из Markdown контента.
    """
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
    """
    Извлекает данные планирования из HTML комментария.
    """
    match = re.search(PATTERNS['scheduling_comment'], content)
    if match:
        return SchedulingData(
            next_review=match.group(1),
            interval=int(match.group(2)),
            ease=int(match.group(3))
        )
    return None


def extract_card_metadata(content: str) -> Tuple[str, Dict]:
    """
    Извлекает метаданные карточки из контента.
    """
    frontmatter = extract_frontmatter(content)

    category = frontmatter.get('category', 'general')
    if category == 'general':
        match = re.search(PATTERNS['header'], content, re.MULTILINE)
        if match:
            category = match.group(1).lower().replace(' ', '_')

    return category, frontmatter


# =============================================================================
# Парсинг карточек разных форматов
# =============================================================================

def detect_card_format(line: str) -> Optional[CardType]:
    """
    Определяет тип карточки по формату строки.
    """
    line = line.strip()

    if re.match(PATTERNS['single_line_bidirectional'], line):
        return CardType.SINGLE_LINE_BIDIRECTIONAL

    if ':::' not in line and re.match(PATTERNS['single_line_basic'], line):
        return CardType.SINGLE_LINE_BASIC

    return None


def parse_cloze_deletions(text: str) -> List[ClozeDeletion]:
    """
    Парсит cloze deletions из текста.
    """
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
    """
    Проверяет наличие cloze deletions в тексте.
    """
    return bool(re.search(r'==.+?==', text, re.DOTALL))


def parse_cards_from_markdown(file_path: str, start_id: int = 1) -> List[InterviewCard]:
    """
    Парсит карточки из Markdown в исходном порядке документа.
    """
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

        # 1. Legacy
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

        # 2. Single-line карточки
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

        # 3. Multi-line карточки
        multiline_parts = split_multiline_block(block)
        if multiline_parts:
            separator, part1, part2 = multiline_parts
            full_block_parts = [block]
            merged_answer = part2
            j = i + 1

            while j < len(blocks) and not is_card_boundary_block(blocks[j]):
                continuation = blocks[j].strip()
                full_block_parts.append(continuation)
                merged_answer = f"{merged_answer}\n\n{continuation}".strip() if merged_answer else continuation
                j += 1

            full_block = '\n\n'.join(part for part in full_block_parts if part).strip()
            scheduling = extract_scheduling_data(full_block)

            if separator == SEPARATORS['multi_line_bidirectional']:
                info1 = remove_scheduling_comment(part1.strip())
                info2 = remove_scheduling_comment(merged_answer.strip())
                code_snippets = extract_code_snippets_from_text(f"{info1}\n\n{info2}")

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

        # 4. Standalone Cloze
        if is_standalone_cloze_block(block):
            add_cloze_card(block, extract_scheduling_data(block))
            i += 1
            continue

        i += 1

    # 5. Финальная валидация
    for card in all_cards:
        try:
            if card.validate():
                cards.append(card)
        except Exception as e:
            logger.warning(f"Карточка {card.id} из {topic_name} не прошла валидацию: {e}")

    # 6. Fallback
    if not cards:
        logger.info(f"Структурированные вопросы не найдены в {topic_name}, создаём карточку из контента")

        fallback_answer = content_without_fm.strip()
        fallback_card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=f"Расскажите о: {topic_name}",
            answer=fallback_answer,
            card_type=CardType.MULTI_LINE_BASIC,
            code_snippets=re.findall(PATTERNS['code_block'], content_without_fm, re.DOTALL),
            tags=tags or [category],
            source_note=topic_name,
            deck_name=deck_name,
            frontmatter=frontmatter,
        )
        try:
            if fallback_card.validate():
                cards.append(fallback_card)
        except Exception as e:
            logger.warning(f"Fallback-карточка для {topic_name} не прошла валидацию: {e}")

    logger.info(f"Парсинг {topic_name}: найдено {len(cards)} карточек")
    return cards


def parse_all_markdown_files(input_dir: str) -> List[InterviewCard]:
    """
    Парсит все Markdown файлы в директории.
    """
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


# =============================================================================
# Очистка и форматирование текста
# =============================================================================

def clean_text(text: str) -> str:
    """
    Очищает текст от лишних пробелов и переносов строк.
    """
    if not text:
        return ""

    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def escape_html(text: str) -> str:
    """
    Экранирует HTML-символы для Anki.
    """
    if not text:
        return ""

    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    return text


def format_code_for_anki(code: str, language: str = 'python') -> str:
    """
    Форматирует код для отображения в Anki.
    """
    if not code:
        return ""

    code = escape_html(code)
    code = code.replace('\n', '<br>')
    code = code.replace('  ', '&nbsp;&nbsp;')

    return (
        f'<pre style="background:#f4f4f4;padding:10px;'
        f'border-radius:5px;overflow-x:auto;">'
        f'<code class="language-{language}">{code}</code></pre>'
    )


def format_markdown_to_html(text: str) -> str:
    """
    Конвертирует Markdown в HTML для Anki.
    """
    if not text:
        return ""

    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(
        r'`([^`]+)`',
        r'<code style="background:#f0f0f0;padding:2px 5px;border-radius:3px;">\1</code>',
        text
    )
    text = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*]\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    text = re.sub(r'(<li>.+</li>\n?)+', r'<ul>\g<0></ul>', text)
    text = re.sub(r'^\s*\d+\.\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    text = text.replace('\n\n', '<br><br>')
    text = text.replace('\n', '<br>')

    return text


def remove_obsidian_links(text: str) -> str:
    """
    Удаляет ссылки Obsidian для чистого Anki экспорта.
    """
    if not text:
        return ""

    text = re.sub(r'\[\[.*?\|(.+?)\]\]', r'\1', text)
    text = re.sub(r'\[\[(.+?)\]\]', r'\1', text)

    return text


def remove_spaced_repetition_tags(text: str) -> str:
    """
    Удаляет теги Spaced Repetition для Anki.
    """
    if not text:
        return ""

    text = re.sub(r'#interview\s*', '', text)
    text = re.sub(r'#flashcards(/[a-zA-Z0-9_/-]+)?\s*', '', text)
    text = re.sub(r'#difficulty/\w+\s*', '', text)

    return text.strip()


# =============================================================================
# Генерация карточек для Obsidian Spaced Repetition
# =============================================================================

def generate_obsidian_topic_file(
        cards: List[InterviewCard],
        topic_name: str,
        output_dir: str,
        deck_name: str = None
) -> str:
    """
    Генерирует объединённый файл темы для Obsidian Spaced Repetition.
    """
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


# =============================================================================
# Генерация карточек для Anki
# =============================================================================

def generate_anki_import_file(
        cards: List[InterviewCard],
        topic_name: str,
        output_path: str,
        deck_prefix: str = "Interview"
) -> str:
    """
    Генерирует файл для импорта в Anki.
    """
    output_dir = output_path if os.path.isdir(output_path) else os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    file_path = os.path.join(output_dir, f"{topic_name}_anki_cards.txt")

    if cards and cards[0].deck_name:
        deck_name = cards[0].deck_name.replace('/', '::')
    else:
        category = cards[0].category if cards else "general"
        deck_name = f"{deck_prefix}::{category.replace('_', ' ').title()}"

    header = "#separator:tab\n#html:true\n#deck column:2\n#tags column:4\n"
    cards_lines: List[str] = []

    for card in cards:
        front_raw = card.question or ""
        back_raw = card.answer or ""

        if card.card_type == CardType.CLOZE:
            front_raw = front_raw or build_cloze_question(back_raw)
            back_raw = reveal_cloze_text(back_raw)

        front_raw = remove_scheduling_comment(front_raw)
        back_raw = remove_scheduling_comment(back_raw)

        front_raw = remove_spaced_repetition_tags(remove_obsidian_links(front_raw))
        back_raw = remove_spaced_repetition_tags(remove_obsidian_links(back_raw))

        front = format_markdown_to_html(front_raw)
        back = format_markdown_to_html(back_raw)

        if card.code_snippets:
            for snippet in card.code_snippets:
                if snippet.strip() and snippet.strip() not in card.answer:
                    back += "<br>" + format_code_for_anki(snippet)

        front = front.replace('\t', '    ')
        back = back.replace('\t', '    ')

        tag_values = normalize_tags(card.tags)
        tags = ' '.join([f"interview/{tag.lstrip('#')}" for tag in tag_values if tag])

        cards_lines.append(f"{front}\t{back}\t{deck_name}\t\t{tags}")

    content = header + '\n'.join(cards_lines)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    logger.info(f"Создан файл Anki: {file_path}")
    return file_path


# =============================================================================
# Утилиты
# =============================================================================

def clean_up_duplicates(file_paths: List[str]) -> int:
    """
    Удаляет дубликаты вопросов из файлов Anki.
    """
    dfs: List[pd.DataFrame] = []

    for file_path in file_paths:
        if os.path.exists(file_path):
            try:
                df = pd.read_csv(
                    file_path,
                    header=None,
                    names=['question', 'answer', 'deck', 'empty', 'tags'],
                    sep='\t',
                    on_bad_lines='skip',
                    comment='#'
                )
                dfs.append(df)
            except Exception as e:
                logger.warning(f"Ошибка чтения {file_path}: {e}")
                continue

    if not dfs:
        return 0

    combined_df = pd.concat(dfs, ignore_index=True)
    initial_count = len(combined_df)
    combined_df.drop_duplicates(subset=['question'], inplace=True)
    final_count = len(combined_df)
    removed_count = initial_count - final_count

    split_dfs = []
    start_idx = 0
    for df in dfs:
        end_idx = start_idx + len(df)
        split_df = combined_df[(combined_df.index >= start_idx) & (combined_df.index < end_idx)]
        split_dfs.append(split_df)
        start_idx = end_idx

    for i, df in enumerate(split_dfs):
        header = "#separator:tab\n#html:true\n#deck column:2\n#tags column:4\n"
        body = df.to_csv(index=False, header=False, sep='\t').strip()
        with open(file_paths[i], 'w', encoding='utf-8') as f:
            f.write(header)
            if body:
                f.write(body + '\n')

    logger.info(f"Удалено дубликатов: {removed_count}")
    return removed_count


def natural_sort(file_paths: List[str]) -> List[str]:
    """
    Естественная сортировка файлов.
    """

    def natural_key(text: str) -> List:
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]

    return sorted(file_paths, key=natural_key)


def get_markdown_files(folder_paths: List[str]) -> List[str]:
    """
    Получает список Markdown файлов из папок.
    """
    file_paths: List[str] = []

    for folder_path in folder_paths:
        if os.path.exists(folder_path):
            files = glob.glob(os.path.join(folder_path, '**/*.md'), recursive=True)
            file_paths.extend(files)

    return natural_sort(file_paths)


def validate_markdown_structure(content: str) -> bool:
    """
    Проверяет структуру Markdown на наличие карточек.
    """
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
        batch_size: int = 500
) -> List[str]:
    """
    Обрабатывает карточки батчами для больших наборов данных.
    """
    output_files: List[str] = []

    for i in range(0, len(cards), batch_size):
        batch = cards[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(cards) + batch_size - 1) // batch_size

        category_name = f"{category}_part{batch_num}" if total_batches > 1 else category
        output_path = os.path.join(output_dir, f"{category_name}.txt")

        generate_anki_import_file(batch, category_name, output_path)
        output_files.append(output_path)

    return output_files


def generate_all_formats(
        cards: List[InterviewCard],
        category: str,
        cards_output: str,
        anki_output: str,
        use_reverse_cards: bool = True
) -> List[str]:
    """
    Генерирует все форматы вывода карточек.
    """
    output_files: List[str] = []

    if not cards:
        return output_files

    topic_name = cards[0].topic
    deck_name = cards[0].deck_name

    logger.info(f"Генерация для темы: {topic_name} (Категория: {category}, Deck: {deck_name})")

    os.makedirs(cards_output, exist_ok=True)
    os.makedirs(anki_output, exist_ok=True)

    # Для Obsidian reverse не нужны: ::: и ?? сами создают двусторонность.
    obsidian_cards = [c for c in cards if not c.is_reverse]

    # Для Anki reverse можно оставить или убрать по флагу.
    anki_cards = cards if use_reverse_cards else [c for c in cards if not c.is_reverse]

    if obsidian_cards:
        merged_file = generate_obsidian_topic_file(
            obsidian_cards,
            topic_name,
            cards_output,
            deck_name
        )
        output_files.append(merged_file)

    if anki_cards:
        anki_file = generate_anki_import_file(
            anki_cards,
            topic_name,
            anki_output
        )
        output_files.append(anki_file)

    logger.info(f"Сгенерировано файлов: {len(output_files)}")
    return output_files
