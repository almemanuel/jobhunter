# Job Hunter Bot

Pipeline automatizado e *serverless* para monitoramento diário (últimas 24h) de vagas de tecnologia (**AI Engineer, LLM Systems, Data Engineer, Analytics Engineer e Tech Lead / Team Lead**), com triagem semântica, investigação ativa de reputação via `gemini-2.5-flash` (Google Search Grounding) e envio de alertas curados no Telegram.

## Arquitetura

- **Coleta:** JSearch API (RapidAPI) + Gupy Public API (extensível, filtrada para empresas brasileiras da whitelist).
- **Deduplicação:** Persistida em `seen_jobs.json` (mantendo os últimos 2.000 IDs), versionada automaticamente via GitHub Actions.
- **Triagem em 2 estágios (`gemini-2.5-flash`):**
  - **Estágio A (Whitelist):** Structured Output JSON direto para empresas pré-aprovadas (`TARGET_COMPANIES_BR_REMOTE` e `TARGET_COMPANIES_PG`).
  - **Estágio B (Descoberta):** Investigação obrigatória com **Google Search Grounding** sobre Glassdoor, Reddit, clima organizacional, flexibilidade e estabilidade — com trava de descarte automático caso não haja evidências concretas.
- **Notificação:** Envio via Telegram Bot API apenas para vagas com `match == true` e `score >= 7`.
- **Agendamento:** GitHub Actions rodando 1x ao dia à noite (`19h00` BRT / `0 22 * * *` UTC) + disparo manual (`workflow_dispatch`).

## Estrutura do Repositório

```text
.
├── .github/workflows/jobs.yml   # Pipeline CI/CD e cron scheduler
├── profile.md                   # Master Profile completo + Roadmap de evolução (TCC)
├── config.py                    # Whitelists, queries e regras de negócio
├── cv_generator.py              # Gerador de currículo ATS em PDF adaptado por vaga
├── bot.py                       # Orquestrador principal
├── seen_jobs.json               # Estado persistido de IDs processados
├── requirements.txt             # Dependências Python
└── tests/test_bot.py            # Testes unitários
```

## Variáveis de Ambiente / GitHub Secrets

Configure os seguintes secrets em **Settings → Secrets and variables → Actions** no GitHub:

| Variável | Descrição |
| --- | --- |
| `RAPIDAPI_KEY` | Chave da RapidAPI com assinatura ativa na **JSearch API** |
| `GEMINI_API_KEY` | Chave de API do Google AI Studio (`gemini-2.5-flash`) |
| `TELEGRAM_TOKEN` | Token do bot gerado pelo `@BotFather` no Telegram |
| `TELEGRAM_CHAT_ID` | ID do chat/usuário que receberá os alertas (ex: via `@userinfobot`) |

Opcional:
- `ENABLE_GUPY`: `"true"` (padrão) ou `"false"` para desabilitar a coleta complementar na Gupy.

## Execução Local

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt

# Copie o modelo de variáveis de ambiente e preencha suas chaves:
cp .env.example .env

# Execute o bot (carrega o .env automaticamente):
python bot.py
```

## Testes

```bash
python -m unittest discover -s tests -v
```
