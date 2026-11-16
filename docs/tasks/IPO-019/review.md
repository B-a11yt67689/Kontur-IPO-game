# IPO-019 — независимое ревью

Ревьюер: отдельный агент `ipo_019_reviewer`, роль `ipo-task-reviewer`.
Проверен полный пакет незакоммиченных файлов задачи относительно
`c986bbf14511197ad352976a04cb8ab59541f637`.
SHA-256 пакета: `464a758d8efda938f8de91f942a7f9869a529c602f7607cfac0212d54fce41f5`.

## Spec Compliance

- ✅ Сигнатура, неизменяемые структуры, валидация, константы семи актов и
  формулы соответствуют brief — `backend/app/pnl.py:10`, `:28`, `:61`, `:72`.
- ✅ Расчёт использует Decimal, отдельно вычитает рекламу, допускает
  отрицательный баланс и не обращается к БД или HTTP — `backend/app/pnl.py:72`.
- ✅ Все предусмотренные brief файлы представлены; публичный контракт и
  границы расчёта описаны — `backend/README.md:420`.

## Strengths

- Конечность Decimal проверяется до сравнения с нулём; bool явно исключён
  из целочисленных полей — `backend/app/pnl.py:18`, `:39`.
- Тесты проверяют реальные результаты трёх примеров Excel, константы всех
  актов и однократный вычет рекламы — `backend/tests/test_pnl.py:56`, `:93`, `:114`.
- Проверены дробные значения, отрицательные балансы, неизменяемость и
  недопустимые входы — `backend/tests/test_pnl.py:125`, `:163`, `:172`, `:186`.
- Отчёт различает полный прогон до переименования и 115 целевых тестов
  с Ruff после него — `docs/tasks/IPO-019/report.md:68`, `:80`.

## Issues

Critical, Important и Minor: не обнаружены.

## Assessment

**Task quality: Approved.** Реализация воспроизводит формулы brief без
дополнительных игровых правил; входной контракт и обязательные граничные
случаи покрыты содержательными тестами. Оснований для дополнительных запусков
тестов при чтении снимка ревьюер не обнаружил.

Заключение относится к карточке IPO-019, составленной по переданным источникам,
а не к отсутствующему оригинальному brief или остальным задачам IPO-015–IPO-020.


---

## Дополнение: API первого акта

# IPO-019 — независимое ревью

Краткая запись полного заключения отдельного агента `/root/review_019_api` по роли ipo-task-reviewer. Ссылки на строки относятся к проверенному снимку.

```text
IPO-019: complete working-copy change packet
Base snapshot: acb96a18d7b698a72f99a40e7d864cb05d42009d88677605edb33de58ffcfdc9
Head snapshot: a8576ce821e2fb17c46d049835a7f793bc080ee3fc43f18a83d6117bfa80f081
Changed files: 7
```

## Spec Compliance

✅ Spec compliant. CFO-only preview использует серверные act/balance, единый снимок и прежнюю pure-функцию. Decimal и безопасный422 проверены; refactor сохраняет lock order hiring.

## Strengths

Предметные свидетельства: backend/app/act_one.py:164; backend/app/act_one.py:195; backend/app/main.py:289; backend/tests/test_act_one_pnl.py:352.
Прочитаны результаты проверок: 457 passed in31.31s; Ruff чист.

## Issues

Critical, Important и Minor: не обнаружены.

## Assessment

**Task quality: Approved.** CFO-only preview использует серверные act/balance, единый снимок и прежнюю pure-функцию. Decimal и безопасный422 проверены; refactor сохраняет lock order hiring.

[Полный пакет изменений](review.diff).
