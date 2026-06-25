# RunCore — Começar sem Terminal (Dashboard Local)

Este guia é para correr a plataforma **100% pelo dashboard**, sem escrever comandos.
Só fazes duplo-clique em ficheiros e depois usas o browser.

---

## Passo 1 — Instalar Python (uma vez, se ainda não tiveres)

1. Vai a **https://www.python.org/downloads/**
2. Descarrega o instalador para macOS e abre-o.
3. Carrega em **Continue → Install**. Pronto.

> Já tens Python? Salta este passo.

---

## Passo 2 — Setup (uma vez)

Na pasta do RunCore, faz **duplo-clique em `Setup.command`**.

- Abre uma janela preta (Terminal) automaticamente.
- Instala tudo sozinho (demora ~1 minuto).
- No fim mostra **"✓ Setup complete!"** — podes fechar a janela.

> **Se o macOS bloquear o ficheiro** ("não é possível abrir porque é de um programador não identificado"):
> clica com o **botão direito** em `Setup.command` → **Abrir** → **Abrir**. Só precisas de fazer isto uma vez.

---

## Passo 3 — Abrir o Dashboard

Faz **duplo-clique em `RunCore.command`**.

- O dashboard arranca e **o browser abre sozinho** em `http://127.0.0.1:8765`.
- Deixa a janela preta aberta enquanto usas o RunCore (é o servidor).
- Para parar: fecha a janela.

A partir daqui **fazes tudo no browser**. Nunca mais escreves comandos.

---

## Passo 4 — Configurar as API Keys (no dashboard)

1. No dashboard, clica no ícone de **engrenagem (⚙)** no canto superior direito.
2. Em **Provider API Keys**, cola a tua chave:
   - **Groq** (grátis e rápido) — obtém em https://console.groq.com → cola em "Groq".
   - **Gemini** (grátis) — obtém em https://aistudio.google.com → cola em "Gemini".
   - **Ollama** (local) — instala a app de https://ollama.com; não precisa de chave.
3. Clica em **Save keys**.
4. O badge ao lado de cada provider fica **verde "ready"** quando está tudo certo.

> As chaves ficam guardadas no teu computador (`.runcore/config.json`) — não precisas de as voltar a meter.

---

## Passo 5 — Validar que está tudo a funcionar

Ainda no menu de **Settings (⚙)**, em baixo:

- Clica em **Run tests**.
- Em poucos segundos mostra **"✓ 303 passed · 0 failed"**.

Isto confirma que o motor do RunCore está saudável — sem tocar no terminal.

---

## Passo 6 — Correr um Benchmark

Na página principal do dashboard:

1. No painel **Run Benchmark**, escolhe o **Agent type** (ex.: support).
2. Define **Runs per task** (3 chega para começar).
3. Define **Savings target %** (25 é o default).
4. Clica em **Run Benchmark**.
5. Vês o progresso em tempo real e os resultados aparecem na tabela **Benchmark History**.

---

## Passo 7 — Obter a Certificação RunCore Score™

1. No fundo da página principal, no widget **RunCore Score™**, clica em **Open Certification →**
   (ou vai ao menu **Certification** no topo).
2. Escolhe o provider e clica em **Run Certification**.
3. No fim tens:
   - O **score 0–100** e a **nota (A+/A/B…)**.
   - Um **relatório HTML** assinado (SHA-256) que podes mostrar a clientes.
   - O selo **Certified** se o score ≥ 60.

---

## Resumo Visual

```
  Setup.command   →  (1x) instala tudo
       │
  RunCore.command →  arranca o dashboard + abre o browser
       │
   ┌───────────────────────────── no browser ─────────────────────────────┐
   │  ⚙ Settings → colar API keys → Save → Run tests (303 passed)          │
   │  Run Benchmark → ver savings em tempo real                            │
   │  Certification → score + relatório HTML certificado                   │
   └───────────────────────────────────────────────────────────────────────┘
```

**Nunca escreves um comando.** Só duplo-clique + browser.

---

## Resolução de Problemas

| Problema | Solução |
|----------|---------|
| O `.command` não abre (aviso de segurança) | Botão direito → Abrir → Abrir (só na 1ª vez) |
| Browser não abriu sozinho | Abre manualmente `http://127.0.0.1:8765` |
| Badge fica "check" (laranja) | A chave foi guardada mas o provider não respondeu — confirma a chave e clica **Re-check** |
| Ollama fica "no key" / a vermelho | Abre a app Ollama primeiro; depois **Re-check** |
| Porta ocupada | Fecha outras instâncias do RunCore e volta a abrir `RunCore.command` |
