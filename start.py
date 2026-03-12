"""
Streamlit UI приложения InterviewCards.
Генератор карточек Spaced Repetition из Markdown.
"""

import io
import os
import traceback
import zipfile

import streamlit as st

from config.Config import get_config
from logic.core import CardGenerator, GenerationResult
from logic.utils import load_markdown_topics, parse_cards_from_markdown
from models.InterviewCard import CardType
from ui.settings import UserSettings

# =============================================================================
# Константы
# =============================================================================

EXPORT_FORMATS = {
    "both": "📦 Оба формата (Obsidian + Anki)",
    "obsidian": "📝 Только Obsidian SR",
    "anki": "🃏 Только Anki",
}

CARD_TYPE_LABELS = {
    CardType.SINGLE_LINE_BASIC: "Basic (::)",
    CardType.SINGLE_LINE_BIDIRECTIONAL: "Bidirectional (:::)",
    CardType.MULTI_LINE_BASIC: "Multi-line (?)",
    CardType.MULTI_LINE_BIDIRECTIONAL: "Multi-line (??)",
    CardType.CLOZE: "Cloze (==…==)",
}

CARD_TYPE_EMOJI = {
    CardType.SINGLE_LINE_BASIC: "🟢",
    CardType.SINGLE_LINE_BIDIRECTIONAL: "🔵",
    CardType.MULTI_LINE_BASIC: "🟡",
    CardType.MULTI_LINE_BIDIRECTIONAL: "🟣",
    CardType.CLOZE: "🟠",
}

APP_VERSION = "2.0"


# =============================================================================
# Инициализация и управление состоянием
# =============================================================================

def init_session_state(config, settings: UserSettings):
    """Инициализирует состояние сессии из сохранённых настроек."""
    defaults = {
        'export_format': settings.export_format,
        'use_reverse_cards': settings.use_reverse_cards,
        'clean_duplicates': settings.clean_duplicates,
        'preview_length': settings.preview_length,
        'generated_files': [],
        'generation_complete': False,
        'last_result': None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    # input_dir: приоритет сохранённых → конфиг
    if 'input_dir' not in st.session_state:
        saved_dir = settings.input_dir or settings.last_input_dir
        st.session_state['input_dir'] = saved_dir or config.materials_source

    # selected_categories: валидируем против текущего конфига
    if 'selected_categories' not in st.session_state:
        saved = settings.selected_categories
        valid = [c for c in saved if c in config.categories]
        st.session_state['selected_categories'] = (
            valid if valid else config.categories[:3]
        )


def collect_current_settings() -> UserSettings:
    """Собирает текущие настройки из session_state."""
    return UserSettings(
        input_dir=st.session_state.get('input_dir', ''),
        selected_categories=st.session_state.get(
            'selected_categories', []
        ),
        export_format=st.session_state.get('export_format', 'both'),
        use_reverse_cards=st.session_state.get('use_reverse_cards', True),
        clean_duplicates=st.session_state.get('clean_duplicates', False),
        preview_length=st.session_state.get('preview_length', 1500),
        last_input_dir=st.session_state.get('input_dir', ''),
    )


def save_current_settings() -> bool:
    """Сохраняет текущие настройки в файл."""
    settings = collect_current_settings()
    return settings.save()


def reset_generation_state():
    """Сбрасывает состояние генерации."""
    st.session_state['generated_files'] = []
    st.session_state['generation_complete'] = False
    st.session_state['last_result'] = None


def reset_all_settings():
    """Сбрасывает все настройки к значениям по умолчанию."""
    UserSettings().save()
    keys_to_clear = [
        'input_dir', 'selected_categories', 'export_format',
        'use_reverse_cards', 'clean_duplicates', 'preview_length',
        'generated_files', 'generation_complete', 'last_result',
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]


# =============================================================================
# Вспомогательные функции
# =============================================================================

def create_zip_from_files(file_paths: list) -> io.BytesIO:
    """Создаёт ZIP-архив из списка файлов."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fp in file_paths:
            if os.path.isfile(fp):
                zf.write(fp, os.path.basename(fp))
    buffer.seek(0)
    return buffer


def get_file_size_str(file_path: str) -> str:
    """Возвращает читаемый размер файла."""
    try:
        size = os.path.getsize(file_path)
        if size > 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size > 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"
    except OSError:
        return "?"


def count_data_lines(file_path: str) -> int:
    """Считает строки данных (без заголовков) в файле Anki."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return sum(
                1 for line in f
                if line.strip() and not line.startswith('#')
            )
    except Exception:
        return 0


