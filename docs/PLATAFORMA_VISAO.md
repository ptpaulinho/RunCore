# RunCore — O que é, para quem é, e como gera receita

*Documento interno — versão de trabalho · Atualizado em Junho de 2026*

> Este é o documento-fonte do plano. O PDF (`PLATAFORMA_VISAO.pdf`) é gerado a
> partir desta visão. Itens marcados com ✅ já estão **implementados e testados**.

---

## 1. O que é o RunCore (em linguagem simples)

O **RunCore** avalia e certifica a eficiência de agentes de inteligência
artificial. Funciona como uma certificação ISO — mas para agentes de IA. Quando
uma empresa diz que o seu agente é rápido e barato, o RunCore mede isso de forma
independente e emite um certificado com uma pontuação chamada **RunCore Score™**
(0 a 100), com impressão digital SHA-256 verificável.

Analogia: é como a etiqueta energética de um eletrodoméstico (A, B, C…), mas
para agentes de IA.

O **RunCore Score™** pesa três dimensões:
- **Poupança de custo** (40%) — usa os tokens de forma económica?
- **Redução de tokens** (35%) — é conciso?
- **Taxa de sucesso** (25%) — cumpre as tarefas?

---

## 2. Estado atual da plataforma

### Já existe e funciona
- Plataforma online em runcore.onrender.com
- Sistema de certificação que corre testes reais com modelos de IA
- Leaderboard público com modelos certificados
- Relatórios HTML com impressão digital SHA-256
- Badges embeddáveis
- SDK (`pip install runcore`) e CLI
- Registo/login e **dashboard isolado por empresa**
- ✅ **Certificar a partir do dashboard, sem terminal** (botão "Run
  Certification" que corre no servidor)
- ✅ **Chave API por empresa** ligada ao motor de certificação (isolada por
  tenant)
- ✅ **Relatórios no dashboard** — ver e descarregar online, com isolamento
  entre empresas
- ✅ **Email automático** com resultado, grade e badge (anexa o certificado)
- ✅ **Certificar o PRÓPRIO agente** — endpoint OpenAI-compatível da empresa é
  conduzido pela suite de benchmark (não apenas modelos genéricos)

### Ainda não está completo
- Interface ainda assume algum conhecimento técnico (mitigado: já não precisa de
  terminal para certificar)
- Pagamentos reais (Stripe configurado, preços por ativar)
- Leaderboard com nome de empresa/produto (dados já guardados; falta expor)
- Onboarding guiado (wizard pós-registo)
- **Persistência de dados em produção** — no Render free tier a base de dados é
  `/tmp` (efémera). Resolvido pela **migração para AWS** (ver §8).

---

## 3. Quem são os clientes

**Cliente 1 — Empresas que constroem agentes de IA (principal).** Querem provar
a clientes/investidores que o agente é eficiente e fiável. Decisor: CTO / Head
of Product / fundador técnico.

**Cliente 2 — Empresas que COMPRAM agentes de IA.** Querem prova objetiva antes
de comprar; pedem o certificado RunCore ao fornecedor. Decisor: Compras / CIO.

**Cliente 3 — Programadores/investigadores (gratuito).** Correm certificações
grátis, alimentam o leaderboard, trazem as suas empresas mais tarde.

---

## 4. Como gera faturação

Modelo **Open-Core**: base gratuita, pagam pelos serviços avançados.

| Plano | Preço | Para quem | Inclui |
|---|---|---|---|
| Gratuito | 0 €/mês | Programadores | 1 cert/mês, leaderboard, SDK |
| Team | 99 €/mês | Startups/PMEs | Certificar o próprio agente, dashboard privado, leaderboard com nome, alertas |
| Enterprise | 499 €/mês | Empresas maiores | Tudo do Team + ilimitado, monitorização contínua, relatório de procurement, suporte |

Receita adicional futura: certificação express (49 €), API de verificação
pública (por chamada), programa de parceiros (LangChain, CrewAI…).

---

## 5. O que o cliente faz AGORA ao aceder (atualizado)

1. Entra em runcore.onrender.com → **/start** (3 passos de onboarding).
2. Regista a empresa em **/register**.
3. Faz login → **/app/dashboard**.
4. ✅ **Settings → guarda a chave Groq** (gratuita em console.groq.com) — fica
   privada à empresa.
5. ✅ **Run Certification** → escolhe "Certificar um modelo" **ou** "Bring your
   own agent" (endpoint do seu agente), carrega no botão. Corre no servidor.
6. ✅ Vê o progresso ao vivo, recebe **grade + score**, **vê/descarrega o
   relatório** no dashboard e (se o email estiver configurado) recebe-o por
   email com o badge.

> Já **não é preciso terminal** para o caminho principal. Falta apenas o
> onboarding guiado e a página de preços com compra ativa.

---

## 6. O que falta para ser totalmente user-friendly

1. ✅ Botão "Run Certification" no dashboard (sem terminal)
2. ✅ Chave API no dashboard ligada ao motor
3. ✅ Relatório diretamente no dashboard (ver + descarregar)
4. ✅ Email automático com relatório + badge
5. ✅ Fluxo "certificar o meu próprio agente" (via endpoint OpenAI-compatível)
6. ⬜ Página de preços com botão de compra funcional (Stripe)

---

## 7. Próximos passos prioritários (roadmap — "todas as sugestões")

Por ordem de impacto. ✅ = feito nesta iteração.

1. ✅ **Certificar sem terminal** a partir do dashboard.
2. ✅ **Certificar o próprio agente** (BYO endpoint).
3. ✅ **Email + relatório no dashboard.**
4. ⬜ **Persistência em produção** → **migrar para AWS** (este passo está
   preparado, ver §8). *Impacto: crítico — sem isto os dados desaparecem nos
   restarts do Render free.*
5. ⬜ **Ativar pagamentos Stripe** — ligar planos Team/Enterprise (1 dia quando
   os preços estiverem fechados).
6. ⬜ **Leaderboard com nome de empresa/produto** — os dados já são guardados
   (`product_name`, `cert_type`); falta uma opção "publicar no leaderboard" e
   render do nome.
7. ⬜ **Onboarding guiado** — wizard de 3 passos no primeiro login + estado
   vazio do dashboard ("o teu primeiro certificado está a 15 min").
8. ⬜ **Monitorização contínua** (feature Enterprise) — re-certificação agendada
   + alertas de regressão por email (a infra de email já existe).
9. ⬜ **Upload de resultados via SDK** — empresas que correm o SDK localmente
   submetem o cert/ATIR para o dashboard (o ingest por API-key já funciona).

---

## 8. Migração Render → AWS (preparada nesta iteração)

**Porquê:** o Render free tier guarda a base de dados em `/tmp` (efémera) — os
registos e certificações desaparecem a cada deploy. Uma VM AWS com disco
persistente resolve isto e dá controlo total.

**Decisões tomadas:**
- Base de dados: **SQLite num volume EBS persistente** (migra para Postgres
  depois sem mudar código — o `storage.py` já suporta `DATABASE_URL`).
- HTTPS: **Caddy** (certificados Let's Encrypt automáticos).
- Provisionamento: **guia manual + scripts**.

**Já criado no repositório:**
- `Dockerfile` (produção: utilizador não-root, healthcheck, dir `/data`)
- `deploy/docker-compose.yml` (app + Caddy)
- `deploy/Caddyfile`, `deploy/.env.example`
- `deploy/runcore.service` (alternativa systemd sem Docker)
- `deploy/deploy.sh` (pull + build + restart) e `deploy/backup-db.sh` (backup
  SQLite + opcional S3)
- **`docs/AWS_MIGRATION.md`** — guia passo-a-passo completo (lançar EC2,
  security groups, DNS, TLS, persistência EBS, backups, decommission do Render).

**Custo estimado:** `t3.small` + 20 GB EBS ≈ **15–20 €/mês**.

**O que falta (requer a tua conta AWS):** lançar a EC2, apontar o domínio, correr
`deploy/deploy.sh`. Tudo descrito em `docs/AWS_MIGRATION.md`.
