# IPO-020 — отчёт исполнителя

**Status:** DONE

## What you implemented

- Новый `tests/test_act_one_end_to_end.py` содержит три параметризованных
  сквозных сценария для бонусов1 (+10AP),9 (+10клиентов),7 (отложенный эксперимент).
  В каждом создаются две сессии; вторая получает фиксированный бонус4.
- Реальный admin login, создание сессий, восемь отдельных участников и
  credential, preparation, капитаны, самостоятельный выбор четырёх ролей,
  снимки подготовки, start/GET/hiring/P&L preview выполняются только через
  публичный HTTP API. Нет helpers с прямой инициализацией или записью в БД.
- Проверены AP140→110/AP130→100, вместимость2→3 при двух junior, сохранность
  остальных ресурсов/бонуса, повторы start/покупки и новые TestClient с
  восстановлением личности по credential. RNG фиксируется тестом и вызывается
  ровно два раза на две сессии, включая все повторы и reconnect.
- Остальные роли получают403 при покупке/preview. Чужой credential в GET/
  hiring/preview получает401 в обоих направлениях между сессиями.
  Состояние второй сессии проверяется неизменным после действий первой.
- P&L по готовым входам Excel даёт -140.86/534.14; постоянный баланс остаётся
  675.00, фаза act_one. Клиентская база и завершение акта не рассчитываются.
- Игровые ответы проверяются на секретные поля, реальные credential и SHA256.
  После закрытия HTTP-клиентов новый engine выполняет только SELECT:
  независимо сверяются состояния, капитаны, участники, digest и роли.
- README описывает сценарии и проверки; прежнее утверждение об отсутствии
  экономических актов обновлено до фактического первого API-среза. Сохранены
  инструкции запуска и ограничение браузерного WebSocket.

Продуктовые Python-файлы и миграции побайтно сохранены относительно
`/private/tmp/ipo-team-review-snapshots/IPO-020/before`; менялись только
новый тест, README и этот отчёт.

## What you tested and test results

Рабочая папка:
`/private/tmp/ipo-team-implementation-egw7qray/backend`.
Python:
`/Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python`.

Предварительные проверки исполнителя без PostgreSQL:

```text
python -m pytest tests/test_act_one_end_to_end.py --collect-only -q
3 tests collected in 0.03s

python -m ruff check .
All checks passed!

python -m ruff format --check .
40 files already formatted
```

Первый focused-запуск контроллера на PostgreSQL выявил дефект нового тестового
helper, не продуктового кода:

```text
python -m pytest tests/test_act_one_end_to_end.py -q --tb=short
3 failed in 1.96s
```

Причина: `_assert_no_secrets` запрещал слово `credential` в любой части JSON,
поэтому ожидаемый 401 `{"detail":"Invalid credentials"}` ошибочно считался
утечкой (строка56 первого снимка). Исправление: рекурсивно запрещены секретные
ключи credential/participant_credential/credential_digest/digest/token/access_token,
а реальные значения всех восьми credential и их SHA256 по-прежнему запрещены
во всём сериализованном payload. Обычный текст detail разрешён. Проверка реальных
токенов не ослаблена, продуктовый код не менялся.

Исполнитель проверил helper без БД: обычный detail принят; пять кейсов
вложенных секретных полей, реального credential и его SHA256 отклонены.
Вывод: `Helper accepts ordinary detail and rejects five secret leak cases.`
После исправления повторены Ruff check/format с результатами выше; код снова
заморожен.

Контроллер выполнил итоговые команды на выделенной настоящей PostgreSQL с
require_escalated для localhost:

```text
env TEST_DATABASE_URL=postgresql+psycopg://ipo_test_runner@127.0.0.1:55439/ipo_act_one_test /Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python -m pytest tests/test_act_one_end_to_end.py -q --tb=short
3 passed in 2.50s

env TEST_DATABASE_URL=postgresql+psycopg://ipo_test_runner@127.0.0.1:55439/ipo_act_one_test /Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python -m pytest -q --tb=short
460 passed in 34.05s
exit 0, без предупреждений
```

Результаты переданы контроллером. Исполнитель не подключался к PostgreSQL
из sandbox. Код и README после последней заморозки не менялись.

## TDD Evidence

TDD brief не требовал. Первый отрицательный focused-запуск выявил ошибку
тестовой проверки, история исправления и итоговый GREEN приведены выше.

## Files changed

- `backend/tests/test_act_one_end_to_end.py` — три сквозных сценария.
- `backend/README.md` — сквозная проверка и актуальная область реализации.
- `docs/tasks/IPO-020/report.md` — этот отчёт.

## Self-review findings

Просмотрен полный diff относительно before020. Проверены только публичный
HTTP setup, отдельные actor/credential, независимые ожидаемые значения,
два вызова RNG, идемпотентность, сравнение второй сессии, отсутствие прямых
записей БД и неизменность продуктовых файлов. Первый self-review не выявил
ошибку поиска слова credential; её обнаружил контроллер focused-запуском.
После исправления отдельно проверены рекурсивные ключи и реальные секретные
значения, затем focused/full PostgreSQL suite прошли полностью.
Итоговых замечаний нет. Предыдущие отчёты и чужие изменения сохранены.

## Any issues or concerns

Нет. Готово к независимому ревью; коммит выполняет контроллер после одобрения.
Коммиты не создавались. HTTP TestClient моделируют отдельные клиенты;
браузерный UI и полный цикл экономического акта остаются вне проверки.
