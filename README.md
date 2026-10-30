# Выход на IPO — FastAPI MVP

Python 3.10+.

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Откройте http://127.0.0.1:8000/docs

## Порядок действий

1. `POST /games` с `{"name":"Выход на IPO"}` — получите `game_id`, `host_token`.
2. Передавайте `Authorization: Bearer <host_token>` для действий ведущего.
3. `POST /games/{game_id}/teams` — создайте команду.
4. `POST /teams/{team_id}/members` — добавьте четыре роли: `cfo`, `cto`, `people`, `cmo`. Каждый получает свой `member_token`.
5. `POST /games/{game_id}/start` — старт.
6. Участник с любым `member_token` отправляет `POST /games/{game_id}/proposals` с `{"domain":"cmo","content":"Провести маркетинговый эксперимент"}`.
7. Только участник с ролью `cmo` вызывает `POST /proposals/{proposal_id}/approve`.
8. Ведущий вызывает `POST /decisions/{decision_id}/confirm`.
9. Ведущий меняет `PATCH /teams/{team_id}/metrics` и переключает этапы через `POST /games/{game_id}/advance`.

## Ограничения

Прототип для локального запуска. Нет frontend, чата, паролей, восстановления токенов и полной экономики настольной игры. Броски кубиков и изменение метрик вводит ведущий. При новом акте автоматически начисляется 130 очков, без дополнительных бонусов. Этапы переключаются вручную; наличие решений от каждой роли не обязательно.
