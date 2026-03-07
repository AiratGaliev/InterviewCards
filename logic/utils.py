"""
Основной модуль логики приложения InterviewCards.
Содержит функции для парсинга Markdown и генерации карточек.
Полностью совместим с Obsidian Spaced Repetition.

Поддерживаемые форматы:
- Single-line Basic: question::answer
- Single-line Bidirectional: info1:::info2 (создает 2 карточки)
- Multi-line Basic: question\n?\nanswer
- Multi-line Bidirectional: info1\n??\ninfo2 (создает 2 карточки)
- Cloze: text with ==hidden parts==
"""

import glob
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml

from models.InterviewCard import (
    InterviewCard, CardType, ClozeDeletion, SchedulingData
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# Константы и паттерны для Spaced Repetition
# =============================================================================

# Разделители карточек (по умолчанию в SR)
SEPARATORS = {
    'single_line_basic': '::',  # question::answer
    'single_line_bidirectional': ':::',  # info1:::info2 (создает 2 карточки)
    'multi_line_basic': '?',  # question\n?\nanswer
    'multi_line_bidirectional': '??',  # info1\n??\ninfo2 (создает 2 карточки)
}

# Паттерны для парсинга Markdown
PATTERNS = {
    # Форматы вопросов-ответов
    'question_answer_v1': r'### Вопрос:\s*(.+?)\nОтвет:\s*(.+?)(?=### Вопрос:|$)',
    'question_answer_v2': r'### Вопрос:\s*(.+?)\n\nОтвет:\s*(.+?)(?=### Вопрос:|$)',

    # SR форматы
    'single_line_basic': r'^(.+?)::(.+?)$',
    'single_line_bidirectional': r'^(.+?):::(.+?)$',

    # HTML комментарий с данными планирования
    'scheduling_comment': r'<!--SR:(\d{4}-\d{2}-\d{2}),(\d+),(\d+)-->',

    # Frontmatter
    'frontmatter': r'^---\n(.+?)\n---\n',

    # Заголовок
    'header': r'^#\s+(.+?)$',

    # Блок кода
    'code_block': r'```(?:python|javascript|java|sql|json|bash|typescript|go|rust)?\n(.+?)\n```',

    # Cloze deletions
    'cloze_simple': r'==(.+?)==',
    'cloze_with_hint': r'==(.+?)==\^\[([^\]]*)\]',
    'cloze_with_sequence': r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]',
    'cloze_generalized': r'==(.+?)==\[\^([ahs]+)\]',
}

