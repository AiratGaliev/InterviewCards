---
tags: [interview/python]
difficulty: medium
category: python
---

# GIL в Python

## Теория

**GIL (Global Interpreter Lock)** — это механизм в CPython, который позволяет только одному потоку выполнять байт-код.

## Ключевые моменты

- Многопоточность не ускоряет CPU-bound задачи
- I/O-bound задачи всё ещё выигрывают от многопоточности
- Для CPU-bound задач используйте multiprocessing

### Вопрос: Что такое GIL в Python?

Ответ: **GIL** — это механизм в CPython, который позволяет только одному потоку выполнять байт-код.

**Последствия:**

- Многопоточность не ускоряет CPU-bound задачи
- I/O-bound задачи всё ещё выигрывают от многопоточности
- Для CPU-bound задач используйте `multiprocessing`

### Вопрос: Когда использовать multiprocessing?

Ответ: Используйте **multiprocessing** для CPU-bound задач.

```python
from multiprocessing import Pool

with Pool(4) as p:
    results = p.map(lambda x: x * x, range(10))
```

#card #interview
