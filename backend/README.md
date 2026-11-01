# IPO backend

Минимальный HTTP-сервис. `/health` подтверждает только ответ HTTP-процесса.

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

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Выход из окружения: `deactivate`.
