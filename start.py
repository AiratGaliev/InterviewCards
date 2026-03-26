import io
import os
import re
import traceback
import zipfile
from pathlib import Path

import streamlit as st

from config.Config import get_config
from logic import find_all_duplicates, generate_duplicate_report_text
from logic.core import CardGenerator, GenerationResult
from logic.utils import (
    load_markdown_topics,
    parse_cards_from_markdown,
    validate_all_files,
    generate_validation_report_text,
    search_cards,
    compute_deck_statistics,
)
from models.InterviewCard import CardType
from ui.settings import UserSettings

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

SEVERITY_EMOJI = {
    'error': '❌',
    'warning': '⚠️',
    'info': 'ℹ️',
}

APP_VERSION = "2.1"

CARDS_PER_PAGE = 25


def _get_dir_mtime(dir_path: str) -> float:
    """Время последнего изменения .md файлов в директории."""
    latest = 0.0
    if not os.path.exists(dir_path):
        return latest
    try:
        for root, _, files in os.walk(dir_path):
            for f in files:
                if f.endswith('.md'):
                    try:
                        mt = os.path.getmtime(os.path.join(root, f))
                        if mt > latest:
                            latest = mt
                    except OSError:
                        pass
    except OSError:
        pass
    return latest


def _get_file_mtime(file_path: str) -> float:
    try:
        return os.path.getmtime(file_path)
    except OSError:
        return 0.0


@st.cache_data(ttl=120, show_spinner=False)
def _cached_load_topics(input_dir: str, dir_mtime: float) -> dict:
    return load_markdown_topics(input_dir)


@st.cache_data(ttl=120, show_spinner=False)
def _cached_parse_cards(file_path: str, file_mtime: float) -> list:
    return parse_cards_from_markdown(file_path)


@st.cache_data(ttl=300, show_spinner="Подсчёт статистики...")
def _cached_statistics(input_dir: str, dir_mtime: float) -> dict:
    return compute_deck_statistics(input_dir)


@st.cache_data(ttl=120, show_spinner=False)
def _cached_find_duplicates(input_dir: str, dir_mtime: float) -> list:
    return find_all_duplicates(input_dir)


def get_topics(input_dir: str) -> dict:
    """Загружает темы с кэшированием по mtime директории."""
    if not os.path.exists(input_dir):
        return {}
    mtime = _get_dir_mtime(input_dir)
    return _cached_load_topics(input_dir, mtime)


def get_cards(file_path: str) -> list:
    """Парсит карточки из файла с кэшированием по mtime файла."""
    if not os.path.exists(file_path):
        return []
    mtime = _get_file_mtime(file_path)
    return _cached_parse_cards(file_path, mtime)


def invalidate_caches():
    """Сброс всех кэшей (после генерации или изменения файлов)."""
    _cached_load_topics.clear()
    _cached_parse_cards.clear()
    _cached_statistics.clear()
    _cached_find_duplicates.clear()


def category_matches(
        topic_category: str,
        selected_categories: list,
) -> bool:
    """Иерархическое сравнение категорий для UI.

    Выбор 'java' включает 'java', 'java/core' и т.д.
    Выбор 'java/core' включает только 'java/core'.
    """
    if not selected_categories:
        return True
    for sel_cat in selected_categories:
        if topic_category == sel_cat:
            return True
        if topic_category.startswith(sel_cat + '/'):
            return True
    return False


