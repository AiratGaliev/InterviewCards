# 📚 Interview Cards

Генератор карточек Spaced Repetition из Markdown для подготовки к техническим собеседованиям.
Поддерживает экспорт в **Obsidian Spaced Repetition** и **Anki**.

---

## 🚀 Возможности

- **Markdown-формат** — пишите карточки в удобном Markdown
- **6 типов карточек** — Single-line, Multi-line, Bidirectional, Cloze, Legacy
- **Два формата экспорта** — Obsidian Spaced Repetition и Anki
- **Streamlit UI и CLI** — выбирайте удобный интерфейс
- **Категории и теги** — организация через frontmatter YAML
- **Дедупликация** — автоматическое удаление дубликатов
- **Batch processing** — обработка больших наборов данных
- **Подсветка кода** — сохранение фрагментов кода в карточках
- **Reverse cards** — автоматическое создание обратных карточек

### Поддерживаемые форматы карточек

| Тип                       | Разделитель              | Описание                             |
|---------------------------|--------------------------|--------------------------------------|
| Single-line Basic         | `::`                     | `вопрос::ответ`                      |
| Single-line Bidirectional | `:::`                    | `инфо1:::инфо2` (создаёт 2 карточки) |
| Multi-line Basic          | `?`                      | Многострочный вопрос и ответ         |
| Multi-line Bidirectional  | `??`                     | Многострочный (создаёт 2 карточки)   |
| Cloze                     | `==text==`               | Карточки с пропусками                |
| Legacy                    | `### Вопрос:` / `Ответ:` | Обратная совместимость               |

---

## 📋 Требования

| Компонент    | Версия / Условие                            |
|--------------|---------------------------------------------|
| **Python**   | 3.10+                                       |
| **Obsidian** | Опционально (для плагина Spaced Repetition) |
| **Anki**     | Опционально (для импорта колод)             |

---

## 🔧 Установка

### Автоматическая установка и запуск (рекомендуется)

```bash
git clone https://github.com/AiratGaliev/InterviewCards.git
cd InterviewCards
```

**Streamlit UI:**

| ОС          | Команда      |
|-------------|--------------|
| Windows     | `start.bat`  |
| Linux / Mac | `./start.sh` |

**CLI:**

| ОС          | Команда          |
|-------------|------------------|
| Windows     | `start_cli.bat`  |
| Linux / Mac | `./start_cli.sh` |

> Скрипты автоматически создадут виртуальное окружение (`.venv`), установят зависимости и запустят приложение.

### Ручная установка

```bash
git clone https://github.com/AiratGaliev/InterviewCards.git
cd InterviewCards

# Создание и активация виртуального окружения
python -m venv .venv

# Windows:
.venv\Scripts\activate
# Linux / Mac:
source .venv/bin/activate

# Установка зависимостей
pip install -r requirements.txt
```

<details>
<summary>📦 Список зависимостей</summary>

```
streamlit==1.42.0
pandas==2.2.3
requests==2.32.3
pyyaml==6.0.2
```

</details>

---

## 📝 Использование

### Streamlit UI (рекомендуется)

```bash
streamlit run start.py
```

Возможности UI:

| Функция                | Описание                            |
|------------------------|-------------------------------------|
| 📁 Выбор источника     | Путь к папке с Markdown-материалами |
| 🏷️ Фильтрация         | По категориям и тегам               |
| 👁️ Предпросмотр       | Просмотр тем и отдельных карточек   |
| 📊 Статистика          | Количество карточек по типам        |
| ⚙️ Настройки экспорта  | Формат, дедупликация, лимиты        |
| 💾 Сохранение настроек | Персистентность между сессиями      |

### CLI

```bash
python start_cli.py
```

<details>
<summary>Пример работы CLI</summary>