# =============================================================================
# Боковая панель
# =============================================================================

def render_sidebar(config, generator: CardGenerator):
    """Отрисовывает боковую панель с настройками."""
    with st.sidebar:
        st.header("⚙️ Настройки")

        # ─── Путь к материалам ───────────────────────
        st.text_input(
            "📁 Путь к материалам",
            key="input_dir",
            help="Папка с Markdown файлами для генерации карточек",
        )

        input_dir = st.session_state['input_dir']

        if os.path.exists(input_dir):
            is_valid, msg = generator.validate_input_directory(input_dir)
            if is_valid:
                st.success(f"✅ {msg}")
            else:
                st.warning(f"⚠️ {msg}")
        else:
            st.error("❌ Папка не найдена")
            if st.button(
                    "📝 Создать с примером",
                    use_container_width=True,
            ):
                try:
                    tpl = os.path.join(
                        os.path.dirname(__file__),
                        "templates", "example_topic.md",
                    )
                    path = generator.create_example_file(input_dir, tpl)
                    st.success(f"✅ Создан: {path}")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {e}")

        st.divider()

        # ─── Категории ───────────────────────────────
        st.multiselect(
            "🏷️ Категории",
            options=config.categories,
            key="selected_categories",
            help="Фильтр по категориям материалов",
        )

        # ─── Формат экспорта ─────────────────────────
        st.radio(
            "📤 Формат экспорта",
            options=list(EXPORT_FORMATS.keys()),
            format_func=lambda x: EXPORT_FORMATS[x],
            key="export_format",
            help=(
                "Оба — Obsidian SR файлы + Anki импорт\n"
                "Obsidian — только .md файлы для плагина SR\n"
                "Anki — только .txt файлы для File → Import"
            ),
        )

        # ─── Опции ───────────────────────────────────
        col1, col2 = st.columns(2)
        with col1:
            st.checkbox(
                "↔️ Reverse",
                key="use_reverse_cards",
                help="Обратные карточки для bidirectional типов",
            )
        with col2:
            st.checkbox(
                "🧹 Дубли",
                key="clean_duplicates",
                help="Удалить дубликаты по Front-стороне",
            )

        st.divider()

        # ─── Управление настройками ──────────────────
        col1, col2 = st.columns(2)
        with col1:
            if st.button(
                    "💾 Сохранить",
                    use_container_width=True,
                    help="Сохранить текущие настройки",
            ):
                if save_current_settings():
                    st.toast("✅ Настройки сохранены!")
                else:
                    st.toast("❌ Ошибка сохранения", icon="❌")

        with col2:
            if st.button(
                    "🔄 Сбросить",
                    use_container_width=True,
                    help="Сбросить к значениям по умолчанию",
            ):
                reset_all_settings()
                st.rerun()

        # ─── Информация о путях ──────────────────────
        with st.expander("📂 Пути вывода", expanded=False):
            paths = generator.get_output_paths()
            st.caption(f"**Vault:** `{paths['obsidian_vault']}`")
            st.caption(f"**Карточки:** `{paths['cards']}`")
            st.caption(f"**Материалы:** `{paths['materials']}`")
            st.caption(f"**Anki:** `{paths['anki']}`")

        # ─── Информация о настройках ─────────────────
        saved_path = UserSettings.get_settings_path()
        if os.path.exists(saved_path):
            settings = UserSettings.load(saved_path)
            if settings.last_saved:
                st.caption(f"💾 Сохранено: {settings.last_saved}")


# =============================================================================
# Вкладка: Темы
# =============================================================================

