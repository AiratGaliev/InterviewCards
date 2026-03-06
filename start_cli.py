import os
import sys

from config.Config import Config
from logic.utils import (
    load_markdown_topics,
    parse_cards_from_markdown,
    generate_all_formats,
    clean_up_duplicates
)


def main():
    categories: list[str] = Config.CATEGORIES_LIST.value
    documents = Config.DOCUMENTS.value
    output_dir = Config.OUTPUT.value
    obsidian_vault = Config.OBSIDIAN_VAULT.value

    print("=" * 60)
    print("📚 Interview Cards - Генератор карточек (Markdown Only)")
    print("=" * 60)

    input_dir = input(
        f"Путь к папке с Markdown темами (по умолчанию: {os.path.join(documents, 'input/interview_topics')}): ")
    if not input_dir:
        input_dir = os.path.join(documents, "input/interview_topics")

    if not os.path.exists(input_dir):
        print(f"❌ Папка не найдена: {input_dir}")
        create = input("Создать папку с примером? y/n: ").lower()
        if create == 'y':
            os.makedirs(input_dir, exist_ok=True)
            example_path = os.path.join(input_dir, "example_topic.md")
            example_content = """# Python GIL

            ## Теория
            **GIL (Global Interpreter Lock)** — это механизм в CPython...
            
            ### Вопрос: Что такое GIL в Python?
            Ответ: **GIL** — это механизм в CPython...
            
            #card #interview #python
            """
            with open(example_path, 'w', encoding='utf-8') as f:
                f.write(example_content)
            print(f"✅ Пример создан: {example_path}")
        else:
            sys.exit(1)
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
    card_id = 1

    topics = load_markdown_topics(input_dir)

    for topic_name, topic_data in topics.items():
        topic_category = topic_data.get('category', 'general')
        if selected_categories and topic_category not in selected_categories:
            continue

        cards = parse_cards_from_markdown(topic_data['path'], card_id)
        card_id += len(cards)

        if not cards:
            print(f"⚠️ Нет валидных карточек в теме '{topic_name}'")
            continue

        obsidian_path = os.path.join(obsidian_vault, "Interview", topic_category)
        anki_path = os.path.join(output_dir, "anki")

        files = generate_all_formats(cards, topic_category, obsidian_path, anki_path)
        output_files.extend(files)
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
