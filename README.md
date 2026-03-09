# 📚 Interview Cards

Генератор карточек Spaced Repetition из Markdown для подготовки к техническим собеседованиям.

## 🚀 Возможности

- ✅ **Markdown формат**
- ✅ Парсинг карточек Spaced Repetition (форматы `::`, `:::`, `?`, `??`, Cloze)
- ✅ Генерация карточек Obsidian Spaced Repetition
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

Отредактируйте `config.ini` для настройки путей и параметров.

## 📄 Поддерживаемые форматы карточек

### Формат 1: Single-line Basic (`::`)

```markdown
Вопрос::Ответ
```

### Формат 2: Single-line Bidirectional (`:::`)

```markdown
Термин:::Определение
```

Создаёт две карточки: Термин → Определение и Определение → Термин

### Формат 3: Multi-line Basic (`?`)

```markdown
Вопрос
?
Ответ на нескольких строках
с сохранением форматирования
```

### Формат 4: Multi-line Bidirectional (`??`)

```markdown
Информация 1
??
Информация 2
```

Создаёт две карточки в обоих направлениях

### Формат 5: Cloze Deletions (`==text==`)

```markdown
Python был создан ==Гвидо ван Россум== в ==1991== году.
```

#### Cloze с подсказками:

```markdown
Столица Франции — ==Париж==^[город любви]
```

#### Cloze с номерами групп:

```markdown
==GIL==^[Global Interpreter Lock][^1] позволяет только ==одному потоку==[^1] выполнять байт-код.
```

### Формат 6: Legacy (обратная совместимость)

```markdown
### Вопрос: Что такое декоратор в Python?

Ответ: **Декоратор** — это функция, которая принимает другую функцию и расширяет её поведение.
```

## 📤 Выходные форматы

### Obsidian Spaced Repetition

Генерируются `.md` файлы в папку Obsidian Vault с правильным форматированием для плагина Spaced Repetition.

### Anki Import

Генерируются `.txt` файлы в формате TSV для импорта в Anki через File → Import.

## 📜 Лицензия

MIT License
