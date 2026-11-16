# IPO-019 — Implementation Report

**Status:** DONE

## What was implemented

- Добавлен независимый модуль `backend/app/pnl.py`: неизменяемые dataclass
  `PnlInputs` и `PnlResult`, чистая функция `calculate_act_pnl`.
- Серверные константы себестоимости заказа и расходов на бизнес заданы для всех
  семи актов. ARPC, выручка, CoGS, операционные расходы, финансовый результат и
  баланс после результата рассчитываются по формулам brief и исходного Excel.
- Реклама вычитается один раз и не включается в Operating Costs. Клиенты, ФОТ
  и рекламный бюджет поступают готовыми; правила расчёта клиентской базы,
  кредитования и банкротства не добавлялись.
- На входе проверяются типы и значения: целые акт 1–7 и неотрицательные клиенты
  без `bool`; конечные `Decimal`, неотрицательные кроме начального баланса.
  Неверный тип даёт `TypeError`, неверное значение — `ValueError`.
- Все выходные величины — `Decimal`. Преобразования во float и округления до
  копеек нет, вычисления используют действующий контекст Decimal.
- Добавлены тесты и раздел «Расчёт P&L» в `backend/README.md`.

Прочитаны brief, сохранённые инструкции роли `ipo-python-implementer`, индекс
проекта, итоги встречи 22 октября, «Работа с агентами IPO», «Метрики и формулы
IPO», «Неоднозначности правил IPO», «Проверка расчётов IPO» и соответствующие
строки полного извлечения Excel. Использованы формулы `8 PnL` строк 5, 7–9,
14–16 и `8 Итоги финансов после акта` строки 2. Ручные 105 клиентов акта 2
сохранены только как вход соответствующего примера Excel; расчёт этого числа
не реализован.

## Tests and results

Среда: Python 3.12, существующий `backend/.venv`; все команды ниже выполнялись
из `backend`. Существующие тесты и настройки окружения не изменялись.

1. `.venv/bin/python -m pytest tests/test_pnl.py -q`

   ```text
   112 passed in 0.06s
   ```

2. После форматирования:
   `.venv/bin/python -m pytest tests/test_pnl.py tests/test_act_one_baseline.py -q`

   ```text
   115 passed in 0.08s
   ```

3. `.venv/bin/python -m ruff check .`

   ```text
   All checks passed!
   ```

4. `.venv/bin/python -m ruff format --check .`

   ```text
   29 files already formatted
   ```

Покрыты все три примера Excel, константы и арифметика каждого акта, нулевые
клиенты, нулевые денежные входы и покупки, отдельный однократный вычет рекламы,
дробные значения без округления до копеек, положительный и отрицательный
финансовый результат, отрицательный начальный и конечный баланс,
детерминированность и неизменяемость входа/выхода. Параметризованные проверки
покрывают неверные типы каждого поля, `bool`, нечисловые и бесконечные Decimal,
отрицательные входы и границы номера акта.

Полный suite выполнил контроллер с выделенной PostgreSQL 17, из `backend`:

```text
env TEST_DATABASE_URL=postgresql+psycopg://ipo_test_runner@127.0.0.1:55439/ipo_act_one_test .venv/bin/python -m pytest -q --tb=short
238 passed in 8.95s
```

Exit code 0, предупреждений нет. Первый запуск контроллера внутри sandbox дал
`140 passed, 98 errors`: интеграционные тесты не смогли подключиться к localhost
из-за `Operation not permitted`. Повтор с escalated-доступом прошёл полностью.
P&L сам не обращается к БД, его focused-тесты БД не требуют.

Полный прогон выполнен **до** механического переименования публичной функции
в `calculate_act_pnl` по актуальной сигнатуре brief. После переименования
исполнитель проверил окончательный снимок повторно:

```text
.venv/bin/python -m pytest tests/test_pnl.py tests/test_act_one_baseline.py -q
115 passed in 0.08s

.venv/bin/python -m ruff check .
All checks passed!

.venv/bin/python -m ruff format --check .
29 files already formatted
```

Изменены только имя функции, импорт/вызовы в тестах и упоминания в документации;
формулы и валидация при переименовании не менялись.

## TDD Evidence

TDD в brief не требовался. RED-проход не выполнялся; результаты GREEN указаны
выше.

## Files changed

- `backend/app/pnl.py` — новый модуль расчёта и валидации P&L.
- `backend/tests/test_pnl.py` — новые тесты расчёта и валидации.
- `backend/README.md` — добавлен раздел «Расчёт P&L».
- `docs/tasks/IPO-019/report.md` — этот отчёт.

## Self-review findings

Перечитаны итоговые файлы и добавленный раздел README, проверены соответствие
формулам источника, наличие всех выходных полей и констант, однократный вычет
рекламы, порядок валидации нечисловых Decimal до сравнения с нулём и отсутствие
мутации входа. Все обязательные случаи brief покрыты. Замечаний к корректности
не осталось. Чужие файлы не менялись; Git-операции и запуск субагентов
исполнителем не выполнялись.

## Issues or concerns

