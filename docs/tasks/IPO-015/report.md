# IPO-015 — отчёт исполнителя

**Status:** DONE

## What you implemented

- Добавлен неизменяемый dataclass `StartingBonus` с шестью полями из brief.
- Экспортируется `BonusCode = Literal[...]` со всеми десятью кодами; поле
  `StartingBonus.code` использует этот тип.
- Чистая `resolve_starting_roll` возвращает соответствующий d10 бонус с коротким
  русским описанием. Только 1 даёт `action_point_bonus=10`, только 9 даёт
  `client_bonus=10`; у них `deferred=False`. Остальные — отложенные одноразовые
  права с нулевыми немедленными прибавками.
- Входные bool и не-int отклоняются `TypeError`, целые вне 1–10 — `ValueError`.
- В resolver нет случайности, записи состояния или применения бонусов к baseline.

## What you tested and test results

Исполняемый Python:
`/Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python`.
Все команды выполнялись в
`/private/tmp/ipo-team-implementation-egw7qray/backend`.

Предварительный baseline:

```text
python -m pytest tests/test_act_one_baseline.py -q
3 passed
```

Итоговый запуск:

```text
python -m pytest tests/test_starting_bonus.py tests/test_act_one_baseline.py -q
.............................................                            [100%]
45 passed in 0.04s
```

42 новых проверки покрывают десять соответствий независимыми ожидаемыми
значениями, описания и эффекты, недопустимые типы включая оба bool,
границы диапазона, неизменяемость каждого поля dataclass, детерминизм
и неизменность baseline при каждом из десяти результатов.
Три существующих проверки baseline также проходят.

```text
python -m ruff check app/starting_bonus.py tests/test_starting_bonus.py
All checks passed!
python -m ruff format --check app/starting_bonus.py tests/test_starting_bonus.py
2 files already formatted
```

Полный PostgreSQL suite не запускался: контроллер явно ограничил pure-задачу
focused tests с baseline и организует интеграционные проверки позже.

## TDD Evidence

TDD не требовался brief, однако тесты добавлены раньше реализации.

RED:

```text
python -m pytest tests/test_starting_bonus.py -q
E   ModuleNotFoundError: No module named 'app.starting_bonus'
1 error in 0.08s
```

Это ожидаемое падение при отсутствии нового модуля.
GREEN — итоговый запуск выше: 45 passed.

## Files changed

- `backend/app/starting_bonus.py` — новый модуль.
- `backend/tests/test_starting_bonus.py` — новые проверки.
- `docs/tasks/IPO-015/report.md` — этот отчёт.

## Self-review findings

Прочитан полный diff двух новых Python-файлов относительно
`/private/tmp/ipo-team-review-snapshots/IPO-015/before`.
Проверены таблица brief, порядок 1–10, отдельный экспорт `BonusCode`,
валидация до индексирования, отсутствие побочных эффектов и случайности.
Исправлена одна длинная строка тестовой таблицы через Ruff formatter,
после чего повторены pytest и Ruff. Замечаний к итоговой реализации нет.
Другие файлы проекта не редактировались, коммиты не создавались.

## Any issues or concerns

Нет. Реализация готова к независимому ревью; коммит выполняет контроллер
после одобрения.
