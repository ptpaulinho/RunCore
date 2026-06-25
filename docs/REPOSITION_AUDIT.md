# RunCore — Reposition Audit

> Auditoria de tudo o que muda, fica ou sai para alinhar o projeto com o [STRATEGY.md](STRATEGY.md):
> **passar de "runtime optimization engine / observabilidade" → "o standard de eficiência para agentes de IA".**
> Regra-mestra: o engine e os guards **ficam** (são o *meio*); a *mensagem* e a *hierarquia do produto* mudam (a certificação é o *produto*).

---

## Princípio

| | Antes | Depois |
|---|-------|--------|
| **Produto** | Runtime optimization engine | RunCore Score™ — certificação de eficiência |
| **Guards** | A feature de topo | O mecanismo que dá bom score |
| **Métrica** | CpST (uma de várias) | CpST como base do score certificado |
| **Comprador** | Engenheiro | CFO / procurement / head of AI |
| **Moat** | "bloqueamos waste" (copiável) | Standard + leaderboard + badge (efeito de rede) |

---

## Inventário por superfície

| Superfície | Estado | Ação | Fase |
|-----------|--------|------|------|
| `README.md` | Reposicionado (hero + tabela lidera com cert) | ✅ Feito | A4 |
| `docs/RUNCORE_SCORE_SPEC.md` | Spec aberta criada | ✅ Feito | A1 |
| `/badge/*.svg` + snippet | Badge embeddável | ✅ Feito | A2 |
| `/leaderboard` + nav | Leaderboard público | ✅ Feito | A3 |
| Dashboard `<title>`, pricing hero | Reposicionado | ✅ Feito | A4 |
| Help modal | Score como produto central | ✅ Feito | (settings) |
| `runcore/cli/main.py` (help text) | "Runtime Optimization Engine" | 🔧 Alterar tagline | A-fix |
| `benchmarks/certification.py` (footer cert) | "runtime optimization verified" | 🔧 Alterar copy | A-fix |
| `PITCH.md` | Lidera com "missing layer", custo | 🔧 Reposicionar para o score/standard | A-fix |
| `docs/BUSINESS.md` | Modelo antigo "3 camadas / optimization engine" | 🔧 Reescrever em torno da certificação | A-fix |
| Docstrings `guards=GuardConfig()` (capture, sdk) | "activate runtime optimization guards" | ✅ **Manter** — guards são o mecanismo, descrição correta | — |
| `ATIR_SPEC.md` | Formato de trace aberto | ✅ **Manter** — suporta o score, é ativo | — |
| `docs/METRICS.md` | Define CpST e métricas | ✅ **Manter** — base técnica do score | — |
| `docs/INTEGRATION.md` | Como integrar SDK | ✅ **Manter** (corrigir link de metodologia → SPEC) | A-fix |
| `PATENT_CLAIMS.md` | Claims sobre guards/runtime | ✅ **Manter** — protege o mecanismo; revisitar na Fase 8 | adiar |
| Adapters (LangGraph/CrewAI/AutoGen/LangChain) | Captam traces | ✅ **Manter** — alimentam a certificação | — |

---

## O que **NÃO** se remove (e porquê)

Nada de código é removido. O risco do reposicionamento é deitar fora o engine — seria um erro:
o score **precisa** dos guards para ser bom, dos adapters para capturar, do ATIR para ser portável,
e do CpST para ter base. Tudo isso é o *como*. Só muda o *o quê* na narrativa.

**Único candidato a remoção real:** dados sintéticos no leaderboard (já removidos — fabricar scores
destruiria a credibilidade do standard).

---

## Fases — o que falta (visão executável)

### Fase A — Tornar a certificação credível
- [x] A1 Metodologia aberta
- [x] A2 Badge embeddável
- [x] A3 Leaderboard público
- [x] A4 Reposicionar narrativa
- [x] A-fix Alinhar taglines residuais (CLI, PITCH, BUSINESS, cert footer)
- [ ] **A5 Popular leaderboard com certs reais** — *bloqueado: precisa de API keys*

### Fase B — Criar a categoria
- [x] Leaderboard público (A3)
- [x] Badge (A2)
- [ ] Conteúdo/manifesto: "porque é que os benchmarks ignoram o custo"
- [ ] Caminho de submissão para o leaderboard

### Fase C — Monetizar
- [ ] Alinhar planos: Free (SDK + self-cert) / Paid (certificação contínua, alertas de regressão, comparação, relatório procurement) / Enterprise
- [ ] Atualizar `billing.py` + página de pricing + BUSINESS

### Pós-C
- [ ] Re-verificação total (testes, compile, render)
- [ ] Plano go-to-market detalhado ([GO_TO_MARKET.md](GO_TO_MARKET.md))
- [ ] *(com o utilizador)* colocar API keys → correr A5 → semear leaderboard
