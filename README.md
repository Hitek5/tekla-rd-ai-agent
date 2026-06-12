# Tekla/RD Local AI Agent MVP

Рабочий пакет для разворота локального ИИ-агента в закрытом контуре КСПД.

Цель MVP: дать пользователю Tekla чат-интерфейс, который работает через локальную LLM, RAG по Tekla API/внутренним примерам и белый список безопасных инструментов Tekla. Дообучение LoRA/QLoRA вынесено во второй этап после появления проверенного корпуса.

## Что уже реализовано в этом репозитории

- `services/orchestrator` - минимальный FastAPI-сервис агента: RAG-контекст, OpenAI-compatible LLM вызов, политика tool approval, JSONL-аудит.
- `src/TeklaAgent.Contracts` - C# DTO-контракты инструментов рабочего места Tekla.
- `src/TeklaWorkstationHost` - стартовая C#-заготовка локального HTTP-хоста рядом с Tekla.
- `configs` - модельная матрица, политика инструментов, список источников RAG, пример eval-задач.
- `scripts` - подготовка air-gap bundle, chunking корпуса, eval harness, Ubuntu GPU bootstrap.
- `docs` - архитектура, безопасность, эксплуатация, корпус данных, оценка качества и исправленная компонентная база.

## Быстрый локальный запуск MVP

1. Скопировать переменные окружения:

```powershell
Copy-Item .env.example .env
```

2. Установить Python-зависимости:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

3. Подготовить тестовый RAG-корпус:

```powershell
python scripts/chunk_corpus.py --source docs --output data/rag/chunks.jsonl
```

4. Запустить orchestrator:

```powershell
uvicorn tekla_agent.main:app --app-dir services/orchestrator --reload --port 8080
```

5. Проверить health:

```powershell
curl http://127.0.0.1:8080/health
```

## Docker MVP

`docker-compose.mvp.yml` поднимает Qdrant, Ollama, orchestrator и nginx. Для закрытого контура образы нужно заранее перенести во внутренний OCI registry, см. [Air-Gap Supply Chain](docs/airgap-supply-chain.md).

```bash
docker compose -f docker-compose.mvp.yml up --build
```

## Рекомендуемый порядок внедрения

1. Запустить orchestrator без Tekla, проверить RAG и eval на документах.
2. Запустить `TeklaWorkstationHost` в режиме заглушек на рабочем месте.
3. Подключить настоящие Tekla Open API адаптеры только для read-only инструментов.
4. Добавить mutating tools через dry-run и approval.
5. Провести pilot на копиях моделей.
6. Только после baseline-оценки готовить LoRA/QLoRA.

## Важные ограничения

- Production-модели Tekla/RD не изменяются автономно.
- Произвольный C#-код не исполняется в production.
- `delete`, `modify`, `export`, `release RD` требуют явного approval token.
- Все tool calls пишутся в JSONL-аудит.

