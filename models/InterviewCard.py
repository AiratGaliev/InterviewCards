"""
Модель карточки для подготовки к интервью (Spaced Repetition).
Представляет собой dataclass с методами валидации и конвертации.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _get_config_limits():
    """
    Получает лимиты из конфигурации.
    Возвращает fallback значения, если конфиг недоступен.
    """
    try:
        from config.Config import get_config
        config = get_config()
        return {
            'max_question_length': config.max_question_length,
            'max_answer_length': config.max_answer_length,
        }
    except Exception:
        # Fallback значения при отсутствии конфига
        return {
            'max_question_length': 2000,
            'max_answer_length': 5000,
        }


# Допустимые уровни сложности
VALID_DIFFICULTIES = frozenset(['easy', 'medium', 'hard'])


@dataclass
class InterviewCard:
    """
    Модель карточки для подготовки к интервью.

    Attributes:
        id: Уникальный идентификатор карточки
        topic: Тема карточки
        category: Категория (python, javascript, etc.)
        question: Текст вопроса
        answer: Текст ответа
        code_snippets: Список фрагментов кода
        difficulty: Уровень сложности (easy, medium, hard)
        tags: Список тегов
        source_note: Имя исходной заметки для ссылок
        frontmatter: Метаданные из YAML frontmatter
        created_at: Дата создания
        updated_at: Дата последнего обновления
        max_question_length: Максимальная длина вопроса (из конфига)
        max_answer_length: Максимальная длина ответа (из конфига)
    """
    id: int
    topic: str
    category: str
    question: str
    answer: str
    code_snippets: List[str] = field(default_factory=list)
    difficulty: str = "medium"
    tags: List[str] = field(default_factory=list)
    source_note: Optional[str] = None
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    max_question_length: int = field(default=None, repr=False)
    max_answer_length: int = field(default=None, repr=False)

    def __post_init__(self):
        """Пост-инициализация: нормализация данных и загрузка лимитов"""
        # Загружаем лимиты из конфига, если не переданы явно
        if self.max_question_length is None or self.max_answer_length is None:
            limits = _get_config_limits()
            if self.max_question_length is None:
                self.max_question_length = limits['max_question_length']
            if self.max_answer_length is None:
                self.max_answer_length = limits['max_answer_length']

        # Нормализация сложности
        if self.difficulty not in VALID_DIFFICULTIES:
            self.difficulty = "medium"

        # Очистка строковых полей
        self.question = self.question.strip() if self.question else ""
        self.answer = self.answer.strip() if self.answer else ""
        self.topic = self.topic.strip() if self.topic else ""
        self.category = self.category.strip() if self.category else "general"

    def validate(self) -> bool:
        """
        Валидация карточки.

        Returns:
            bool: True если карточка валидна, иначе False
        """
        # Проверка наличия обязательных полей
        if not self.question or not self.answer:
            return False

        # Проверка длины полей (используем значения из конфига)
        if len(self.question) > self.max_question_length:
            print(f"⚠️ Вопрос слишком длинный: {len(self.question)} символов (макс. {self.max_question_length})")
            return False

        if len(self.answer) > self.max_answer_length:
            print(f"⚠️ Ответ слишком длинный: {len(self.answer)} символов (макс. {self.max_answer_length})")
            return False

        return True

    def has_code(self) -> bool:
        """Проверяет наличие фрагментов кода"""
        return bool(self.code_snippets)

    def get_formatted_tags(self) -> str:
        """
        Возвращает отформатированную строку тегов для Obsidian.

        Проверяет, начинается ли тег уже с префикса 'interview/'
        во избежание дублирования (interview/interview/python).
        """
        if not self.tags:
            return "interview/general"

        formatted = []
        for tag in self.tags:
            if tag:
                # Проверяем наличие префикса перед добавлением
                if tag.startswith('interview/'):
                    formatted.append(tag)
                else:
                    formatted.append(f'interview/{tag}')

        return ', '.join(formatted) if formatted else "interview/general"

    def _is_code_in_answer(self, code: str) -> bool:
        """
        Проверяет, содержится ли фрагмент кода в ответе.

        Args:
            code: Фрагмент кода для проверки

        Returns:
            bool: True если код найден в ответе
        """
        if not code or not self.answer:
            return False

        # Нормализуем код и ответ для сравнения
        # Убираем лишние пробелы и переносы строк
        normalized_code = ' '.join(code.split())
        normalized_answer = ' '.join(self.answer.split())

        return normalized_code in normalized_answer

    def to_markdown(self) -> str:
        """
        Конвертирует карточку в Markdown формат для Obsidian.

        Returns:
            str: Markdown-контент карточки
        """
        # Frontmatter без лишних отступов (важно для YAML-парсера)
        frontmatter_lines = [
            "---",
            f"tags: [{self.get_formatted_tags()}]",
            f"created: {self.created_at.strftime('%Y-%m-%d')}",
            f"updated: {self.updated_at.strftime('%Y-%m-%d')}",
            f"source: \"[[{self.source_note}]]\"" if self.source_note else "source: ",
            f"difficulty: {self.difficulty}",
            f"category: {self.category}",
            "---",
            "",
        ]
        frontmatter = '\n'.join(frontmatter_lines)

        # Основной контент
        content_lines = [f"# {self.question}", "", "---", "", self.answer, ""]

        # Добавляем примеры кода только если их нет в ответе
        if self.code_snippets:
            # Фильтруем сниппеты, которые ещё не содержатся в ответе
            unique_snippets = [
                snippet for snippet in self.code_snippets
                if not self._is_code_in_answer(snippet)
            ]

            if unique_snippets:
                content_lines.append("## Примеры кода")
                content_lines.append("")
                for snippet in unique_snippets:
                    content_lines.append(f"```python\n{snippet}\n```")
                    content_lines.append("")

        # Ссылка на исходный материал
        if self.source_note:
            content_lines.append(f"[[{self.source_note}|📎 Полный материал]]")
            content_lines.append("")

        # Теги Spaced Repetition
        content_lines.append("#card #interview")

        content = '\n'.join(content_lines)

        return frontmatter + content

    def to_anki_format(self, include_deck: bool = True, deck_prefix: str = "Interview") -> str:
        """
        Конвертирует карточку в формат для импорта в Anki.

        Args:
            include_deck: Включать ли имя колоды
            deck_prefix: Префикс имени колоды

        Returns:
            str: Строка в формате Anki import
        """
        # Базовое форматирование (без HTML, простой текст)
        front = self.question.replace('\n', '<br>')
        back = self.answer.replace('\n', '<br>')

        # Формируем теги
        tags = ' '.join([f"interview/{tag}" for tag in self.tags] + [f"difficulty/{self.difficulty}"])

        if include_deck:
            deck_name = f"{deck_prefix}::{self.category.replace('_', ' ').title()}"
            return f"{front}\t{back}\t{deck_name}\t\t{tags}"
        else:
            return f"{front}\t{back}\t\t{tags}"

    def to_dict(self) -> Dict[str, Any]:
        """
        Конвертирует карточку в словарь для сериализации.

        Returns:
            dict: Словарь с данными карточки
        """
        return {
            'id': self.id,
            'topic': self.topic,
            'category': self.category,
            'question': self.question,
            'answer': self.answer,
            'code_snippets': self.code_snippets,
            'difficulty': self.difficulty,
            'tags': self.tags,
            'source_note': self.source_note,
            'frontmatter': self.frontmatter,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InterviewCard':
        """
        Создаёт карточку из словаря.

        Args:
            data: Словарь с данными карточки

        Returns:
            InterviewCard: Новый экземпляр карточки
        """
        # Парсинг дат
        created_at = datetime.now()
        updated_at = datetime.now()

        if 'created_at' in data and data['created_at']:
            try:
                created_at = datetime.fromisoformat(data['created_at'])
            except (ValueError, TypeError):
                pass

        if 'updated_at' in data and data['updated_at']:
            try:
                updated_at = datetime.fromisoformat(data['updated_at'])
            except (ValueError, TypeError):
                pass

        return cls(
            id=data.get('id', 0),
            topic=data.get('topic', ''),
            category=data.get('category', 'general'),
            question=data.get('question', ''),
            answer=data.get('answer', ''),
            code_snippets=data.get('code_snippets', []),
            difficulty=data.get('difficulty', 'medium'),
            tags=data.get('tags', []),
            source_note=data.get('source_note'),
            frontmatter=data.get('frontmatter', {}),
            created_at=created_at,
            updated_at=updated_at
        )

    def __str__(self) -> str:
        """Строковое представление карточки"""
        return f"InterviewCard(id={self.id}, topic='{self.topic}', category='{self.category}')"

    def __repr__(self) -> str:
        """Официальное строковое представление"""
        return self.__str__()
