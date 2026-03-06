# 📚 Interview Cards

Генератор карточек Spaced Repetition из Markdown для подготовки к техническим собеседованиям.

## 🚀 Возможности

- ✅ **Markdown формат**
- ✅ Парсинг карточек Spaced Repetition (#card)
- ✅ Генерация карточек Obsidian Spaced Repetition
- ✅ Генерация карточек Obsidian_to_Anki
- ✅ Генерация файлов для импорта в Anki
- ✅ Поддержка категорий и тегов
- ✅ Удаление дубликатов
- ✅ Разбиение на батчи для больших наборов
- ✅ Streamlit UI и CLI версии
- ✅ Форматирование Markdown в HTML
- ✅ Подсветка кода в карточках
- ✅ Frontmatter поддержка (YAML)

## 📋 Требования

- Python 3.10+
- Obsidian (опционально)
- Anki (опционально)

## 🔧 Установка

```bash
git clone <repository-url>
cd InterviewCards
pip install -r requirements.txt
```

## 📝 Использование

### Streamlit UI

```bash
streamlit run start.py
```

### CLI

```bash
python start_cli.py
```

## 📁 Структура проекта

```
InterviewCards/
├── config/
│   ├── __init__.py
│   └── Config.py          # Конфигурация (dataclass)
├── logic/
│   ├── __init__.py
│   ├── core.py            # Общая логика генерации
│   └── utils.py           # Утилиты парсинга и генерации
├── models/
│   ├── __init__.py
│   └── InterviewCard.py   # Модель карточки
├── templates/
│   └── example_topic.md   # Пример темы
├── config.ini             # Файл конфигурации
├── requirements.txt       # Зависимости
├── start.py               # Streamlit UI версия
└── start_cli.py           # CLI версия
```

## ⚙️ Конфигурация

Отредактируйте `config.ini` для настройки путей и параметров:

```ini
[main]
categories = python, javascript, java, sql, system_design
documents = ~/Documents/InterviewCards/
output = ~/Documents/InterviewCards/output/

[directories-linux]
obsidian_vault = ~/Documents/ObsidianVault/
anki_collection_media = ~/.local/share/Anki2/User 1/collection.media/

[limits]
max_questions_per_deck = 500
max_question_length = 2000
max_answer_length = 5000
```

## 📄 Формат Markdown

Карточки могут быть в нескольких форматах:

### Формат 1: Вопрос-Ответ

```markdown
### Вопрос: Что такое GIL в Python?

Ответ: **GIL** — это механизм в CPython...
```

### Формат 2: #card формат

```markdown
#card

Что такое GIL?
:::
GIL — это Global Interpreter Lock...
```

### Формат 3: Заголовок + контент

```markdown
# GIL в Python

---

**GIL** — это механизм в CPython...
```

## 📜 Лицензия

MIT License
