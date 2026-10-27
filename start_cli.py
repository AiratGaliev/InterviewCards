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
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from config.Config import get_config
from logic.core import CardGenerator, GenerationResult
from logic.utils import validate_all_files, generate_validation_report_text

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


def _write_json_report(path: Optional[str], payload: Dict) -> None:
    """Сохраняет машиночитаемый отчёт CI, если путь задан."""
    if not path:
        return
    report_path = Path(path).expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as report_file:
        json.dump(payload, report_file, ensure_ascii=False, indent=2)
        report_file.write("\n")
    print(f"JSON-отчёт: {report_path}")


def _build_markdown_validation_report(input_dir: str) -> tuple:
    """Проверяет Markdown-файлы и возвращает данные и текст отчёта."""
    root = Path(input_dir).expanduser()
    reports = validate_all_files(str(root)) if root.is_dir() else []
    errors = sum(report.error_count for report in reports)
    warnings = sum(report.warning_count for report in reports)
    infos = sum(report.info_count for report in reports)
    cards = sum(report.total_cards_found for report in reports)

    if not root.is_dir():
        errors += 1
        problem = f"Каталог не найден: {root}"
    elif not reports:
        errors += 1
        problem = f"В каталоге нет Markdown-файлов: {root}"
    else:
        problem = ""

    payload = {
        "kind": "markdown-validation",
        "directory": str(root.resolve()) if root.exists() else str(root),
        "files_checked": len(reports),
        "cards_found": cards,
        "error_count": errors,
        "warning_count": warnings,
        "info_count": infos,
        "reports": [
            {
                "file_path": report.file_path,
                "topic_name": report.topic_name,
                "is_valid": report.is_valid,
                "has_mixed_formats": report.has_mixed_formats,
                "total_cards_found": report.total_cards_found,
                "detected_formats": report.detected_formats,
                "issues": [asdict(issue) for issue in report.issues],
            }
            for report in reports
        ],
    }
    if problem:
        payload["errors"] = [problem]

    text_report = generate_validation_report_text(reports)
    if problem:
        text_report = f"{problem}\n\n{text_report}"
    return payload, text_report


def _build_anki_validation_report(input_dir: str) -> Dict:
    """Проверяет структуру экспортированных Anki TSV-файлов."""
    root = Path(input_dir).expanduser()
    payload = {
        "kind": "anki-export-validation",
        "directory": str(root.resolve()) if root.exists() else str(root),
        "files_checked": 0,
        "rows_checked": 0,
        "error_count": 0,
        "warning_count": 0,
        "files": [],
        "errors": [],
    }

    if not root.is_dir():
        payload["error_count"] = 1
        payload["errors"].append(f"Каталог Anki-экспорта не найден: {root}")
        return payload

    files = sorted(root.rglob("*.txt"))
    if not files:
        payload["error_count"] = 1
        payload["errors"].append(f"В каталоге нет Anki .txt-файлов: {root}")
        return payload

    allowed_note_types = {"Basic", "Basic (and reversed card)", "Cloze"}
    for file_path in files:
        item = {
            "file_path": str(file_path),
            "row_count": 0,
            "note_type": "",
            "deck": "",
            "errors": [],
        }
        try:
            lines = file_path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            item["errors"].append(f"Не удалось прочитать файл: {exc}")
            payload["error_count"] += len(item["errors"])
            payload["files"].append(item)
            continue

        headers = []
        data_lines = []
        data_started = False
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            if not data_started and line.startswith("#"):
                headers.append(line)
                continue
            data_started = True
            data_lines.append((line_number, line))

        header_values = {
            header.split(":", 1)[0]: header.split(":", 1)[1].strip()
            for header in headers
            if ":" in header
        }

        if header_values.get("#separator") != "tab":
            item["errors"].append("Отсутствует или неверен заголовок #separator:tab")
        if header_values.get("#html") != "true":
            item["errors"].append("Отсутствует или неверен заголовок #html:true")

        note_type = header_values.get("#notetype", "")
        item["note_type"] = note_type
        if note_type not in allowed_note_types:
            item["errors"].append(f"Неподдерживаемый или отсутствующий note type: {note_type!r}")

        deck = header_values.get("#deck", "")
        item["deck"] = deck
        if not deck:
            item["errors"].append("Отсутствует заголовок #deck")

        if header_values.get("#tags column") != "3":
            item["errors"].append("Отсутствует или неверен заголовок #tags column:3")

        if not data_lines:
            item["errors"].append("Файл не содержит строк карточек")

        for line_number, line in data_lines:
            fields = line.split("\t")
            if len(fields) != 3:
                item["errors"].append(
                    f"Строка {line_number}: ожидалось 3 TSV-поля, получено {len(fields)}"
                )
                continue

            front, back, _tags = fields
            item["row_count"] += 1
            if not front.strip():
                item["errors"].append(f"Строка {line_number}: пустое поле Front")
            if note_type != "Cloze" and not back.strip():
                item["errors"].append(f"Строка {line_number}: пустое поле Back")
            if note_type == "Cloze" and not re.search(r"\{\{c\d+::.+?\}\}", front):
                item["errors"].append(
                    f"Строка {line_number}: не найден маркер Cloze формата {{{{cN::...}}}}"
                )

        payload["rows_checked"] += item["row_count"]
        payload["error_count"] += len(item["errors"])
        payload["files"].append(item)

    payload["files_checked"] = len(files)
    return payload


