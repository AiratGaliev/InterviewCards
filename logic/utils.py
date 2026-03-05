import asyncio
import glob
import json
import os
import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime

import pandas as pd
import aiohttp
import streamlit as st

from models.InterviewCard import InterviewCard


# =============================================================================
# Загрузка и парсинг данных
# =============================================================================

def load_json_questions(file_path: str) -> List[Dict]:
    """Загрузка вопросов из JSON файла"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if isinstance(data, dict) and 'questions' in data:
        return data['questions']
    elif isinstance(data, list):
        return data
    else:
        raise ValueError(
            "Неверный формат JSON. Ожидается массив вопросов или объект с ключом 'questions'"
        )


def load_categories_questions(file_path: str) -> Dict[str, List[Dict]]:
    """Загрузка вопросов по категориям из JSON"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if isinstance(data, dict):
        return data
    elif isinstance(data, list):
        categorized = {}
        for item in data:
            category = item.get('category', 'uncategorized')
            if category not in categorized:
                categorized[category] = []
            categorized[category].append(item)
        return categorized
    else:
        raise ValueError("Неверный формат JSON")


def load_markdown_topics(input_dir: str) -> Dict[str, Dict]:
    """Загрузка AI-сгенерированных материалов из Markdown файлов"""
    topics = {}

    if not os.path.exists(input_dir):
        return topics

    for file in os.listdir(input_dir):
        if file.endswith('.md'):
            topic_name = file.replace('.md', '')
            with open(os.path.join(input_dir, file), 'r', encoding='utf-8') as f:
                content = f.read()

            topics[topic_name] = {
                'content': content,
                'path': os.path.join(input_dir, file),
                'category': extract_category_from_content(content)
            }

    return topics


def extract_category_from_content(content: str) -> str:
    """Извлечение категории из контента (по заголовку или тегам)"""
    match = re.search(r'^#\s*(.+?)$', content, re.MULTILINE)
    if match:
        return match.group(1).lower().replace(' ', '_')
    return 'general'


def parse_cards_from_topic(topic_name: str, topic_data: Dict, card_id: int) -> List[InterviewCard]:
    """Парсинг карточек из темы (AI-сгенерированные вопросы)"""
    cards = []
    content = topic_data['content']
    category = topic_data.get('category', 'general')

    # Паттерн для поиска вопросов в формате AI-генерации
    questions = re.findall(
        r'### Вопрос:\s*(.+?)\nОтвет:\s*(.+?)(?=### Вопрос:|$)',
        content, re.DOTALL
    )

    for q_text, a_text in questions:
        code_snippets = re.findall(r'```(?:python|javascript|java|sql)?\n(.+?)\n```', a_text, re.DOTALL)

        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=q_text.strip(),
            answer=a_text.strip(),
            code_snippets=code_snippets,
            difficulty=extract_difficulty(content),
            tags=[category],
            source_note=topic_name
        )

        if card.validate():
            cards.append(card)
            card_id += 1

    # Если не найдено вопросов в специальном формате, создаём одну карточку из всего контента
    if not cards:
        card = InterviewCard(
            id=card_id,
            topic=topic_name,
            category=category,
            question=f"Расскажите о: {topic_name}",
            answer=content,
            code_snippets=re.findall(r'```(?:python|javascript|java|sql)?\n(.+?)\n```', content, re.DOTALL),
            difficulty='medium',
            tags=[category],
            source_note=topic_name
        )
        if card.validate():
            cards.append(card)

    return cards


def extract_difficulty(content: str) -> str:
    """Извлечение уровня сложности из контента"""
    if 'difficulty: hard' in content.lower() or '#hard' in content.lower():
        return 'hard'
    elif 'difficulty: easy' in content.lower() or '#easy' in content.lower():
        return 'easy'
    return 'medium'


def parse_questions_to_objects(questions_data: List[Dict]) -> List[InterviewCard]:
    """Конвертация данных в объекты InterviewCard"""
    cards = []
    for idx, data in enumerate(questions_data, 1):
        card = InterviewCard(
            id=data.get('id', idx),
            topic=data.get('topic', data.get('category', 'general')),
            category=data.get('category', 'general'),
            question=data.get('question', data.get('q', '')),
            answer=data.get('answer', data.get('a', '')),
            code_snippets=data.get('code_snippets', []),
            difficulty=data.get('difficulty', 'medium'),
            tags=data.get('tags', []),
            source_note=data.get('source_note')
        )
        if card.validate():
            cards.append(card)
    return cards


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


# =============================================================================
# Генерация карточек для Obsidian
# =============================================================================