def render_tab_topics(generator: CardGenerator):
    """Отрисовывает вкладку обзора тем и карточек."""
    input_dir = st.session_state['input_dir']

    if not os.path.exists(input_dir):
        st.info(
            "📁 Укажите путь к папке с материалами "
            "в боковой панели"
        )
        return

    topics = load_markdown_topics(input_dir)

    if not topics:
        st.warning("⚠️ Нет Markdown файлов в указанной папке")
        st.info(
            "Нажмите **📝 Создать с примером** "
            "в боковой панели"
        )
        return

    # ─── Таблица тем ─────────────────────────────
    st.markdown(f"### 📚 Темы ({len(topics)})")

    table_data = []
    for name, data in topics.items():
        cards = parse_cards_from_markdown(data['path'])

        type_counts = {}
        for card in cards:
            label = CARD_TYPE_LABELS.get(card.card_type, "?")
            type_counts[label] = type_counts.get(label, 0) + 1

        types_str = ", ".join(
            f"{count}× {label}" for label, count in type_counts.items()
        )

        table_data.append({
            "Тема": name,
            "Категория": data.get('category', 'general'),
            "Карточек": len(cards),
            "Колода": data.get('deck_name', ''),
            "Типы": types_str or "—",
        })

    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Тема": st.column_config.TextColumn(width="medium"),
            "Категория": st.column_config.TextColumn(width="small"),
            "Карточек": st.column_config.NumberColumn(width="small"),
            "Колода": st.column_config.TextColumn(width="medium"),
            "Типы": st.column_config.TextColumn(width="large"),
        },
    )

    st.divider()

    # ─── Предпросмотр темы ───────────────────────
    st.markdown("### 🔍 Предпросмотр темы")

    topic_names = list(topics.keys())
    selected_topic = st.selectbox(
        "Выберите тему",
        topic_names,
        key="preview_topic",
    )

    if not selected_topic:
        return

    topic_data = topics[selected_topic]

    # Содержимое файла
    with st.expander("📄 Markdown содержимое", expanded=False):
        content = topic_data['content']
        preview_len = st.session_state.get('preview_length', 1500)

        if len(content) > preview_len:
            st.code(
                content[:preview_len] + "\n\n... (обрезано)",
                language="markdown",
            )
            st.caption(
                f"Показано {preview_len} из {len(content)} символов"
            )
        else:
            st.code(content, language="markdown")

    # Карточки
    cards = parse_cards_from_markdown(topic_data['path'])

    if not cards:
        st.warning("Карточки не найдены в этой теме")
        return

    # Метрики
    col1, col2, col3 = st.columns(3)
    col1.metric("Всего карточек", len(cards))

    unique_types = set(c.card_type for c in cards)
    col2.metric("Типов", len(unique_types))

    reverse_count = sum(1 for c in cards if c.is_reverse)
    col3.metric("Обратных", reverse_count)

    # Список карточек
    st.markdown("#### Карточки")

    for i, card in enumerate(cards):
        emoji = CARD_TYPE_EMOJI.get(card.card_type, "⚪")
        label = CARD_TYPE_LABELS.get(
            card.card_type, card.card_type.value
        )

        # Короткий заголовок
        title_text = card.question[:100]
        if len(card.question) > 100:
            title_text += "…"
        if card.is_reverse:
            title_text = f"↩️ {title_text}"

        with st.expander(
                f"{emoji} #{i + 1} {title_text}",
                expanded=(i == 0),
        ):
            # Метаданные
            meta_parts = [f"**Тип:** {label}"]
            if card.is_reverse:
                meta_parts.append("**Reverse:** Да")
            if card.tags:
                meta_parts.append(
                    f"**Теги:** {', '.join(card.tags[:5])}"
                )
            st.caption(" · ".join(meta_parts))

            # Front / Back
            col_f, col_b = st.columns(2)

            with col_f:
                st.markdown("**Front:**")
                st.markdown(
                    f"> {card.question[:500]}"
                    f"{'…' if len(card.question) > 500 else ''}"
                )

            with col_b:
                st.markdown("**Back:**")
                answer_preview = card.answer[:500]
                if len(card.answer) > 500:
                    answer_preview += "…"
                st.markdown(f"> {answer_preview}")

            # Код
            if card.code_snippets:
                st.markdown(
                    f"**Код:** {len(card.code_snippets)} фрагмент(ов)"
                )


# =============================================================================
# Вкладка: Генерация
# =============================================================================

