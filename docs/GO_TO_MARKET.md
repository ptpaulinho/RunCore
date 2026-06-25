# RunCore — Go-To-Market & Action Plan

> Plano comercial detalhado: posicionamento, concorrentes, clientes (com listas de empresas reais),
> como abordar, como ganhar dinheiro, e plano de 90 dias.
> Baseado em pesquisa de mercado (Junho 2026). Acompanha [STRATEGY.md](STRATEGY.md).

---

## 0. TL;DR

- **O que vendemos:** o **RunCore Score™** — o standard aberto de eficiência para agentes de IA.
- **Porque agora:** o mercado de agentes cresce de **$7.84B (2025) → $52.62B (2030)**, mas **nenhum
  benchmark mede custo/eficiência** e **não existe certificação**. Janela aberta.
- **Quem paga:** primeiro quem *constrói* agentes (precisa de provar eficiência), depois quem os
  *compra* (precisa de comparar fornecedores).
- **Como ganhamos:** open-core. SDK grátis → Team $99/mo (certificação contínua) → Enterprise
  $499/mo (comparação + relatório de procurement) → futura receita de *certificação como serviço*.

---

## 1. Posicionamento

**Categoria que criamos:** *Agent Efficiency Certification.*

**One-liner:** "RunCore is the efficiency standard for AI agents — the score that proves your
agent isn't burning money."

**Analogias que vendem (usar conforme o interlocutor):**
- Para engenharia: *"SWE-Bench diz se o agente consegue. RunCore diz a que custo."*
- Para finanças/procurement: *"É o score de crédito dos agentes de IA."*
- Para founders: *"É o SOC 2 da eficiência — o selo que o teu cliente vai exigir."*

**Mensagem central:** medir inteligência já é resolvido; o gargalo agora é **eficiência**, e ninguém
a tornou mensurável, comparável e certificável. Nós tornamos.

**O que NÃO somos (evitar):** "mais uma ferramenta de observabilidade". Esse mercado consolidou-se
(ver §2) e perdemos de frente. Somos um *standard*, não um dashboard.

---

## 2. Mapa de Concorrentes

### 2.1 Ninguém faz o RunCore Score — porquê é verdade

Pesquisa direta (Junho 2026): *"None of the major agent benchmarks integrate cost-efficiency into
their primary scoring rubric… there isn't yet a formal certification or standardized benchmark that
makes cost-per-successful-task part of the official scoring framework."* Os benchmarks (SWE-Bench,
GAIA, Terminal-Bench) ignoram custo; as ferramentas de observabilidade medem mas não **certificam**.
O CpST é usado informalmente, nunca foi produtizado num standard. **Esse é o espaço vazio.**

### 2.2 Concorrentes adjacentes (fazem *pedaços*, não o standard)

| Player | O que faz | Sobreposição | Estado 2026 |
|--------|-----------|--------------|-------------|
| **Langfuse** | Observabilidade open-source | Tracing, custo | Comprada pela ClickHouse (Jan) |
| **Helicone** | Observabilidade + caching | Custo, cache | Comprada pela Mintlify (Mar) |
| **Braintrust** | Eval de LLM/agentes | Avaliação | $80M Série B (Fev) |
| **Portkey** | AI gateway / control panel | Caching, routing, custo | 1T tokens/dia |
| **LangSmith** | Observabilidade (LangChain) | Tracing, eval | Incumbente |
| **AgentOps / Galileo** | Observabilidade de agentes | Replay, custo | Ativos |
| **AgentGuard47, tool-loop-guard** | Runtime guards (budget/loop) | **Os nossos guards** | OSS, MIT |

**Leitura estratégica:**
- Os guards são **commodity** (AgentGuard47 et al.). Não competimos aí — usamo-los como mecanismo.
- A observabilidade está **consolidada e capitalizada**. Não competimos de frente.
- **Nenhum** destes oferece um *score de eficiência certificado + leaderboard + badge*. É aí que
  ganhamos: numa camada nova, por cima de todos eles. Podemos até **certificar agentes que correm
  nessas plataformas** (parceria, não confronto).

### 2.3 Risco competitivo

O maior risco é um incumbente (Braintrust, Portkey) lançar um "efficiency score". Defesa: **chegar
primeiro ao estatuto de standard** (open methodology + leaderboard com tração + badge espalhado).
Standards têm efeito de rede — o segundo a chegar raramente desaloja o primeiro.

---

## 3. Clientes (ICP) + Listas de Empresas

Duas ondas: primeiro **quem constrói** agentes (dor: provar eficiência / cortar custo), depois
**quem compra** (dor: comparar fornecedores).

