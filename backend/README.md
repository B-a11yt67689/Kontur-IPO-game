# IPO backend

HTTP-сервис и постоянное хранение игровых сессий в PostgreSQL через SQLAlchemy,
psycopg и Alembic. `/health` подтверждает только ответ HTTP-процесса.

Проверено в Windows PowerShell на Python 3.12.10. Нужен установленный Python 3.12.
Команды ниже выполняются из папки `backend`.

## Окружение и зависимости

В примере указан проверенный путь Python из Microsoft Store. Если Python 3.12
установлен иначе, задайте в `$python312` полный путь к своему `python.exe`.

```powershell
$python312 = "$env:LOCALAPPDATA\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"
& $python312 --version
$venv = ".venv"
& $python312 -m venv $venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
& "$venv\Scripts\Activate.ps1"
python --version
python -m pip install -r requirements.txt
```

Политика RemoteSigned применяется только к текущему процессу PowerShell и позволяет
активировать локально созданную среду. После закрытия окна она не сохраняется.

Для разработки дополнительно установите тесты и Ruff:

```powershell
python -m pip install -r requirements-dev.txt
```

Прямые зависимости закреплены на версиях, проверенных вместе на Python 3.12.10.
Starlette 0.52.1 и AnyIO 4.9.0 также закреплены для совместимости TestClient
с HTTPX 0.28.1 без предупреждений об устаревшем API.
Для повторной проверки в чистой среде используйте `$venv = ".venv-readme"`
вместо `.venv` в блоке создания окружения, затем выполните остальные команды.

## Запуск

В терминале с активированным окружением:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Во втором окне PowerShell:

```powershell
$response = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
$response.StatusCode
$response.Headers['Content-Type']
$response.Content
```

Ожидается `200`, `application/json` и ровно `{"status":"ok"}`.
Повторный запрос возвращает тот же ответ. Аккаунт, БД и секретные переменные
не требуются. Остановка сервера: `Ctrl+C`.

## Проверки

Из `backend` с активированным окружением:

Перед полным запуском подготовьте отдельную пустую тестовую БД и задайте
`TEST_DATABASE_URL` по инструкции ниже. Отсутствующая настройка приводит к ошибке
интеграционных тестов, а не к их пропуску.

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Выход из окружения: `deactivate`.

## Локальная PostgreSQL

Нужен PostgreSQL 13+ (`gen_random_uuid()` доступна без расширений). Проверено на
Windows, PostgreSQL 18.3 и Python 3.12.10. Docker не требуется. Установите PostgreSQL
с официального сайта, если его ещё нет. Следующий пример создаёт **новый** локальный
кластер; не указывайте каталог существующего кластера. Команды из `backend`:

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$pgData = Join-Path $env:LOCALAPPDATA 'ipo-local-postgres'
if (Test-Path $pgData) { throw 'Выберите новый каталог для кластера' }
& "$pgBin\initdb.exe" -D $pgData -U ipo -A scram-sha-256 --pwprompt --encoding=UTF8 --locale=C
& "$pgBin\pg_ctl.exe" -D $pgData -l "$pgData\server.log" -o '-h 127.0.0.1 -p 55432' -w start
& "$pgBin\createdb.exe" -h 127.0.0.1 -p 55432 -U ipo -W ipo
& "$pgBin\createdb.exe" -h 127.0.0.1 -p 55432 -U ipo -W ipo_test
```

`--pwprompt` задаёт пароль интерактивно, `-W` запрашивает его для создания БД.
Если порт занят, выберите свободный и измените его во всех командах и URL.
При наличии своей PostgreSQL достаточно создать две отдельные базы с правами
на создание таблиц; настройку существующих служб менять не нужно.

`.env.example` содержит только вымышленные настройки. Файлы `.env` автоматически
не загружаются. Задайте переменные в текущем PowerShell. Следующий вариант запрашивает
пароль без отображения и кодирует специальные символы для URL:

```powershell
$credential = Get-Credential -UserName ipo -Message 'Пароль локальной PostgreSQL'
$dbPassword = [Uri]::EscapeDataString($credential.GetNetworkCredential().Password)
$env:DATABASE_URL = "postgresql+psycopg://ipo:${dbPassword}@127.0.0.1:55432/ipo"
$env:TEST_DATABASE_URL = "postgresql+psycopg://ipo:${dbPassword}@127.0.0.1:55432/ipo_test"
Remove-Variable dbPassword, credential
python -m alembic upgrade head
python -m alembic upgrade head
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Не выводите переменные с URL и не сохраняйте настоящие пароли в коде или Git.
Миграции читают `DATABASE_URL`. Приложение не создаёт таблицы при запуске.
Alembic можно откатить командой `python -m alembic downgrade base`; она удаляет
`game_sessions` вместе с её данными, поэтому используйте её только намеренно.