Вопросов к корректности по согласованному brief нет. Независимое ревью ещё не
выполнено; коммит выполняет контроллер после одобрения. Новые игровые допущения
не вводились. Единственная применяемая точность — текущий контекст Decimal,
без дополнительного правила валютного округления.

---

## Продолжение 16 ноября — API предпросмотра

**Status:** DONE

### What you implemented

- Добавлен `POST /sessions/{session_id}/acts/1/pnl/preview`, доступный
  участнику cfo своей сессии. Общий reader получает авторизацию, роль,
  фазу и состояние одним SELECT; роль проверяется до фазы. GET IPO-017
  сохраняет один SELECT. Общая actor-query используется также покупкой.
- `PnlPreviewInputs` требует семь полей из дополнения brief и запрещает
  дополнительные. clients — strict int>=0; финансовые поля — конечные
  Decimal>=0, допускаются JSON-числа или строки.
- Сервер использует акт1 и сохранённый баланс, вызывает существующий
  `calculate_act_pnl` и возвращает все восемь полей результата строковыми
  Decimal. Preview не записывает результат и не меняет состояние/фазу/RNG.
  DecimalException, включая Overflow, даёт безопасный 422.
- В main.safe_validation добавлены только новые POST-команды start, hiring
  и preview. Их ошибки возвращают безопасный общий 422 без отражения input.
  Причина расширения: контроллер воспроизвёл на before019 конкретный дефект —
  сырой `NaN` в лишнем поле пустой команды вызывал 500, поскольку стандартный
  validation handler пытался сериализовать нечисловой float. Теперь
  `{"roll":NaN}` для start и `{"cost":Infinity}` для hiring дают 422;
  оба случая закреплены HTTP-регрессионными тестами. Старые API не менялись.
- README дополнен явным контрактом preview, входами, ответом и ошибками.

`backend/app/pnl.py` и `backend/tests/test_pnl.py` побайтно совпадают со снимком
`/private/tmp/ipo-team-review-snapshots/IPO-019/before`: формулы, исходная
валидация и одобренные pure-тесты не менялись. История отчёта выше сохранена.

### What you tested and test results

Рабочая папка:
`/private/tmp/ipo-team-implementation-egw7qray/backend`.
Python:
`/Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python`.

Исполнитель выполнил до freeze без PostgreSQL:

```text
python -m pytest tests/test_act_one_pnl.py --collect-only -q
93 tests collected in 0.05s

python -m pytest tests/test_act_one_pnl.py -k validation -q
82 passed, 11 deselected in 0.23s

python -m pytest tests/test_pnl.py tests/test_health.py tests/test_act_one_baseline.py tests/test_starting_bonus.py -q
160 passed in 0.07s

python -m ruff check .
All checks passed!

python -m ruff format --check .
39 files already formatted
```

Контроллер выполнил полный замороженный suite на выделенной настоящей
PostgreSQL с require_escalated для localhost:

```text
env TEST_DATABASE_URL=postgresql+psycopg://ipo_test_runner@127.0.0.1:55439/ipo_act_one_test /Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python -m pytest -q --tb=short
457 passed in 31.31s
exit 0, без предупреждений
```

Результат передан контроллером. Исполнитель не подключался к PostgreSQL
из sandbox; код и тесты после freeze не менялись.

93 новых кейса покрывают пример Excel1, строковые Decimal и JSON-числа,
использование сохранённого баланса без округления, повторы и отсутствие
мутаций/RNG; cfo/другие роли/капитана/участника без роли/чужую сессию/admin/
неизвестный UUID, auth/role до фазы и отсутствующее состояние; один SELECT
без записей; обязательные/лишние поля, строгий clients, каждый финансовый
вход с отрицательным/неконечным/неверным значением, malformed JSON, сырой
NaN/Infinity/слишком большой JSON-float; Decimal Overflow→422, БД503 без
утечек, два регрессионных теста пустых команд.

### TDD Evidence

TDD дополнение brief не требовало. Конкретный прежний 500 воспроизведён
контроллером на before019; итоговые регрессионные кейсы входят в полный
прошедший suite. Pure-история RED/GREEN выше не изменялась.

### Files changed

- `backend/app/act_one.py` — общий авторизованный reader с ролью и preview.
- `backend/app/act_one_api.py` — новый маршрут.
- `backend/app/act_one_schemas.py` — входы/результат preview.
- `backend/app/main.py` — безопасная валидация только новых POST-команд.
- `backend/tests/test_act_one_pnl.py` — 93 кейса.
- `backend/README.md` — контракт preview.
- `docs/tasks/IPO-019/report.md` — это дополнение.

### Self-review findings

Просмотрен diff относительно before019 и полный новый тестовый файл.
Проверены общий actor-query без копирования больших блоков, авторизация/роль
до фазы, согласованный снимок одним SELECT, сохранение GET single SELECT,
серверные act/balance, отсутствие записи/RNG, Decimal-ошибки и ограничение
handler тремя новыми командами. Проверено побайтовое равенство pure-файлов.
Замечаний нет. Предыдущие отчёты и чужие изменения сохранены.

### Any issues or concerns

Нет. Готово к независимому ревью; коммит выполняет контроллер после одобрения.
Коммиты не создавались.
