"""
Модель карточки для подготовки к интервью (Spaced Repetition).
Представляет собой dataclass с методами валидации и конвертации.

Поддерживаемые форматы Spaced Repetition:
- Single-line Basic: question::answer
- Single-line Bidirectional: info1:::info2 (создает 2 карточки)
- Multi-line Basic: question\n?\nanswer
- Multi-line Bidirectional: info1\n??\ninfo2 (создает 2 карточки)
- Cloze: text with ==hidden parts==
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class CardType(Enum):
    """Типы карточек Spaced Repetition"""
    SINGLE_LINE_BASIC = "single_line_basic"  # question::answer
    SINGLE_LINE_BIDIRECTIONAL = "single_line_bidirectional"  # info1:::info2
    MULTI_LINE_BASIC = "multi_line_basic"  # question\n?\nanswer
    MULTI_LINE_BIDIRECTIONAL = "multi_line_bidirectional"  # info1\n??\ninfo2
    CLOZE = "cloze"  # text with ==hidden parts==


class ClozeType(Enum):
    """Типы Cloze deletions"""
    SIMPLIFIED = "simplified"  # ==text==^[hint]
    CLASSIC = "classic"  # ==text==^[hint][^1]
    GENERALIZED = "generalized"  # ==text==[^ahhs]


@dataclass
class ClozeDeletion:
    """Представляет одну Cloze deletion в карточке"""
    text: str  # Текст, который будет скрыт
    position: int  # Позиция в тексте
    hint: Optional[str] = None  # Подсказка (отображается как [hint])
    sequence: Optional[int] = None  # Номер группы (для classic clozes)
    actions: Optional[str] = None  # Действия (для generalized clozes: "ahhs")

    def to_sr_format(self) -> str:
        """
        Конвертирует deletion в формат Spaced Repetition.

        Returns:
            str: Формат ==text==^[hint][^seq] или ==text==[^actions]
        """
        result = f"=={self.text}=="

        if self.actions:
            # Generalized cloze
            result += f"[^{self.actions}]"
        elif self.hint or self.sequence is not None:
            result += "^["
            if self.hint:
                result += self.hint
            if self.sequence is not None:
                result += f"][^{self.sequence}"
            result += "]"

        return result


@dataclass
class SchedulingData:
    """Данные планирования карточки"""
    next_review: Optional[str] = None  # Дата следующего повторения (YYYY-MM-DD)
    interval: Optional[int] = None  # Интервал в днях
    ease: Optional[int] = None  # Фактор лёгкости

    def to_html_comment(self) -> str:
        """
        Генерирует HTML комментарий с данными планирования.
        Формат: <!--SR:2024-01-15,4,270-->

        Returns:
            str: HTML комментарий с данными SR
        """
        if self.next_review and self.interval is not None and self.ease is not None:
            return f"<!--SR:{self.next_review},{self.interval},{self.ease}-->"
        return ""

    @classmethod
    def from_html_comment(cls, comment: str) -> Optional['SchedulingData']:
        """
        Парсит HTML комментарий с данными планирования.

        Args:
            comment: Строка с HTML комментарием

        Returns:
            SchedulingData или None
        """
        import re
        match = re.search(r'<!--SR:(\d{4}-\d{2}-\d{2}),(\d+),(\d+)-->', comment)
        if match:
            return cls(
                next_review=match.group(1),
                interval=int(match.group(2)),
                ease=int(match.group(3))
            )
        return None


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
    Полностью совместима с Obsidian Spaced Repetition.

    Attributes:
        id: Уникальный идентификатор карточки
        topic: Тема карточки
        category: Категория (python, javascript, etc.)
        question: Текст вопроса (front side)
        answer: Текст ответа (back side)
        card_type: Тип карточки (single-line, multi-line, cloze)
        code_snippets: Список фрагментов кода
        difficulty: Уровень сложности (easy, medium, hard)
        tags: Список тегов
        source_note: Имя исходной заметки для ссылок
        frontmatter: Метаданные из YAML frontmatter
        created_at: Дата создания
        updated_at: Дата последнего обновления
        scheduling: Данные планирования SR
        deck_name: Имя колоды (например, "flashcards/python")
        cloze_deletions: Список Cloze deletions (для cloze карточек)
        is_reverse: Является ли карточка обратной (для bidirectional)
        sibling_id: ID карточки-близнеца (для bidirectional)
    """
    id: int
    topic: str
    category: str
    question: str
    answer: str
    card_type: CardType = CardType.SINGLE_LINE_BASIC
    code_snippets: List[str] = field(default_factory=list)
    difficulty: str = "medium"
    tags: List[str] = field(default_factory=list)
    source_note: Optional[str] = None
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    scheduling: Optional[SchedulingData] = None
    deck_name: str = "flashcards"
    cloze_deletions: List[ClozeDeletion] = field(default_factory=list)
    is_reverse: bool = False
    sibling_id: Optional[int] = None
    max_question_length: int = field(default=None, repr=False)
    max_answer_length: int = field(default=None, repr=False)

    def __post_init__(self):
        """Пост-инициализация: нормализация данных и загрузка лимитов"""
        # Загружаем лимиты из конфига
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

        # Для cloze карточек проверяем наличие deletions
        if self.card_type == CardType.CLOZE and not self.cloze_deletions:
            return False

        # Проверка длины полей
        if len(self.question) > self.max_question_length:
            print(f"⚠️ Вопрос слишком длинный: {len(self.question)} символов")
            return False

        if len(self.answer) > self.max_answer_length:
            print(f"⚠️ Ответ слишком длинный: {len(self.answer)} символов")
            return False

        return True

    def has_code(self) -> bool:
        """Проверяет наличие фрагментов кода"""
        return bool(self.code_snippets)

    def get_deck_tag(self) -> str:
        """
        Генерирует тег колоды для Spaced Repetition.
        Формат: #flashcards/category или #flashcards/subcategory/category

        Returns:
            str: Тег колоды
        """
        # Формируем путь колоды
        deck_path = self.deck_name

        # Если deck_name не начинается с flashcards, добавляем
        if not deck_path.startswith("flashcards"):
            if deck_path.startswith("#"):
                deck_path = deck_path[1:]
            deck_path = f"flashcards/{deck_path}"

        # Добавляем категорию если нужно
        if self.category and self.category != "general":
            if not deck_path.endswith(self.category):
                deck_path = f"{deck_path}/{self.category}"

        return f"#{deck_path}"

    def get_formatted_tags(self) -> str:
        """
        Возвращает отформатированную строку тегов для Obsidian.
        Не включает deck tag, так как он добавляется отдельно.

        Returns:
            str: Строка тегов через запятую
        """
        if not self.tags:
            return ""

        formatted = []
        for tag in self.tags:
            if tag and not tag.startswith('#'):
                formatted.append(tag)

        return ', '.join(formatted) if formatted else ""

    def _is_code_in_answer(self, code: str) -> bool:
        """
        Проверяет, содержится ли фрагмент кода в ответе.
        """
        if not code or not self.answer:
            return False

        normalized_code = ' '.join(code.split())
        normalized_answer = ' '.join(self.answer.split())

        return normalized_code in normalized_answer

    def to_markdown(self) -> str:
        """
        Конвертирует карточку в Markdown формат для Obsidian Spaced Repetition.
        Генерирует правильный формат в зависимости от типа карточки.

        Returns:
            str: Markdown-контент карточки
        """
        # Frontmatter
        frontmatter_lines = [
            "---",
            f"tags: [{self.get_formatted_tags()}]",
            f"created: {self.created_at.strftime('%Y-%m-%d')}",
            f"updated: {self.updated_at.strftime('%Y-%m-%d')}",
            f"source: \"[[{self.source_note}]]\"" if self.source_note else "source: ",
            f"difficulty: {self.difficulty}",
            f"category: {self.category}",
            f"card_type: {self.card_type.value}",
            "---",
            "",
        ]
        frontmatter = '\n'.join(frontmatter_lines)

        # Основной контент зависит от типа карточки
        content_lines = []

        if self.card_type == CardType.CLOZE:
            # Cloze карточка
            content_lines.append(self.answer)  # Для cloze answer содержит весь текст

        elif self.card_type == CardType.SINGLE_LINE_BASIC:
            # Single-line Basic: question::answer
            content_lines.append(f"{self.question}::{self.answer}")

        elif self.card_type == CardType.SINGLE_LINE_BIDIRECTIONAL:
            # Single-line Bidirectional: info1:::info2
            content_lines.append(f"{self.question}:::{self.answer}")

        elif self.card_type == CardType.MULTI_LINE_BASIC:
            # Multi-line Basic: question\n?\nanswer
            content_lines.append(self.question)
            content_lines.append("?")
            content_lines.append(self.answer)

        elif self.card_type == CardType.MULTI_LINE_BIDIRECTIONAL:
            # Multi-line Bidirectional: info1\n??\ninfo2
            content_lines.append(self.question)
            content_lines.append("??")
            content_lines.append(self.answer)

        content_lines.append("")

        # Добавляем примеры кода только если их нет в ответе
        if self.code_snippets:
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

        # Тег колоды - ОБЯЗАТЕЛЬНЫЙ для Spaced Repetition
        content_lines.append(self.get_deck_tag())

        # Данные планирования в HTML комментарии (если есть)
        if self.scheduling:
            scheduling_comment = self.scheduling.to_html_comment()
            if scheduling_comment:
                content_lines.append(scheduling_comment)

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
        # Базовое форматирование
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
        """
        return {
            'id': self.id,
            'topic': self.topic,
            'category': self.category,
            'question': self.question,
            'answer': self.answer,
            'card_type': self.card_type.value,
            'code_snippets': self.code_snippets,
            'difficulty': self.difficulty,
            'tags': self.tags,
            'source_note': self.source_note,
            'frontmatter': self.frontmatter,
            'deck_name': self.deck_name,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'scheduling': {
                'next_review': self.scheduling.next_review if self.scheduling else None,
                'interval': self.scheduling.interval if self.scheduling else None,
                'ease': self.scheduling.ease if self.scheduling else None,
            } if self.scheduling else None,
            'cloze_deletions': [
                {
                    'text': cd.text,
                    'position': cd.position,
                    'hint': cd.hint,
                    'sequence': cd.sequence,
                    'actions': cd.actions,
                } for cd in self.cloze_deletions
            ] if self.cloze_deletions else [],
            'is_reverse': self.is_reverse,
            'sibling_id': self.sibling_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InterviewCard':
        """
        Создаёт карточку из словария.
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

        # Парсинг типа карточки
        card_type = CardType.SINGLE_LINE_BASIC
        if 'card_type' in data:
            try:
                card_type = CardType(data['card_type'])
            except ValueError:
                pass

        # Парсинг данных планирования
        scheduling = None
        if data.get('scheduling'):
            sd = data['scheduling']
            if sd.get('next_review'):
                scheduling = SchedulingData(
                    next_review=sd['next_review'],
                    interval=sd.get('interval'),
                    ease=sd.get('ease'),
                )

        # Парсинг cloze deletions
        cloze_deletions = []
        if data.get('cloze_deletions'):
            for cd_data in data['cloze_deletions']:
                cloze_deletions.append(ClozeDeletion(
                    text=cd_data.get('text', ''),
                    position=cd_data.get('position', 0),
                    hint=cd_data.get('hint'),
                    sequence=cd_data.get('sequence'),
                    actions=cd_data.get('actions'),
                ))

        return cls(
            id=data.get('id', 0),
            topic=data.get('topic', ''),
            category=data.get('category', 'general'),
            question=data.get('question', ''),
            answer=data.get('answer', ''),
            card_type=card_type,
            code_snippets=data.get('code_snippets', []),
            difficulty=data.get('difficulty', 'medium'),
            tags=data.get('tags', []),
            source_note=data.get('source_note'),
            frontmatter=data.get('frontmatter', {}),
            deck_name=data.get('deck_name', 'flashcards'),
            created_at=created_at,
            updated_at=updated_at,
            scheduling=scheduling,
            cloze_deletions=cloze_deletions,
            is_reverse=data.get('is_reverse', False),
            sibling_id=data.get('sibling_id'),
        )

    def create_sibling_card(self, new_id: int) -> Optional['InterviewCard']:
        """
        Создаёт карточку-близнеца для bidirectional карточек.
        Меняет местами вопрос и ответ.

        Args:
            new_id: ID для новой карточки

        Returns:
            InterviewCard: Карточка-близнец или None
        """
        if self.card_type not in [CardType.SINGLE_LINE_BIDIRECTIONAL, CardType.MULTI_LINE_BIDIRECTIONAL]:
            return None

        return InterviewCard(
            id=new_id,
            topic=self.topic,
            category=self.category,
            question=self.answer,  # Меняем местами
            answer=self.question,  # Меняем местами
            card_type=self.card_type,
            code_snippets=self.code_snippets.copy(),
            difficulty=self.difficulty,
            tags=self.tags.copy(),
            source_note=self.source_note,
            frontmatter=self.frontmatter.copy(),
            deck_name=self.deck_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            is_reverse=True,
            sibling_id=self.id,
        )

    def __str__(self) -> str:
        """Строковое представление карточки"""
        return f"InterviewCard(id={self.id}, topic='{self.topic}', type={self.card_type.value})"

    def __repr__(self) -> str:
        """Официальное строковое представление"""
        return self.__str__()
