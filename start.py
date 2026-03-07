"""
Streamlit UI версия генератора карточек InterviewCards.
"""

import os
import traceback

import streamlit as st

from config.Config import get_config
from logic.core import CardGenerator, GenerationResult


def init_session_state():
    """Инициализация состояния сессии Streamlit"""
    if 'clicked' not in st.session_state:
        st.session_state.clicked = False
    if 'generated_files' not in st.session_state:
        st.session_state.generated_files = []
    if 'generation_complete' not in st.session_state:
        st.session_state.generation_complete = False
    if 'last_result' not in st.session_state:
        st.session_state.last_result = None


def click_button():
    """Обработчик нажатия кнопки генерации"""
    st.session_state.clicked = True
    st.session_state.generation_complete = False
    st.session_state.generated_files = []


def reset_button():
    """Обработчик нажатия кнопки сброса"""
    st.session_state.clicked = False
    st.session_state.generation_complete = False
    st.session_state.generated_files = []
    st.session_state.last_result = None


def display_paths_info(generator: CardGenerator):
    """Отображает информацию о путях"""
    paths = generator.get_output_paths()
    with st.expander("📍 Информация о путях", expanded=False):
        st.code(f"""
        Obsidian Vault: {paths['obsidian_vault']}
        Материалы (исходники): {generator.config.materials_source}
        Карточки (генерация): {paths['cards']}
        Anki вывод: {paths['anki']}
        """)


def display_generation_result(result: GenerationResult, show_warnings: bool = True):
    """
    Отображает результат генерации.

    Args:
        result: Результат генерации
        show_warnings: Показывать ли предупреждения в expander (False если внутри status)
    """
    if result.success:
        st.success(f"### ✅ Генерация завершена!")
        col1, col2, col3 = st.columns(3)
        col1.metric("Всего карточек", result.total_cards)
        col2.metric("Тем обработано", result.topics_processed)
        col3.metric("Файлов создано", len(result.output_files))
    else:
        st.error("❌ Генерация не удалась")
        for error in result.errors:
            st.error(error)

    # Предупреждения показываем только снаружи status блока
    if show_warnings and result.warnings:
        with st.expander("⚠️ Предупреждения", expanded=False):
            for warning in result.warnings:
                st.warning(warning)


def display_download_buttons(files: list):
    """Отображает кнопки скачивания файлов"""
    st.markdown("### 📥 Скачивание файлов")

    for idx, file_path in enumerate(files):
        if os.path.isfile(file_path):
            try:
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
            except Exception as e:
                st.warning(f"Не удалось прочитать {file_path}: {e}")


def display_statistics(files: list, selected_categories: list):
    """Отображает статистику"""
    with st.expander("📊 Статистика"):
        total_cards = 0

        for file_path in files:
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    if file_path.endswith('.txt'):
                        lines = [l for l in content.split('\n') if l.strip() and not l.startswith('#')]
                        total_cards += len(lines)
                    else:
                        total_cards += content.count('::') - content.count(':::')  # single-line basic
                        total_cards += content.count(':::')  # bidirectional (создает 2 карточки)
                except Exception:
                    continue

        col1, col2, col3 = st.columns(3)
        col1.metric("Файлов сгенерировано", len(files))
        col2.metric("Всего карточек", total_cards)
        col3.metric("Категорий", len(selected_categories))


