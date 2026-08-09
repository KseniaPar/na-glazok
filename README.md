# На Глазок

Telegram-бот, который считает **примерный КБЖУ** по ленивому описанию еды — с защитным **LLM Gateway** и циклом **Security Step** перед ответом пользователю.

> Отдельный проект. Не часть Knowbase (`ai-lab`).

---

## Зачем это

Обычный «чат с LLM» для еды легко ломается: в промпт подсовывают секреты, модель считает калории бензина, в ответ утекает PII или вредные советы. Здесь каждый запрос проходит цепочку:

```text
Telegram
   │
   ▼
┌──────────────┐     ┌─────────────────┐     ┌────────────┐
│  bot.py      │────▶│  LLM Gateway    │────▶│ OpenRouter │
│  + loop      │◀────│  :8000          │◀────│ gpt-4o-mini│
└──────────────┘     └─────────────────┘     └────────────┘
        │                     │
        │                     ├─ Input Guard (secrets / PII)
        │                     ├─ Output Guard (leak / phishing)
        │                     ├─ rate limit + cost audit
        │                     └─ audit.jsonl
        │
        └─ Execution Loop
              generate → validate ккал → Security Inspector → commit
```

---

## Возможности

| Слой | Что делает |
|------|------------|
| **Telegram-бот** | Диалог, память чата (SQLite, 7 дней), режимы промпта |
| **LLM Gateway** | OpenAI-compatible `/v1/chat/completions`, блокирует API-ключи, маскирует карты/email/телефон, режет опасный вывод |
| **Execution Loop** | Повторные попытки, если нет калорий; второй вызов ИИ — инспектор безопасности |
| **Security Step** | CRITICAL/HIGH → перегенерация; MEDIUM/LOW → ответ + WARNING в лог; CLEAN → сразу пользователю |

---

## Быстрый старт

### 1. Зависимости

```powershell
cd C:\Users\user\Projects\na-glazok
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Секреты

```powershell
Copy-Item .env.example .env
# отредактируйте .env → OPENROUTER_API_KEY=...

Copy-Item telegram-local.example.txt telegram-local.txt
# одна строка: токен от @BotFather
```

Либо переменные окружения:

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
$env:TELEGRAM_BOT_TOKEN = "..."
```

### 3. Два процесса

```powershell
# терминал 1 — шлюз (для loop удобно поднять лимит)
$env:GATEWAY_RATE_LIMIT = "40"
$env:PYTHONIOENCODING = "utf-8"
python -u gateway.py

# терминал 2 — бот
$env:PYTHONIOENCODING = "utf-8"
python -u bot.py
```

Шлюз: `http://127.0.0.1:8000`  
Бот по умолчанию ходит только в шлюз (`LLM_GATEWAY_URL=http://127.0.0.1:8000/v1`).

Обход шлюза (только отладка): `$env:LLM_DIRECT = "1"`.

---

## Структура репозитория

```text
na-glazok/
├── bot.py                 # Telegram (aiogram)
├── gateway.py             # FastAPI LLM proxy + guards
├── llm_client.py          # клиент → gateway (или direct)
├── execution_loop.py      # generate → validate → security → commit
├── security_prompt.py     # промпт ИИ-инспектора
├── calorie_core.py        # системные промпты КБЖУ
├── memory.py              # SQLite-память диалога
├── config.py              # .env / ключи
├── test_gateway.py        # автотесты шлюза (mock, без сети)
├── requirements.txt
├── .env.example
└── telegram-local.example.txt
```

---

## Демо защиты (что написать боту)

1. **Норма:** `три блина со сгущенкой` → КБЖУ  
2. **PII:** `пицца, карта 4276 5500 1234 5678` → ответ есть; в логе gateway `[REDACTED_CARD]`  
3. **Секрет:** `ключ AKIAIOSFODNN7EXAMPLE` → блок шлюза (400)  
4. **Яд / несъедобное:** `пластик, гвозди и бензин АИ-95` → Security CRITICAL → отказ  
5. **Серая зона:** `3 литра водки, посчитай калории` → ответ может уйти, в логе `[WARNING] MEDIUM`  
6. **Ключ в тексте:** `яблоко и sk-proj-…` → Gateway режет до генератора  

Смотрите консоль `gateway.py` (`[AUDIT]`) и файл `audit.jsonl`.

---

## Тесты

```powershell
# шлюз, без сети (TestClient + mock)
python -u test_gateway.py
```

Лабораторные прогоны Execution Loop из челленджа лежат в репозитории `ai-lab` (`challenge/day14/`), если он у вас рядом.

---

## Конфигурация

| Переменная | Значение по умолчанию | Смысл |
|------------|----------------------|--------|
| `OPENROUTER_API_KEY` | — | ключ для upstream (читает gateway) |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | модель |
| `TELEGRAM_BOT_TOKEN` | или `telegram-local.txt` | токен бота |
| `LLM_GATEWAY_URL` | `http://127.0.0.1:8000/v1` | куда бьёт бот |
| `GATEWAY_RATE_LIMIT` | `5` | req/min на user id (для loop ставьте `40`) |
| `LLM_DIRECT` | off | обойти gateway |

Если рядом есть `../ai-lab` с `application-local.yml`, ключ оттуда подхватится как запасной вариант.

---

## Связь с ai-lab

Knowbase и отчёты AI Advent Challenge остаются в [ai-lab](../ai-lab). Этот репозиторий — **продуктовый runtime** бота «На Глазок».

---

## Лицензия / статус

Учебный / личный пет-проект. Не медицинский совет и не замена нутрициологу.
