import glob
import os
import re
from typing import List, Dict

import pandas as pd
import yaml

from models.InterviewCard import InterviewCard


# =============================================================================
# Загрузка и парсинг Markdown файлов
# =============================================================================

def load_markdown_topics(input_dir: str) -> Dict[str, Dict]:
    """Рекурсивно загружает материалы из Markdown файлов, определяя категорию по имени папки."""
    topics = {}

    if not os.path.exists(input_dir):
        return topics

    # Рекурсивно обходим все подпапки
    for root, dirs, files in os.walk(input_dir):
        for file in files:
            if file.endswith('.md'):
                file_path = os.path.join(root, file)
                topic_name = file.replace('.md', '')

                # Определяем категорию как имя последней папки в пути относительно input_dir
                rel_path = os.path.relpath(root, input_dir)
                if rel_path == '.':
                    category = 'general'  # файл в корне
                else:
                    # Берём первую часть пути (если вложенность глубже, можно скорректировать)
                    category = rel_path.split(os.sep)[0]

                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                topics[topic_name] = {
                    'content': content,
                    'path': file_path,
                    'category': category,
                    'frontmatter': extract_frontmatter(content)
                }

    return topics


def extract_frontmatter(content: str) -> dict:
    """Извлечение frontmatter из Markdown"""
    frontmatter_match = re.match(r'^---\n(.+?)\n---\n', content, re.DOTALL)
    if frontmatter_match:
        try:
            frontmatter = yaml.safe_load(frontmatter_match.group(1))
            return frontmatter if frontmatter else {}
        except yaml.YAMLError:
            return {}
    return {}


def extract_category_from_content(content: str) -> str:
    """Извлечение категории из контента"""
    # Пробуем извлечь из frontmatter
    frontmatter = extract_frontmatter(content)
    if 'category' in frontmatter:
        return frontmatter['category']

    # Пробуем из заголовка
    match = re.search(r'^#\s*(.+?)$', content, re.MULTILINE)
    if match:
        return match.group(1).lower().replace(' ', '_')

    return 'general'


def extract_difficulty(content: str) -> str:
    """Извлечение уровня сложности"""
    frontmatter = extract_frontmatter(content)
    if 'difficulty' in frontmatter:
        return frontmatter['difficulty']

    if 'difficulty: hard' in content.lower() or '#hard' in content.lower():
        return 'hard'
    elif 'difficulty: easy' in content.lower() or '#easy' in content.lower():
        return 'easy'
    return 'medium'


def parse_cards_from_markdown(file_path: str, card_id: int = 1) -> List[InterviewCard]:
    """Парсинг карточек Spaced Repetition из Markdown файла"""
    cards = []

    # 🔴 ДОБАВЬТЕ ТОЛЬКО ЭТИ СТРОКИ:
    print(f"\n🔍 Парсинг: {file_path}")
    print(f"📁 Файл существует: {os.path.exists(file_path)}")

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    print(f"📊 Длина: {len(content)} символов")
    print(f"📊 'Вопрос:' в контенте: {'Вопрос:' in content}")
    # 🔴 КОНЕЦ ОТЛАДКИ

    topic_name = os.path.basename(file_path).replace('.md', '')
    category = extract_category_from_content(content)
    frontmatter = extract_frontmatter(content)

    # Паттерн 1: ### Вопрос: ... Ответ: ...
    questions_v1 = re.findall(
        r'### Вопрос:\s*(.+?)\nОтвет:\s*(.+?)(?=### Вопрос:|$)',
        content, re.DOTALL
    )

    # Паттерн 2: #card формат (Obsidian_to_Anki)
    questions_v2 = re.findall(
        r'#card\n\n(.+?)\n:::\n(.+?)(?=#card|$)',
        content, re.DOTALL
    )

    # Паттерн 3: Заголовок как вопрос, контент после --- как ответ
    questions_v3 = re.findall(
        r'^#\s*(.+?)\n\n---\n\n(.+?)(?=^#\s|#card|$)',
        content, re.MULTILINE | re.DOTALL
    )

    all_questions = questions_v1 + questions_v2 + questions_v3

    for q_text, a_text in all_questions:
        code_snippets = re.findall(
            r'```(?:python|javascript|java|sql|json|bash)?\n(.+?)\n```',
            a_text, re.DOTALL
        )

        tags = frontmatter.get('tags', []) if isinstance(frontmatter.get('tags'), list) else []
        if isinstance(tags, str):
            tags = [tags]

        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=q_text.strip(),
            answer=a_text.strip(),
            code_snippets=code_snippets,
            difficulty=extract_difficulty(content),
            tags=tags,
            source_note=topic_name,
            frontmatter=frontmatter
        )

        if card.validate():
            cards.append(card)
            card_id += 1

    # Если не найдено вопросов, создаём одну карточку из всего контента
    if not cards:
        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=f"Расскажите о: {topic_name}",
            answer=content,
            code_snippets=re.findall(
                r'```(?:python|javascript|java|sql|json|bash)?\n(.+?)\n```',
                content, re.DOTALL
            ),
            difficulty='medium',
            tags=[category],
            source_note=topic_name,
            frontmatter=frontmatter
        )
        if card.validate():
            cards.append(card)

    return cards