def main():
    """Главная функция Streamlit приложения"""
    # Загрузка конфигурации
    config = get_config()
    config.create_directories()

    generator = CardGenerator(config)

    # Инициализация состояния
    init_session_state()

    # Настройка страницы
    st.set_page_config(
        page_title="Interview Cards - Генератор карточек",
        page_icon="📚",
        layout="wide"
    )

    st.title("📚 Interview Cards")
    st.subheader("Генератор карточек Spaced Repetition из Markdown")

    # Информация о путях
    display_paths_info(generator)

    # Боковая панель
    with st.sidebar:
        st.header("⚙️ Настройки")

        # Путь к папке с темами
        input_dir = st.text_input(
            "Путь к папке с Markdown темами",
            value=config.materials_source
        )

        # Проверка папки
        if os.path.exists(input_dir):
            st.success(f"✅ Папка найдена")
            topics = generator.validate_input_directory(input_dir)
            if topics[0]:
                st.info(f"📁 {topics[1]}")

        # Создание примера
        if st.button("📝 Создать пример"):
            try:
                template_path = os.path.join(
                    os.path.dirname(__file__),
                    "templates",
                    "example_topic.md"
                )
                example_path = generator.create_example_file(input_dir, template_path)
                st.success(f"✅ Пример создан: {example_path}")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Ошибка: {e}")

        # Выбор категорий
        selected_categories = st.multiselect(
            "Выберите категории",
            config.categories,
            default=config.categories[:3] if config.categories else []
        )

        # Настройки
        is_clean_duplicates = st.checkbox("Удалить дубликаты", value=False)

        st.markdown("---")
        st.info(f"Тег колоды: `#flashcards/<category>`")

        if st.session_state.clicked:
            st.button("🔄 Сбросить", on_click=reset_button, use_container_width=True)

    # Основная область
    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown("### 📋 Предпросмотр Markdown файлов")

        if os.path.exists(input_dir):
            from logic.utils import load_markdown_topics
            topics = load_markdown_topics(input_dir)

            if topics:
                topic_names = list(topics.keys())
                selected_topic = st.selectbox("Выберите тему для предпросмотра", topic_names)

                if selected_topic:
                    with st.expander(f"📄 {selected_topic}", expanded=True):
                        preview_content = topics[selected_topic]['content'][:1000]
                        if len(topics[selected_topic]['content']) > 1000:
                            preview_content += "..."
                        st.markdown(preview_content)

                    topic_data = topics[selected_topic]
                    # Подсчёт карточек разных форматов
                    content = topic_data['content']
                    cards_count = (
                            content.count('::') - content.count(':::') +  # single-line basic
                            content.count(':::') * 2 +  # bidirectional (2 карточки)
                            content.count('\n?\n') +  # multi-line basic
                            content.count('\n??\n') * 2  # multi-line bidirectional
                    )
                    cards_count = max(1, cards_count)
                    st.metric("Карточек в теме", cards_count)
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

    # Выполнение генерации
    if st.session_state.clicked and not st.session_state.generation_complete:
        if not os.path.exists(input_dir):
            st.error("❌ Папка с Markdown файлами не найдена!")
            st.session_state.clicked = False
        else:
            # Генерация БЕЗ status блока, чтобы избежать вложенности expander
            try:
                with st.spinner("🚧 Обработка Markdown файлов..."):
                    result = generator.generate(
                        input_dir=input_dir,
                        selected_categories=selected_categories or None,
                        clean_duplicates=is_clean_duplicates
                    )

                st.session_state.last_result = result
                st.session_state.generated_files = result.output_files
                st.session_state.clicked = False
                st.session_state.generation_complete = True

                # Отображаем результат ПОСЛЕ завершения spinner
                display_generation_result(result)

                st.rerun()

            except Exception as e:
                st.error(f"❌ Произошла ошибка: {e}")
                with st.expander("🔍 Детали ошибки"):
                    st.code(traceback.format_exc())
                st.session_state.clicked = False

    # Отображение результатов (при rerun)
    if st.session_state.last_result and st.session_state.generation_complete:
        display_generation_result(st.session_state.last_result)

    # Кнопки скачивания
    if st.session_state.generated_files:
        display_download_buttons(st.session_state.generated_files)

        if st.button("🔄 Новая генерация", on_click=reset_button):
            st.session_state.generated_files = []
            st.rerun()

    # Статистика
    if st.session_state.generated_files:
        display_statistics(
            st.session_state.generated_files,
            selected_categories
        )


if __name__ == '__main__':
    main()
