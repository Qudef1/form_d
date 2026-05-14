# 🚀 SEC EDGAR Parser + Enricher: Полный гайд

## 📋 Overview

Это двухэтапный пайплайн для сбора и обогащения данных о компаниях из SEC EDGAR:

1. **form_d_companies.py** — Собирает компании из SEC filings (Forms D, C, A) с фильтрами по индустрии и сумме
2. **form_d_enricher.py** — Обогащает результаты через GPT web search, находя LinkedIn, сайт и контакты

---

## 1️⃣ Установка зависимостей

```bash
pip install -r requirements.txt
```

**Ключевые зависимости:**
- `requests`, `beautifulsoup4` — для работы с SEC API
- `pandas` — обработка CSV
- `openai>=1.10.0` — GPT с web search
- `python-dotenv` — управление API ключами

---

## 2️⃣ Настройка API ключей

### OpenAI (обязательно для enricher)

Создайте `.env` файл в той же папке:

```bash
# .env
OPENAI_API_KEY=sk-your-openai-key-here
```

**Как получить ключ:**
1. Зарегистрируйтесь на https://platform.openai.com/
2. Перейдите в API keys → Create new secret key
3. Скопируйте и вставьте в `.env`

### SEC User-Agent (обязательно)

Откройте `form_d_companies.py`, найдите строку 98:

```python
HEADERS = {"User-Agent":"Yame yddfr@email.com", ...}
```

**Замените `Yame yddfr@email.com` на:**
- Ваше реальное имя и email (например: `"John Doe john@example.com"`)
- SEC требует действительный User-Agent, иначе будут 403 ошибки

---

## 3️⃣ Этап 1: Сбор данных из SEC

### Базовый запуск (Form D за последние 30 дней)

```bash
python form_d_companies.py
```

По умолчанию:
- Формы: D, C, A
- Период: последние 30 дней
- Минимальная сумма для Form D: $500,000
- Для Forms C/A: $100,000
- Максимальная сумма: $20,000,000
- Фильтр: исключаются хедж-фонды/PE фонды
- Отрасли: только из `TARGET_INDUSTRIES_D`
- Ключевые слова: `INDUSTRY_KEYWORDS["software"]` (IT/SaaS)
- Выход: `sec_results.csv`

### Примеры с параметрами

```bash
# Только Form D за последние 60 дней, финансисты
python form_d_companies.py \
  --forms D \
  --days 60 \
  --industry fintech \
  --min-amount-d 1000000 \
  --output fintech_d_companies

# Form C (токены) + Form A (микро), biotech
python form_d_companies.py \
  --forms C,A \
  --industry healthtech \
  --min-amount-ca 50000 \
  --max-amount 5000000

# Без фильтра по отрасли, только суммы
python form_d_companies.py \
  --keep-all \
  --show-reasons \
  --verbose

# Быстрый режим (меньше задержек, риск 500 ошибок)
python form_d_companies.py --fast

# Самый безопасный режим (максимальные задержки)
python form_d_companies.py --safe
```

### Ключевые параметры

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `--forms` | Формы: `D`, `C`, `A` через запятую | `D,C,A` |
| `--days` | Количество дней назад | `30` |
| `--industry` | Индустрия из `INDUSTRY_KEYWORDS` | `software` |
| `--keywords` | Доп. ключевые слова через запятую | — |
| `--min-amount-d` | Мин. сумма для Form D | `500000` |
| `--min-amount-ca` | Мин. сумма для Forms C/A | `100000` |
| `--max-amount` | Макс. сумма | `20000000` |
| `--output` | Имя CSV файла (без .csv) | `sec_results` |
| `--keep-all` | Не фильтровать по отрасли/сумме | `False` |
| `--show-reasons` | Показывать, почему компании отфильтрованы | `False` |
| `--verbose` | Детальный лог | `False` |
| `--fast` | Уменьшенные задержки (быстрее, но риск 500 ошибок) | — |
| `--safe` | Увеличенные задержки, offset-лимит=200 (медленнее, но надёжно) | — |

---

## 4️⃣ Этап 2: Обогащение через GPT

После получения CSV файла из этапа 1:

```bash
python form_d_enricher.py --input sec_results.csv
```

**Параметры:**
- `--input` — обязательный, путь к CSV из этапа 1
- `--output` — имя выходного файла (по умолчанию `enriched_<имя>.csv`)
- `--limit` — ограничить количество строк (полезно для тестов)
- `--verbose` — показывать прогресс для каждой компании

### Примеры