def parse_all_markdown_files(input_dir: str) -> List[InterviewCard]:
    """Парсинг всех Markdown файлов в директории"""
    all_cards = []
    card_id = 1

    if not os.path.exists(input_dir):
        return all_cards

    for file in os.listdir(input_dir):
        if file.endswith('.md'):
            file_path = os.path.join(input_dir, file)
            cards = parse_cards_from_markdown(file_path, card_id)
            all_cards.extend(cards)
            card_id += len(cards)

    return all_cards


# =============================================================================
# Очистка и форматирование текста
# =============================================================================

def clean_text(text: str) -> str:
    """Очистка текста от лишних символов"""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text


def escape_html(text: str) -> str:
    """Экранирование HTML для Anki"""
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    return text


def format_code_for_anki(code: str, language: str = 'python') -> str:
    """Форматирование кода для Anki"""
    code = escape_html(code)
    code = code.replace('\n', '<br>')
    code = code.replace('  ', '&nbsp;&nbsp;')
    return f'<pre style="background:#f4f4f4;padding:10px;border-radius:5px;overflow-x:auto;"><code>{code}</code></pre>'


def format_markdown_to_html(text: str) -> str:
    """Конвертация Markdown в HTML для Anki"""
    # Жирный текст
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    # Курсив
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    # Код в строке
    text = re.sub(r'`([^`]+)`', r'<code style="background:#f0f0f0;padding:2px 5px;border-radius:3px;">\1</code>', text)
    # Заголовки
    text = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    # Списки
    text = re.sub(r'^\s*[-*]\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    text = re.sub(r'(<li>.+</li>\n?)+', r'<ul>\g<0></ul>', text)
    # Нумерованные списки
    text = re.sub(r'^\s*\d+\.\s+(.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    # Переносы строк
    text = text.replace('\n\n', '<br><br>')
    text = text.replace('\n', '<br>')
    return text


def remove_obsidian_links(text: str) -> str:
    """Удаление ссылок Obsidian для чистого Anki экспорта"""
    # [[Note#Section|Text]] -> Text
    text = re.sub(r'\[\[.*?\|(.+?)\]\]', r'\1', text)
    # [[Note]] -> Note
    text = re.sub(r'\[\[(.+?)\]\]', r'\1', text)
    return text


def remove_spaced_repetition_tags(text: str) -> str:
    """Удаление тегов Spaced Repetition для Anki"""
    text = re.sub(r'#card\s*', '', text)
    text = re.sub(r'#interview\s*', '', text)
    text = re.sub(r'#\w+\s*', '', text)
    return text.strip()


# =============================================================================
# Генерация карточек для Obsidian
# =============================================================================

def generate_obsidian_card(card: InterviewCard, output_dir: str) -> str:
    """Генерация карточки для Obsidian Spaced Repetition"""
    # 🔴 Создаём директорию если не существует
    os.makedirs(output_dir, exist_ok=True)

    markdown_content = card.to_markdown()

    file_path = os.path.join(output_dir, f"{card.topic}.md")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

    print(f"📝 Создан файл: {file_path}")
    return file_path


def generate_obsidian_merged_file(cards: List[InterviewCard], category: str, output_dir: str) -> str:
    """Генерация объединённого файла для категории (Obsidian_to_Anki формат)"""
    # 🔴 Создаём директорию если не существует
    os.makedirs(output_dir, exist_ok=True)

    content = ""

    for card in cards:
        answer = card.answer
        answer = remove_obsidian_links(answer)
        answer = remove_spaced_repetition_tags(answer)

        content += f"#card\n\n{card.question}\n:::\n{answer}\n\n"

        if card.code_snippets:
            for snippet in card.code_snippets:
                content += f"```python\n{snippet}\n```\n\n"

        content += "---\n\n"

    file_path = os.path.join(output_dir, f"{category}.md")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"📝 Создан файл: {file_path}")
    return file_path


# =============================================================================
# Генерация карточек для Anki
# =============================================================================

def generate_anki_import_file(
        cards: List[InterviewCard],
        category: str,
        output_path: str,
        deck_prefix: str = "Interview"
) -> str:
    """Генерация файла для импорта в Anki"""
    # 🔴 Создаём директорию если не существует
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    deck_name = f"{deck_prefix}::{category.replace('_', ' ').title()}"

    header = "#separator:tab\n#html:true\n#deck column:3\n#tags column:5\n"

    cards_content = ""
    for card in cards:
        front = format_markdown_to_html(card.question)
        back = format_markdown_to_html(card.answer)

        back = remove_obsidian_links(back)
        back = remove_spaced_repetition_tags(back)

        if card.code_snippets:
            for snippet in card.code_snippets:
                back += "<br>" + format_code_for_anki(snippet)

        tags = ' '.join([f"interview/{tag}" for tag in card.tags] +
                        [f"difficulty/{card.difficulty}"])

        cards_content += f"{front}\t{back}\t{deck_name}\t\t{tags}\n"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header + cards_content)

    print(f"📝 Создан файл: {output_path}")
    return output_path