# Языки программирования для подсветки кода
SUPPORTED_LANGUAGES = ['python', 'javascript', 'java', 'sql', 'json', 'bash', 'typescript', 'go', 'rust']


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

            # Определяем категорию как имя первой подпапки
            rel_path = file_path.relative_to(input_path)
            if len(rel_path.parts) > 1:
                category = rel_path.parts[0]
            else:
                category = 'general'

            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Парсим frontmatter
            frontmatter = extract_frontmatter(content)

            # Определяем deck из тегов или структуры
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

    Использует только свойства из frontmatter:
    - deck: явное указание колоды
    - tags: теги материала (берём первый тег если это flashcards/...)
    - category: категория материала

    Приоритет:
    1. Поле deck в frontmatter
    2. Первый тег из tags если он начинается с flashcards/
    3. Категория -> flashcards/category

    Returns:
        str: Имя колоды (без #, формат: flashcards/...)
    """
    # Проверяем явное поле deck в frontmatter
    if 'deck' in frontmatter:
        deck = frontmatter['deck']
        if isinstance(deck, str):
            if not deck.startswith('flashcards'):
                deck = f"flashcards/{deck}"
            return deck

    # Проверяем tags - ищем тег начинающийся с flashcards/
    tags = frontmatter.get('tags', [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.startswith('flashcards/'):
                return tag
    elif isinstance(tags, str) and tags.startswith('flashcards/'):
        return tags

    # По умолчанию - категория
    return f"flashcards/{category}"


def extract_frontmatter(content: str) -> Dict:
    """
    Извлекает YAML frontmatter из Markdown контента.

    Args:
        content: Содержимое Markdown файла

    Returns:
        Dict: Распарсенный frontmatter или пустой словарь
    """
    match = re.match(PATTERNS['frontmatter'], content, re.DOTALL)
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

    Args:
        content: Текст карточки

    Returns:
        SchedulingData или None
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
    Из материалов используются только tags и category.

    Args:
        content: Содержимое Markdown файла

    Returns:
        Tuple[str, Dict]: (категория, frontmatter)
    """
    frontmatter = extract_frontmatter(content)

    # Категория из frontmatter или заголовка
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

    Args:
        line: Строка для анализа

    Returns:
        CardType или None
    """
    line = line.strip()

    # Single-line Bidirectional (3 двоеточия)
    if ':::' in line and line.count(':::') == 1:
        return CardType.SINGLE_LINE_BIDIRECTIONAL

    # Single-line Basic (2 двоеточия)
    if '::' in line and ':::' not in line:
        # Проверяем, что это не часть кода
        if not line.startswith('```') and not line.startswith('    '):
            return CardType.SINGLE_LINE_BASIC

    return None


def parse_cloze_deletions(text: str) -> List[ClozeDeletion]:
    """
    Парсит cloze deletions из текста.

    Поддерживаемые форматы:
    - Simplified: ==text==
    - With hint: ==text==^[hint]
    - Classic: ==text==^[hint][^1]
    - Generalized: ==text==[^ahhs]

    Args:
        text: Текст с cloze deletions

    Returns:
        List[ClozeDeletion]: Список найденных deletions
    """
    deletions = []

    # Generalized cloze: ==text==[^ahhs]
    for match in re.finditer(r'==(.+?)==\[\^([ahs]+)\]', text):
        deletions.append(ClozeDeletion(
            text=match.group(1),
            position=match.start(),
            actions=match.group(2),
        ))

    # Classic/Simplified with hint and sequence: ==text==^[hint][^1]
    for match in re.finditer(r'==(.+?)==\^\[([^\]]*)\]\[\^(\d+)\]', text):
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
                hint=match.group(2) if match.group(2) else None,
                sequence=int(match.group(3)),
            ))

    # With hint only: ==text==^[hint]
    for match in re.finditer(r'==(.+?)==\^\[([^\]]+)\](?!\[\^)', text):
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
                hint=match.group(2),
            ))

    # Simple cloze: ==text==
    for match in re.finditer(r'==(.+?)==', text):
        # Проверяем, что это не уже обработанный формат
        end_pos = match.end()
        if end_pos < len(text) and text[end_pos:end_pos + 2] in ['^[', '[^']:
            continue
        if not any(d.position == match.start() for d in deletions):
            deletions.append(ClozeDeletion(
                text=match.group(1),
                position=match.start(),
            ))

    # Сортируем по позиции
    deletions.sort(key=lambda d: d.position)

    return deletions


def has_cloze_deletions(text: str) -> bool:
    """Проверяет наличие cloze deletions в тексте"""
    return bool(re.search(r'==.+?==', text))


def parse_cards_from_markdown(file_path: str, start_id: int = 1) -> List[InterviewCard]:
    """
    Парсит карточки Spaced Repetition из Markdown файла.
    Поддерживает все форматы SR.

    Args:
        file_path: Путь к Markdown файлу
        start_id: Начальный ID для карточек

    Returns:
        List[InterviewCard]: Список карточек
    """
    cards = []
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

    # Удаляем frontmatter из контента для парсинга
    content_without_fm = re.sub(PATTERNS['frontmatter'], '', content, count=1, flags=re.DOTALL)

    all_cards = []

    # 1. Парсинг Single-line карточек (:: и :::)
    for line in content_without_fm.split('\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('```'):
            continue

        card_type = detect_card_format(line)
        if card_type:
            if card_type == CardType.SINGLE_LINE_BASIC:
                parts = line.split('::', 1)
                if len(parts) == 2:
                    question = parts[0].strip()
                    answer = parts[1].strip()

                    # Проверяем на cloze в ответе
                    if has_cloze_deletions(answer):
                        cloze_deletions = parse_cloze_deletions(answer)
                        card = InterviewCard(
                            id=card_id,
                            topic=topic_name,
                            category=category,
                            question=question,
                            answer=answer,
                            card_type=CardType.CLOZE,
                            cloze_deletions=cloze_deletions,
                            tags=frontmatter.get('tags', []),
                            source_note=topic_name,
                            deck_name=deck_name,
                            frontmatter=frontmatter,
                        )
                        all_cards.append(card)
                        card_id += 1
                    else:
                        card = InterviewCard(
                            id=card_id,
                            topic=topic_name,
                            category=category,
                            question=question,
                            answer=answer,
                            card_type=CardType.SINGLE_LINE_BASIC,
                            tags=frontmatter.get('tags', []),
                            source_note=topic_name,
                            deck_name=deck_name,
                            frontmatter=frontmatter,
                        )
                        all_cards.append(card)
                        card_id += 1

            elif card_type == CardType.SINGLE_LINE_BIDIRECTIONAL:
                parts = line.split(':::', 1)
                if len(parts) == 2:
                    info1 = parts[0].strip()
                    info2 = parts[1].strip()

                    # Создаем первую карточку
                    card1 = InterviewCard(
                        id=card_id,
                        topic=topic_name,
                        category=category,
                        question=info1,
                        answer=info2,
                        card_type=CardType.SINGLE_LINE_BIDIRECTIONAL,
                        tags=frontmatter.get('tags', []),
                        source_note=topic_name,
                        deck_name=deck_name,
                        frontmatter=frontmatter,
                        is_reverse=False,
                    )
                    all_cards.append(card1)
                    card_id += 1

                    # Создаем карточку-близнеца (reverse)
                    card2 = InterviewCard(
                        id=card_id,
                        topic=topic_name,
                        category=category,
                        question=info2,
                        answer=info1,
                        card_type=CardType.SINGLE_LINE_BIDIRECTIONAL,
                        tags=frontmatter.get('tags', []),
                        source_note=topic_name,
                        deck_name=deck_name,
                        frontmatter=frontmatter,
                        is_reverse=True,
                        sibling_id=card1.id,
                    )
                    all_cards.append(card2)
                    card_id += 1

    # 2. Парсинг Multi-line карточек (? и ??)
    # Разделяем контент на секции по пустым строкам
    sections = re.split(r'\n\s*\n', content_without_fm)

    for section in sections:
        # Multi-line Basic (?)
        if '\n?\n' in section:
            parts = section.split('\n?\n', 1)
            if len(parts) == 2:
                question = parts[0].strip()
                answer = parts[1].strip()

                # Извлекаем данные планирования
                scheduling = extract_scheduling_data(section)

                card = InterviewCard(
                    id=card_id,
                    topic=topic_name,
                    category=category,
                    question=question,
                    answer=answer,
                    card_type=CardType.MULTI_LINE_BASIC,
                    tags=frontmatter.get('tags', []),
                    source_note=topic_name,
                    deck_name=deck_name,
                    frontmatter=frontmatter,
                    scheduling=scheduling,
                )
                all_cards.append(card)
                card_id += 1

        # Multi-line Bidirectional (??)
        elif '\n??\n' in section:
            parts = section.split('\n??\n', 1)
            if len(parts) == 2:
                info1 = parts[0].strip()
                info2 = parts[1].strip()

                scheduling = extract_scheduling_data(section)

                # Первая карточка
                card1 = InterviewCard(
                    id=card_id,
                    topic=topic_name,
                    category=category,
                    question=info1,
                    answer=info2,
                    card_type=CardType.MULTI_LINE_BIDIRECTIONAL,
                    tags=frontmatter.get('tags', []),
                    source_note=topic_name,
                    deck_name=deck_name,
                    frontmatter=frontmatter,
                    scheduling=scheduling,
                    is_reverse=False,
                )
                all_cards.append(card1)
                card_id += 1

                # Карточка-близнец
                card2 = InterviewCard(
                    id=card_id,
                    topic=topic_name,
                    category=category,
                    question=info2,
                    answer=info1,
                    card_type=CardType.MULTI_LINE_BIDIRECTIONAL,
                    tags=frontmatter.get('tags', []),
                    source_note=topic_name,
                    deck_name=deck_name,
                    frontmatter=frontmatter,
                    scheduling=scheduling,
                    is_reverse=True,
                    sibling_id=card1.id,
                )
                all_cards.append(card2)
                card_id += 1

    # 3. Парсинг старых форматов для обратной совместимости
    # Формат: ### Вопрос: ... Ответ: ...
    legacy_questions = re.findall(PATTERNS['question_answer_v1'], content_without_fm, re.DOTALL)
    for q_text, a_text in legacy_questions:
        code_snippets = re.findall(PATTERNS['code_block'], a_text, re.DOTALL)

        # Очистка ответа от лишних пробелов
        a_text_clean = a_text.strip()

        tags = frontmatter.get('tags', [])
        if isinstance(tags, str):
            tags = [tags]
        tags = [tag for tag in tags if tag]

        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=q_text.strip(),
            answer=a_text_clean,
            card_type=CardType.MULTI_LINE_BASIC,
            code_snippets=code_snippets,
            tags=tags,
            source_note=topic_name,
            deck_name=deck_name,
            frontmatter=frontmatter,
        )
        all_cards.append(card)
        card_id += 1

    # 4. Парсинг Cloze карточек (отдельные строки с ==text==)
    for line in content_without_fm.split('\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('```'):
            continue

        # Пропускаем уже обработанные форматы
        if '::' in line or ':::' in line:
            continue

        if has_cloze_deletions(line):
            deletions = parse_cloze_deletions(line)
            if deletions:
                scheduling = extract_scheduling_data(line)

                card = InterviewCard(
                    id=card_id,
                    topic=topic_name,
                    category=category,
                    question="",  # Для cloze вопрос - пустой
                    answer=line,
                    card_type=CardType.CLOZE,
                    cloze_deletions=deletions,
                    tags=frontmatter.get('tags', []),
                    source_note=topic_name,
                    deck_name=deck_name,
                    frontmatter=frontmatter,
                    scheduling=scheduling,
                )
                all_cards.append(card)
                card_id += 1

    # Валидация карточек
    for card in all_cards:
        if card.validate():
            cards.append(card)

    # Если карточек не найдено, создаём карточку из всего контента
    if not cards:
        logger.info(f"Структурированные вопросы не найдены в {topic_name}, создаём карточку из контента")

        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=f"Расскажите о: {topic_name}",
            answer=content_without_fm.strip(),
            card_type=CardType.MULTI_LINE_BASIC,
            code_snippets=re.findall(PATTERNS['code_block'], content_without_fm, re.DOTALL),
            tags=[category],
            source_note=topic_name,
            deck_name=deck_name,
            frontmatter=frontmatter,
        )
        if card.validate():
            cards.append(card)

    logger.info(f"Парсинг {topic_name}: найдено {len(cards)} карточек")
    return cards


def parse_all_markdown_files(input_dir: str) -> List[InterviewCard]:
    """
    Парсит все Markdown файлы в директории.

    Args:
        input_dir: Путь к директории с файлами

    Returns:
        List[InterviewCard]: Список всех карточек
    """
    all_cards = []
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

    Args:
        text: Исходный текст

    Returns:
        str: Очищенный текст
    """
    if not text:
        return ""

    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def escape_html(text: str) -> str:
    """
    Экранирует HTML-символы для Anki.

    Args:
        text: Исходный текст

    Returns:
        str: Экранированный текст
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

    Args:
        code: Исходный код
        language: Язык программирования

    Returns:
        str: HTML-форматированный код
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

    Args:
        text: Текст в Markdown формате

    Returns:
        str: HTML-форматированный текст
    """
    if not text:
        return ""

    # Жирный текст
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    # Курсив
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    # Инлайн код
    text = re.sub(
        r'`([^`]+)`',
        r'<code style="background:#f0f0f0;padding:2px 5px;border-radius:3px;">\1</code>',
        text
    )
    # Заголовки
    text = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    # Маркированные списки
    text = re.sub(r'^\s*[-*]\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    text = re.sub(r'(<li>.+</li>\n?)+', r'<ul>\g<0></ul>', text)
    # Нумерованные списки
    text = re.sub(r'^\s*\d+\.\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    # Переносы строк
    text = text.replace('\n\n', '<br><br>')
    text = text.replace('\n', '<br>')

    return text


def remove_obsidian_links(text: str) -> str:
    """
    Удаляет ссылки Obsidian для чистого Anki экспорта.

    Args:
        text: Текст с ссылками Obsidian

    Returns:
        str: Текст без ссылок
    """
    if not text:
        return ""

    # [[Note#Section|Text]] -> Text
    text = re.sub(r'\[\[.*?\|(.+?)\]\]', r'\1', text)
    # [[Note]] -> Note
    text = re.sub(r'\[\[(.+?)\]\]', r'\1', text)

    return text


def remove_spaced_repetition_tags(text: str) -> str:
    """
    Удаляет теги Spaced Repetition для Anki.
    Удаляет только теги формата #flashcards/...

    Args:
        text: Текст с тегами

    Returns:
        str: Очищенный текст
    """
    if not text:
        return ""

    # Удаляем теги Spaced Repetition (Obsidian Spaced Repetition plugin)
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
    Все карточки в одном файле с правильными SR форматами.

    Args:
        cards: Список карточек
        topic_name: Имя темы
        output_dir: Папка вывода
        deck_name: Имя колоды

    Returns:
        str: Путь к созданному файлу
    """
    os.makedirs(output_dir, exist_ok=True)

    if not deck_name and cards:
        deck_name = cards[0].deck_name
    elif not deck_name:
        deck_name = "flashcards"

    # Frontmatter
    category = cards[0].category if cards else 'general'
    tags = cards[0].tags if cards else []
    tags_str = ', '.join(tags) if tags else 'flashcards'

    frontmatter_lines = [
        "---",
        f"tags: [{tags_str}]",
        f"category: {category}",
        "---",
        "",
        f"# {topic_name}",
        "",
    ]

    content_parts = ['\n'.join(frontmatter_lines)]

    for card in cards:
        # Разделитель между карточками
        content_parts.append("---")
        content_parts.append("")

        if card.card_type == CardType.CLOZE:
            # Cloze карточка
            content_parts.append(card.answer)

        elif card.card_type == CardType.SINGLE_LINE_BASIC:
            # Single-line Basic
            content_parts.append(f"{card.question}::{card.answer}")

        elif card.card_type == CardType.SINGLE_LINE_BIDIRECTIONAL:
            # Single-line Bidirectional (только одна карточка, sibling создаётся отдельно)
            if not card.is_reverse:
                content_parts.append(f"{card.question}:::{card.answer}")

        elif card.card_type == CardType.MULTI_LINE_BASIC:
            # Multi-line Basic
            content_parts.append(card.question)
            content_parts.append("?")
            content_parts.append(card.answer)

        elif card.card_type == CardType.MULTI_LINE_BIDIRECTIONAL:
            # Multi-line Bidirectional (только одна карточка)
            if not card.is_reverse:
                content_parts.append(card.question)
                content_parts.append("??")
                content_parts.append(card.answer)

        # Добавляем данные планирования если есть
        if card.scheduling:
            scheduling_comment = card.scheduling.to_html_comment()
            if scheduling_comment:
                content_parts.append("")
                content_parts.append(scheduling_comment)

        content_parts.append("")

    # Тег колоды в конце файла
    content_parts.append(f"#{deck_name}")

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
    Имя файла: {topic}.txt

    Args:
        cards: Список карточек
        topic_name: Имя темы
        output_path: Папка вывода
        deck_prefix: Префикс колоды

    Returns:
        str: Путь к созданному файлу
    """
    output_dir = output_path if os.path.isdir(output_path) else os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    file_path = os.path.join(output_dir, f"{topic_name}_anki_cards.txt")

    category = cards[0].category if cards else "general"
    deck_name = f"{deck_prefix}::{category.replace('_', ' ').title()}"

    # Заголовок Anki
    header = "#separator:tab\n#html:true\n#deck column:2\n#tags column:4\n"

    cards_lines = []

    for card in cards:
        front = card.question.replace('\n', '<br>')
        back = card.answer.replace('\n', '<br>')

        # Форматируем markdown в HTML
        front = format_markdown_to_html(front)
        back = format_markdown_to_html(back)

        # Удаляем Obsidian ссылки
        front = remove_obsidian_links(front)
        back = remove_obsidian_links(back)

        # Добавляем уникальные сниппеты кода
        if card.code_snippets:
            for snippet in card.code_snippets:
                if snippet.strip() not in card.answer:
                    back += "<br>" + format_code_for_anki(snippet)

        tags = ' '.join([f"interview/{tag}" for tag in card.tags if tag])

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

    Args:
        file_paths: Список путей к файлам

    Returns:
        int: Количество удалённых дубликатов
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
                    on_bad_lines='skip'
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

    # Разбиваем обратно по файлам
    split_dfs = []
    start_idx = 0
    for df in dfs:
        end_idx = start_idx + len(df)
        split_df = combined_df[(combined_df.index >= start_idx) & (combined_df.index < end_idx)]
        split_dfs.append(split_df)
        start_idx = end_idx

    for i, df in enumerate(split_dfs):
        df.to_csv(file_paths[i], index=False, header=False, sep='\t')

    logger.info(f"Удалено дубликатов: {removed_count}")
    return removed_count


def natural_sort(file_paths: List[str]) -> List[str]:
    """
    Естественная сортировка файлов (1, 2, 10 вместо 1, 10, 2).

    Args:
        file_paths: Список путей к файлам

    Returns:
        List[str]: Отсортированный список
    """

    def natural_key(text: str) -> List:
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]

    return sorted(file_paths, key=natural_key)


def get_markdown_files(folder_paths: List[str]) -> List[str]:
    """
    Получает список Markdown файлов из папок.

    Args:
        folder_paths: Список путей к папкам

    Returns:
        List[str]: Список путей к файлам
    """
    file_paths = []

    for folder_path in folder_paths:
        if os.path.exists(folder_path):
            files = glob.glob(os.path.join(folder_path, '**/*.md'), recursive=True)
            file_paths.extend(files)

    return natural_sort(file_paths)


def validate_markdown_structure(content: str) -> bool:
    """
    Проверяет структуру Markdown на наличие карточек.

    Args:
        content: Содержимое Markdown файла

    Returns:
        bool: True если структура валидна
    """
    # SR форматы
    has_single_basic = bool(re.search(r'[^:]::[^:]', content))
    has_single_bidirectional = bool(re.search(r':::', content))
    has_multi_basic = bool(re.search(r'\n\?\n', content))
    has_multi_bidirectional = bool(re.search(r'\n\?\?\n', content))
    has_cloze = bool(re.search(r'==.+?==', content))

    # Legacy формат
    has_question = bool(re.search(r'### Вопрос:', content))

    return (has_single_basic or has_single_bidirectional or
            has_multi_basic or has_multi_bidirectional or
            has_cloze or has_question)


def process_cards_batch(
        cards: List[InterviewCard],
        category: str,
        output_dir: str,
        batch_size: int = 500
) -> List[str]:
    """
    Обрабатывает карточки батчами для больших наборов данных.

    Args:
        cards: Список карточек
        category: Имя категории
        output_dir: Путь к папке вывода
        batch_size: Размер батча

    Returns:
        List[str]: Список путей к созданным файлам
    """
    output_files = []

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

    Генерирует:
    1. Obsidian Spaced Repetition - один объединённый файл темы (.md)
    2. Anki Import (.txt файл)

    Args:
        cards: Список карточек
        category: Категория
        cards_output: Папка для Obsidian карточек
        anki_output: Папка для Anki файлов
        use_reverse_cards: Использовать обратные карточки

    Returns:
        List[str]: Список путей к созданным файлам
    """
    output_files: List[str] = []

    if not cards:
        return output_files

    topic_name = cards[0].topic
    deck_name = cards[0].deck_name

    logger.info(f"Генерация для темы: {topic_name} (Категория: {category}, Deck: {deck_name})")

    # Создаём директории
    os.makedirs(cards_output, exist_ok=True)
    os.makedirs(anki_output, exist_ok=True)

    # 1. Объединённый файл темы для Obsidian Spaced Repetition
    # Фильтруем reverse карточки для объединённого файла (они создаются автоматически из ::: и ??)
    main_cards = [c for c in cards if not c.is_reverse] if use_reverse_cards else cards
    if main_cards:
        merged_file = generate_obsidian_topic_file(
            main_cards,
            topic_name,
            cards_output,
            deck_name
        )
        output_files.append(merged_file)

    # 2. Anki Import файл
    anki_file = generate_anki_import_file(
        cards,
        topic_name,
        anki_output
    )
    output_files.append(anki_file)

    logger.info(f"Сгенерировано файлов: {len(output_files)}")
    return output_files