```
📚 Interview Cards — Генератор карточек v2.0
============================================================

Путь к папке с Markdown темами (по умолчанию: ~/Documents/InterviewCards/Materials):

Доступные категории: typescript, javascript, java, react, next, python, sql, ...
Выберите категории (через запятую, или 'all'): python, algorithms

Формат экспорта:
  1. Оба формата (Obsidian + Anki)
  2. Только Obsidian SR
  3. Только Anki
Выберите (1-3, по умолчанию 1): 1

🚀 Начало генерации...

  [  0%] Python Basics
  [ 50%] Data Structures
  [100%] Завершено
```

</details>

---

## 📄 Форматы карточек — подробности

### 1. Single-line Basic (`::`)

```markdown
Что такое GIL в Python?::Механизм в CPython, позволяющий только одному потоку выполнять байт-код
```

### 2. Single-line Bidirectional (`:::`)

```markdown
Python:::Язык программирования высокого уровня
```

> Создаёт **две карточки**: `Python → Описание` и `Описание → Python`.

### 3. Multi-line Basic (`?`)

```markdown
Опишите механизм работы GIL в Python
?
**GIL (Global Interpreter Lock)** — это мьютекс в CPython, который:

- Разрешает только одному потоку выполнять байт-код
- Необходим для защиты структур данных CPython
- Влияет на производительность CPU-bound задач
```

### 4. Multi-line Bidirectional (`??`)

```markdown
**list (список)**

- Изменяемый
- Упорядоченный
- Допускает дубликаты

??

**tuple (кортеж)**

- Неизменяемый
- Упорядоченный
- Допускает дубликаты
```

> Создаёт **две карточки** в обоих направлениях.

### 5. Cloze Deletions (`==text==`)

**Базовый формат:**

```markdown
Python был создан ==Гвидо ван Россум== в ==1991== году.
```

**С подсказками:**

```markdown
Столица Франции — ==Париж==^[город любви]
```

**С номерами групп:**

```markdown
==GIL==^[Global Interpreter Lock][^1] позволяет только ==одному потоку==[^1] выполнять байт-код.
```

### 6. Legacy-формат (обратная совместимость)

```markdown
### Вопрос: Что такое декоратор в Python?

Ответ: **Декоратор** — это функция, которая принимает другую функцию и расширяет её поведение.
```

---

## 📝 Формат исходных Markdown-файлов

### Frontmatter (YAML)

```yaml
---
tags: [ flashcards/python, interview ]
category: python
---
```

| Поле       | Описание                          |
|------------|-----------------------------------|
| `tags`     | Список тегов для фильтрации       |
| `category` | Категория (определяет имя колоды) |

### Полный пример файла

```markdown
---
tags: [flashcards/python, interview]
category: python
---

# Python Basics

Что такое GIL?::Global Interpreter Lock в CPython

GIL расшифровывается как ==Global Interpreter Lock==.
```

---

## 📤 Выходные форматы

### Obsidian Spaced Repetition

Файлы `.md` сохраняются в `Interview/Cards/<category>/` внутри Obsidian Vault.

```markdown
---
tags: [flashcards/python, interview]
category: python
---

Что такое GIL?::Global Interpreter Lock в CPython
```

Особенности:

