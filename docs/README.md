# Первый акт — IPO-015–IPO-020

Карточки задач составлены по материалам проекта и поручению пользователя
самостоятельно определить описания. Выбранные трактовки и происхождение
параметров зафиксированы в [решениях первого акта](ACT-ONE-DECISIONS.md).
Исходные Holst/Excel, материалы встречи и роли агентов сохранены.

Проверка готовой реализации: **460 passed**, три сквозных сценария, Ruff
без замечаний. Все задачи получили независимое **Approved**.

## Задачи

| Задача | Реализация | Материалы |
| --- | --- | --- |
| IPO-015 | Детерминированный результат d10 и десять стартовых бонусов | [brief](tasks/IPO-015/brief.md), [review brief](tasks/IPO-015/review-brief.md), [report](tasks/IPO-015/report.md), [review](tasks/IPO-015/review.md) |
| IPO-016 | Однократный запуск, сохранение состояния и миграция0006 | [brief](tasks/IPO-016/brief.md), [review brief](tasks/IPO-016/review-brief.md), [report](tasks/IPO-016/report.md), [review](tasks/IPO-016/review.md) |
| IPO-017 | Авторизованное чтение состояния своей сессии | [brief](tasks/IPO-017/brief.md), [review brief](tasks/IPO-017/review-brief.md), [report](tasks/IPO-017/report.md), [review](tasks/IPO-017/review.md) |
| IPO-018 | Первая ступень найма за30AP, вместимость2→3 | [brief](tasks/IPO-018/brief.md), [review brief](tasks/IPO-018/review-brief.md), [report](tasks/IPO-018/report.md), [review](tasks/IPO-018/review.md) |
| IPO-019 | P&L по готовым входам и авторизованный API предпросмотра | [brief](tasks/IPO-019/brief.md), [review brief](tasks/IPO-019/review-brief.md), [report](tasks/IPO-019/report.md), [review](tasks/IPO-019/review.md) |
| IPO-020 | Сквозной HTTP-сценарий двух изолированных сессий | [brief](tasks/IPO-020/brief.md), [review brief](tasks/IPO-020/review-brief.md), [report](tasks/IPO-020/report.md), [review](tasks/IPO-020/review.md) |

## API

| Метод и путь | Доступ | Результат |
| --- | --- | --- |
| POST /sessions/{id}/acts/1/start | Администратор | Создать акт или вернуть текущее сохранённое состояние |
| GET /sessions/{id}/acts/1 | Участник своей сессии | Получить снимок акта |
| POST /sessions/{id}/acts/1/hiring/upgrade | Роль people своей сессии | Купить первую ступень один раз |
| POST /sessions/{id}/acts/1/pnl/preview | Роль cfo своей сессии | Рассчитать P&L без изменения игрового состояния |

Начало требует статуса preparation, капитана и всех четырёх ролей. После начала
статус — act_one. Бросок выбирает сервер; первые два POST принимают пустую
команду. Деньги возвращаются строками Decimal. Подробные тела и коды ошибок —
в [инструкции бэкенда](../backend/README.md).

Бонусы +10AP и +10 клиентов применяются при старте. Другие стартовые карточки
сохраняются как доступные разовые права; их активация относится к дальнейшим
игровым действиям. Найм повышает вместимость, сохраняя две фактически нанятые
junior-команды. Предпросмотр P&L возвращает результат и расчётный остаток;
постоянный баланс и текущая фаза сохраняются. Полный переход к следующему акту,
кредиты, эксперименты и риски относятся к дальнейшим задачам.

## Проверка

Применить миграцию в настроенной БД приложения: из backend выполнить
`python -m alembic upgrade head`. Для тестов нужна отдельная пустая PostgreSQL
с именем, заканчивающимся на `_test`, в `TEST_DATABASE_URL`. Проверки из backend:

```sh
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Тесты используют настоящую PostgreSQL, отдельные HTTP-клиенты, транзакции,
конкурирующие запросы и ошибки commit. Фактические результаты и независимые
заключения сохранены рядом с карточками задач. Пользовательский интерфейс
в этот API-срез не входит.
