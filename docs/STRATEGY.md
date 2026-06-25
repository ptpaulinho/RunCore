# RunCore — Análise de Mercado & Estratégia de Posicionamento

> Baseado em pesquisa de mercado ao vivo (Junho 2026). Validar números antes de decisões grandes.

---

## 1. O Mercado (Junho 2026)

- **Tamanho:** mercado de observabilidade LLM ~$2.69B em 2026 → projeção $9.26B em 2030 (CAGR ~36%).
- **Adoção:** só ~15% dos deployments de GenAI instrumentam observabilidade. Espaço ainda por penetrar.
- **2026 = ano de consolidação:**
  - ClickHouse comprou a **Langfuse** (Jan 2026)
  - Mintlify comprou a **Helicone** (Mar 2026, após 14.2T tokens processados)
  - **Braintrust** levantou $80M Série B (Fev 2026)
  - **Portkey** processou 1 trilião de tokens/dia (Mar 2026); reposicionou-se de "observability tool" para "control panel for production AI"

**Leitura:** o mercado é grande e a crescer, mas os incumbentes têm guerra e capital. Competir de frente em "observabilidade" é suicídio.

---

## 2. Os Guards NÃO são o diferencial (commodity)

A pesquisa encontrou concorrentes diretos aos runtime guards do RunCore, vários **open-source e MIT**:

| Projeto | O que faz | Sobreposição |
|---------|-----------|--------------|
| **AgentGuard47** (`bmdhodl/agent47`) | Budget cap, loop detection, kill switch, traces locais, zero-dep, MIT | LoopGuard, BudgetGuard ≈ os nossos guards |
| **agent-guard** (Java) | Budget, tool auth, human approval, loop brakes | runtime governance |
| **tool-loop-guard** | Deteção de loop de tool calls | o nosso loop-break |
| **token-budget-py / agent-deadline** | Budget + timeout | guards de custo |
| OpenAI Agents SDK Guardrails | Validação de comportamento (sem conceito de custo) | parcial |

**Conclusão dura:** "dedup + loop-break + compressão como guard" já existe como categoria. Não lideres com isto. É tecnologia copiável e já copiada.

---

## 3. O WEDGE real — Certificação / Standard (território ABERTO)

A descoberta mais importante da pesquisa, citação direta:

> *"None of the major agent benchmarks currently integrate cost-efficiency or safety into their primary scoring rubric. A score of 88% on SWE-Bench achieved with $50 of inference per task is treated as identical to one achieved with $0.50."*

> *"There isn't yet a formal certification or standardized benchmark that makes [cost per successful task] part of the official scoring framework as of 2026."*

Ou seja:
- **CpST (cost per successful task)** já é usado informalmente ("teams divide pass-rate by dollars-per-task") — **mas ninguém o produtizou num standard/certificação.**
- Os benchmarks grandes (SWE-Bench, GAIA, Terminal-Bench) ignoram custo no score primário.
- **Há um buraco: não existe o "selo de eficiência" para agentes de IA.**

Isto é exatamente o **RunCore Score™ / Certificação**. É o único pedaço do RunCore que é território aberto e defensável.

---

## 4. Posicionamento recomendado

**NÃO:** "Mais uma ferramenta de observabilidade / redução de custo."
**SIM:** **"O standard de eficiência para agentes de IA — o selo que prova que o teu agente não está a queimar dinheiro."**

Analogia: o que o **SOC 2** é para segurança, ou o **score de crédito** é para risco financeiro — o **RunCore Score™** é para eficiência de agentes.

Os guards passam a ser o *meio* (como chegas a um bom score), não o *produto*. O produto é a **autoridade da certificação**.

---

## 5. Porque é que isto é um moat (e os guards não são)

| Guards | Certificação/Standard |
|--------|----------------------|
| Copiável em semanas | Efeito de rede: quanto mais empresas certificam, mais o selo vale |
| MIT competitors já existem | Os incumbentes têm **conflito de interesse** em certificar contra si próprios |
| Sem lock-in | Lock-in via histórico de scores + benchmark proprietário |
| Vende a engenheiros | Vende a CFOs/procurement (quem assina cheques) |

**Standards criam monopólios naturais.** Quem fica como referência primeiro, fica.

---

## 6. Plano de ataque (12 meses)

### Fase A — Tornar o Score credível (0-3 meses)
- Publicar a **metodologia** do RunCore Score™ aberta (transparência = confiança).
- Benchmark suite reprodutível + fingerprint SHA-256 (já temos).
- Certificar 5-10 agentes open-source conhecidos e publicar os scores → cria buzz e prova social.

### Fase B — Criar a categoria (3-6 meses)
- **Leaderboard público** de eficiência de agentes (como o Hugging Face leaderboard, mas para CpST).
- Conteúdo: "porque é que SWE-Bench mente sobre custo", etc. Vender o problema.
- Badge embeddável ("RunCore Certified — Grade A") para READMEs e landing pages.

### Fase C — Monetizar (6-12 meses)
- **Free:** SDK + self-cert (open-core, já temos).
- **Pago (Cloud):** certificação contínua em produção, alertas de regressão de score, comparação vs. concorrentes, relatório para procurement.
- **Enterprise:** "RunCore Certified" como requisito em RFPs — vender às empresas que *compram* agentes, não só às que os constroem.

---

## 7. A resposta honesta à pergunta "é único / bilionário?"

- **Tecnologia única?** Não. Os guards são commodity.
- **Categoria por ocupar?** Sim — a certificação de eficiência não existe ainda. Isso é raro e valioso.
- **Bilionário garantido?** Não existe garantia. Empresas grandes nascem de **execução + posicionamento + timing**, não de tech impossível de copiar. Stripe não inventou pagamentos; Datadog não inventou monitoring.
- **O que torna isto possível:** seres o **primeiro a fazer da eficiência um standard** antes que a consolidação do mercado feche a janela. A janela está aberta **agora** (2026) precisamente porque os grandes estão ocupados a comprar-se uns aos outros.

**A aposta não é "tenho tech que ninguém tem". É "consigo tornar-me a autoridade de uma categoria antes dos outros perceberem que ela existe".** Essa é uma aposta real e jogável.

---

## Fontes
- LLM observability market / consolidação 2026 — aimultiple, latitude.so, buildmvpfast
- Benchmarks ignoram custo / CpST não standardizado — aiagentsquare, automationanywhere, arxiv
- Concorrentes de guards (AgentGuard47, agent-guard, tool-loop-guard) — github.com/bmdhodl/agent47, agentguard.tech
- AgentGuard cost control — bmdpat.com
