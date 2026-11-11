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

Версии прямых зависимостей закреплены. WebSocket лобби проверен на Python 3.12.5,
Uvicorn 0.54.0 и websockets 17.4.
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

`tests/test_end_to_end_scenario.py` проверяет полный сценарий IPO-009 на настоящей
PostgreSQL: администратор создаёт две сессии, несколько независимых HTTP/WebSocket
клиентов присоединяются, после обновления клиента личность восстанавливается по
сохранённому credential, затем администратор запускает подготовку. Проверяются
снимки и обновления WebSocket, сохранённый статус и участники, а также изоляция
сессий и credential. Репозиторий не содержит frontend, поэтому эти автоматические
HTTP/WebSocket-клиенты моделируют отдельные браузеры; проверка настоящего браузерного
интерфейса не выполняется. Отдельный запуск из `backend`:

```powershell
python -m pytest tests/test_end_to_end_scenario.py
```

Успешный результат — `1 passed`, HTTP `201` при создании/входе, идентичность
с прежним ID после «обновления», снимки и обновления WebSocket с ID только своей
сессии при запуске подготовки в обеих сессиях, а также соответствующие участники
и статусы в PostgreSQL. Полный `python -m pytest` дополнительно выполняет остальные
backend-тесты.

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
Статус хранится текстом с начальным значением `lobby`; администратор может
перевести сессию в `preparation` через API. Ограничение на набор статусов
в БД не вводится.
Участники хранятся отдельно в `participants` и связаны с сессией через внешний ключ.
Миграция `0003_participants` добавляет эту таблицу; отображаемые имена не уникальны.

Документация: [SQLAlchemy + psycopg](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg),
[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html).


## Создание сессии через API

Endpoint требует Bearer-токен администратора. Перед запросом задайте `DATABASE_URL` и примените
`python -m alembic upgrade head`, как описано выше. Затем запустите сервер.
В этом же окне сначала настройте администратора по следующему разделу.

```powershell
$body = @{ name = '  Команда 1  ' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/sessions `
    -Headers @{ Authorization = "Bearer $($login.access_token)" } `
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

## Начало подготовки сессии

Только администратор с действующим Bearer-токеном может начать подготовку:

```powershell
Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/sessions/$sessionId/preparation/start" `
    -Headers @{ Authorization = "Bearer $($login.access_token)" }
```

Для статуса `lobby` endpoint возвращает `200` и состояние сессии со статусом
`preparation`. Подписанные WebSocket-клиенты получают обновлённый снимок лобби;
HTTP-снимок лобби также показывает новый статус. Повторный запрос возвращает
тот же текущий статус без повторного изменения; он также повторно публикует
актуальный снимок, чтобы восстановить уведомление после временной ошибки.
Подписчики могут получить тот же снимок повторно. Неизвестный UUID возвращает
`404`, неверная или отсутствующая авторизация — `401`. Параллельные запросы
сериализуются блокировкой строки в PostgreSQL.

## Вход участника в сессию

Участник присоединяется по UUID приглашённой сессии; отдельный публичный список
сессий и дополнительные права для участника в этом API не вводятся. Имя является
только отображаемой подписью: совпадающие имена создают разные записи с разными
ID и учётными данными и не дают доступа к чужой записи.

```powershell
$sessionId = 'UUID сессии из приглашения'
$body = @{ display_name = 'Аналитик' } | ConvertTo-Json
$participant = Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/sessions/$sessionId/participants" `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($body))
# Не выводите participant_credential; сохраните его на клиенте для восстановления.
```

`POST /sessions/{session_id}/participants` принимает `display_name` длиной
1–255 символов после обрезки пробелов и возвращает `201` с `id`,
`display_name`, случайным `participant_credential` и `token_type="bearer"`.
Секрет показывается только при присоединении. Сервер хранит только SHA-256 digest
случайного токена, не сам bearer credential. Клиенту необходимо сохранить его
в собственном защищённом хранилище, чтобы восстановить личность после обновления
страницы или переподключения.

Для восстановления клиент передаёт этот credential как Bearer-токен:

```powershell
$identity = Invoke-RestMethod -Method Get `
    -Uri "http://127.0.0.1:8000/sessions/$sessionId/participants/me" `
    -Headers @{ Authorization = "Bearer $($participant.participant_credential)" }
