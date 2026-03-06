import os
import shutil

import streamlit as st

from config.Config import Config
from logic.utils import (
    load_markdown_topics,
    parse_cards_from_markdown,
    generate_all_formats,
    clean_up_duplicates
)

Config.create_directories()

categories_list: list[str] = Config.CATEGORIES_LIST.value
documents = Config.DOCUMENTS.value
output_dir = Config.OUTPUT.value
obsidian_vault = Config.OBSIDIAN_VAULT.value
materials_source = Config.MATERIALS_SOURCE.value  # 📂 новая переменная
card_tag = Config.CARD_TAG.value

if __name__ == '__main__':
    if 'clicked' not in st.session_state:
        st.session_state.clicked = False
    if 'generated_files' not in st.session_state:
        st.session_state.generated_files = []
    if 'generation_complete' not in st.session_state:
        st.session_state.generation_complete = False


    def click_button():
        st.session_state.clicked = True
        st.session_state.generation_complete = False
        st.session_state.generated_files = []


    def reset_button():
        st.session_state.clicked = False
        st.session_state.generation_complete = False
        st.session_state.generated_files = []


    st.set_page_config(
        page_title="Interview Cards - Генератор карточек",
        page_icon="📚",
        layout="wide"
    )

    st.title("📚 Interview Cards")
    st.subheader("Генератор карточек Spaced Repetition из Markdown")

    with st.expander("📍 Информация о путях", expanded=False):
        st.code(f"""
        Obsidian Vault: {obsidian_vault}
        Материалы (исходники): {materials_source}
        Карточки (генерация): {os.path.join(obsidian_vault, 'Interview', 'Cards')}
        Anki вывод: {os.path.join(output_dir, 'anki')}
        """)

    # Боковая панель
    with st.sidebar:
        st.header("⚙️ Настройки")

        input_dir = st.text_input(
            "Путь к папке с Markdown темами",
            value=materials_source  # ← по умолчанию папка в Vault
        )

        if os.path.exists(input_dir):
            st.success(f"✅ Папка найдена: {input_dir}")
            topics = load_markdown_topics(input_dir)
            st.info(f"📁 Тем найдено: {len(topics)}")

        # 🔧 Кнопка создания примера теперь кладёт файл прямо в materials_source
        if st.button("📝 Создать пример в Materials"):
            try:
                os.makedirs(input_dir, exist_ok=True)
                example_path = os.path.join(input_dir, "example_topic.md")

                template_path = os.path.join(os.path.dirname(__file__), "templates", "example_topic.md")
                if os.path.exists(template_path):
                    with open(template_path, 'r', encoding='utf-8') as f:
                        example_content = f.read()
                else:
                    st.error(f"❌ Шаблон не найден: {template_path}")
                    st.stop()

                with open(example_path, 'w', encoding='utf-8') as f:
                    f.write(example_content)
                st.success(f"✅ Пример создан: {example_path}")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Ошибка: {e}")

        selected_categories = st.multiselect(
            "Выберите категории",
            categories_list,
            default=categories_list[:3] if categories_list else []
        )

        output_format = st.multiselect(
            "Форматы вывода",
            ["Obsidian (со ссылками)", "Obsidian_to_Anki", "Anki Import"],
            default=["Obsidian (со ссылками)", "Anki Import"]
        )

        is_clean_duplicates = st.checkbox("Удалить дубликаты", value=False)

        st.markdown("---")
        st.info(f"Тег карточки: `{card_tag}`")

        if st.session_state.clicked:
            st.button("🔄 Сбросить", on_click=reset_button, use_container_width=True)

    # Основная область
    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown("### 📋 Предпросмотр Markdown файлов")

        if os.path.exists(input_dir):
            topics = load_markdown_topics(input_dir)

            if topics:
                topic_names = list(topics.keys())
                selected_topic = st.selectbox("Выберите тему для предпросмотра", topic_names)

                if selected_topic:
                    with st.expander(f"📄 {selected_topic}", expanded=True):
                        st.markdown(topics[selected_topic]['content'][:1000] + "...")

                    topic_data = topics[selected_topic]
                    cards_count = len(topic_data['content'].split('#card')) - 1 if '#card' in topic_data[
                        'content'] else 1
                    st.metric(f"Карточек в теме", max(cards_count, 1))
            else:
                st.warning("⚠️ Нет Markdown файлов в папке")

    with col2:
        st.markdown("### 🚀 Генерация")

        start_btn = st.button(
            '🚩 Начать генерацию',
            type="primary",
            on_click=click_button,
            disabled=st.session_state.clicked,
            use_container_width=True
        )

        if st.session_state.clicked:
            st.info("⏳ Обработка... Пожалуйста, подождите")

    # 🔴 Результаты генерации
    if st.session_state.clicked and not st.session_state.generation_complete:
        if not os.path.exists(input_dir):
            st.error("❌ Папка с Markdown файлами не найдена!")
            st.session_state.clicked = False
        else:
            with st.status("🚧 Обработка Markdown файлов...", expanded=True) as status:
                try:
                    output_files = []
                    total_generated = 0
                    all_cards = []

                    topics = load_markdown_topics(input_dir)
                    card_id = 1

                    for topic_name, topic_data in topics.items():
                        topic_category = topic_data.get('category', 'general')

                        if selected_categories and topic_category not in selected_categories:
                            st.warning(f"⚠️ Категория '{topic_category}' не выбрана")
                            continue

                        cards = parse_cards_from_markdown(topic_data['path'], card_id)
                        card_id += len(cards)

                        if not cards:
                            st.warning(f"⚠️ Нет валидных карточек в теме '{topic_name}'")
                            continue

                        all_cards.extend(cards)

                        materials_path = os.path.join(obsidian_vault, "Interview", "Materials", topic_category)
                        cards_path = os.path.join(obsidian_vault, "Interview", "Cards", topic_category)
                        anki_path = os.path.join(output_dir, "anki")

                        input_file = topic_data['path']
                        material_file = os.path.join(materials_path, f"{topic_name}.md")
                        os.makedirs(materials_path, exist_ok=True)

                        if os.path.abspath(input_file) != os.path.abspath(material_file):
                            try:
                                shutil.copy2(input_file, material_file)
                            except PermissionError as e:
                                st.warning(f"⚠️ Не удалось скопировать {topic_name}: файл занят или нет прав.")
                        else:
                            pass

                        files = generate_all_formats(cards, topic_category, cards_path, anki_path, materials_path)
                        output_files.extend(files)
                        total_generated += len(cards)
                        st.success(f"✅ Тема '{topic_name}': {len(cards)} карточек")

                    if is_clean_duplicates and output_files:
                        txt_files = [f for f in output_files if f.endswith('.txt')]
                        if txt_files:
                            removed = clean_up_duplicates(txt_files)
                            if removed > 0:
                                st.info(f"🧹 Удалено дубликатов: {removed}")

                    status.update(label=f"🏁 Готово! Сгенерировано {total_generated} карточек", state="complete")
                    st.session_state.generated_files = output_files
                    st.session_state.clicked = False
                    st.session_state.generation_complete = True

                    st.success(f"""
                        ### 📂 Файлы сгенерированы!
                        **Obsidian (карточки):** `{obsidian_vault}/Interview/Cards/`
                        **Obsidian (материалы):** `{obsidian_vault}/Interview/Materials/`
                        **Anki:** `{output_dir}/anki/`
                        """)
                    st.rerun()

                except Exception as e:
                    status.update(label="❌ Ошибка генерации", state="error")
                    st.error(f"Произошла ошибка: {e}")
                    import traceback

                    st.code(traceback.format_exc())
                    st.session_state.clicked = False

    # 🔴 Скачивание файлов
    if st.session_state.generated_files:
        st.markdown("### 📥 Скачивание файлов")

        for idx, file_path in enumerate(st.session_state.generated_files):
            if os.path.isfile(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                file_name = os.path.basename(file_path)
                st.download_button(
                    label=f"📄 {file_name}",
                    data=content,
                    file_name=file_name,
                    mime="text/plain",
                    key=f"download_{idx}_{file_name}"
                )
            else:
                continue

        if st.button("🔄 Новая генерация", on_click=reset_button):
            st.session_state.generated_files = []
            st.rerun()

    # Статистика
    with st.expander("📊 Статистика"):
        if st.session_state.generated_files:
            total_cards = 0
            for file_path in st.session_state.generated_files:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if file_path.endswith('.txt'):
                            lines = [l for l in content.split('\n') if l.strip() and not l.startswith('#')]
                            total_cards += len(lines)
                        else:
                            total_cards += content.count('#card')

            col1, col2, col3 = st.columns(3)
            col1.metric("Файлов сгенерировано", len(st.session_state.generated_files))
            col2.metric("Всего карточек", total_cards)
            col3.metric("Категорий", len(selected_categories))
