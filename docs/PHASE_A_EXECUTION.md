# RunCore — Fase A: Tornar a Certificação um Standard (Execução)

> Objetivo: reposicionar o RunCore como **o standard de eficiência para agentes de IA** no menor tempo possível, reutilizando ~90% do que já está construído.

---

## Primeiro: temos de reescrever o que já fizemos? **NÃO.**

A boa notícia: **quase nada do código é deitado fora.** O reposicionamento é 90% narrativa + arquitetura de informação, 10% features novas. Mapa do que muda:

| O que já existe | Destino no novo posicionamento | Ação |
|-----------------|-------------------------------|------|
| `benchmarks/certification.py` (RunCoreScore, fingerprint) | **Vira o produto central** | Manter, expor metodologia |
| Runtime guards (dedup, loop, compressão) | Passa a ser "como atinges bom score" | Manter, despromover na narrativa |
| Dashboard + benchmarks | Ferramenta de medição → input da cert | Manter |
| `/certification` page + widget | Centro do produto | Elevar, ligar a leaderboard |
| SDK open-core + Cloud pago | Modelo de negócio | Manter |
| README / hero / pricing | Mensagem "observability/custo" | **Reescrever copy** |

**Conclusão:** não há reescrita de engine. Há reescrita de *mensagem* + 3 features novas pequenas (spec aberta, badge, leaderboard).

---

## Sub-tarefas (ordenadas por dependência)

### A1 — Publicar a metodologia aberta do RunCore Score™  `[base de credibilidade]`
**Porquê:** transparência = confiança. Um standard fechado não é standard. Sem isto, ninguém confia no selo.

**Ficheiro:** `docs/RUNCORE_SCORE_SPEC.md`

**Conteúdo a documentar (já existe no código, é só formalizar):**
- Fórmula exata: `40% cost + 35% tokens + 25% success` (`SCORE_WEIGHTS` em `certification.py:40`)
- Targets: cost ≥ 25%, tokens ≥ 20% (`COST_TARGET`/`TOKEN_TARGET`, linha 41-42)
- Curva de dimensão: `_dimension_score()` — 0→70 abaixo do target, 70→100 entre target e 2× target (linha 97)
- Definição de **CpST** (cost per successful task) e porque é a métrica-norte
- Grades: A+/A/B+/B/C/F e o limiar `certified ≥ 60`
- Suites usadas (support, research, coding, analytics) e tarefas
- Reprodutibilidade: `random.seed(42)` + fingerprint SHA-256 (linha 300)
- Versão da spec (v1) + política de versionamento

**Mudanças de código:** nenhuma. Só doc + links a partir do README e da página `/certification`.

---

### A2 — Badge embeddável "RunCore Certified"  `[viralidade / distribuição]`
**Porquê:** cada README com o badge é marketing grátis e prova social. É o mecanismo de efeito de rede.

**Mudanças de código (`runcore/server/app.py`):**
- `GET /badge/{grade}.svg` — devolve um SVG com cor por grade (verde A+/A, azul B, etc.)
- Opcional: `GET /badge/score/{value}.svg` — badge dinâmico estilo shields.io
- No relatório de certificação (`generate_cert_html`) e no widget do dashboard, mostrar o **snippet markdown** pronto a copiar:
  `[![RunCore Certified](https://.../badge/A.svg)](link-para-o-relatório)`

**Aceitação:** consigo colar o badge num README e renderiza com a grade certa.

---

### A3 — Leaderboard público de eficiência  `[a feature que cria a categoria]`
**Porquê:** é o "Hugging Face leaderboard" da eficiência. Torna o RunCore o sítio onde se *compara* eficiência de agentes — autoridade.

**Mudanças de código:**
- Storage: tabela `leaderboard` (ou reutilizar resultados de cert) com `provider, model, suite, score, grade, cpst, timestamp, fingerprint`
- `GET /leaderboard` — página HTML que ordena por RunCore Score, com filtro por suite/provider
- `POST /leaderboard/submit` (opcional, Cloud) — submeter um resultado certificado
- Link no nav: Dashboard | Certification | **Leaderboard** | Cloud | Pricing

**Aceitação:** página pública lista agentes ordenados por score, com badge e link ao relatório.

---

### A4 — Reposicionar a narrativa em todas as superfícies  `[a mudança de mensagem]`
**Porquê:** é aqui que o reposicionamento realmente acontece. Mesmo produto, história diferente.

**Mudanças (copy, sem lógica nova):**
- **README.md** — novo hero: *"The efficiency standard for AI agents. Prove your agent isn't burning money."* Guards descem para uma secção "How RunCore improves your score".
- **Dashboard hero/nav** (`app.py`) — tagline nova; destacar Score/Certification.
- **Pricing** (`/cloud/billing/plans`) — Free: self-cert + SDK. Pago: certificação contínua, alertas de regressão de score, comparação vs concorrentes, relatório para procurement.
- **Help modal** — explicar o Score como produto central.

**Aceitação:** um visitante percebe em 5 segundos que o RunCore é "o selo de eficiência", não "mais uma dashboard".

---

### A5 — Certificar agentes de referência + popular o leaderboard  `[prova social]`
**Porquê:** um leaderboard vazio não convence ninguém. Precisamos de scores reais à mostra.

**Ações (precisam de provider keys — Groq/Gemini grátis):**
- Correr `runcore certify` contra Groq llama-3.1/3.3 e Gemini flash nas 4 suites
- Publicar os relatórios HTML assinados
- Semear o leaderboard com 5-10 resultados reais
- Guardar 2-3 relatórios como exemplos para usar em pitch/landing

**Aceitação:** leaderboard com ≥5 entradas reais + relatórios partilháveis.

---

## Ordem de execução (caminho mais curto)

```
A1 (spec)  ──►  A2 (badge)  ──►  A3 (leaderboard)  ──►  A5 (popular)
   │                                   │
   └──────────►  A4 (narrativa) ◄───────┘   (A4 pode correr em paralelo após A1)
```

A1 e A4 desbloqueiam tudo. A2 e A3 são as features novas. A5 é o fecho (prova).

---

## Definição de "Fase A concluída"

- [ ] Metodologia do Score pública e linkada (A1)
- [ ] Badge embeddável a funcionar + snippet copiável (A2)
- [ ] Leaderboard público no ar (A3)
- [ ] README + dashboard + pricing reposicionados (A4)
- [ ] ≥5 scores reais no leaderboard + relatórios de exemplo (A5)
- [ ] Todos os testes continuam verdes (303 passed)

Quando isto estiver feito, o RunCore deixou de ser "uma ferramenta" e passou a ser "um standard com prova" — pronto para a Fase B (criar a categoria com conteúdo e leaderboard público divulgado).