def _clean_generated_output(generator: CardGenerator, config) -> Dict[str, int]:
    """Удаляет только артефакты, формируемые генератором, перед полной сборкой."""
    paths = generator.get_output_paths()
    cards_dir = Path(paths["cards"])
    anki_dir = Path(config.output) / "anki"
    removed_cards = 0
    removed_anki = 0

    if cards_dir.is_dir():
        for file_path in cards_dir.rglob("*_obsidian_cards.md"):
            if file_path.is_file():
                file_path.unlink()
                removed_cards += 1

    if anki_dir.is_dir():
        for file_path in anki_dir.rglob("*_anki_*.txt"):
            if file_path.is_file():
                file_path.unlink()
                removed_anki += 1

    return {"obsidian_cards": removed_cards, "anki_files": removed_anki}




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
    validation_group = parser.add_mutually_exclusive_group()
    validation_group.add_argument(
        '--validate',
        action='store_true',
        help='Проверить все Markdown-файлы в --input-dir и завершиться',
    )
    validation_group.add_argument(
        '--validate-anki-dir',
        metavar='PATH',
        help='Проверить все Anki .txt-файлы в каталоге и завершиться',
    )
    parser.add_argument(
        '--json-report',
        metavar='PATH',
        help='Записать результат --validate или --validate-anki-dir в JSON',
    )
    parser.add_argument(
        '--strict-warnings',
        action='store_true',
        help='Считать предупреждения ошибками при --validate',
    )
    parser.add_argument(
        '--clean-generated-output',
        action='store_true',
        help='Удалить старые файлы, созданные генератором, перед полной генерацией',
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

    # Определяем входной каталог до проверки, чтобы CI не запускал интерактивный режим.
    input_dir = args.input_dir
    if not input_dir:
        input_dir = saved_settings.get('input_dir') or saved_settings.get('last_input_dir')
    if not input_dir:
        input_dir = config.materials_source

    if args.validate_anki_dir:
        report = _build_anki_validation_report(args.validate_anki_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        _write_json_report(args.json_report, report)
        sys.exit(1 if report['error_count'] else 0)

    if args.validate:
        report, text_report = _build_markdown_validation_report(input_dir)
        print(text_report)
        print(
            f"\nИтого: файлов={report['files_checked']}, "
            f"карточек={report['cards_found']}, ошибок={report['error_count']}, "
            f"предупреждений={report['warning_count']}, информации={report['info_count']}"
        )
        _write_json_report(args.json_report, report)
        failed = report['error_count'] > 0 or (
            args.strict_warnings and report['warning_count'] > 0
        )
        sys.exit(1 if failed else 0)

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
    if args.clean_generated_output:
        removed = _clean_generated_output(generator, config)
        print(
            "🧹 Удалены старые артефакты: "
            f"Obsidian={removed['obsidian_cards']}, Anki={removed['anki_files']}"
        )

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