# =============================================================================
# Утилиты
# =============================================================================

def clean_up_duplicates(file_paths: List[str]) -> int:
    """Удаление дубликатов вопросов по тексту вопроса"""
    dfs: List[pd.DataFrame] = []

    for file_path in file_paths:
        if os.path.exists(file_path):
            try:
                df = pd.read_csv(file_path, header=None, names=['question', 'answer', 'deck', 'empty', 'tags'])
                dfs.append(df)
            except Exception:
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
        df.to_csv(file_paths[i], index=False, header=False)

    return removed_count


def natural_sort(file_paths: List[str]) -> List[str]:
    """Естественная сортировка файлов"""

    def atoi(text):
        return int(text) if text.isdigit() else text

    def natural_keys(text):
        return [atoi(c) for c in re.split(r'(\d+)', text)]

    return sorted(file_paths, key=natural_keys)


def get_markdown_files(folder_paths: List[str]) -> List[str]:
    """Получение списка Markdown файлов"""
    file_paths = []
    for folder_path in folder_paths:
        if os.path.exists(folder_path):
            files = glob.glob(os.path.join(folder_path, '*.md'))
            file_paths.extend(files)
    return natural_sort(file_paths)


def validate_markdown_structure(content: str) -> bool:
    """Валидация структуры Markdown"""
    # Проверяем наличие хотя бы одного вопроса или карточки
    has_question = bool(re.search(r'### Вопрос:', content))
    has_card = bool(re.search(r'#card', content))
    has_header = bool(re.search(r'^#\s+', content, re.MULTILINE))

    return has_question or has_card or has_header


async def validate_markdown_file_async(file_path: str) -> Dict:
    """Асинхронная валидация Markdown файла"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        is_valid = validate_markdown_structure(content)
        cards_count = len(re.findall(r'#card', content))

        return {
            'valid': is_valid,
            'path': file_path,
            'cards_count': cards_count if cards_count > 0 else 1,
            'category': extract_category_from_content(content)
        }
    except Exception as e:
        return {
            'valid': False,
            'path': file_path,
            'error': str(e)
        }


def process_cards_batch(
        cards: List[InterviewCard],
        category: str,
        output_dir: str,
        batch_size: int = 500
) -> List[str]:
    """Обработка карточек батчами для больших наборов данных"""
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
        cards_output: str,  # 🔴 Путь к Cards в Vault
        anki_output: str,
        materials_path: str = None  # 🔴 Путь к Materials для ссылок
) -> List[str]:
    """Генерация всех форматов вывода"""
    output_files: List[str] = []

    print(f"\n🔵 Генерация для категории: {category}")
    print(f"📂 Cards путь: {cards_output}")
    print(f"📂 Materials путь: {materials_path}")
    print(f"📂 Anki путь: {anki_output}")

    # Создаём директории
    os.makedirs(cards_output, exist_ok=True)
    os.makedirs(os.path.join(cards_output, 'anki_sync'), exist_ok=True)
    os.makedirs(anki_output, exist_ok=True)

    # Obsidian карточки (в Vault/Cards)
    for card in cards:
        card.source_note = f"Interview/Materials/{category}/{card.topic}" if materials_path else card.topic  # ✅ Правильная ссылка
        file_path = generate_obsidian_card(card, cards_output)
        output_files.append(file_path)

    # Obsidian_to_Anki (промежуточный)
    merged_file = generate_obsidian_merged_file(
        cards,
        category,
        os.path.join(cards_output, 'anki_sync')
    )
    output_files.append(merged_file)

    # Anki Import (без ссылок)
    anki_file = generate_anki_import_file(
        cards,
        category,
        os.path.join(anki_output, f"{category}.txt")
    )
    output_files.append(anki_file)

    print(f"✅ Сгенерировано файлов: {len(output_files)}")
    return output_files