```

`GET /sessions/{session_id}/participants/me` возвращает ID, отображаемое имя и
время создания; credential в этом ответе отсутствует. Неверный, отсутствующий
или относящийся к другой сессии credential даёт `401`; неизвестная сессия при
присоединении даёт `404`. Невалидное имя даёт `422`, недоступность БД —
санитизированный `503`. Идентичность определяется только случайным credential,
а не именем.

Авторизованный участник получает актуальный снимок лобби обычным HTTP-запросом:

```powershell
$lobby = Invoke-RestMethod -Method Get `
    -Uri "http://127.0.0.1:8000/sessions/$sessionId/lobby" `
    -Headers @{ Authorization = "Bearer $($participant.participant_credential)" }
```

`GET /sessions/{session_id}/lobby` возвращает ID и имя сессии, её текущий статус,
а также ID и отображаемое имя всех её участников. Credential нужен в Bearer
заголовке, но credential и его digest в ответ не включаются. Неизвестная сессия
даёт `404`; отсутствующий, неверный или относящийся к другой сессии credential —
`401`. Это разовый HTTP-снимок; новый запрос после переподключения получает
актуальный список.

## WebSocket лобби

Установите зависимости через `python -m pip install -r requirements.txt`.
Uvicorn автоматически выбирает установленный `websockets`. Подготовьте БД и
настройте администратора по разделам [«Локальная PostgreSQL»](#локальная-postgresql)
и [«Вход администратора»](#вход-администратора), затем запустите из `backend`:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Подключайтесь к `/sessions/{session_id}/lobby/ws`. При соединении сервер отправляет
такой же снимок, как HTTP endpoint, а после присоединения нового участника рассылает
обновлённый снимок всем подключённым участникам этой сессии. Переподключение
также сразу возвращает актуальное состояние. Доступ остаётся ограничен ID
сессии; неверный или выданный для другой сессии credential отклоняется до
принятия WebSocket-соединения.

Для авторизации при handshake передайте `Authorization: Bearer <credential>`
или предложите subprotocol `ipo-participant.<credential>`. С действительным
credential в subprotocol сервер отвечает `101` без согласованного subprotocol.
Этот способ проверен Python-клиентом `websockets` в
[сетевом сценарии](#проверка-через-uvicorn).

Встроенный браузерный `WebSocket` не позволяет задать заголовок `Authorization`.
При предложенном subprotocol браузер отклоняет ответ `101` без заголовка
`Sec-WebSocket-Protocol`. Поэтому авторизованное подключение через браузерный
`WebSocket` с текущим контрактом недоступно.
[Стандарт WHATWG WebSockets, шаг 11.2](https://websockets.spec.whatwg.org/#concept-websocket-establish).

Снимок содержит только ID и отображаемые имена участников, ID, имя и статус сессии.
Credential и его digest в снимок не включаются. Отключение клиента удаляет его
из рассылки. Переход в подготовку реализован через API выше. Экономические акты
и пользовательский интерфейс пока не реализованы.

### Проверка через Uvicorn

Используйте отдельную PostgreSQL БД с именем, оканчивающимся на `_test`, и
вымышленные учётные данные. Укажите URL этой БД в `TEST_DATABASE_URL` по примеру
из раздела [«Локальная PostgreSQL»](#локальная-postgresql). В отдельном PowerShell
из `backend` примените миграции и запустите сетевую проверку:

```powershell
$env:DATABASE_URL = $env:TEST_DATABASE_URL
python -m alembic upgrade head
python smoke_lobby_websocket.py
```

Клиенту достаточно `requirements.txt`. Скрипт запускает Uvicorn на свободном
порту `127.0.0.1`, задаёт вымышленные данные администратора и останавливает сервер
после проверки. Он проверяет `101`, первый снимок, рассылку при входе второго
участника, переподключение и `403` для чужого или отсутствующего credential.
Сессии и участники остаются в проверочной БД. Для `pytest` используйте другую
пустую БД `*_test`.

`python -m pytest tests/test_participants.py` проверяет присоединение,
восстановление после нового HTTP-клиента, отдельные идентичности при совпадающих
именах, изоляцию по сессии, ошибки авторизации и отсутствие исходного credential
в PostgreSQL и в снимке лобби. `python -m pytest tests/test_lobby_websocket.py`
проверяет WebSocket-снимок и обновления, авторизацию handshake, переподключение,
отключение и отсутствие credential/digest в payload.

## Вход администратора

В предварительной модели есть один администратор из окружения, без таблицы
пользователей, регистрации, refresh-токенов и интерфейса входа. Используются
PyJWT 2.10.1 и argon2-cffi 25.1.0. Argon2 — рекомендуемый алгоритм в
[официальном руководстве FastAPI](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/).
Готовая библиотека Argon2 проверяет хеш; криптография приложения не реализуется.

В окне запуска сервера задайте три переменные. Пароль вводится без отображения,
хеш и случайный ключ передаются из дочернего Python в переменные PowerShell
без вывода в консоль. Не сохраняйте эти значения в репозитории или истории команд:

```powershell
$env:ADMIN_USERNAME = Read-Host 'Логин администратора'
$env:ADMIN_PASSWORD_HASH = python -c "from getpass import getpass; from argon2 import PasswordHasher; p=getpass('Admin password: '); assert p, 'Password must not be empty'; print(PasswordHasher().hash(p))"
$env:JWT_SIGNING_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`ADMIN_USERNAME` — непустая строка; `ADMIN_PASSWORD_HASH` — корректный Argon2-хеш
непустого пароля; `JWT_SIGNING_KEY` — случайный секрет минимум 32 байта UTF-8
(проверка длины не доказывает случайность, используйте генератор выше).
Переменные читаются при запросе, `.env` автоматически не загружается.
При отсутствующей или испорченной настройке вход и защищённый доступ дают `401`.
Ключа по умолчанию нет. `/health` не требует этих настроек и не обращается к БД.