def render_tab_generation(config, generator: CardGenerator):
    """Отрисовывает вкладку генерации."""
    input_dir = st.session_state['input_dir']
    categories = st.session_state.get('selected_categories', [])
    export_fmt = st.session_state.get('export_format', 'both')
    use_reverse = st.session_state.get('use_reverse_cards', True)
    clean_dupes = st.session_state.get('clean_duplicates', False)

    # ─── Параметры ───────────────────────────────
    st.markdown("### 📋 Параметры генерации")

    col1, col2, col3 = st.columns(3)

    dir_name = (
        os.path.basename(input_dir.rstrip('/\\'))
        if input_dir else "—"
    )
    col1.metric("📁 Папка", dir_name)
    col2.metric("🏷️ Категорий", len(categories) or "Все")

    fmt_label = EXPORT_FORMATS.get(export_fmt, export_fmt)
    # Убираем эмодзи для метрики
    fmt_short = fmt_label.split(' ', 1)[1] if ' ' in fmt_label else fmt_label
    col3.metric("📤 Формат", fmt_short)

    # Опции
    options_parts = []
    if use_reverse:
        options_parts.append("↔️ Reverse карточки")
    if clean_dupes:
        options_parts.append("🧹 Дедупликация")
    if options_parts:
        st.caption(f"Опции: {' · '.join(options_parts)}")
    else:
        st.caption("Опции: стандартные")

    st.divider()

    # ─── Кнопка генерации ────────────────────────
    can_generate = os.path.exists(input_dir)

    if not can_generate:
        st.warning(
            "❌ Папка с материалами не найдена. "
            "Укажите корректный путь в боковой панели."
        )

    generate_clicked = st.button(
        "🚩 Начать генерацию",
        type="primary",
        use_container_width=True,
        disabled=not can_generate,
    )

    if generate_clicked:
        reset_generation_state()

        # Автосохранение настроек
        settings = collect_current_settings()
        if settings.auto_save:
            settings.save()

        # Прогресс-бар
        progress_bar = st.progress(0, text="Подготовка...")
        status_text = st.empty()

        def on_progress(current, total, topic_name):
            if total > 0:
                progress_bar.progress(
                    current / total,
                    text=f"Обработка: {topic_name} "
                         f"({current}/{total})",
                )
            status_text.caption(f"📄 {topic_name}")

        try:
            result = generator.generate(
                input_dir=input_dir,
                selected_categories=categories or None,
                clean_duplicates=clean_dupes,
                export_format=export_fmt,
                use_reverse_cards=use_reverse,
                progress_callback=on_progress,
            )

            progress_bar.progress(1.0, text="✅ Завершено!")
            status_text.empty()

            st.session_state['last_result'] = result
            st.session_state['generated_files'] = result.output_files
            st.session_state['generation_complete'] = True

        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"❌ Произошла ошибка: {e}")
            with st.expander("🔍 Трассировка ошибки"):
                st.code(traceback.format_exc())

    # ─── Результаты ──────────────────────────────
    result = st.session_state.get('last_result')
    if not result:
        return

    st.divider()
    _display_generation_result(result)


def _display_generation_result(result: GenerationResult):
    """Отображает результат генерации с метриками."""
    if result.success:
        st.success("### ✅ Генерация завершена!")

        col1, col2, col3 = st.columns(3)
        col1.metric("📝 Карточек", result.total_cards)
        col2.metric("📚 Тем", result.topics_processed)
        col3.metric("📄 Файлов", len(result.output_files))

        # Статистика по типам
        if result.cards_by_type:
            with st.expander("📊 По типам карточек", expanded=False):
                for type_name, count in sorted(
                        result.cards_by_type.items()
                ):
                    label = type_name.replace('_', ' ').title()
                    st.caption(f"• **{label}:** {count}")

        # Статистика по категориям
        if result.cards_by_category:
            with st.expander("📊 По категориям", expanded=False):
                for cat, count in sorted(
                        result.cards_by_category.items()
                ):
                    st.caption(f"• **{cat}:** {count}")

    else:
        st.error("### ❌ Генерация не удалась")
        for error in result.errors:
            st.error(error)

    # Предупреждения
    if result.warnings:
        with st.expander(
                f"⚠️ Предупреждения ({len(result.warnings)})",
                expanded=False,
        ):
            for w in result.warnings:
                st.warning(w)


# =============================================================================
# Вкладка: Файлы
# =============================================================================

