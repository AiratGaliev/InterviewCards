"""
CLI версия генератора карточек InterviewCards.
"""

import os
import sys

from config.Config import get_config
from logic.core import CardGenerator, GenerationResult

EXPORT_FORMAT_OPTIONS = {
    '1': ('both', 'Оба формата (Obsidian + Anki)'),
    '2': ('obsidian', 'Только Obsidian SR'),
    '3': ('anki', 'Только Anki'),
}


def print_header():
    """Выводит заголовок приложения."""
    print("=" * 60)
    print("📚 Interview Cards — Генератор карточек v2.0")
    print("=" * 60)


def print_result(result: GenerationResult):
    """Выводит результат генерации."""
    print("-" * 60)

    if result.success:
        print(f"🏁 Готово! Сгенерировано {result.total_cards} карточек")
        print(f"   Тем обработано: {result.topics_processed}")
        print(f"   Файлов создано: {len(result.output_files)}")

        if result.cards_by_type:
            print("\n📊 По типам:")
            for type_name, count in sorted(
                    result.cards_by_type.items()
            ):
                label = type_name.replace('_', ' ').title()
                print(f"   • {label}: {count}")

        if result.cards_by_category:
            print("\n📊 По категориям:")
            for cat, count in sorted(
                    result.cards_by_category.items()
            ):
                print(f"   • {cat}: {count}")

    else:
        print("❌ Генерация не удалась")
        for error in result.errors:
            print(f"   Ошибка: {error}")

    if result.warnings:
        print(f"\n⚠️ Предупреждения ({len(result.warnings)}):")
        for warning in result.warnings:
            print(f"   {warning}")


def print_output_paths(generator: CardGenerator):
    """Выводит пути вывода."""
    paths = generator.get_output_paths()
    print(f"\n📂 Результаты:")
    print(f"   Obsidian карточки: {paths['cards']}")
    print(f"   Obsidian материалы: {paths['materials']}")
    print(f"   Anki файлы: {paths['anki']}")


def get_user_input(config) -> tuple:
    """Получает ввод от пользователя."""
    # Путь
    default_path = config.materials_source
    input_dir = input(
        f"\nПуть к папке с Markdown темами "
        f"(по умолчанию: {default_path}): "
    ).strip()

    if not input_dir:
        input_dir = default_path

    # Категории
    print(f"\nДоступные категории: {', '.join(config.categories)}")
    categories_input = input(
        "Выберите категории (через запятую, или 'all'): "
    ).strip()

    if categories_input.lower() == 'all' or not categories_input:
        selected_categories = None
    else:
        selected_categories = [
            c.strip() for c in categories_input.split(',') if c.strip()
        ]

    # Формат экспорта
    print("\nФормат экспорта:")
    for key, (_, label) in EXPORT_FORMAT_OPTIONS.items():
        print(f"  {key}. {label}")
    fmt_input = input("Выберите (1-3, по умолчанию 1): ").strip()
    export_format = EXPORT_FORMAT_OPTIONS.get(
        fmt_input, ('both', '')
    )[0]

    # Дубликаты
    clean_input = input(
        "Удалить дубликаты? (y/n, по умолчанию n): "
    ).strip().lower()
    clean_duplicates = clean_input == 'y'

    return input_dir, selected_categories, export_format, clean_duplicates


def handle_missing_directory(
        generator: CardGenerator, input_dir: str
) -> bool:
    """Обрабатывает случай отсутствующей директории."""
    print(f"❌ Папка не найдена: {input_dir}")
    create = input(
        "Создать папку с примером? (y/n): "
    ).strip().lower()

    if create == 'y':
        try:
            tpl = os.path.join(
                os.path.dirname(__file__),
                "templates", "example_topic.md",
            )
            example_path = generator.create_example_file(
                input_dir, tpl
            )
            print(f"✅ Пример создан: {example_path}")
            return True
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return False

    return False


def main():
    """Главная функция CLI."""
    print_header()

    config = get_config()
    config.create_directories()
    generator = CardGenerator(config)

    # Ввод
    (input_dir, selected_categories,
     export_format, clean_duplicates) = get_user_input(config)

    # Проверка директории
    if not os.path.exists(input_dir):
        if not handle_missing_directory(generator, input_dir):
            sys.exit(1)

    # Валидация
    is_valid, message = generator.validate_input_directory(input_dir)
    if not is_valid:
        print(f"❌ {message}")
        sys.exit(1)

    print(f"\n✅ {message}")
    print(f"📤 Формат: {export_format}")
    print("\n🚀 Начало генерации...\n")

    # Генерация
    def on_progress(current, total, topic_name):
        pct = int(current / total * 100) if total > 0 else 0
        print(f"  [{pct:3d}%] {topic_name}")

    result = generator.generate(
        input_dir=input_dir,
        selected_categories=selected_categories,
        clean_duplicates=clean_duplicates,
        export_format=export_format,
        progress_callback=on_progress,
    )

    print_result(result)
    print_output_paths(generator)
    print("=" * 60)

    sys.exit(0 if result.success else 1)


if __name__ == '__main__':
    main()