```bash
# Обогатить все
python form_d_enricher.py --input sec_results.csv

# Только первые 10 компаний (тест)
python form_d_enricher.py --input sec_results.csv --limit 10 --verbose

# Кастомный выход
python form_d_enricher.py --input fintech_d_companies.csv --output fintech_enriched.csv
```

---

## 5️⃣ Полный пайплайн (рекомендуемый)

```bash
# 1️⃣ Сбор (FinTech, Form D за 90 дней)
python form_d_companies.py \
  --forms D \
  --days 90 \
  --industry fintech \
  --min-amount-d 1000000 \
  --output fintech_d_companies

# 2️⃣ Обогащение (тест на 20)
python form_d_enricher.py \
  --input fintech_d_companies.csv \
  --limit 20 \
  --verbose

# 3️⃣ Если всё OK — полное обогащение
python form_d_enricher.py --input fintech_d_companies.csv
```

---

## 6️⃣ Как работают фильтры

### Фильтр фондов (`is_fund_by_name`)

Автоматически исключает:
- Слова: `fund`, `hedge fund`, `mutual fund`, `investment fund`, `venture fund`, `private equity`, `asset management`, `pension fund`, `ETF`
- Паттерны: `* capital`, `* ventures`, `* partners`, `* holdings` + окончания `lp/llc/ltd/limited partnership` + содержит `fund`

**Примеры исключений:** `Sequoia Capital LP`, `BlackRock Investment Management`

### Фильтр по отрасли (Form D)

Использует `TARGET_INDUSTRIES_D` (стр.37-69):
- Ядро IT: `Technology`, `Computers`, `Internet`
- Финтех: `Banking & Financial Services`, `Insurance`, `Investing`
- HealthTech: `Health Care`, `Biotechnology`
- Ритэйл: `Retailing`
- Energy/Proptech: `Energy Conservation`, `Other Real Estate`
- Общее: `Other` (стартапы часто выбирают эту категорию)

**Примечание:** Строка 310 — `ind and ind not in ind_d` — значит, если `industry_group` пустой, компания **пройдёт** фильтр (не дропается).

### Фильтр по ключевым словам (Forms C/A)

Ищет в XML тексте (не в заголовках!) слова из `INDUSTRY_KEYWORDS[industry]`:
- `software`: SaaS, AI, cloud computing, API, blockchain…
- `fintech`: payment processing, digital banking, neobank…
- `healthtech`: telehealth, telemedicine, EHR, biotech…

---

## 7️⃣ Формат выходных файлов

### CSV от form_d_companies.py

```csv
company_name,cik,accession,form_type,file_date,period,file_num,industry_group,offering_amount,sold_amount,keywords_found
"Acme AI Inc","0001234567","0001234567-24-000001","D","2024-03-15","2024:03:15","000-123456","Technology",5000000.0,4500000.0,""
```

**Поля:**
- `company_name` — название компании
- `cik` — Central Index Key (SEC ID)
- `accession` — accession number (для построения URL к XML)
- `form_type` — D/C/A
- `file_date` — дата подачи
- `industry_group` — категория из SEC
- `offering_amount` — общая сумма размещения
- `sold_amount` — проданная сумма
- `keywords_found` — найденные ключевые слова (для C/A)

### CSV от form_d_enricher.py

Добавляет 3 поля:
```csv
linkedin,email,website
"https://www.linkedin.com/company/acme-ai","contact@acmeai.com","https://acmeai.com"
```

---

## 8️⃣ Распространённые ошибки

| Ошибка | Решение |
|--------|---------|
| `OPENAI_API_KEY not found` | Создайте `.env` файл с ключом |
| `403 Forbidden` от SEC | Замените User-Agent на реальный email |
| `HTTP 429/500` | Увеличьте `API_DELAY` (стр.104) или используйте `--safe` |
| `XML не найден` | Установите `keep_all=True` или проверьте вручную accession number |
| Нет результатов | Увеличьте `--days` или ослабьте фильтры (`--keep-all`) |
| Слишком долго | Используйте `--fast` или уменьшите `CHUNK_DAYS` в коде |
| Квоты OpenAI | Проверьте баланс на platform.openai.com/usage |

---

## 9️⃣ Производительность и рейт-лимиты

### Настройки в коде (стр.104-108)

```python
API_DELAY = 1.5          # Между запросами к SEC API (минимум 1.0)
MAX_RETRIES = 3          # Повторов при ошибках
BACKOFF_FACTOR = 2       # Экспоненциальный backoff (1.5 → 3 → 6 сек)
OFFSET_LIMIT = 1000      # Безопасный лимит пагинации (SEC может глючить после)
PAGE_SIZE = 100          # Результатов на страницу (фиксировано SEC)
```

