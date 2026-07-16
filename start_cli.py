"""
CLI версия генератора карточек InterviewCards.
Поддерживает аргументы командной строки и интерактивный режим.

Примеры:
  python start_cli.py
  python start_cli.py -c java_epam
  python start_cli.py -c java_epam,java8 -f obsidian --clean
  python start_cli.py --save-config -c java_epam
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

from config.Config import get_config
from logic.core import CardGenerator, GenerationResult

EXPORT_FORMAT_OPTIONS = {
    '1': ('both', 'Оба формата (Obsidian + Anki)'),
    '2': ('obsidian', 'Только Obsidian SR'),
    '3': ('anki', 'Только Anki'),
}

FORMAT_ALIASES = {
    'both': 'both',
    'obsidian': 'obsidian',
    'anki': 'anki',
}

SETTINGS_FILE = '.interview_cards_settings.json'


def load_settings() -> Dict:
    settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILE)
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_settings(settings: Dict) -> None:
    settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILE)
    try:
        allowed_keys = {
            'input_dir', 'selected_categories', 'export_format',
            'use_reverse_cards', 'clean_duplicates', 'last_input_dir',
        }
        filtered = {k: v for k, v in settings.items() if k in allowed_keys}
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(filtered, f, indent=2, ensure_ascii=False)
        print(f"✅ Настройки сохранены в {SETTINGS_FILE}")
    except Exception as e:
        print(f"⚠️ Не удалось сохранить настройки: {e}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Interview Cards — генератор карточек для подготовки к интервью',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Примеры:\n'
            '  %(prog)s                          # интерактивный режим\n'
            '  %(prog)s -c java_epam             # без интерактива\n'
            '  %(prog)s -c java_epam -f obsidian # Obsidian + фильтр\n'
            '  %(prog)s -c java_epam --clean     # с удалением дубликатов\n'
            '  %(prog)s --save-config -c java_epam\n'
        ),
    )
    parser.add_argument(
        '-i', '--input-dir',
        help='Путь к папке с Markdown файлами',
        default=None,
    )
    parser.add_argument(
        '-c', '--category',
        help='Категории через запятую. Без флага — интерактивный ввод.',
        nargs='?',
        const='__all__',
        default='__interactive__',
    )
    parser.add_argument(
        '-f', '--format',
        choices=['both', 'obsidian', 'anki'],
        help='Формат экспорта',
        default='both',
    )
    parser.add_argument(
        '--clean', '-d',
        action='store_true',
        help='Удалить дубликаты',
    )
    parser.add_argument(
        '--no-reverse',
        action='store_true',
        help='Отключить обратные карточки',
    )
    parser.add_argument(
        '--save-config',
        action='store_true',
        help='Сохранить параметры как настройки по умолчанию',
    )
    parser.add_argument(
        '--init',
        action='store_true',
        help='Создать директории и выйти',
    )
    return parser


def print_header():
    print("=" * 60)
    print("📚 Interview Cards — Генератор карточек v2.0")
    print("=" * 60)


def print_result(result: GenerationResult):
    print("-" * 60)
    if result.success:
        print(f"🏁 Готово! Сгенерировано {result.total_cards} карточек")
        print(f"   Тем обработано: {result.topics_processed}")
        print(f"   Файлов создано: {len(result.output_files)}")
        if result.cards_by_type:
            print("\n📊 По типам:")
            for type_name, count in sorted(result.cards_by_type.items()):
                label = type_name.replace('_', ' ').title()
                print(f"   • {label}: {count}")
        if result.cards_by_category:
            print("\n📊 По категориям:")
            for cat, count in sorted(result.cards_by_category.items()):
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
    paths = generator.get_output_paths()
    print(f"\n📂 Результаты:")
    print(f"   Obsidian карточки: {paths['cards']}")
    print(f"   Obsidian материалы: {paths['materials']}")
    print(f"   Anki файлы: {paths['anki']}")


def get_user_input(config, default_categories: Optional[List[str]] = None) -> tuple:
    default_path = config.materials_source
    input_dir = input(
        f"\nПуть к папке с Markdown темами "
        f"(по умолчанию: {default_path}): "
    ).strip()
    if not input_dir:
        input_dir = default_path

    cat_hint = ""
    if default_categories:
        cat_hint = f" (по умолчанию: {','.join(default_categories)})"
    categories_input = input(
        f"Задайте категории (через запятую, или 'all'){cat_hint}: "
    ).strip()
    if categories_input.lower() == 'all' or not categories_input:
        selected_categories = default_categories if not categories_input else None
    else:
        selected_categories = [c.strip() for c in categories_input.split(',') if c.strip()]

    print("\nФормат экспорта:")
    for key, (_, label) in EXPORT_FORMAT_OPTIONS.items():
        print(f"  {key}. {label}")
    fmt_input = input("Выберите (1-3, по умолчанию 1): ").strip()
    export_format = EXPORT_FORMAT_OPTIONS.get(fmt_input, ('both', ''))[0]

    clean_input = input("Удалить дубликаты? (y/n, по умолчанию n): ").strip().lower()
    clean_duplicates = clean_input == 'y'

    return input_dir, selected_categories, export_format, clean_duplicates


def handle_missing_directory(generator: CardGenerator, input_dir: str) -> bool:
    print(f"❌ Папка не найдена: {input_dir}")
    create = input("Создать папку с примером? (y/n): ").strip().lower()
    if create == 'y':
        try:
            tpl = os.path.join(os.path.dirname(__file__), "templates", "example_topic.md")
            example_path = generator.create_example_file(input_dir, tpl)
            print(f"✅ Пример создан: {example_path}")
            return True
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return False
    return False


def main():
    parser = build_parser()
    args = parser.parse_args()

    # --help уже обработан argparse, просто выходим
    # (ниже не выполняется при --help)

    saved_settings = load_settings()
    config = get_config()
    generator = CardGenerator(config)

    if args.init:
        config.create_directories()
        print("✅ Директории созданы")
        return

    # Определяем параметры
    input_dir = args.input_dir
    if not input_dir:
        input_dir = saved_settings.get('input_dir') or saved_settings.get('last_input_dir')
    if not input_dir:
        input_dir = config.materials_source

    saved_cats = saved_settings.get('selected_categories', None)

    # Категории
    if args.category == '__interactive__':
        selected_categories = None  # будет запрошено в интерактиве
    elif args.category == '__all__':
        selected_categories = None  # все темы
    else:
        selected_categories = [c.strip() for c in args.category.split(',') if c.strip()]

    export_format = args.format
    clean_duplicates = args.clean
    use_reverse_cards = not args.no_reverse

    # Сохранение конфигурации (до интерактива, чтобы сохранить переданные флаги)
    if args.save_config:
        new_settings = dict(saved_settings)
        new_settings['input_dir'] = input_dir
        new_settings['last_input_dir'] = input_dir
        if args.category not in ('__interactive__', '__all__', None):
            new_settings['selected_categories'] = [
                c.strip() for c in args.category.split(',') if c.strip()
            ]
        new_settings['export_format'] = export_format
        new_settings['use_reverse_cards'] = use_reverse_cards
        new_settings['clean_duplicates'] = clean_duplicates
        save_settings(new_settings)
        # Если передан только --save-config без флага -c, выходим без генерации
        if args.category == '__interactive__':
            print("💡 Запустите без --save-config для генерации карточек")
            return

    # Интерактивный режим
    if args.category == '__interactive__':
        print_header()
        if not os.path.exists(input_dir):
            if not handle_missing_directory(generator, input_dir):
                sys.exit(1)
        (input_dir, selected_categories, export_format, clean_duplicates) = \
            get_user_input(config, saved_cats)
    else:
        print_header()

    # Валидация
    if not os.path.exists(input_dir):
        print(f"❌ Папка не найдена: {input_dir}")
        sys.exit(1)

    is_valid, message = generator.validate_input_directory(input_dir)
    if not is_valid:
        print(f"❌ {message}")
        sys.exit(1)

    print(f"\n✅ {message}")
    if selected_categories:
        print(f"📂 Категории: {', '.join(selected_categories)}")
    else:
        print(f"📂 Категории: все")
    print(f"📤 Формат: {export_format}")
    print(f"🔁 Обратные карточки: {'да' if use_reverse_cards else 'нет'}")
    print(f"🧹 Чистка дубликатов: {'да' if clean_duplicates else 'нет'}")
    print("\n🚀 Начало генерации...\n")

    def on_progress(current, total, topic_name):
        pct = int(current / total * 100) if total > 0 else 0
        print(f"  [{pct:3d}%] {topic_name}")

    result = generator.generate(
        input_dir=input_dir,
        selected_categories=selected_categories,
        clean_duplicates=clean_duplicates,
        export_format=export_format,
        use_reverse_cards=use_reverse_cards,
        progress_callback=on_progress,
    )

    print_result(result)
    print_output_paths(generator)
    print("=" * 60)
    sys.exit(0 if result.success else 1)


if __name__ == '__main__':
    main()
