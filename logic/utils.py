"""
Основной модуль логики приложения InterviewCards.
Содержит функции для парсинга Markdown и генерации карточек.
"""

import glob
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import yaml

from models.InterviewCard import InterviewCard

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# Константы
# =============================================================================

# Паттерны для парсинга Markdown
PATTERNS = {
    'question_answer': r'### Вопрос:\s*(.+?)\nОтвет:\s*(.+?)(?=### Вопрос:|$)',
    'card_format': r'#card\n\n(.+?)\n:::\n(.+?)(?=#card|$)',
    'card_reverse_format': r'#card-reverse\n\n(.+?)\n:::\n(.+?)(?=#card-reverse|$)',
    'header_content': r'^#\s*(.+?)\n\n---\n\n(.+?)(?=^#\s|#card|$)',
    'code_block': r'```(?:python|javascript|java|sql|json|bash|typescript|go|rust)?\n(.+?)\n```',
    'frontmatter': r'^---\n(.+?)\n---\n',
    'header': r'^#\s*(.+?)$',
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

            # Парсим frontmatter один раз
            frontmatter = extract_frontmatter(content)

            topics[topic_name] = {
                'content': content,
                'path': str(file_path),
                'category': frontmatter.get('category', category),
                'frontmatter': frontmatter,
                'difficulty': frontmatter.get('difficulty', 'medium')
            }

        except Exception as e:
            logger.error(f"Ошибка при чтении {file_path}: {e}")
            continue

    logger.info(f"Загружено тем: {len(topics)}")
    return topics


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


def extract_card_metadata(content: str) -> Tuple[str, str, Dict]:
    """
    Извлекает метаданные карточки из контента.

    Args:
        content: Содержимое Markdown файла

    Returns:
        Tuple[str, str, Dict]: (категория, сложность, frontmatter)
    """
    frontmatter = extract_frontmatter(content)

    # Категория из frontmatter или заголовка
    category = frontmatter.get('category', 'general')
    if category == 'general':
        match = re.search(PATTERNS['header'], content, re.MULTILINE)
        if match:
            category = match.group(1).lower().replace(' ', '_')

    # Сложность
    difficulty = frontmatter.get('difficulty', 'medium')
    if difficulty not in ['easy', 'medium', 'hard']:
        difficulty = 'medium'

    return category, difficulty, frontmatter


def parse_cards_from_markdown(file_path: str, start_id: int = 1) -> List[InterviewCard]:
    """
    Парсит карточки Spaced Repetition из Markdown файла.

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
    category, difficulty, frontmatter = extract_card_metadata(content)

    # Поиск вопросов по различным паттернам
    all_questions: List[Tuple[str, str]] = []

    # Паттерн 1: ### Вопрос: ... Ответ: ...
    questions_v1 = re.findall(PATTERNS['question_answer'], content, re.DOTALL)
    all_questions.extend(questions_v1)

    # Паттерн 2: #card формат (Obsidian_to_Anki)
    questions_v2 = re.findall(PATTERNS['card_format'], content, re.DOTALL)
    all_questions.extend(questions_v2)

    # Паттерн 2b: #card-reverse формат (двухсторонние карточки)
    questions_v2b = re.findall(PATTERNS['card_reverse_format'], content, re.DOTALL)
    all_questions.extend(questions_v2b)

    # Паттерн 3: Заголовок как вопрос, контент после --- как ответ
    questions_v3 = re.findall(PATTERNS['header_content'], content, re.MULTILINE | re.DOTALL)
    all_questions.extend(questions_v3)

    # Обработка найденных вопросов
    for q_text, a_text in all_questions:
        # Извлечение фрагментов кода из ответа
        code_snippets = re.findall(PATTERNS['code_block'], a_text, re.DOTALL)

        # Обработка тегов
        tags = frontmatter.get('tags', [])
        if isinstance(tags, str):
            tags = [tags]
        tags = [tag for tag in tags if tag]

        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=q_text.strip(),
            answer=a_text.strip(),
            code_snippets=code_snippets,
            difficulty=difficulty,
            tags=tags,
            source_note=topic_name,
            frontmatter=frontmatter
        )

        if card.validate():
            cards.append(card)
            card_id += 1

    # Если вопросов не найдено, создаём карточку из всего контента
    if not cards:
        logger.info(f"Структурированные вопросы не найдены в {topic_name}, создаём карточку из контента")
        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=f"Расскажите о: {topic_name}",
            answer=content,
            code_snippets=re.findall(PATTERNS['code_block'], content, re.DOTALL),
            difficulty=difficulty,
            tags=[category],
            source_note=topic_name,
            frontmatter=frontmatter
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

    Args:
        text: Текст с тегами

    Returns:
        str: Очищенный текст
    """
    if not text:
        return ""

    text = re.sub(r'#card-reverse\s*', '', text)
    text = re.sub(r'#card\s*', '', text)
    text = re.sub(r'#interview\s*', '', text)
    text = re.sub(r'#difficulty/\w+\s*', '', text)

    return text.strip()


# =============================================================================
# Генерация карточек для Obsidian
# =============================================================================

def generate_obsidian_card(card: InterviewCard, output_dir: str) -> str:
    """
    Генерирует карточку для Obsidian Spaced Repetition.
    Имя файла: {topic}_{id}.md
    """
    os.makedirs(output_dir, exist_ok=True)

    # Добавляем ID к имени файла, чтобы избежать перезаписи при наличии нескольких карточек в одной теме
    file_name = f"{card.topic}_{card.id}.md"
    file_path = os.path.join(output_dir, file_name)

    markdown_content = card.to_markdown()

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

    logger.debug(f"Создан файл: {file_path}")
    return file_path


def generate_obsidian_merged_file(
        cards: List[InterviewCard],
        topic_name: str,
        output_dir: str,
        use_reverse_cards: bool = True
) -> str:
    """
    Генерирует объединённый файл темы для Obsidian_to_Anki.
    Имя файла: {topic}_to_anki.md
    """
    os.makedirs(output_dir, exist_ok=True)

    content_parts = []

    for card in cards:
        answer = card.answer
        answer = remove_obsidian_links(answer)
        answer = remove_spaced_repetition_tags(answer)

        # Используем #card-reverse для двухсторонних карточек
        card_tag = "#card-reverse" if use_reverse_cards else "#card"
        content_parts.append(f"{card_tag}\n\n{card.question}\n:::\n{answer}\n")

        # Добавляем только уникальные сниппеты кода
        if card.code_snippets:
            for snippet in card.code_snippets:
                # Проверяем, есть ли этот код уже в ответе
                if snippet.strip() not in answer:
                    content_parts.append(f"```python\n{snippet}\n```\n")

        content_parts.append("---\n")

    content = '\n'.join(content_parts)

    # Используем имя темы и суффикс _to_anki
    file_path = os.path.join(output_dir, f"{topic_name}_to_anki.md")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    logger.info(f"Создан объединённый файл: {file_path}")
    return file_path


# =============================================================================
# Генерация карточек для Anki
# =============================================================================

def generate_anki_import_file(
        cards: List[InterviewCard],
        topic_name: str,  # Изменено: принимаем имя темы
        output_path: str,  # Это полный путь к файлу или папке? В оригинале было messy. Уточним.
        deck_prefix: str = "Interview"
) -> str:
    """
    Генерирует файл для импорта в Anki.
    Имя файла: {topic}.txt
    """
    # Определяем директорию вывода
    # Если output_path это папка, создаем там файл. Если полный путь - используем как было.
    # Для чистоты будем считать, что передаем директорию (для согласованности с generate_all_formats)
    output_dir = output_path if os.path.isdir(output_path) else os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    # Имя файла на основе темы
    file_path = os.path.join(output_dir, f"{topic_name}.txt")

    # Имя колоды оставляем на основе категории (берем из первой карточки)
    category = cards[0].category if cards else "general"
    deck_name = f"{deck_prefix}::{category.replace('_', ' ').title()}"

    header = "#separator:tab\n#html:true\n#deck column:3\n#tags column:5\n"

    cards_lines = []
    for card in cards:
        front = format_markdown_to_html(card.question)
        back = format_markdown_to_html(card.answer)

        back = remove_obsidian_links(back)
        back = remove_spaced_repetition_tags(back)

        # Добавляем только уникальные сниппеты кода
        if card.code_snippets:
            for snippet in card.code_snippets:
                # Проверяем, есть ли код в ответе
                if snippet.strip() not in card.answer:
                    back += "<br>" + format_code_for_anki(snippet)

        tags = ' '.join([f"interview/{tag}" for tag in card.tags if tag] +
                        [f"difficulty/{card.difficulty}"])

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
    has_question = bool(re.search(r'### Вопрос:', content))
    has_card = bool(re.search(r'#card', content))
    has_card_reverse = bool(re.search(r'#card-reverse', content))
    has_header = bool(re.search(r'^#\s+', content, re.MULTILINE))

    return has_question or has_card or has_card_reverse or has_header


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
    Имена файлов связываются с названием темы (topic).
    """
    output_files: List[str] = []

    if not cards:
        return output_files

    # Получаем имя темы из первой карточки (предполагаем, что все карточки из одного файла)
    topic_name = cards[0].topic

    logger.info(f"Генерация для темы: {topic_name} (Категория: {category})")

    # Создаём директории
    os.makedirs(cards_output, exist_ok=True)
    os.makedirs(os.path.join(cards_output, 'anki_sync'), exist_ok=True)
    os.makedirs(anki_output, exist_ok=True)

    # 1. Obsidian карточки (индивидуальные файлы)
    for card in cards:
        file_path = generate_obsidian_card(card, cards_output)
        output_files.append(file_path)

    # 2. Obsidian_to_Anki (файл синхронизации) -> Имя: {topic}_to_anki.md
    merged_file = generate_obsidian_merged_file(
        cards,
        topic_name,
        os.path.join(cards_output, 'anki_sync'),
        use_reverse_cards=use_reverse_cards
    )
    output_files.append(merged_file)

    # 3. Anki Import (файл импорта) -> Имя: {topic}.txt
    anki_file = generate_anki_import_file(
        cards,
        topic_name,
        anki_output  # Передаем папку, функция сама создаст файл
    )
    output_files.append(anki_file)

    logger.info(f"Сгенерировано файлов: {len(output_files)}")
    return output_files