def init_session_state(config, settings: UserSettings):
    """Инициализация session_state из сохранённых настроек.

    Значения из settings (файл .json) имеют приоритет
    над defaults. Повторная инициализация не перезаписывает
    уже установленные значения в session_state.
    """

    # ── input_dir: приоритет saved > config ──
    if 'input_dir' not in st.session_state:
        saved_dir = settings.input_dir or settings.last_input_dir
        st.session_state['input_dir'] = (
            saved_dir if saved_dir else config.materials_source
        )

    # ── selected_categories: приоритет saved (даже если пустой) ──
    if 'selected_categories' not in st.session_state:
        if settings.selected_categories is not None:
            # Фильтруем только те, что есть в config
            valid = [
                c for c in settings.selected_categories
                if c in config.categories
            ]
            st.session_state['selected_categories'] = valid
        else:
            # settings.selected_categories is None —
            # файл настроек отсутствует или повреждён
            st.session_state['selected_categories'] = []

    # ── Остальные настройки: saved > default ──
    defaults = {
        'export_format': settings.export_format or 'both',
        'use_reverse_cards': settings.use_reverse_cards,
        'clean_duplicates': settings.clean_duplicates,
        'preview_length': settings.preview_length or 1500,
        'generated_files': [],
        'generation_complete': False,
        'last_result': None,
        'search_query': '',
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    # ── Обработка отложенных операций с категориями ──
    cats_to_remove = st.session_state.pop('_categories_to_remove', [])
    if cats_to_remove:
        current = st.session_state.get('selected_categories', [])
        st.session_state['selected_categories'] = [
            c for c in current if c not in cats_to_remove
        ]

    # ── Обработка отложенного переименования ──
    rename_info = st.session_state.pop('_category_rename', None)
    if rename_info:
        old_name, new_name = rename_info
        current = st.session_state.get('selected_categories', [])
        st.session_state['selected_categories'] = [
            new_name if c == old_name else c for c in current
        ]

    # ── Валидация: убираем несуществующие категории ──
    current = st.session_state.get('selected_categories', [])
    valid = [c for c in current if c in config.categories]
    if len(valid) != len(current):
        st.session_state['selected_categories'] = valid


def collect_current_settings() -> UserSettings:
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
    settings = collect_current_settings()
    return settings.save()


def reset_generation_state():
    st.session_state['generated_files'] = []
    st.session_state['generation_complete'] = False
    st.session_state['last_result'] = None


def reset_all_settings():
    UserSettings().save()
    invalidate_caches()
    keys_to_clear = [
        'input_dir', 'selected_categories', 'export_format',
        'use_reverse_cards', 'clean_duplicates', 'preview_length',
        'generated_files', 'generation_complete', 'last_result',
        'search_query', 'new_category_input', 'renaming_category',
        'validation_reports', 'found_duplicates',
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]


def create_zip_from_files(file_paths: list) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fp in file_paths:
            if os.path.isfile(fp):
                zf.write(fp, os.path.basename(fp))
    buffer.seek(0)
    return buffer


def get_file_size_str(file_path: str) -> str:
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
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return sum(
                1 for line in f
                if line.strip() and not line.startswith('#')
            )
    except Exception:
        return 0


def render_sidebar(config, generator: CardGenerator):
    with st.sidebar:
        st.header("⚙️ Настройки")

        st.text_input(
            "📁 Путь к материалам",
            key="input_dir",
            help="Папка с Markdown файлами для генерации карточек",
        )

        input_dir = st.session_state['input_dir']

        if os.path.exists(input_dir):
            # ── Используем кэшированную загрузку вместо validate_input_directory ──
            topics = get_topics(input_dir)
            if topics:
                st.success(f"✅ Найдено тем: {len(topics)}")
            else:
                st.warning("⚠️ Нет Markdown файлов в папке")
        else:
            st.error("❌ Папка не найдена")
            if st.button(
                    "📁 Создать с примером",
                    use_container_width=True,
            ):
                try:
                    tpl = os.path.join(
                        os.path.dirname(__file__),
                        "templates", "example_topic.md",
                    )
                    path = generator.create_example_file(input_dir, tpl)
                    st.success(f"✅ Создан: {path}")
                    invalidate_caches()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {e}")

        # ── Остаток функции без изменений ──
        st.divider()

        st.multiselect(
            "🏷️ Категории",
            options=config.categories,
            key="selected_categories",
            help=(
                "Фильтр по категориям материалов. "
                "Новые категории добавляются на вкладке ⚙️ Настройки."
            ),
        )

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

        with st.expander("📂 Пути вывода", expanded=False):
            paths = generator.get_output_paths()
            st.caption(f"**Vault:** `{paths['obsidian_vault']}`")
            st.caption(f"**Карточки:** `{paths['cards']}`")
            st.caption(f"**Материалы:** `{paths['materials']}`")
            st.caption(f"**Anki:** `{paths['anki']}`")

        saved_path = UserSettings.get_settings_path()
        if os.path.exists(saved_path):
            settings = UserSettings.load(saved_path)
            if settings.last_saved:
                st.caption(f"💾 Сохранено: {settings.last_saved}")


def render_tab_topics(generator: CardGenerator):
    input_dir = st.session_state['input_dir']
    selected_categories = st.session_state.get('selected_categories', [])

    if not os.path.exists(input_dir):
        st.info(
            "📁 Укажите путь к папке с материалами "
            "в боковой панели"
        )
        return

    # ── Кэшированная загрузка ──
    topics = get_topics(input_dir)

    if not topics:
        st.warning("⚠️ Нет Markdown файлов в указанной папке")
        st.info(
            "Нажмите **📁 Создать с примером** "
            "в боковой панели"
        )
        return

    if selected_categories:
        filtered_topics = {
            name: data for name, data in topics.items()
            if category_matches(
                data.get('category', 'general'),
                selected_categories,
            )
        }
    else:
        filtered_topics = topics

    total_count = len(topics)
    filtered_count = len(filtered_topics)

    if selected_categories and filtered_count < total_count:
        st.markdown(
            f"### 📚 Темы ({filtered_count} из {total_count})"
        )
        st.caption(
            f"🏷️ Фильтр: {', '.join(selected_categories)}"
        )
    else:
        st.markdown(f"### 📚 Темы ({total_count})")

    if not filtered_topics:
        st.warning(
            f"⚠️ Нет тем для выбранных категорий: "
            f"{', '.join(selected_categories)}"
        )
        st.info(
            "Измените категории в боковой панели "
            "или добавьте материалы с нужными категориями"
        )

        existing_categories = sorted(set(
            data.get('category', 'general')
            for data in topics.values()
        ))
        if existing_categories:
            st.caption(
                f"Доступные категории в файлах: "
                f"{', '.join(existing_categories)}"
            )
        return

    # ── Таблица тем (с кэшированным парсингом) ──
    table_data = []
    for name, data in filtered_topics.items():
        cards = get_cards(data['path'])  # ← КЭШИРОВАНО

        type_counts = {}
        for card in cards:
            label = CARD_TYPE_LABELS.get(card.card_type, "?")
            type_counts[label] = type_counts.get(label, 0) + 1

        types_str = ", ".join(
            f"{count}× {label}" for label, count in type_counts.items()
        )

        # ── Быстрая проверка смешения форматов ──
        has_mix = len(type_counts) > 2
        mix_indicator = " ⚠️" if has_mix else ""

        table_data.append({
            "Тема": name + mix_indicator,
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

    total_cards = sum(row["Карточек"] for row in table_data)
    categories_in_view = sorted(set(
        row["Категория"] for row in table_data
    ))

    col1, col2, col3 = st.columns(3)
    col1.metric("Тем", filtered_count)
    col2.metric("Карточек", total_cards)
    col3.metric("Категорий", len(categories_in_view))

    st.divider()

    # ── Поиск (с кэшированным парсингом) ──
    st.markdown("### 🔎 Поиск по карточкам")

    search_col1, search_col2 = st.columns([3, 1])
    with search_col1:
        search_query = st.text_input(
            "Поиск",
            key="search_query",
            placeholder="Введите текст для поиска...",
            label_visibility="collapsed",
        )
    with search_col2:
        search_in = st.selectbox(
            "Искать в",
            ["Везде", "Вопросах", "Ответах"],
            key="search_scope",
            label_visibility="collapsed",
        )

    if search_query and search_query.strip():
        all_cards_for_search = []
        for name, data in filtered_topics.items():
            cards = get_cards(data['path'])  # ← КЭШИРОВАНО
            all_cards_for_search.extend(cards)

        search_in_q = search_in in ("Везде", "Вопросах")
        search_in_a = search_in in ("Везде", "Ответах")

        found_cards = search_cards(
            all_cards_for_search,
            search_query,
            search_in_answers=search_in_a,
            search_in_questions=search_in_q,
        )

        if found_cards:
            st.success(f"Найдено: {len(found_cards)} карточек")

            # ── Пагинация результатов поиска ──
            show_count = min(len(found_cards), 20)
            for i, card in enumerate(found_cards[:show_count]):
                emoji = CARD_TYPE_EMOJI.get(card.card_type, "⚪")
                title = card.question[:80] + (
                    "…" if len(card.question) > 80 else ""
                )
                with st.expander(
                        f"{emoji} {title} [{card.topic}]",
                        expanded=False,
                ):
                    col_f, col_b = st.columns(2)
                    with col_f:
                        st.markdown("**Front:**")
                        st.markdown(f"> {card.question[:500]}")
                    with col_b:
                        st.markdown("**Back:**")
                        st.markdown(f"> {card.answer[:500]}")
                    st.caption(
                        f"Тема: {card.topic} · "
                        f"Тип: {CARD_TYPE_LABELS.get(card.card_type, '?')}"
                    )

            if len(found_cards) > show_count:
                st.info(
                    f"Показано {show_count} из "
                    f"{len(found_cards)} результатов"
                )
        else:
            st.info(
                f"Ничего не найдено по запросу: «{search_query}»"
            )

        st.divider()

    # ── Предпросмотр темы ──
    st.markdown("### 🔍 Предпросмотр темы")

    topic_names = list(filtered_topics.keys())

    current_preview = st.session_state.get('preview_topic', '')
    default_idx = 0
    if current_preview in topic_names:
        default_idx = topic_names.index(current_preview)

    selected_topic = st.selectbox(
        "Выберите тему",
        topic_names,
        index=default_idx,
        key="preview_topic_select",
    )

    if not selected_topic:
        return

    topic_data = filtered_topics[selected_topic]

    col_cat, col_deck = st.columns(2)
    col_cat.caption(
        f"🏷️ Категория: **{topic_data.get('category', 'general')}**"
    )
    col_deck.caption(
        f"📦 Колода: **{topic_data.get('deck_name', '')}**"
    )

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

    # ── Карточки (кэшировано) ──
    cards = get_cards(topic_data['path'])

    if not cards:
        st.warning("Карточки не найдены в этой теме")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Всего карточек", len(cards))

    unique_types = set(c.card_type for c in cards)
    col2.metric("Типов", len(unique_types))

    reverse_count = sum(1 for c in cards if c.is_reverse)
    col3.metric("Обратных", reverse_count)

    # ── Предупреждение о смешении форматов (inline) ──
    if len(unique_types) > 2:
        st.warning(
            f"⚠️ В этой теме используется {len(unique_types)} "
            f"типов карточек. "
            f"Это может привести к неожиданному парсингу. "
            f"Рекомендуется использовать 1-2 типа на файл. "
            f"Подробности — на вкладке **🔍 Валидация**."
        )

    all_types_in_topic = sorted(
        set(c.card_type for c in cards),
        key=lambda t: t.value,
    )

    if len(all_types_in_topic) > 1:
        type_filter = st.multiselect(
            "Фильтр по типу карточки",
            options=all_types_in_topic,
            default=all_types_in_topic,
            format_func=lambda t: (
                f"{CARD_TYPE_EMOJI.get(t, '⚪')} "
                f"{CARD_TYPE_LABELS.get(t, t.value)}"
            ),
            key="card_type_filter",
        )
        if type_filter:
            cards = [c for c in cards if c.card_type in type_filter]

    # ── Пагинация карточек ──
    total_pages = max(1, (len(cards) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
    st.markdown(f"#### Карточки ({len(cards)})")

    if total_pages > 1:
        page = st.number_input(
            "Страница",
            min_value=1,
            max_value=total_pages,
            value=1,
            key="cards_page",
        )
        st.caption(
            f"Страница {page} из {total_pages} "
            f"(по {CARDS_PER_PAGE} на страницу)"
        )
    else:
        page = 1

    start_idx = (page - 1) * CARDS_PER_PAGE
    end_idx = start_idx + CARDS_PER_PAGE
    page_cards = cards[start_idx:end_idx]

    for i, card in enumerate(page_cards):
        card_num = start_idx + i
        emoji = CARD_TYPE_EMOJI.get(card.card_type, "⚪")
        label = CARD_TYPE_LABELS.get(
            card.card_type, card.card_type.value
        )

        title_text = card.question[:100]
        if len(card.question) > 100:
            title_text += "…"
        if card.is_reverse:
            title_text = f"↩️ {title_text}"

        with st.expander(
                f"{emoji} #{card_num + 1} {title_text}",
                expanded=(card_num == 0 and page == 1),
        ):
            meta_parts = [f"**Тип:** {label}"]
            if card.is_reverse:
                meta_parts.append("**Reverse:** Да")
            if card.tags:
                meta_parts.append(
                    f"**Теги:** {', '.join(card.tags[:5])}"
                )
            st.caption(" · ".join(meta_parts))

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

            if card.code_snippets:
                st.markdown(
                    f"**Код:** {len(card.code_snippets)} фрагмент(ов)"
                )


def render_tab_validation():
    """Вкладка валидации материалов."""
    input_dir = st.session_state.get('input_dir', '')

    st.markdown("### 🔍 Валидация материалов")
    st.caption(
        "Проверка файлов на ошибки формата, смешение типов карточек, "
        "и другие потенциальные проблемы"
    )

    if not os.path.exists(input_dir):
        st.info("📁 Укажите путь к папке с материалами в боковой панели")
        return

    if st.button("🔍 Запустить валидацию", type="primary", use_container_width=True):
        with st.spinner("Проверка файлов..."):
            reports = validate_all_files(input_dir)
            st.session_state['validation_reports'] = reports

    reports = st.session_state.get('validation_reports', None)
    if reports is None:
        st.info("Нажмите кнопку выше для запуска валидации")
        return

    if not reports:
        st.warning("Нет файлов для валидации")
        return

    # --- Сводка ---
    total_errors = sum(r.error_count for r in reports)
    total_warnings = sum(r.warning_count for r in reports)
    total_info = sum(r.info_count for r in reports)
    mixed_count = sum(1 for r in reports if r.has_mixed_formats)
    clean_count = sum(1 for r in reports if not r.issues)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📄 Файлов", len(reports))
    col2.metric("❌ Ошибок", total_errors)
    col3.metric("⚠️ Предупр.", total_warnings)
    col4.metric("🔀 Смешение", mixed_count)
    col5.metric("✅ Чистых", clean_count)

    if total_errors == 0 and total_warnings == 0:
        st.success("✅ Все файлы прошли проверку без ошибок и предупреждений!")
    elif total_errors > 0:
        st.error(
            f"Найдено {total_errors} ошибок в {sum(1 for r in reports if r.error_count > 0)} файлах. "
            f"Эти проблемы могут привести к потере карточек при генерации."
        )

    if mixed_count > 0:
        st.warning(
            f"⚠️ В {mixed_count} файлах обнаружено смешение форматов карточек. "
            f"Это может привести к неожиданному разбору — рекомендуется "
            f"использовать один формат на файл."
        )

    # --- Скачивание отчёта ---
    report_text = generate_validation_report_text(reports)
    st.download_button(
        "📥 Скачать отчёт валидации",
        data=report_text,
        file_name="validation_report.txt",
        mime="text/plain",
        use_container_width=True,
    )

    st.divider()

    # --- Фильтр отображения ---
    show_filter = st.radio(
        "Показать",
        ["Все файлы", "Только с проблемами", "Только со смешением форматов"],
        horizontal=True,
        key="validation_filter",
    )

    if show_filter == "Только с проблемами":
        display_reports = [r for r in reports if r.issues]
    elif show_filter == "Только со смешением форматов":
        display_reports = [r for r in reports if r.has_mixed_formats]
    else:
        display_reports = reports

    if not display_reports:
        st.info("Нет файлов, соответствующих фильтру")
        return

    # --- Детали по каждому файлу ---
    for report in display_reports:
        # Иконка статуса
        if report.error_count > 0:
            status_icon = "❌"
        elif report.has_mixed_formats:
            status_icon = "🔀"
        elif report.warning_count > 0:
            status_icon = "⚠️"
        else:
            status_icon = "✅"

        issue_summary = []
        if report.error_count:
            issue_summary.append(f"{report.error_count} ош.")
        if report.warning_count:
            issue_summary.append(f"{report.warning_count} пред.")
        if report.info_count:
            issue_summary.append(f"{report.info_count} инфо")
        summary_str = f" ({', '.join(issue_summary)})" if issue_summary else ""

        with st.expander(
                f"{status_icon} **{report.topic_name}** — "
                f"{report.total_cards_found} карточек{summary_str}",
                expanded=(report.error_count > 0 or report.has_mixed_formats),
        ):
            # Обнаруженные форматы
            if report.detected_formats:
                st.markdown("**Обнаруженные форматы:**")
                format_labels = {
                    'single_line_basic': '🟢 Basic (::)',
                    'single_line_bidirectional': '🔵 Bidirectional (:::)',
                    'multi_line_basic_sep': '🟡 Multi-line (?)',
                    'multi_line_bidirectional_sep': '🟣 Multi-line (??)',
                    'cloze': '🟠 Cloze (==…==)',
                    'legacy_question': '📋 Legacy (### Вопрос:)',
                }
                for fmt, line_nums in report.detected_formats.items():
                    label = format_labels.get(fmt, fmt)
                    lines_preview = ", ".join(str(ln) for ln in line_nums[:8])
                    extra = f" ...+{len(line_nums) - 8}" if len(line_nums) > 8 else ""
                    st.caption(f"  {label} — строки: {lines_preview}{extra}")

            if report.has_mixed_formats:
                st.warning("🔀 **Смешение форматов обнаружено!**")

            # Проблемы
            if report.issues:
                st.markdown("**Проблемы:**")
                for issue in report.issues:
                    icon = SEVERITY_EMOJI.get(issue.severity, '•')

                    # Цветовое оформление по severity
                    if issue.severity == 'error':
                        st.error(
                            f"{icon} **Строка {issue.line_number}:** {issue.message}"
                        )
                    elif issue.severity == 'warning':
                        st.warning(
                            f"{icon} **Строка {issue.line_number}:** {issue.message}"
                        )
                    else:
                        st.info(
                            f"{icon} **Строка {issue.line_number}:** {issue.message}"
                        )

                    # Показать проблемную строку
                    if issue.line_text:
                        display_text = issue.line_text[:150]
                        if len(issue.line_text) > 150:
                            display_text += "…"
                        st.code(display_text, language="markdown")

                    # Совет
                    if issue.suggestion:
                        st.caption(f"💡 {issue.suggestion}")
            else:
                st.success("✅ Проблем не обнаружено")

    # ── Кросс-файловые дубликаты ──
    st.divider()
    st.markdown("### 🔍 Дубликаты между файлами")
    st.caption(
        "Поиск одинаковых вопросов в разных файлах. "
        "Помогает найти и удалить лишние карточки из исходных материалов."
    )

    if st.button(
            "🔍 Найти дубликаты",
            use_container_width=True,
            key="find_duplicates_btn",
    ):
        dir_mtime = _get_dir_mtime(input_dir)
        duplicates = _cached_find_duplicates(input_dir, dir_mtime)
        st.session_state['found_duplicates'] = duplicates

    duplicates = st.session_state.get('found_duplicates', None)

    if duplicates is None:
        st.info(
            "Нажмите кнопку выше для поиска дубликатов "
            "между файлами"
        )
    elif not duplicates:
        st.success("✅ Дубликатов не найдено!")
    else:
        cross_file = [d for d in duplicates if d.is_cross_file]
        same_file = [d for d in duplicates if not d.is_cross_file]

        col1, col2, col3 = st.columns(3)
        col1.metric("🔄 Всего", len(duplicates))
        col2.metric("📁 Между файлами", len(cross_file))
        col3.metric("📄 Внутри файлов", len(same_file))

        # Кнопка скачивания отчёта
        dup_report = generate_duplicate_report_text(duplicates)
        st.download_button(
            "📥 Скачать отчёт о дубликатах",
            data=dup_report,
            file_name="duplicates_report.txt",
            mime="text/plain",
            use_container_width=True,
        )

        if cross_file:
            st.markdown("#### 📁 Дубликаты между файлами")
            st.caption(
                "Один и тот же вопрос найден в разных файлах. "
                "Удалите лишнюю карточку из одного из файлов."
            )

            for i, dup in enumerate(cross_file):
                q_short = dup.question_preview[:80]
                ellipsis = "…" if len(dup.question_preview) > 80 else ""

                with st.expander(
                        f"🔄 {q_short}{ellipsis}",
                        expanded=(i < 3),
                ):
                    col_orig, col_dup = st.columns(2)

                    with col_orig:
                        st.markdown("**📗 Оригинал:**")
                        st.markdown(f"**Тема:** `{dup.original_topic}`")
                        orig_name = Path(dup.original_source).name
                        st.caption(f"Файл: `{orig_name}`")

                    with col_dup:
                        st.markdown("**📕 Дубликат:**")
                        st.markdown(f"**Тема:** `{dup.duplicate_topic}`")
                        dup_name = Path(dup.duplicate_source).name
                        st.caption(f"Файл: `{dup_name}`")

                    st.markdown("**Вопрос:**")
                    st.markdown(f"> {dup.question_preview}")

                    if dup.answer_preview:
                        st.markdown("**Ответ (начало):**")
                        st.caption(f"{dup.answer_preview}…")

                    if dup.card_type:
                        type_labels = {
                            'single_line_basic': 'Basic (::)',
                            'single_line_bidirectional': 'Bidirectional (:::)',
                            'multi_line_basic': 'Multi-line (?)',
                            'multi_line_bidirectional': 'Multi-line (??)',
                            'cloze': 'Cloze (==…==)',
                        }
                        label = type_labels.get(
                            dup.card_type, dup.card_type
                        )
                        st.caption(f"Тип: {label}")

        if same_file:
            st.markdown("#### 📄 Дубликаты внутри файлов")
            st.caption(
                "Одинаковые вопросы внутри одного файла."
            )

            for dup in same_file:
                q_short = dup.question_preview[:60]
                ellipsis = "…" if len(dup.question_preview) > 60 else ""
                st.warning(
                    f"🔄 **{dup.duplicate_topic}**: "
                    f"'{q_short}{ellipsis}'"
                )


def render_tab_statistics():
    input_dir = st.session_state.get('input_dir', '')

    st.markdown("### 📊 Статистика")

    if not os.path.exists(input_dir):
        st.info("📁 Укажите путь к папке с материалами")
        return

    # ── Кэшированная статистика ──
    dir_mtime = _get_dir_mtime(input_dir)
    stats = _cached_statistics(input_dir, dir_mtime)

    if stats['total_files'] == 0:
        st.warning("Нет файлов для анализа")
        return

    # ── Остальной код без изменений ──
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📄 Файлов", stats['total_files'])
    col2.metric("📝 Карточек", stats['total_cards'])
    col3.metric("🟠 Cloze", stats['cloze_count'])
    col4.metric("💻 С кодом", stats['code_count'])

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("↔️ Reverse", stats['reverse_count'])
    col6.metric("📦 Колод", len(stats['by_deck']))
    col7.metric("Ø вопрос", f"{stats['avg_question_length']:.0f} сим.")
    col8.metric("Ø ответ", f"{stats['avg_answer_length']:.0f} сим.")

    st.divider()

    if stats['by_type']:
        st.markdown("#### По типам карточек")

        type_labels = {
            'single_line_basic': '🟢 Basic (::)',
            'single_line_bidirectional': '🔵 Bidirectional (:::)',
            'multi_line_basic': '🟡 Multi-line (?)',
            'multi_line_bidirectional': '🟣 Multi-line (??)',
            'cloze': '🟠 Cloze (==…==)',
        }

        type_data = []
        for type_name, count in sorted(
                stats['by_type'].items(), key=lambda x: -x[1]
        ):
            label = type_labels.get(type_name, type_name)
            pct = (
                (count / stats['total_cards'] * 100)
                if stats['total_cards'] else 0
            )
            type_data.append({
                "Тип": label,
                "Количество": count,
                "Доля": f"{pct:.1f}%",
            })

        st.dataframe(
            type_data, use_container_width=True, hide_index=True
        )

    if stats['by_deck']:
        st.markdown("#### По колодам")
        deck_data = []
        for deck, count in sorted(
                stats['by_deck'].items(), key=lambda x: -x[1]
        ):
            deck_data.append({"Колода": deck, "Карточек": count})
        st.dataframe(
            deck_data, use_container_width=True, hide_index=True
        )

    if stats['by_category']:
        st.markdown("#### По категориям")
        cat_data = []
        for cat, count in sorted(
                stats['by_category'].items(), key=lambda x: -x[1]
        ):
            cat_data.append({"Категория": cat, "Карточек": count})
        st.dataframe(
            cat_data, use_container_width=True, hide_index=True
        )

    if stats['by_file']:
        with st.expander("📄 Детали по файлам", expanded=False):
            file_data = []
            for name, info in sorted(stats['by_file'].items()):
                types_str = ", ".join(
                    f"{v}×{k}" for k, v in info['types'].items()
                )
                file_data.append({
                    "Файл": name,
                    "Категория": info['category'],
                    "Колода": info['deck'],
                    "Карточек": info['cards'],
                    "Типы": types_str,
                })
            st.dataframe(
                file_data, use_container_width=True, hide_index=True
            )

    st.markdown("#### 🏆 Рекорды")
    q_text, q_len = stats['longest_question']
    a_text, a_len = stats['longest_answer']
    st.caption(
        f"Самый длинный вопрос: {q_len} сим. — «{q_text}…»"
    )
    st.caption(
        f"Самый длинный ответ: {a_len} сим. — «{a_text}…»"
    )


def render_tab_generation(config, generator: CardGenerator):
    input_dir = st.session_state['input_dir']
    categories = st.session_state.get('selected_categories', [])
    export_fmt = st.session_state.get('export_format', 'both')
    use_reverse = st.session_state.get('use_reverse_cards', True)
    clean_dupes = st.session_state.get('clean_duplicates', False)

    topics_count = 0
    filtered_count = 0

    if os.path.exists(input_dir):
        topics = get_topics(input_dir)  # ← КЭШИРОВАНО
        topics_count = len(topics)

        if categories:
            filtered_count = sum(
                1 for data in topics.values()
                if category_matches(
                    data.get('category', 'general'), categories
                )
            )
        else:
            filtered_count = topics_count

    st.markdown("### 📋 Параметры генерации")

    col1, col2, col3, col4 = st.columns(4)

    if categories and filtered_count < topics_count:
        col1.metric(
            "📚 Тем к обработке",
            f"{filtered_count}/{topics_count}",
        )
    else:
        col1.metric("📚 Тем", topics_count)

    col2.metric("🏷️ Категорий", len(categories) or "Все")

    fmt_label = EXPORT_FORMATS.get(export_fmt, export_fmt)
    fmt_short = (
        fmt_label.split(' ', 1)[1] if ' ' in fmt_label else fmt_label
    )
    col3.metric("📤 Формат", fmt_short)

    col4.metric(
        "↔️ Reverse",
        "Да" if use_reverse else "Нет",
    )

    if categories and filtered_count == 0 and topics_count > 0:
        st.error(
            f"❌ Нет тем для выбранных категорий: "
            f"{', '.join(categories)}. "
            f"Измените фильтр в боковой панели."
        )

        existing_cats = sorted(set(
            data.get('category', 'general')
            for data in topics.values()
        ))
        if existing_cats:
            st.info(
                f"Категории в файлах: {', '.join(existing_cats)}"
            )

    # --- Предварительная проверка валидации ---
    if os.path.exists(input_dir) and filtered_count > 0:
        validation_reports = st.session_state.get('validation_reports', None)
        if validation_reports:
            total_errors = sum(r.error_count for r in validation_reports)
            mixed_count = sum(1 for r in validation_reports if r.has_mixed_formats)

            if total_errors > 0 or mixed_count > 0:
                warning_parts = []
                if total_errors > 0:
                    warning_parts.append(f"{total_errors} ошибок в материалах")
                if mixed_count > 0:
                    warning_parts.append(f"{mixed_count} файлов со смешением форматов")

                st.warning(
                    f"⚠️ Обнаружены проблемы: {', '.join(warning_parts)}. "
                    f"Рекомендуется исправить перед генерацией. "
                    f"См. вкладку **🔍 Валидация**."
                )

    options_parts = []
    if use_reverse:
        options_parts.append("↔️ Reverse карточки")
    if clean_dupes:
        options_parts.append("🧹 Дедупликация")
    if categories:
        options_parts.append(
            f"🏷️ {', '.join(categories)}"
        )
    if options_parts:
        st.caption(f"Опции: {' · '.join(options_parts)}")

    st.divider()

    can_generate = (
            os.path.exists(input_dir) and filtered_count > 0
    )

    if not os.path.exists(input_dir):
        st.warning(
            "❌ Папка с материалами не найдена. "
            "Укажите корректный путь в боковой панели."
        )
    elif filtered_count == 0:
        st.warning(
            "❌ Нет тем для генерации. "
            "Проверьте фильтр категорий."
        )

    generate_clicked = st.button(
        "🚩 Начать генерацию",
        type="primary",
        use_container_width=True,
        disabled=not can_generate,
    )

    if generate_clicked:
        reset_generation_state()

        settings = collect_current_settings()
        if settings.auto_save:
            settings.save()

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

            # ── Сбрасываем кэш — файлы изменились ──
            invalidate_caches()

            st.session_state['last_result'] = result
            st.session_state['generated_files'] = result.output_files
            st.session_state['generation_complete'] = True

        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"❌ Произошла ошибка: {e}")
            with st.expander("🔍 Трассировка ошибки"):
                st.code(traceback.format_exc())

    result = st.session_state.get('last_result')
    if not result:
        return

    st.divider()
    _display_generation_result(result)


def _display_generation_result(result: GenerationResult):
    if result.success:
        st.success("### ✅ Генерация завершена!")

        col1, col2, col3 = st.columns(3)
        col1.metric("📝 Карточек", result.total_cards)
        col2.metric("📚 Тем", result.topics_processed)
        col3.metric("📄 Файлов", len(result.output_files))

        if result.cards_by_type:
            with st.expander("📊 По типам карточек", expanded=False):
                for type_name, count in sorted(
                        result.cards_by_type.items()
                ):
                    label = type_name.replace('_', ' ').title()
                    st.caption(f"• **{label}:** {count}")

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

    if result.warnings:
        with st.expander(
                f"⚠️ Предупреждения ({len(result.warnings)})",
                expanded=False,
        ):
            for w in result.warnings:
                st.warning(w)

    # ── Найденные дубликаты ──
    if result.duplicates_found:
        with st.expander(
                f"🔄 Найденные дубликаты в материалах "
                f"({len(result.duplicates_found)})",
                expanded=False,
        ):
            st.caption(
                "Эти карточки дублируются в исходных материалах. "
                "Рекомендуется удалить лишние вручную."
            )

            cross = [
                d for d in result.duplicates_found
                if d.is_cross_file
            ]
            same = [
                d for d in result.duplicates_found
                if not d.is_cross_file
            ]

            if cross:
                st.markdown("**Между файлами:**")
                for dup in cross:
                    st.warning(
                        f"📁 '{dup.question_preview[:60]}…' — "
                        f"в **{dup.original_topic}** и "
                        f"**{dup.duplicate_topic}**"
                    )

            if same:
                st.markdown("**Внутри файлов:**")
                for dup in same:
                    st.info(
                        f"📄 '{dup.question_preview[:60]}…' — "
                        f"повторяется в **{dup.duplicate_topic}**"
                    )

    if result.duplicates_removed:
        with st.expander(
                f"🧹 Удалённые дубликаты из выходных файлов "
                f"({len(result.duplicates_removed)})",
                expanded=False,
        ):
            st.caption(
                "Эти карточки были удалены из сгенерированных файлов "
                "при дедупликации."
            )

            for dup in result.duplicates_removed:
                st.caption(
                    f"• '{dup.question_preview[:70]}…' "
                    f"(из {dup.duplicate_source})"
                )


def render_tab_files(generator: CardGenerator):
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

    obsidian_files = [f for f in existing_files if f.endswith('.md')]
    anki_basic = [
        f for f in existing_files
        if '_anki_basic' in os.path.basename(f)
    ]
    anki_reversed = [
        f for f in existing_files
        if '_anki_reversed' in os.path.basename(f)
    ]
    anki_cloze = [
        f for f in existing_files
        if '_anki_cloze' in os.path.basename(f)
    ]

    grouped = set(obsidian_files + anki_basic + anki_reversed + anki_cloze)
    other_files = [f for f in existing_files if f not in grouped]

    file_idx = 0

    if obsidian_files:
        st.markdown("#### 📝 Obsidian Spaced Repetition")
        for fp in obsidian_files:
            _render_file_row(fp, file_idx, "obs")
            file_idx += 1

    if anki_basic:
        st.markdown("#### 🟢 Anki — Basic (:: и ?)")
        st.caption("Note type: Basic · одно направление")
        for fp in anki_basic:
            _render_file_row(fp, file_idx, "basic")
            file_idx += 1

    if anki_reversed:
        st.markdown("#### 🔵 Anki — Bidirectional (::: и ??)")
        st.caption(
            "Note type: Basic (and reversed card) · "
            "Anki создаёт оба направления автоматически"
        )
        for fp in anki_reversed:
            _render_file_row(fp, file_idx, "rev")
            file_idx += 1

    if anki_cloze:
        st.markdown("#### 🟠 Anki — Cloze (==…==)")
        st.caption(
            "Note type: Cloze · "
            "==text== → {{c1::text}}"
        )
        for fp in anki_cloze:
            _render_file_row(fp, file_idx, "cloze")
            file_idx += 1

    if other_files:
        st.markdown("#### 📄 Другие файлы")
        for fp in other_files:
            _render_file_row(fp, file_idx, "other")
            file_idx += 1

    st.divider()

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


def render_tab_settings(config):
    st.markdown("### ⚙️ Конфигурация")

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

    # ──────────────────────────────────────
    #  Управление категориями
    # ──────────────────────────────────────
    with st.expander("🏷️ Управление категориями", expanded=True):
        _render_category_manager(config)

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

    with st.expander("🃏 Импорт в Anki"):
        st.markdown("""
        **Как импортировать в Anki:**

        1. Откройте Anki → **File → Import...**
        2. Выберите нужный файл
        3. Anki автоматически определит формат из заголовков
        4. Нажмите **Import**

        **Типы файлов (соответствуют Obsidian SR):**

        | Файл | Note Type | Исходный формат |
        |------|-----------|-----------------|
        | `*_anki_basic.txt` | Basic | `::` и `?` |
        | `*_anki_reversed.txt` | Basic (and reversed card) | `:::` и `??` |
        | `*_anki_cloze.txt` | Cloze | `==text==` |

        **Особенности:**
        - **Basic** — одно направление: Front → Back
        - **Reversed** — Anki автоматически создаёт оба направления
        - **Cloze** — конвертация `==text==` → `{{c1::text}}`
        - Теги и колоды проставляются автоматически
        - При повторном импорте дубликаты обновляются
        """)

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
        - 🔍 Валидация форматов с указанием проблемных строк
        - 🔎 Поиск по карточкам
        - 📊 Детальная статистика
        """)

        st.caption(
            f"Файл настроек: `{UserSettings.get_settings_path()}`"
        )
        st.caption(f"Конфигурация: `{config._config_path}`")


def _render_category_manager(config):
    """Виджет управления категориями."""

    # Текущие категории
    if config.categories:
        st.markdown("**Текущие категории:**")

        categories_to_remove = []

        for i, cat in enumerate(config.categories):
            col_name, col_rename, col_del = st.columns([3, 1, 1])

            with col_name:
                cat_path = config.get_materials_path(cat)
                exists = os.path.exists(cat_path)
                icon = "📁" if exists else "📂"
                st.markdown(f"{icon} `{cat}`")

            with col_rename:
                if st.button(
                        "✏️",
                        key=f"rename_cat_{i}",
                        help=f"Переименовать '{cat}'",
                ):
                    st.session_state['renaming_category'] = cat

            with col_del:
                if st.button(
                        "🗑️",
                        key=f"del_cat_{i}",
                        help=f"Удалить '{cat}'",
                ):
                    categories_to_remove.append(cat)

        for cat in categories_to_remove:
            if config.remove_category(cat):
                # Помечаем категорию для удаления из фильтра
                # (будет применено при следующем рендере)
                st.session_state['_categories_to_remove'] = (
                        st.session_state.get('_categories_to_remove', []) + [cat]
                )
                st.toast(f"🗑️ Категория '{cat}' удалена")
                st.rerun()
    else:
        st.info("Нет категорий. Добавьте первую категорию ниже.")

    # Переименование
    renaming = st.session_state.get('renaming_category', None)
    if renaming:
        st.markdown(f"**Переименовать `{renaming}`:**")
        col_input, col_ok, col_cancel = st.columns([3, 1, 1])

        with col_input:
            new_name = st.text_input(
                "Новое имя",
                value=renaming,
                key="rename_input",
                label_visibility="collapsed",
            )

        with col_ok:
            if st.button("✅", key="rename_ok", help="Применить"):
                if config.rename_category(renaming, new_name):
                    # Нормализация с поддержкой /
                    parts = new_name.strip().split('/')
                    norm_parts = []
                    for part in parts:
                        p = part.strip().lower().replace(' ', '_')
                        p = re.sub(
                            r'[^a-zA-Z0-9_а-яА-ЯёЁ-]', '', p
                        )
                        if p:
                            norm_parts.append(p)
                    normalized = '/'.join(norm_parts)
                    # Помечаем переименование для применения
                    st.session_state['_category_rename'] = (renaming, normalized)
                    st.session_state['renaming_category'] = None
                    st.toast(f"✅ Переименовано: {renaming} → {new_name}")
                    st.rerun()
                else:
                    st.error("Не удалось переименовать")

        with col_cancel:
            if st.button("❌", key="rename_cancel", help="Отмена"):
                st.session_state['renaming_category'] = None
                st.rerun()

    st.divider()

    # ──────────────────────────────────────
    #  Добавление новой категории
    # ──────────────────────────────────────
    st.markdown("**Добавить категорию:**")

    # Флаг для очистки поля после успешного добавления
    if st.session_state.get('_clear_new_category', False):
        st.session_state['_clear_new_category'] = False
        default_value = ""
    else:
        default_value = st.session_state.get('_new_category_value', '')

    col_input, col_add = st.columns([4, 1])

    with col_input:
        new_category = st.text_input(
            "Имя новой категории",
            value=default_value,
            key="new_category_input",
            placeholder="например: python, java, system_design...",
            label_visibility="collapsed",
        )

    # Сохраняем введённое значение
    st.session_state['_new_category_value'] = new_category

    # Валидация в реальном времени
    if new_category and new_category.strip():
        is_valid, normalized, error_msg = config.validate_category_name(
            new_category
        )

        if normalized and normalized != new_category.strip():
            st.caption(f"Будет сохранено как: `{normalized}`")

        if not is_valid:
            st.caption(f"⚠️ {error_msg}")

    with col_add:
        add_clicked = st.button(
            "➕",
            key="add_category_btn",
            help="Добавить категорию",
            use_container_width=True,
        )

    if add_clicked and new_category and new_category.strip():
        is_valid, normalized, error_msg = config.validate_category_name(
            new_category
        )

        if is_valid:
            if config.add_category(normalized):
                st.toast(f"✅ Категория '{normalized}' добавлена!")

                st.session_state['_clear_new_category'] = True
                st.session_state['_new_category_value'] = ''

                st.rerun()
            else:
                st.error("Не удалось добавить категорию")
        else:
            st.error(f"❌ {error_msg}")

    # Подсказки
    with st.popover("💡 Правила именования"):
        st.markdown("""
        **Допустимые символы:**
        - Латинские буквы: `a-z`
        - Кириллица: `а-я`
        - Цифры: `0-9`
        - Подчёркивание: `_`
        - Дефис: `-`
        - Слэш: `/` (разделитель подкатегорий)

        **Примеры:**
        - `python` — категория
        - `java/core` — категория/подкатегория
        - `system_design` — категория
        - `java/core/advanced` — многоуровневая

        **Как работает иерархия:**
        - Выбор `java` включает `java`, `java/core`, `java/core`
        - Выбор `java/core` включает только `java/core`
        - Файлы размещаются в `Materials/java/core/`
        - Anki колода: `Interview::Java::Core`
        - Obsidian тег: `#flashcards/java/core`

        **Автоматически:**
        - Пробелы → `_`
        - Приведение к нижнему регистру
        - Удаление спецсимволов
        """)


def main():
    st.set_page_config(
        page_title="Interview Cards",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
        .stTabs [data-baseweb="tab-list"] { gap: 4px; }
        .stTabs [data-baseweb="tab"] {
            padding: 8px 16px;
            font-size: 0.95em;
        }
    </style>
    """, unsafe_allow_html=True)

    config = get_config()
    config.create_directories()

    settings = UserSettings.load()
    settings.merge_with_config(config)

    generator = CardGenerator(config)

    init_session_state(config, settings)

    st.title("📚 Interview Cards")
    st.caption(
        "Генератор карточек Spaced Repetition из Markdown"
    )

    render_sidebar(config, generator)

    tab_topics, tab_validation, tab_stats, tab_gen, tab_files, tab_settings = st.tabs([
        "📋 Темы",
        "🔍 Валидация",
        "📊 Статистика",
        "🚀 Генерация",
        "📂 Файлы",
        "⚙️ Настройки",
    ])

    with tab_topics:
        render_tab_topics(generator)

    with tab_validation:
        render_tab_validation()

    with tab_stats:
        render_tab_statistics()

    with tab_gen:
        render_tab_generation(config, generator)

    with tab_files:
        render_tab_files(generator)

    with tab_settings:
        render_tab_settings(config)


if __name__ == '__main__':
    main()
