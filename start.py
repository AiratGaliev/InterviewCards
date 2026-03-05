import os
import streamlit as st
from config.Config import Config
from logic.utils import (
    load_json_questions,
    load_categories_questions,
    load_markdown_topics,
    parse_cards_from_topic,
    parse_questions_to_objects,
    generate_all_formats,
    clean_up_duplicates,
    validate_json_structure,
    process_questions_batch
)

categories_list: list[str] = Config.CATEGORIES_LIST.value
documents = Config.DOCUMENTS.value
output_dir = Config.OUTPUT.value
obsidian_vault = Config.OBSIDIAN_VAULT.value
max_questions = Config.MAX_QUESTIONS_PER_DECK.value

if __name__ == '__main__':
    if 'clicked' not in st.session_state:
        st.session_state.clicked = False
    if 'generated_files' not in st.session_state:
        st.session_state.generated_files = []

    def click_button():
        st.session_state.clicked = True

    st.set_page_config(
        page_title="Interview Cards - Генератор карточек",
        page_icon="📚",
        layout="wide"
    )

    st.title("📚 Interview Cards")
    st.subheader("Генератор карточек для подготовки к собеседованиям")

    # Боковая панель
    with st.sidebar:
        st.header("⚙️ Настройки")

        input_type = st.radio(
            "Тип входных данных",
            ["JSON файлы", "Markdown темы (AI)"],
            index=0
        )

        if input_type == "JSON файлы":
            json_file = st.text_input(
                "Путь к JSON файлу",
                value=os.path.join(documents, "interview_questions.json")
            )
        else:
            input_dir = st.text_input(
                "Путь к папке с темами",
                value=os.path.join(documents, "input/interview_topics")
            )

        selected_categories = st.multiselect(
            "Выберите категории",
            categories_list,
            default=categories_list[:3]
        )

        output_format = st.multiselect(
            "Форматы вывода",
            ["Obsidian (со ссылками)", "Obsidian_to_Anki", "Anki Import"],
            default=["Obsidian (со ссылками)", "Anki Import"]
        )

        is_clean_duplicates = st.checkbox("Удалить дубликаты", value=False)
        batch_processing = st.checkbox("Разбить на батчи", value=False)
        batch_size = st.number_input("Размер батча", min_value=100, max_value=1000, value=500) if batch_processing else 500

    # Основная область
    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown("### 📋 Предпросмотр данных")

        if input_type == "JSON файлы" and os.path.exists(json_file):
            try:
                import json
                with open(json_file, 'r', encoding='utf-8') as f:
                    preview_data = json.load(f)

                if isinstance(preview_data, dict):
                    st.metric("Категорий", len(preview_data))
                    total_q = sum(len(v) for v in preview_data.values())
                    st.metric("Всего вопросов", total_q)

                    category_stats = {k: len(v) for k, v in preview_data.items()}
                    st.bar_chart(category_stats)
                elif isinstance(preview_data, list):
                    st.metric("Всего вопросов", len(preview_data))

                with st.expander("Пример вопроса"):
                    if isinstance(preview_data, dict):
                        first_cat = list(preview_data.keys())[0]
                        if preview_data[first_cat]:
                            st.json(preview_data[first_cat][0])
                    elif isinstance(preview_data, list) and preview_data:
                        st.json(preview_data[0])

            except Exception as e:
                st.error(f"Ошибка загрузки файла: {e}")
        elif input_type == "Markdown темы (AI)" and os.path.exists(input_dir):
            try:
                topics = load_markdown_topics(input_dir)
                st.metric("Тем найдено", len(topics))

                if topics:
                    first_topic = list(topics.keys())[0]
                    with st.expander(f"Пример: {first_topic}"):
                        st.markdown(topics[first_topic]['content'][:500] + "...")
            except Exception as e:
                st.error(f"Ошибка загрузки тем: {e}")
        else:
            st.warning("Файл или папка не найдены. Укажите корректный путь.")

    with col2:
        st.markdown("### 🚀 Генерация")

        start_btn = st.button(
            '🚩 Начать генерацию',
            type="primary",
            on_click=click_button,
            disabled=st.session_state.clicked,
            use_container_width=True
        )

    # Результаты
    if start_btn:
        input_exists = (input_type == "JSON файлы" and os.path.exists(json_file)) or \
                       (input_type == "Markdown темы (AI)" and os.path.exists(input_dir))

        if not input_exists:
            st.error("Файл или папка не найдены!")
            st.session_state.clicked = False
        else:
            with st.status("🚧 Обработка данных... Пожалуйста, подождите.", expanded=True) as status:
                try:
                    output_files = []
                    total_generated = 0
                    all_cards = []

                    if input_type == "JSON файлы":
                        categories_data = load_categories_questions(json_file)

                        for category in selected_categories:
                            if category not in categories_data:
                                st.warning(f"Категория '{category}' не найдена в файле")
                                continue

                            questions_data = categories_data[category]
                            cards = parse_questions_to_objects(questions_data)

                            if not cards:
                                st.warning(f"Нет валидных вопросов в категории '{category}'")
                                continue

                            all_cards.extend(cards)

                            obsidian_path = os.path.join(obsidian_vault, "Interview", category)
                            anki_path = os.path.join(output_dir, "anki")

                            files = generate_all_formats(cards, category, obsidian_path, anki_path)
                            output_files.extend(files.values())
                            total_generated += len(cards)
                            st.success(f"✅ Категория '{category}': {len(cards)} карточек")

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
                                st.warning(f"Нет валидных карточек в теме '{topic_name}'")
                                continue

                            all_cards.extend(cards)

                            obsidian_path = os.path.join(obsidian_vault, "Interview", topic_category)
                            anki_path = os.path.join(output_dir, "anki")

                            files = generate_all_formats(cards, topic_category, obsidian_path, anki_path)
                            output_files.extend(files.values())
                            total_generated += len(cards)
                            st.success(f"✅ Тема '{topic_name}': {len(cards)} карточек")

                    if is_clean_duplicates and output_files:
                        txt_files = [f for f in output_files if f.endswith('.txt')]
                        if txt_files:
                            removed = clean_up_duplicates(txt_files)
                            if removed > 0:
                                st.info(f"🧹 Удалено дубликатов: {removed}")

                    status.update(
                        label=f"🏁 Готово! Сгенерировано {total_generated} карточек",
                        state="complete"
                    )

                    st.session_state.generated_files = output_files
                    st.session_state.clicked = False

                except Exception as e:
                    status.update(label="❌ Ошибка генерации", state="error")
                    st.error(f"Произошла ошибка: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.session_state.clicked = False

    # Скачивание файлов
    if st.session_state.generated_files:
        st.markdown("### 📥 Скачивание файлов")

        for file_path in st.session_state.generated_files:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                file_name = os.path.basename(file_path)
                st.download_button(
                    label=f"📄 {file_name}",
                    data=content,
                    file_name=file_name,
                    mime="text/plain"
                )

        # Инструкция по импорту
        with st.expander("📖 Как импортировать"):
            st.markdown("""
            ### Obsidian
            1. Поместите файлы в папку вашего Obsidian Vault
            2. Установите плагин **Spaced Repetition** или **Obsidian_to_Anki**
            3. Карточки с тегом `#card` будут автоматически обнаружены

            ### Anki
            1. Откройте Anki
            2. Нажмите **Файл** → **Импорт**
            3. Выберите скачанный файл (.txt)
            4. Убедитесь, что разделитель - **Tab**
            5. Нажмите **Импорт**
            """)

    # Статистика
    with st.expander("📊 Статистика"):
        if st.session_state.generated_files:
            total_cards = 0
            for file_path in st.session_state.generated_files:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = [l for l in f.readlines() if l.strip() and not l.startswith('#')]
                        if file_path.endswith('.txt'):
                            total_cards += len(lines)
                        else:
                            total_cards += lines.count('#card')

            col1, col2, col3 = st.columns(3)
            col1.metric("Файлов сгенерировано", len(st.session_state.generated_files))
            col2.metric("Всего карточек", total_cards)
            col3.metric("Категорий", len(selected_categories))