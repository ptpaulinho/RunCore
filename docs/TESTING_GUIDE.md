# RunCore — Guia de Testes, Treino e Validação

Este guia explica como instalar, correr e registar testes na plataforma RunCore — desde unit tests locais até benchmarks reais com LLMs gratuitos.

---

## 1. Instalação do Ambiente

```bash
# Clonar o repositório
git clone https://github.com/ptpaulinho/RunCore.git
cd RunCore

# Criar ambiente virtual
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Instalar tudo (SDK + providers + dev tools)
pip install -e ".[all,dev]"
```

Verificar que está tudo ok:

```bash
python -c "import runcore; print(runcore.__version__)"
runcore --help
```

---

## 2. Chaves de API (Providers Gratuitos)

Os benchmarks usam providers gratuitos. Obter as chaves:

| Provider | Registo | Env var |
|----------|---------|---------|
| **Groq** (recomendado — rápido, gratuito) | https://console.groq.com | `GROQ_API_KEY` |
| **Gemini** (Google, tier gratuito) | https://aistudio.google.com | `GEMINI_API_KEY` |
| **Ollama** (local, sem chave) | https://ollama.com + `ollama pull llama3` | — |

Configurar as chaves:

```bash
# Adicionar ao ~/.zshrc ou ~/.bashrc
export GROQ_API_KEY="gsk_..."
export GEMINI_API_KEY="AIza..."

# Ou criar ficheiro .env na raiz do projecto
echo "GROQ_API_KEY=gsk_..." >> .env
```

---

## 3. Testes Unitários (sem API, instantâneos)

Correm offline, sem qualquer chave. Verificam a lógica do SDK, guards, ATIR e trace.

```bash
# Correr todos os unit tests
python -m pytest tests/unit/ -v

# Correr só um módulo específico
python -m pytest tests/unit/test_guards.py -v
python -m pytest tests/unit/test_sdk.py -v
python -m pytest tests/unit/test_benchmark.py -v
python -m pytest tests/unit/test_trace.py -v
python -m pytest tests/unit/test_loops.py -v
```

Resultado esperado: **58+ testes passam em < 1 segundo**.

O que cada ficheiro testa:

| Ficheiro | O que valida |
|----------|-------------|
| `test_guards.py` | Dedup guard, loop breaker, context compression |
| `test_sdk.py` | `runcore.capture()`, ATIR export, cloud push |
| `test_benchmark.py` | Runner de benchmarks, scoring, comparação |
| `test_trace.py` | Cálculo de tokens, custo por modelo |
| `test_loops.py` | Detector de loops, risk score |
| `test_server.py` | Endpoints FastAPI (sem servidor real) |
| `test_advisor.py` | Motor de sugestões de optimização |

---

## 4. Testes de Integração (requerem API keys)

Chamadas reais a LLMs. Cada provider tem o seu ficheiro de testes.

```bash
# Groq (llama-3.1-8b — gratuito, mais rápido)
python -m pytest tests/integration/test_groq.py -v

# Gemini (gemini-1.5-flash — gratuito)
python -m pytest tests/integration/test_gemini.py -v

# Ollama (local — precisa de ollama a correr)
ollama serve &
ollama pull llama3
python -m pytest tests/integration/test_ollama.py -v

# Pipeline completo (SDK + provider + guards)
python -m pytest tests/integration/test_full_pipeline.py -v
```

Se a chave não estiver configurada, o teste é **automaticamente ignorado** (skip) — não falha.

---

## 5. Benchmark de Agentes (mede savings reais)

Os benchmarks correm agentes simulados com tarefas reais e medem savings de custo e tokens.

### 5a. Via CLI (recomendado)

```bash
# Benchmark rápido — suite support, 3 runs por tarefa
runcore benchmark --suite support --runs 3 --provider groq

# Benchmark completo — todas as suites
runcore benchmark --suite all --runs 5 --provider groq

# Com Gemini
runcore benchmark --suite research --runs 5 --provider gemini

# Com Ollama (local)
runcore benchmark --suite coding --runs 3 --provider ollama
```

### 5b. Via Python

```python
from benchmarks.run_benchmark import run_suite

result = run_suite(
    suite="support",
    provider_name="groq",
    model="llama-3.1-8b-instant",
    runs_per_task=5,
)
print(f"Cost savings: {result.avg_cost_savings_pct:.1f}%")
print(f"Token reduction: {result.avg_token_reduction_pct:.1f}%")
```

Os resultados são **gravados automaticamente** na base de dados local e aparecem no dashboard.

---

## 6. Certificação RunCore Score™

A certificação gera um score 0–100, um relatório HTML assinado com SHA-256, e marca o agente como **Certified** se score ≥ 60.

### 6a. Via CLI