**Рекомендации:**
- Для больших запросов (>10k компаний) используйте `--safe` с `CHUNK_DAYS=1` (уже по умолчанию)
- Если часто 500 ошибки → уменьшите `OFFSET_LIMIT` до 200
- Если слишком медленно → `--fast` (но рискуете 429/500)

---

## 🔟 Тонкая настройка

### Изменение целевых индустрий

Отредактируйте `TARGET_INDUSTRIES_D` (строки 37-69):
```python
TARGET_INDUSTRIES_D = [
    "Technology",        # ← добавляйте/удаляйте
    "Banking & Financial Services",
    "Health Care",
    # ...
]
```

### Добавление своих ключевых слов

В `INDUSTRY_KEYWORDS` (строки 71-96):
```python
INDUSTRY_KEYWORDS = {
    "your_category": [
        "keyword1",
        "keyword2",
        # ...
    ]
}
```

Затем используйте: `--industry your_category`

---

## 📊 Анализ результатов

### Быстрая статистика

```bash
# Количество строк
wc -l sec_results.csv

# Топ индустрий
python -c "
import pandas as pd
df = pd.read_csv('sec_results.csv')
print(df['industry_group'].value_counts().head(10))
"

# Распределение сумм
python -c "
import pandas as pd
df = pd.read_csv('sec_results.csv')
print(df['offering_amount'].describe())
"
```

---

## 🔄 Автоматизация (пример bash скрипта)

```bash
#!/bin/bash
# auto_pipeline.sh

DATE=$(date +%Y%m%d)

# Этап 1: сбор
python form_d_companies.py \
  --forms D \
  --days 30 \
  --industry software \
  --output results_${DATE}

# Этап 2: обогащение
python form_d_enricher.py \
  --input results_${DATE}.csv \
  --output enriched_${DATE}.csv

echo "✅ Готово: enriched_${DATE}.csv"
```

---

## ⚠️ Важные ограничения

1. **SEC API лимиты**: ≤10 запросов/сек, настоятельно рекомендуется 1.5-2.5с задержка
2. **OpenAI Web Search**: Только модель `gpt-4o-mini-search-preview` (не входит в стандартный ChatGPT)
3. **XML не всегда доступен**: У старых filings могут быть другие форматы (PDF, approves)
4. **Keywords ищутся по всему XML тексту**: Могут быть ложные срабатывания
5. **Квоты OpenAI**: Бесплатно только начальный грант, дальше платно ($/мин)

---

## 🎯 Best Practices

✅ **Делайте:**
- Используйте `--limit 10` для теста перед полным запуском
- Сохраняйте `.env` в `.gitignore` (не коммитьте API ключи!)
- Мониторьте лог на `⚠️` и `❌`
- Используйте `--show-reasons` при первом запуске, чтобы понять фильтрацию
- Выбирайте `--industry` вместо `--keywords` для более точных результатов

❌ **Не делайте:**
- Не запускайте без `OPENAI_API_KEY` (enricher упадёт)
- Не ставить `API_DELAY < 1.0` (риск бана SEC)
- Не используйте `--fast` для массовых запросов (>1000 компаний)
- Не забудьте проверить User-Agent

---

## 📁 Итоговая структура папки

```
form_d/
├── form_d_companies.py     # SEC parser
├── form_d_enricher.py      # GPT enricher
├── requirements.txt        # зависимости
├── .env                   # ← создайте (OPENAI_API_KEY)
├── sec_results.csv         # ← output этапа 1
└── enriched_sec_results.csv # ← output этапа 2
```

---

## 🎬 Quick Start (копировать-вставить)

```bash
# 1. Установка
pip install -r requirements.txt

# 2. Настройка .env
echo "OPENAI_API_KEY=sk-your-key" > .env

# 3. Замена User-Agent (отредактируйте form_d_companies.py, стр.98)
nano form_d_companies.py  # замените email на свой

# 4. Тестовый запуск (10 компаний, IT)
python form_d_companies.py --industry software --days 7 --output test_run --verbose
python form_d_enricher.py --input test_run.csv --limit 5 --verbose

# 5. Полный запуск
python form_d_companies.py --forms D --days 90 --industry software --output companies
python form_d_enricher.py --input companies.csv
```

**Готово!** Файл `enriched_companies.csv` содержит: название, CIK, сумма, отрасль + LinkedIn, сайт, email.