Во втором окне выполните JSON-вход, затем команду создания сессии выше:

```powershell
$credential = Get-Credential -Message 'Администратор IPO'
$body = @{ username = $credential.UserName; password = $credential.GetNetworkCredential().Password } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/login `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
Remove-Variable body, credential
# Не выводите access_token: используйте $login в Authorization при создании сессии.
```

Успех входа: `200` с `access_token`, `token_type="bearer"`, `expires_in=1800`.
JWT содержит только логин в `sub` и срок в `exp`; содержимое JWT доступно читателю
токена, пароль и хеш туда не включаются. Подпись HS256 фиксирована сервером;
`sub` и `exp` обязательны, `sub` должен совпадать с настроенным администратором.
Неверный логин или пароль дают одинаковый `401 {"detail":"Invalid credentials"}`.
Некорректное тело входа даёт `422 {"detail":"Invalid login request"}` без отражения
введённых данных. Отсутствующий, просроченный, подменённый или некорректный Bearer
даёт `401` до обращения к БД и создания записи. После истечения 30 минут войдите
повторно. Смена ключа аннулирует прежние токены; смена пароля сама по себе их
не отзывает. Индивидуальный отзыв, rate limiting и аудит входов пока не реализованы.
Для доступа вне loopback требуется HTTPS на уровне развёртывания.

`python -m pytest tests/test_auth.py` проверяет вход, JWT, безопасные ошибки
и отказы с проверкой отсутствия записи на выделенной PostgreSQL. Полный набор
сохраняет прежние проверки IPO-001–003 и использует настоящий вход для API-тестов.