```bash
# Certificação completa com Groq (gratuito)
runcore certify --provider groq --runs 5

# Certificação com modelo específico
runcore certify --provider groq --model llama-3.3-70b-versatile --runs 10

# Guardar relatório num path específico
runcore certify --provider groq --runs 5 --output ./my_cert.html

# Não abrir browser automaticamente
runcore certify --provider groq --runs 5 --no-open
```

O comando:
1. Corre benchmarks em 4 suites (support, research, coding, analytics)
2. Calcula RunCore Score™ = 40% cost + 35% tokens + 25% success rate
3. Gera relatório HTML com fingerprint SHA-256
4. Guarda em `benchmarks/results/certifications/`
5. Abre o relatório no browser
6. **Exit code 0** se certified, **exit code 1** se não (para usar em CI/CD)

### 6b. Via API do dashboard

```bash
curl -X POST http://localhost:8000/certification/run \
  -H "Content-Type: application/json" \
  -d '{"provider": "groq", "model": "llama-3.1-8b-instant", "runs_per_task": 5}'
```

### 6c. Ver relatórios

```bash
# Listar certificações existentes
ls benchmarks/results/certifications/

# O dashboard mostra o widget de certificação em /
runcore server
# → http://localhost:8000
# → http://localhost:8000/certification
```

---

## 7. Dashboard — Ver Resultados em Tempo Real

```bash
# Iniciar o servidor do dashboard
runcore server
# → http://localhost:8000

# Ou com porta personalizada
runcore server --port 8765
```

O dashboard mostra:
- **Stats globais** — total de runs, avg savings, token reduction, pass rate
- **Benchmark History** — todos os runs com filtros por resultado/agente/data
- **RunCore Score™ widget** — estado da certificação na página principal
- **Certification page** — `/certification` — histórico e relatórios detalhados

---

## 8. Integrar com o Teu Agente

Instrumentar um agente existente para registar tudo automaticamente:

```python
import runcore
from runcore import GuardConfig

# Configurar guards
guards = GuardConfig(
    dedup_enabled=True,
    loop_break_enabled=True,
    context_compression_enabled=True,
)

# Capturar um run do teu agente
with runcore.capture("my_agent", task="Process invoice #1001", guards=guards) as cap:
    # O teu agente corre aqui normalmente
    result = my_agent.run("Process invoice #1001")

# Ver o relatório
atir = cap.get_atir()
print(f"Cost: ${atir.total_cost_usd:.5f}")
print(f"Duplicate calls avoided: {atir.duplicate_tool_calls}")
print(f"CpST: ${atir.cost_per_successful_task:.5f}")
```

Enviar para o RunCore Cloud:

```python
import httpx

httpx.post(
    "https://your-runcore.onrender.com/cloud/ingest",
    headers={"X-RunCore-Key": "rc_your_key"},
    json=atir.model_dump(),
)
```

---

## 9. Fluxo Recomendado (do zero ao certificado)

```
Passo 1 — Instalar
  pip install -e ".[all,dev]"

Passo 2 — Verificar offline
  python -m pytest tests/unit/ -q
  → 58 passed

Passo 3 — Configurar provider
  export GROQ_API_KEY="gsk_..."

Passo 4 — Testar integração
  python -m pytest tests/integration/test_groq.py -v
  → 5 passed

Passo 5 — Correr benchmark rápido
  runcore benchmark --suite support --runs 3 --provider groq

Passo 6 — Ver no dashboard
  runcore server → http://localhost:8000

Passo 7 — Obter certificação
  runcore certify --provider groq --runs 5
  → Abre relatório HTML com score e fingerprint

Passo 8 — Integrar no teu agente
  Ver docs/INTEGRATION.md
```

---

## 10. Suites de Tarefas Disponíveis

| Suite | Tarefas | O que testa |
|-------|---------|-------------|
| `support` | 3 tarefas | Agente de suporte ao cliente, refunds, status |
| `research` | 2 tarefas | Agente de pesquisa web, loops de search |
| `coding` | 2 tarefas | Agente de debug, re-leitura de ficheiros |
| `analytics` | 1 tarefa | Agente de dados, fetch repetido de datasets |
| `all` | 8 tarefas | Todas as acima |

Cada tarefa corre com e sem os guards do RunCore, e mede:
- **Cost savings** — diferença de custo em USD
- **Token reduction** — tokens poupados
- **Task success** — se o agente completou a tarefa correctamente

---

## 11. Referência Rápida de Comandos

```bash
# Unit tests (offline)
python -m pytest tests/unit/ -q

# Integration tests (precisa de GROQ_API_KEY)
python -m pytest tests/integration/ -v

# Benchmark
runcore benchmark --suite support --runs 3 --provider groq

# Certificação
runcore certify --provider groq --runs 5

# Dashboard
runcore server

# Ver versão
runcore --version

# Ajuda
runcore --help
runcore benchmark --help
runcore certify --help
```