### 3.1 ICP primário — Startups/scale-ups que constroem agentes em produção
Têm faturas de LLM a doer, equipas pequenas, e ganham com um selo que os diferencia em vendas.

**Suporte ao cliente (agentes de alto volume = custo alto):**
- Sierra AI · Kore.ai · Cognigy · Omilia · Yellow.ai · SoundHound · Decagon · Lorikeet

**Coding / dev agents (consumo de tokens enorme):**
- Cursor · Cognition AI (Devin) · Imbue · Truffle AI · Continue · Sweep

**Vertical / workflow agents (YC e afins — ICP mais alcançável):**
- Harvey (legal) · Glean (enterprise search) · Bravi (home services) · Vortexify (supply chain)
- + a coorte da [Agentic List 2026 (120 empresas)](https://www.agentconference.com/agenticlist/2026)
  e [AI Assistant startups YC](https://www.ycombinator.com/companies/industry/ai-assistant)

> **Porque começar aqui:** ciclo de venda curto, dor de custo imediata, e cada logo certificado
> alimenta o leaderboard (prova social). O badge no README deles é distribuição grátis.

### 3.2 ICP secundário — Plataformas/frameworks de agentes (parcerias + integração)
Querem dar aos seus utilizadores uma forma de provar eficiência → integram o RunCore Score.
- CrewAI · LangChain/LangGraph · AutoGen · UiPath AI Agents · Salesforce Agentforce
- Microsoft Copilot Studio · Google Vertex AI Agent Builder · IBM watsonx Orchestrate

### 3.3 ICP terciário (Fase 2) — Compradores enterprise / procurement
Quando o score for conhecido, são eles que *exigem* "qual é o vosso RunCore Score?" em RFPs.
- Equipas de procurement de IA usando Zip, Levelpath, Ivalua, ZBrain (Hackett)
- Fortune 500 a comprar agentes (referência: relatórios McKinsey/Gartner sobre procurement agêntico)

> Lista de empresas é ponto de partida de pesquisa — validar contactos e timing antes de abordar.

---

## 4. Como Abordar os Clientes (Playbook)

### 4.1 Sequência de entrada (a "wedge")
1. **Certifica-os tu primeiro (grátis).** Corre o RunCore Score em agentes OSS/públicos conhecidos
   e publica no leaderboard. Cria prova e dá motivo de contacto: *"certificámos o vosso tipo de
   agente — querem reclamar/melhorar o vosso score?"*
2. **Conteúdo cria a categoria.** Publica o [MANIFESTO.md](MANIFESTO.md) ("Why AI agent benchmarks
   lie about cost") no blog/HN/LinkedIn. Vende o *problema*, não o produto.
3. **Badge = distribuição viral.** Cada README com "RunCore Certified — Grade A" é marketing grátis.

### 4.2 Canais
- **Bottom-up (devs):** PyPI, GitHub, Hacker News, r/LocalLLaMA, dev.to, X. SDK grátis + `runcore
  certify` num comando. Objetivo: estrelas, instalações, certs no leaderboard.
- **Founder-led outreach:** email/DM direto a fundadores das empresas da §3.1. Curto, com prova.
- **Parcerias:** integrar o Score nos frameworks (§3.2) → distribuição instantânea.

### 4.3 Scripts

**Cold email a um founder (ICP primário):**
> Assunto: o vosso agente — Grade B+ no RunCore Score
>
> Olá {Nome}, corremos o RunCore Score™ (o standard aberto de eficiência de agentes) num agente do
> tipo do {Produto} e deu **B+ — ~22% do custo é desperdício evitável** (tool calls duplicadas +
> contexto inchado). Relatório reproduzível aqui: {link}.
> O SDK é open-source — em 1 comando provam o vosso score real e mostram-no a clientes. Vale 15 min?

**Mensagem de parceria (framework):**
> Os vossos utilizadores não conseguem provar que os agentes deles são eficientes. Integramos o
> RunCore Score no {Framework} — cada agente ganha um selo de eficiência verificável. Sem custo
> para vocês, valor imediato para eles. Exploramos?

**Pitch a procurement (Fase 2):**
> Estão a comprar agentes sem forma de comparar custo-eficiência. O RunCore Score dá-vos um número
> normalizado e um relatório auditável por fornecedor — como pedir um SOC 2, mas para eficiência.

### 4.4 Métricas de tração (o que perseguir, por ordem)
1. Certs no leaderboard (prova) → 2. Estrelas/instalações (categoria) → 3. Badges espalhados
(distribuição) → 4. Conversões Team (receita) → 5. Pedidos de procurement (validação da categoria).

---

## 5. Como Ganhar Dinheiro (Modelo de Receita)

**Open-core.** O SDK e a self-certification são grátis (motor de adoção e de credibilidade do
standard). Paga-se pela certificação **contínua, comparável e protegida** em produção.

| Plano | Preço | Para quem | O que desbloqueia |
|-------|-------|-----------|-------------------|
| **Free** | $0 | Devs, OSS | SDK, self-cert, Score + badge, dashboard básico |
| **Team** | $99/mo | Startups com agentes em prod | Certificação contínua (CI/prod), alertas de regressão de score, listagem no leaderboard, Advisor |
| **Enterprise** | $499/mo | Quem compra/vende a sério | Comparação vs concorrentes, relatório para procurement, SSO, audit log, suporte prioritário |

### 5.1 Linhas de receita futuras (expansão)
- **Certification-as-a-Service:** taxa por certificação oficial/auditada de um agente de terceiros
  (o modelo "selo pago", como auditorias SOC 2).
- **Procurement marketplace:** acesso pago a compradores que querem filtrar fornecedores por score.
- **Private leaderboards** para enterprises (benchmark interno de equipas/modelos).

### 5.2 Matemática simples (sanity check, não promessa)
- 100 clientes Team = **$9.9k MRR** (~$119k ARR).
- 30 Enterprise = **$15k MRR** (~$180k ARR).
- Alvo realista ano 1: dezenas de clientes pagantes + leaderboard com tração. Bilionário **não** se
  decide aqui — decide-se por tornar-se o standard de uma categoria em crescimento de 36% CAGR.

### 5.3 Honestidade sobre o moat
O valor não está nos guards (copiáveis). Está em **ser o standard antes dos incumbentes acordarem**.
Cada cert, badge e parceria aumenta o efeito de rede e a barreira à entrada.

---

## 6. Plano de 90 Dias

**Dias 0–30 — Credibilidade (Fase A + B)**
- [ ] Colocar API keys e correr A5: certificar 5–10 agentes/modelos reais → semear o leaderboard.
- [ ] Publicar o MANIFESTO (blog + HN + LinkedIn) e a metodologia aberta.
- [ ] Lançar v0.10.0 no PyPI; README com badge e link ao leaderboard.

**Dias 30–60 — Categoria (Fase B)**
- [ ] Outreach a 20 founders da §3.1 com "certificámos o vosso tipo de agente" + relatório.
- [ ] 2–3 conversas de parceria com frameworks (§3.2).
- [ ] Conteúdo semanal (1 estudo de caso de eficiência por semana no leaderboard).

**Dias 60–90 — Receita (Fase C)**
- [ ] Configurar Stripe a sério (Price IDs reais) e abrir o Team plan.
- [ ] Converter os primeiros 5–10 Team a partir do outreach.
- [ ] Primeira conversa Enterprise/procurement.

---

## 7. O que falta tecnicamente (entra antes de escalar)

Gaps já identificados no produto (não bloqueiam o GTM inicial, mas resolver antes de Enterprise):
- Stripe Price IDs reais (placeholders atuais) — bloqueia cobrança real.
- Rate limiting em `/benchmark` e `/cloud/ingest`; transação atómica no contador de ingest.
- `audit_log` (anunciado no Enterprise) por implementar.
- Auto-instrument para Gemini/Ollama (atualmente só Anthropic/OpenAI).
- Self-service signup de tenants (atualmente via API).

> Ver estado no [REPOSITION_AUDIT.md](REPOSITION_AUDIT.md). A única dependência para semear o
> leaderboard (A5) és tu colocares as API keys.

---

## Fontes
- Mercado de agentes $7.84B→$52.62B; listas de empresas — [Agentic List 2026](https://www.agentconference.com/agenticlist/2026), [YC AI Assistant](https://www.ycombinator.com/companies/industry/ai-assistant), [AI Funding Tracker](https://aifundingtracker.com/top-ai-agent-startups/)
- Benchmarks ignoram custo / sem certificação — [aiagentsquare](https://aiagentsquare.com/blog/ai-agent-benchmarks-2026.html), [Automation Anywhere](https://www.automationanywhere.com/company/blog/product-insights/ai-agent-benchmark)
- Consolidação observabilidade — [latitude.so](https://latitude.so/blog/best-ai-agent-observability-tools-2026-comparison)
- Agentes de suporte / vendors — [Kore.ai buyer's guide](https://www.kore.ai/blog/top-ai-agents-for-customer-service-tested-reviewed)
- Procurement agêntico — [McKinsey](https://www.mckinsey.com/capabilities/operations/our-insights/redefining-procurement-performance-in-the-era-of-agentic-ai), [Gartner Peer Insights](https://www.gartner.com/reviews/market/ai-agent-for-procurement)