def generate_obsidian_card(card: InterviewCard, output_dir: str) -> str:
    """Генерация карточки для Obsidian Spaced Repetition"""
    frontmatter = f"""---
tags: [{', '.join([f'interview/{tag}' for tag in card.tags])}]
created: {card.created_at.strftime('%Y-%m-%d')}
updated: {card.updated_at.strftime('%Y-%m-%d')}
source: "[[{card.source_note}]]"
difficulty: {card.difficulty}
category: {card.category}
---

# {card.question}

---

{card.answer}

"""

    if card.code_snippets:
        frontmatter += "## Примеры кода\n\n"
        for snippet in card.code_snippets:
            frontmatter += f"```python\n{snippet}\n```\n\n"

    if card.source_note:
        frontmatter += f"[[{card.source_note}|📎 Полный материал]]\n\n"

    frontmatter += "#card #interview\n"

    file_path = os.path.join(output_dir, f"{card.topic}.md")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(frontmatter)

    return file_path


def generate_obsidian_merged_file(cards: List[InterviewCard], category: str, output_dir: str) -> str:
    """Генерация объединённого файла для категории (Obsidian_to_Anki формат)"""
    content = ""

    for card in cards:
        answer = card.answer
        # Убираем ссылки для Anki
        answer = remove_obsidian_links(answer)

        content += f"#card\n\n{card.question}\n:::\n{answer}\n\n"

        if card.code_snippets:
            for snippet in card.code_snippets:
                content += f"```python\n{snippet}\n```\n\n"

        content += "---\n\n"

    file_path = os.path.join(output_dir, f"{category}.md")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

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
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    deck_name = f"{deck_prefix}::{category.replace('_', ' ').title()}"

    header = "#separator:tab\n#html:true\n#deck column:3\n#tags column:5\n"

    cards_content = ""
    for card in cards:
        front = format_markdown_to_html(card.question)
        back = format_markdown_to_html(card.answer)

        # Убираем ссылки Obsidian
        back = remove_obsidian_links(back)

        if card.code_snippets:
            for snippet in card.code_snippets:
                back += "<br>" + format_code_for_anki(snippet)

        tags = ' '.join([f"interview/{tag}" for tag in card.tags] +
                        [f"difficulty/{card.difficulty}"])

        cards_content += f"{front}\t{back}\t{deck_name}\t\t{tags}\n"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header + cards_content)

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


def get_file_paths(folder_paths: List[str]) -> List[str]:
    """Получение списка файлов"""
    file_paths = []
    for folder_path in folder_paths:
        if os.path.exists(folder_path):
            files = glob.glob(os.path.join(folder_path, '*.txt'))
            file_paths.extend(files)
    return natural_sort(file_paths)


def validate_json_structure(data: Dict) -> bool:
    """Валидация структуры JSON"""
    required_fields = ['question', 'answer']

    if isinstance(data, list):
        for item in data:
            if not all(field in item for field in required_fields):
                return False
        return True
    elif isinstance(data, dict):
        for category, questions in data.items():
            if not isinstance(questions, list):
                return False
            for item in questions:
                if not all(field in item for field in required_fields):
                    return False
        return True
    return False


async def validate_json_file_async(file_path: str) -> Dict:
    """Асинхронная валидация JSON файла"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        is_valid = validate_json_structure(data)

        return {
            'valid': is_valid,
            'path': file_path,
            'categories': list(data.keys()) if isinstance(data, dict) else ['default'],
            'total_questions': sum(len(v) for v in data.values()) if isinstance(data, dict) else len(data)
        }
    except Exception as e:
        return {
            'valid': False,
            'path': file_path,
            'error': str(e)
        }


def process_questions_batch(
    cards: List[InterviewCard],
    category: str,
    output_dir: str,
    batch_size: int = 500
) -> List[str]:
    """Обработка вопросов батчами для больших наборов данных"""
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
    obsidian_output: str,
    anki_output: str
) -> Dict[str, str]:
    """Генерация всех форматов вывода"""
    output_files = {}

    # Obsidian Spaced Repetition (со ссылками)
    for card in cards:
        generate_obsidian_card(card, obsidian_output)
    output_files['obsidian'] = obsidian_output

    # Obsidian_to_Anki (промежуточный)
    merged_file = generate_obsidian_merged_file(cards, category, os.path.join(obsidian_output, 'anki_sync'))
    output_files['obsidian_to_anki'] = merged_file

    # Anki Import (без ссылок)
    anki_file = generate_anki_import_file(cards, category, os.path.join(anki_output, f"{category}.txt"))
    output_files['anki'] = anki_file

    return output_files