def render_tab_files(generator: CardGenerator):
    """Отрисовывает вкладку управления файлами."""
    files = st.session_state.get('generated_files', [])

    if not files:
        st.info(
            "📂 Файлы появятся после генерации. "
            "Перейдите на вкладку **🚀 Генерация**."
        )
        return

    existing_files = [f for f in files if os.path.isfile(f)]

    if not existing_files:
        st.warning("⚠️ Сгенерированные файлы не найдены на диске")
        return

    st.markdown(f"### 📂 Файлы ({len(existing_files)})")

    # ─── Скачать всё ZIP ─────────────────────────
    if len(existing_files) > 1:
        zip_buffer = create_zip_from_files(existing_files)
        st.download_button(
            "📦 Скачать всё (ZIP)",
            data=zip_buffer,
            file_name="interview_cards_export.zip",
            mime="application/zip",
            use_container_width=True,
        )
        st.divider()

    # ─── Группировка по типу ─────────────────────
    obsidian_files = [f for f in existing_files if f.endswith('.md')]
    anki_files = [f for f in existing_files if f.endswith('.txt')]

    if obsidian_files:
        st.markdown("#### 📝 Obsidian Spaced Repetition")
        for idx, fp in enumerate(obsidian_files):
            _render_file_row(fp, idx, "obs")

    if anki_files:
        st.markdown("#### 🃏 Anki Import")
        for idx, fp in enumerate(anki_files):
            _render_file_row(fp, idx + len(obsidian_files), "anki")

    st.divider()

    # ─── Управление ──────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
                "🗑️ Удалить файлы с диска",
                use_container_width=True,
                type="secondary",
        ):
            deleted, errors = generator.clean_output_files(existing_files)
            if deleted > 0:
                st.toast(f"🗑️ Удалено файлов: {deleted}")
            for err in errors:
                st.warning(err)
            reset_generation_state()
            st.rerun()

    with col2:
        if st.button(
                "🔄 Очистить список",
                use_container_width=True,
        ):
            reset_generation_state()
            st.rerun()


def _render_file_row(file_path: str, idx: int, prefix: str):
    """Отрисовывает строку файла с предпросмотром и скачиванием."""
    file_name = os.path.basename(file_path)
    file_size = get_file_size_str(file_path)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        st.warning(f"Ошибка чтения {file_name}: {e}")
        return

    col1, col2 = st.columns([4, 1])

    with col1:
        # Подсчёт карточек для Anki файлов
        extra = ""
        if file_path.endswith('.txt'):
            lines_count = count_data_lines(file_path)
            extra = f" · {lines_count} карт."

        with st.expander(
                f"📄 {file_name} ({file_size}{extra})",
                expanded=False,
        ):
            lines = content.split('\n')
            max_preview = 40
            preview = '\n'.join(lines[:max_preview])
            if len(lines) > max_preview:
                preview += (
                    f"\n\n... ещё {len(lines) - max_preview} строк"
                )

            lang = "markdown" if file_name.endswith('.md') else "text"
            st.code(preview, language=lang)

    with col2:
        st.download_button(
            "⬇️ Скачать",
            data=content,
            file_name=file_name,
            mime="text/plain",
            key=f"dl_{prefix}_{idx}_{file_name}",
            use_container_width=True,
        )


# =============================================================================
# Вкладка: Настройки
# =============================================================================

