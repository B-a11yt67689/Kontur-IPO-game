# IPO-018 — отчёт исполнителя

**Status:** DONE

## What you implemented

- Добавлен `POST /sessions/{session_id}/acts/1/hiring/upgrade`: participant
  Bearer своей сессии с ролью people, полный ActOneSnapshot после commit.
- Общий `require_session_role` проверяет участника и роль до чтения фазы,
  без загрузки изменяемого состояния. Неизвестный UUID/неверный credential/
  чужая сессия/администраторский JWT дают одинаковый 401 с WWW-Authenticate;
  своя роль без people — 403, включая капитана.
- Затем блокируется строка GameSession общим `locked_game`; ActOneState
  загружается только после получения блокировки, чтобы ожидающий запрос
  увидел свежие ресурсы. В активном первом акте первая покупка при AP>=30
  списывает серверную константу 30 AP и меняет level 0→1, capacity 2→3.
- Уже купленный level=1 возвращает текущий снимок без списания, даже при
  AP<30. Повторные и параллельные запросы идемпотентны. Недостаток AP,
  неподходящая фаза или отсутствие состояния — 409 без изменений.
- Деньги, клиенты, состав из двух junior, эксперименты, started_at и
  стартовый бонус сохраняются. Общая транзакция санитизирует ошибки записи
  и commit в 503 с полным откатом.
- Пустая команда переименована в `EmptyActOneCommand` и используется
  существующим start и новой покупкой; нет тела/null/{} разрешены,
  дополнительные поля запрещены. Контракт start сохранён.
- README объясняет API и разницу вместимости и фактического состава.

## What you tested and test results

Все команды выполнялись из
`/private/tmp/ipo-team-implementation-egw7qray/backend`.
Исполняемый Python:
`/Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python`.

До freeze исполнитель проверил сборку тестов и Ruff без доступа к БД:

```text
python -m pytest tests/test_act_one_hiring.py --collect-only -q
27 tests collected in 0.05s

python -m ruff check .
All checks passed!

python -m ruff format --check .
38 files already formatted
```

После freeze контроллер выполнил полный suite на выделенной настоящей
PostgreSQL с require_escalated для localhost:

```text
env TEST_DATABASE_URL=postgresql+psycopg://ipo_test_runner@127.0.0.1:55439/ipo_act_one_test /Users/yaroslavmikhailov/Kontur-IPO-game/backend/.venv/bin/python -m pytest -q --tb=short
364 passed in 28.28s
exit 0, без предупреждений
```

Результат передан контроллером. Исполнитель не подключался к PostgreSQL
из sandbox; код после freeze не менялся.

27 новых проверок включены в полный suite и покрывают:

- AP130→100, AP140→110 и бонус клиентов; полное сравнение снимка/записи
  подтверждает сохранение всех остальных ресурсов и двух junior.
- Повтор, reconnect, GET и повтор start возвращают покупку, RNG вызывается
  только при первоначальном старте.
- AP0/AP29 — 409 без изменений; AP30 — успех с AP0, повтор с AP0 — 200.
- Отсутствующий/неверный/чужой Bearer, неизвестный UUID, admin JWT, все
  остальные роли, капитан и участник без роли; auth/role до фазы.
- Неверная фаза, отсутствие состояния, формы пустой команды и лишние поля.
- Настоящая очередь двух параллельных покупок наблюдается через pg_locks;
  обе возвращают одинаковый результат, списание происходит один раз.
- Отдельный regression test меняет AP на30, пока HTTP-запрос ждёт game lock;
  итог AP0 доказывает загрузку состояния после ожидания блокировки.
- Настоящие PostgreSQL triggers отклоняют UPDATE при записи и при
  отложенном commit: 503 без утечки, сохранение всей исходной записи,
  последующая успешная покупка после снятия trigger.
- Изоляция другой сессии и отсутствие обращения к БД без Bearer.

## TDD Evidence

TDD brief не требовал. Реализация и интеграционные тесты подготовлены одним
срезом; замороженный код подтверждён полным PostgreSQL suite.

## Files changed

- `backend/app/act_one.py` — общая проверка роли и покупка.
- `backend/app/act_one_api.py` — маршрут и переиспользование пустой команды.
- `backend/app/act_one_schemas.py` — общее имя EmptyActOneCommand.
- `backend/tests/test_act_one_hiring.py` — 27 проверок с фикстурами IPO-016.
- `backend/README.md` — контракт покупки.
- `docs/tasks/IPO-018/report.md` — этот отчёт.

## Self-review findings

Прочитан полный diff относительно
`/private/tmp/ipo-team-review-snapshots/IPO-018/before`.
Проверены авторизация до фазы, общий порядок блокировок, отсутствие
предварительно загруженного ORM state, ветка повторной покупки до проверки
AP, сохранность остальных ресурсов, граница commit, rollback и отсутствие
дублирования схемы команды. Старые отчёты, модели и миграции не менялись.
Ruff исправил форматирование до freeze. Замечаний нет.

## Any issues or concerns

Нет. Готово к независимому ревью; коммит выполняет контроллер после одобрения.
Коммиты не создавались.
