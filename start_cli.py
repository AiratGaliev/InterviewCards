import os
import sys
import json
from config.Config import Config
from logic.utils import (
    load_categories_questions,
    load_markdown_topics,
    parse_cards_from_topic,
    parse_questions_to_objects,
    generate_all_formats,
    clean_up_duplicates
)


def main():
    categories: list[str] = Config.CATEGORIES_LIST.value
    documents = Config.DOCUMENTS.value
    output_dir = Config.OUTPUT.value
    obsidian_vault = Config.OBSIDIAN_VAULT.value

    print("=" * 60)
    print("📚 Interview Cards - Генератор карточек")
    print("=" * 60)

    # Выбор типа входных данных
    input_type = input("Тип входных данных (1=JSON, 2=Markdown): ").strip()
    if input_type == "2":
        input_dir = input(f"Путь к папке с темами (по умолчанию: {os.path.join(documents, 'input/interview_topics')}): ")
        if not input_dir:
            input_dir = os.path.join(documents, "input/interview_topics")
        json_file = None
    else:
        json_file = input(f"Путь к JSON файлу (по умолчанию: {os.path.join(documents, 'interview_questions.json')}): ")
        if not json_file:
            json_file = os.path.join(documents, "interview_questions.json")
        input_dir = None

    # Проверка существования
    if json_file and not os.path.exists(json_file):
        print(f"❌ Файл не найден: {json_file}")
        sys.exit(1)
    if input_dir and not os.path.exists(input_dir):
        print(f"❌ Папка не найдена: {input_dir}")
        sys.exit(1)

    if json_file:
        print(f"✅ Файл найден: {json_file}")
        with open(json_file, 'r', encoding='utf-8') as f:
            preview_data = json.load(f)

        if isinstance(preview_data, dict):
            print(f"📁 Категорий: {len(preview_data)}")
            total_q = sum(len(v) for v in preview_data.values())
            print(f"📝 Всего вопросов: {total_q}")
        elif isinstance(preview_data, list):
            print(f"📝 Всего вопросов: {len(preview_data)}")
    else:
        print(f"✅ Папка найдена: {input_dir}")
        topics = load_markdown_topics(input_dir)
        print(f"📁 Тем найдено: {len(topics)}")

    print(f"\nДоступные категории: {', '.join(categories)}")

    selected_categories_input = input("Выберите категории (через запятую, или все): ")
    if selected_categories_input.strip().lower() == 'all' or not selected_categories_input.strip():
        selected_categories = categories
    else:
        selected_categories = [c.strip() for c in selected_categories_input.split(',')]

    # Форматы вывода
    print("\nФорматы вывода:")
    print("1. Obsidian (со ссылками)")
    print("2. Obsidian_to_Anki")
    print("3. Anki Import")
    format_input = input("Выберите форматы (через запятую, например 1,3): ").strip()
    output_formats = [int(x.strip()) for x in format_input.split(',')] if format_input else [1, 3]

    is_clean_duplicates = input("Удалить дубликаты? y/n (по умолчанию n): ").lower() == 'y'

    print("\n🚀 Начало генерации...")
    print("-" * 60)

    output_files = []
    total_generated = 0

    if json_file:
        categories_data = load_categories_questions(json_file)

        for category in selected_categories:
            if category not in categories_data:
                print(f"⚠️ Категория '{category}' не найдена в файле")
                continue

            questions_data = categories_data[category]
            cards = parse_questions_to_objects(questions_data)

            if not cards:
                print(f"⚠️ Нет валидных вопросов в категории '{category}'")
                continue

            obsidian_path = os.path.join(obsidian_vault, "Interview", category)
            anki_path = os.path.join(output_dir, "anki")

            files = generate_all_formats(cards, category, obsidian_path, anki_path)
            output_files.extend(files.values())
            total_generated += len(cards)
            print(f"✅ Категория '{category}': {len(cards)} карточек")
    else:
        topics = load_markdown_topics(input_dir)
        card_id = 1

        for topic_name, topic_data in topics.items():
            topic_category = topic_data.get('category', 'general')
            if topic_category not in selected_categories:
                continue

            cards = parse_cards_from_topic(topic_name, topic_data, card_id)
            card_id += len(cards)

            if not cards:
                print(f"⚠️ Нет валидных карточек в теме '{topic_name}'")
                continue

            obsidian_path = os.path.join(obsidian_vault, "Interview", topic_category)
            anki_path = os.path.join(output_dir, "anki")

            files = generate_all_formats(cards, topic_category, obsidian_path, anki_path)
            output_files.extend(files.values())
            total_generated += len(cards)
            print(f"✅ Тема '{topic_name}': {len(cards)} карточек")

    if is_clean_duplicates and output_files:
        txt_files = [f for f in output_files if f.endswith('.txt')]
        if txt_files:
            removed = clean_up_duplicates(txt_files)
            if removed > 0:
                print(f"🧹 Удалено дубликатов: {removed}")

    print("-" * 60)
    print(f"🏁 Готово! Сгенерировано {total_generated} карточек")
    print(f"📂 Путь вывода Obsidian: {obsidian_vault}/Interview/")
    print(f"📂 Путь вывода Anki: {output_dir}/anki/")
    print("=" * 60)


if __name__ == '__main__':
    main()