- Формат для плагина [Spaced Repetition](https://github.com/st3v3nmw/obsidian-spaced-repetition)
- Теги колод: `#flashcards/<category>`
- HTML-комментарии с планированием: `<!--SR:2024-01-15,4,270-->`
- Автоматические ссылки на исходные материалы

### Anki Import

TSV-файлы `.txt` для импорта через **File → Import**.

```
Front	Back	Deck::Name	Tags
Что такое GIL?	Global Interpreter Lock	Interview::Python	interview/python
```

Расположение: `<output>/anki/<category>.txt`

---

## 📁 Структура проекта

```
InterviewCards/
├── config/
│   ├── __init__.py
│   └── Config.py              # Конфигурация (dataclass)
├── logic/
│   ├── __init__.py
│   ├── core.py                # Основная логика генерации
│   └── utils.py               # Утилиты парсинга
├── models/
│   ├── __init__.py
│   └── InterviewCard.py       # Модель карточки
├── templates/
│   └── example_topic.md       # Пример темы
├── ui/
│   ├── __init__.py
│   └── settings.py            # Пользовательские настройки
├── config.ini                 # Файл конфигурации
├── requirements.txt           # Зависимости
├── start.py                   # Streamlit UI
├── start_cli.py               # CLI-версия
├── start.bat / start.sh       # Автозапуск UI
└── start_cli.bat / start_cli.sh # Автозапуск CLI
```

---

## ⚙️ Конфигурация (`config.ini`)

### Основные настройки

```ini
[main]
categories = typescript, javascript, java, react, next, python, sql, ...
documents = ~/Documents/InterviewCards/
output = ~/Documents/InterviewCards/output/
```

### Пути к хранилищам

```ini
[directories-windows]
obsidian_vault = ~/Documents/ObsidianVault/
anki_collection_media = ~/AppData/Roaming/Anki2/User 1/collection.media/

[directories-linux]
obsidian_vault = ~/Documents/ObsidianVault/
anki_collection_media = ~/.local/share/Anki2/User 1/collection.media/
```

### Лимиты

```ini
[limits]
max_questions_per_deck = 500
max_question_length = 2000
max_answer_length = 5000
```

### Разделители

```ini
[separators]
single_line_basic = ::
single_line_bidirectional = :::
multi_line_basic = ?
multi_line_bidirectional = ??

[cloze]
start = ==
end = ==
```

---

## 🗂️ Организация данных

### Исходные материалы

```
<materials_source>/
├── python/
│   ├── basics.md
│   └── advanced.md
├── javascript/
│   └── core.md
└── algorithms/
    └── sorting.md
```

### Результат генерации

```
Obsidian Vault/
└── Interview/
    ├── Cards/
    │   ├── python/
    │   ├── javascript/
    │   └── algorithms/
    └── Materials/
        ├── python/
        └── ...
```

---

## 🎯 Best Practices

### Создание эффективных карточек

| Принцип     | Рекомендация                            |
|-------------|-----------------------------------------|
| Атомарность | Одна идея = одна карточка               |
| Контекст    | Добавляйте теги для категоризации       |
| Код         | Используйте блоки кода для примеров     |
| Cloze       | Идеальны для запоминания терминов и дат |

### Организация категорий

```yaml
tags: [ interview, oop ]    # Тема / контекст
category: python          # Язык / технология
```

### Дедупликация

Включите опцию **Дедупликация** в настройках, чтобы автоматически удалять карточки с одинаковыми вопросами.

---

## 🔍 Устранение неполадок

| Проблема                 | Решение                                                                                                                 |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Карточки не генерируются | Проверьте разделители (`::`, `?`, `==`) и наличие `category` во frontmatter                                             |
| Неправильные пути        | Убедитесь, что пути в `config.ini` корректны и есть права доступа. Для Windows используйте секцию `directories-windows` |
| Ошибка импорта в Anki    | Файлы `.txt` должны быть в UTF-8, разделитель полей — TAB, имя колоды — в 3-й колонке                                   |

---

## 🤝 Contributing

1. Сделайте fork репозитория
2. Создайте feature-ветку: `git checkout -b feature/amazing-feature`
3. Зафиксируйте изменения: `git commit -m 'Add amazing feature'`
4. Отправьте в удалённый репозиторий: `git push origin feature/amazing-feature`
5. Откройте Pull Request

---

## 📞 Поддержка

- **Примеры:** см. `templates/example_topic.md`
- **Баги:** создавайте [GitHub Issues](https://github.com/AiratGaliev/InterviewCards/issues)
- **Настройка:** редактируйте `config.ini`

---

## 📜 Лицензия

[MIT License](LICENSE)