def render_tab_settings(config):
    """Отрисовывает вкладку конфигурации и справки."""

    st.markdown("### ⚙️ Конфигурация")

    # ─── Пути ────────────────────────────────────
    with st.expander("📂 Пути приложения", expanded=True):
        st.text_input(
            "Obsidian Vault",
            value=config.obsidian_vault,
            disabled=True,
            key="cfg_vault",
        )
        st.text_input(
            "Папка материалов",
            value=config.materials_source,
            disabled=True,
            key="cfg_materials",
        )
        st.text_input(
            "Папка вывода",
            value=config.output,
            disabled=True,
            key="cfg_output",
        )

        anki_media = getattr(config, 'anki_collection_media', '')
        if anki_media:
            st.text_input(
                "Anki collection.media",
                value=anki_media,
                disabled=True,
                key="cfg_anki_media",
            )

        st.info(
            "💡 Для изменения путей отредактируйте файл "
            "`config.ini` и перезапустите приложение."
        )

    # ─── Лимиты ──────────────────────────────────
    with st.expander("📏 Лимиты", expanded=False):
        col1, col2, col3 = st.columns(3)
        col1.metric(
            "Карточек в колоде", config.max_questions_per_deck
        )
        col2.metric(
            "Длина вопроса", config.max_question_length
        )
        col3.metric(
            "Длина ответа", config.max_answer_length
        )

    # ─── Категории ───────────────────────────────
    with st.expander("🏷️ Категории", expanded=False):
        if config.categories:
            cols = st.columns(
                min(len(config.categories), 5)
            )
            for i, cat in enumerate(config.categories):
                cols[i % len(cols)].code(cat)
        else:
            st.warning("Категории не настроены")

    # ─── Предпросмотр (длина) ────────────────────
    with st.expander("🔧 Настройки UI", expanded=False):
        st.slider(
            "Длина предпросмотра (символы)",
            min_value=500,
            max_value=5000,
            step=250,
            key="preview_length",
            help="Сколько символов показывать "
                 "при предпросмотре Markdown",
        )

    st.divider()

    # ─── Справка по форматам ─────────────────────
    with st.expander("📖 Справка по форматам карточек"):
        st.markdown("""
        | Формат | Синтаксис | Описание |
        |--------|-----------|----------|
        | **Basic** | `вопрос::ответ` | Односторонняя карточка |
        | **Bidirectional** | `инфо1:::инфо2` | Двусторонняя (создаёт 2 карточки) |
        | **Multi-line** | `вопрос` ↵ `?` ↵ `ответ` | Многострочный вопрос/ответ |
        | **Multi-line Bidi** | `инфо1` ↵ `??` ↵ `инфо2` | Многострочная двусторонняя |
        | **Cloze** | `==скрытый текст==` | Пропуски |
        | **Cloze + hint** | `==текст==^[подсказка]` | Пропуск с подсказкой |
        | **Cloze + seq** | `==текст==^[hint][^1]` | Группированные пропуски |
        | **Legacy** | `### Вопрос: ... Ответ: ...` | Старый формат |
        
        #### Frontmatter (YAML)
        
        ```yaml
        ---
        tags: [flashcards/python]
        category: python
        deck: flashcards/python
        ---
        ```
        
        **Приоритет определения колоды:**
        1. Поле `deck` во frontmatter
        2. Тег `flashcards/...` в `tags`
        3. `flashcards/<category>` (автоматически)
        """)

    # ─── Anki Import ─────────────────────────────
    with st.expander("🃏 Импорт в Anki"):
        st.markdown("""
        **Как импортировать в Anki:**
        
        1. Откройте Anki → **File → Import...**
        2. Выберите файл `*_anki_basic.txt` или `*_anki_cloze.txt`
        3. Anki автоматически определит формат из заголовков
        4. Нажмите **Import**
        
        **Особенности:**
        - Basic и Cloze карточки в **раздельных файлах**
          (разные Note Types)
        - Cloze автоматически конвертируется:
          `==text==` → `{{c1::text}}`
        - Теги и колоды проставляются автоматически
        - При повторном импорте дубликаты обновляются
        """)

    # ─── О приложении ────────────────────────────
    with st.expander("ℹ️ О приложении"):
        st.markdown(f"""
        **Interview Cards** v{APP_VERSION}
        
        Генератор карточек Spaced Repetition
        из Markdown для подготовки к техническим собеседованиям.
        
        **Возможности:**
        - 📝 Obsidian Spaced Repetition формат
        - 🃏 Anki импорт (Basic + Cloze)
        - 📦 Пакетная обработка тем
        - 🧹 Дедупликация карточек
        - ↔️ Двусторонние карточки
        - 🔗 Ссылки на исходные материалы
        """)

        st.caption(
            f"Файл настроек: `{UserSettings.get_settings_path()}`"
        )
        st.caption(f"Конфигурация: `{config._config_path}`")


# =============================================================================
# Главная функция
# =============================================================================

def main():
    """Главная функция Streamlit приложения."""

    # ─── Конфигурация страницы (ПЕРВЫЙ вызов Streamlit) ──
    st.set_page_config(
        page_title="Interview Cards",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Минимальная стилизация
    st.markdown("""
    <style>
        .stTabs [data-baseweb="tab-list"] { gap: 4px; }
        .stTabs [data-baseweb="tab"] {
            padding: 8px 16px;
            font-size: 0.95em;
        }
    </style>
    """, unsafe_allow_html=True)

    # ─── Загрузка конфигурации ───────────────────
    config = get_config()
    config.create_directories()

    # ─── Загрузка настроек ───────────────────────
    settings = UserSettings.load()
    settings.merge_with_config(config)

    # ─── Генератор ───────────────────────────────
    generator = CardGenerator(config)

    # ─── Инициализация состояния ─────────────────
    init_session_state(config, settings)

    # ─── Заголовок ───────────────────────────────
    st.title("📚 Interview Cards")
    st.caption(
        "Генератор карточек Spaced Repetition из Markdown"
    )

    # ─── Боковая панель ──────────────────────────
    render_sidebar(config, generator)

    # ─── Вкладки основного контента ──────────────
    tab_topics, tab_gen, tab_files, tab_settings = st.tabs([
        "📋 Темы",
        "🚀 Генерация",
        "📂 Файлы",
        "⚙️ Настройки",
    ])

    with tab_topics:
        render_tab_topics(generator)

    with tab_gen:
        render_tab_generation(config, generator)

    with tab_files:
        render_tab_files(generator)

    with tab_settings:
        render_tab_settings(config)


if __name__ == '__main__':
    main()