Тесты читают **только явно заданную** `TEST_DATABASE_URL`; имя базы должно
заканчиваться на `_test`, база должна отличаться от `DATABASE_URL`, её схема `public`
должна быть пустой. Тесты откажутся менять непустую схему. Они применяют миграцию
дважды, создают данные и в конце удаляют только созданные ими таблицы откатом
миграции и удалением `alembic_version`. Используйте выделенную БД без других
пользователей/параллельных запусков. После аварийного прерывания проверьте остатки
вручную или создайте новую пустую тестовую БД; автоматического сброса чужих данных нет.
Только HTTP-проверку можно выполнить без БД: `python -m pytest tests/test_health.py`.

Остановка созданного кластера (из того же окна PowerShell):

```powershell
& "$pgBin\pg_ctl.exe" -D $pgData -m fast -w stop
```

## Использование хранения и ограничения модели

`create_database_engine()` из `app.database` читает `DATABASE_URL` при вызове,
не при импорте HTTP-приложения. Владелец engine освобождает пул через `dispose()`.
`with transaction(engine) as session:` открывает ORM-сессию, фиксирует успешную
операцию, откатывает исключение и закрывает сессию. Внутри можно вызвать
`session.add(GameSession(name="Команда"))` из `app.models`.

Одна сессия соответствует одной команде. PostgreSQL генерирует UUID и время
`TIMESTAMP WITH TIME ZONE`; обязательность всех полей и уникальность UUID
защищены БД. Названия могут повторяться. CHECK-ограничение `ck_game_sessions_name_length`
ограничивает текстовое поле 255 символами; API дополнительно удаляет пробелы
по краям и отклоняет пустое название. Миграция `0002_session_name_length`
проверяет существующие данные и откажется применяться при названиях длиннее 255;
она не обрезает и не удаляет их. Такие данные нужно согласованно исправить
перед повторным применением миграции.
Статус хранится текстом с начальным значением `lobby`; список последующих статусов
и переходы ещё не определены, ограничение на их набор не вводится.

Документация: [SQLAlchemy + psycopg](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg),
[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html).


## Создание сессии через API

Endpoint доступен только для локальной разработки; авторизация администратора
будет отдельной задачей. Перед запросом задайте `DATABASE_URL` и примените
`python -m alembic upgrade head`, как описано выше. Затем запустите сервер.

```powershell
$body = @{ name = '  Команда 1  ' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/sessions `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Ожидается `201 Created` и JSON с `id` (UUID), `name` (`Команда 1`),
`status` (`lobby`) и `created_at` (ISO 8601 с часовым поясом). Допустимо
1–255 символов после удаления пробельных символов по краям. Одинаковые названия
создают разные UUID. Пустое, слишком длинное или отсутствующее название,
нестроковое значение и дополнительные поля (в том числе `id`, `status`,
`created_at`) возвращают `422` без записи.

Ответ создаётся из серверных defaults после `flush` и возвращается только после
успешного commit. При ошибке подключения, записи или commit возвращается `503`
с `{"detail":"Session creation unavailable"}` без SQL, DSN и traceback.
ORM-сессия закрывается, engine освобождается после каждого запроса.
`/health` продолжает работать независимо от настройки и доступности БД.

`python -m pytest tests/test_sessions.py` проверяет API на настоящей выделенной
PostgreSQL с теми же требованиями к `TEST_DATABASE_URL`, что и полный набор.
В том числе тесты читают запись через новый engine после ответа, вызывают
реальную ошибку записи и отложенную до commit ошибку PostgreSQL и проверяют откат.
