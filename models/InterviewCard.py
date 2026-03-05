from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class InterviewCard:
    """Модель карточки для подготовки к интервью"""
    id: int
    topic: str
    category: str
    question: str
    answer: str
    code_snippets: List[str] = field(default_factory=list)
    difficulty: str = "medium"  # easy, medium, hard
    tags: List[str] = field(default_factory=list)
    source_note: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def validate(self) -> bool:
        """Валидация карточки"""
        if not self.question or not self.answer:
            return False
        if len(self.question) > 2000:
            return False
        if len(self.answer) > 5000:
            return False
        return True

    def has_code(self) -> bool:
        """Проверка наличия кода"""
        return len(self.code_snippets) > 0

    def to_dict(self) -> dict:
        """Конвертация в словарь"""
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
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'InterviewCard':
        """Создание из словаря"""
        return cls(
            id=data.get('id', 0),
            topic=data.get('topic', ''),
            category=data.get('category', ''),
            question=data.get('question', ''),
            answer=data.get('answer', ''),
            code_snippets=data.get('code_snippets', []),
            difficulty=data.get('difficulty', 'medium'),
            tags=data.get('tags', []),
            source_note=data.get('source_note'),
            created_at=datetime.fromisoformat(data['created_at']) if 'created_at' in data else datetime.now(),
            updated_at=datetime.fromisoformat(data['updated_at']) if 'updated_at' in data else datetime.now()
        )