# System Design Document (SDD): Job Hunter Bot

## 1. Visão Geral e Objetivo

Desenvolver um pipeline automatizado e *serverless* para monitoramento diário (últimas 24h) de vagas de tecnologia (**AI Engineer, LLM Systems, Data Engineer, Analytics Engineer e Tech Lead / Team Lead**), aplicando triagem semântica e investigação ativa de reputação via LLM (`gemini-2.5-flash`) cruzada com o currículo, regras salariais/contratuais e perfil comportamental do candidato, com entrega de alertas curados via Telegram.

## 2. Arquitetura e Stack Tecnológica

* **Linguagem:** Python 3.11+

* **Orquestração / Agendamento:** GitHub Actions (`cron` 2x ao dia: 10h00 e 19h00 BRT / `0 13,22 * * *` UTC).

* **Persistência de Estado (Deduplicação):** Arquivo local `seen_jobs.json` versionado automaticamente no próprio repositório via commit do GitHub Actions (com limite de retenção dos últimos 2.000 IDs).

* **Fontes de Dados (Coleta):**

  1. **JSearch API (RapidAPI):** Busca agregada (LinkedIn, Google Jobs, Indeed, Glassdoor) com filtro temporal `date_posted="today"`.

  2. **Gupy Public API (Extensível):** Consulta direta aos endpoints públicos (`https://portal.api.gupy.io/api/v1/jobs?name=...`) para empresas brasileiras da whitelist.

* **Motor de Triagem & Investigação:** Google GenAI SDK (`google-genai`) utilizando `gemini-2.5-flash` em dois estágios:

  * **Estágio A (Whitelist):** Structured Outputs (`response_mime_type="application/json"`) direto para empresas já pré-aprovadas pelo candidato.

  * **Estágio B (Descoberta / Fora da Whitelist):** Chamada com **Google Search Grounding** (`tools=[{"google_search": {}}]`) obrigatória para investigar clima organizacional, Glassdoor, Reddit e histórico de demissões/toxicidade antes de aprovar.

* **Canal de Notificação:** Telegram Bot API (`sendMessage` via HTTP POST).

## 3. Estrutura de Arquivos do Repositório

```
job-hunter-bot/
├── .github/
│   └── workflows/
│       └── jobs.yml          # Pipeline CI/CD e cron scheduler
├── config.py                 # Whitelists de empresas, queries, currículo e regras de negócio
├── bot.py                    # Orquestrador: coleta, deduplicação, investigação LLM e envio
├── seen_jobs.json            # Estado persistido de IDs já processados (inicia com [])
├── requirements.txt          # Dependências Python
└── README.md                 # Instruções de setup e variáveis de ambiente
```

## 4. Regras de Negócio e Critérios de Triagem

### 4.1. Perfil Técnico e Histórico do Candidato

* **Nome:** Emanuel Almeida

* **Posicionamento:** AI Engineer | LLM Systems & Data Platforms (Nível Pleno / Sênior / Tech Lead)

* **Resumo:** Engenheiro com sólida formação em Ciência da Computação (UTFPR) e experiência prática na construção de pipelines de dados escaláveis, agentes autônomos e sistemas com LLMs. Atuação no desenvolvimento de fluxos orquestrados (LangChain, Python), pipelines de contexto (RAG) e engenharia de dados em larga escala. Foco em arquiteturas robustas, integração de modelos generativos a sistemas produtivos e governança/sanitização de dados (PII).

* **Experiências:**

  * *Grupo UNIGUAÇU | Analytics Engineer Pleno (09/2026 - Presente):* Pipelines corporativos (ELT/ETL), camadas analíticas de alta performance, automação e governança de dados.

  * *iFood | Estagiário de Dados (02/2025 - 03/2026):* Queries SQL complexas no Databricks, métricas operacionais, testes A/B em larga escala, automação com agentes de IA e data quality.

  * *Princesa dos Campos | Analytics Engineer Jr (08/2022 - 01/2025):* Pipelines ETL/ELT (Prefect, Airflow, PySpark, DuckDB), modelagem analítica, SQL de alta performance, automação Python (Pandas/SQLAlchemy), catálogo de dados e referência técnica.

* **Formação, Liderança & Pesquisa:**

  * Bacharelado em Ciência da Computação - UTFPR Ponta Grossa (2020 - 2027).

  * Iniciação Científica (2026): Anonimização de dados (PII) via OCR, RegEx e LLMs.

  * Orion Aerospace Design (2021 - 2023): 2º lugar INPE Cube Design (Dados), telemetria em tempo real (Python), Gerente de Computação Científica e Diretor de Projetos.

* **Stack Técnica:** LLMs, LangChain, Agentes Autônomos, Prompt Engineering, RAG, Sanitização de PII, Python (FastAPI, SQLAlchemy, Pandas, PySpark), SQL Avançado, Databricks, Apache Airflow, Prefect, DuckDB, PostgreSQL, Docker, Git/GitHub.

### 4.2. Perfil Comportamental e Inegociáveis de Cultura (Hard Criteria)

1. **Inegociável — Saúde Mental, Família e Flexibilidade Real:**

   * Respeito estrito a horários e vida pessoal/familiar.

   * **Pode haver registro de ponto (padrão CLT)**, mas é obrigatório que exista **flexibilidade real de horário e foco em entregas** (*outcome-based*), além de pacote de benefícios estruturado.

   * Descartar imediatamente vagas com sinais de microgerenciamento, cobrança rígida de minuto a minuto, monitoramento de presença online constante, "ritmo frenético/pressão extrema" ou cultura tóxica.

2. **Cultura Escrita e Espaço para Produzir:**

   * Preferência por times *remote-first* com forte cultura de documentação escrita e comunicação assíncrona (poucas reuniões, blocos livres para execução profunda).

   * Prefere, mas não exige atuar de ponta a ponta (*end-to-end*) em tudo: valoriza atuar em um escopo específico bem definido desde que haja espaço para fazer um trabalho técnico bem feito e com qualidade.

3. **Voz Ativa e Liderança Técnica:**

   * Busca ambientes onde tenha voz ativa nas decisões de arquitetura e engenharia.

   * Considerar também vagas de **Tech Lead / Team Lead** (Dados ou IA) quando a stack e a cultura forem aderentes.

### 4.3. Regras Eliminatórias de Local, Regime e Salário

1. **Cenário A — Brasil Remoto:**

   * Regime **exclusivamente CLT** (descartar imediatamente PJ, Cooperado, Associado ou Temporário).

   * Modalidade **100% Remota** (descartar híbrido ou presencial fora de Ponta Grossa).

   * Deve pertencer à `TARGET_COMPANIES_BR_REMOTE`.

   * Faixa salarial estimada/compatível: $\ge$ **R\$ 5.000,00 CLT**.

2. **Cenário B — Ponta Grossa-PR (Presencial ou Híbrido):**

   * Regime **exclusivamente CLT**.

   * Localização obrigatória em **Ponta Grossa, PR** (presencial ou híbrido).

   * Prioridade máxima para `TARGET_COMPANIES_PG`.

   * Faixa salarial estimada/compatível: $\ge$ **R\$ 6.000,00 CLT**.

3. **Cenário C — Exterior (Remoto Internacional):**

   * Inglês **não pode ser bloqueador**: aceitar vagas com inglês técnico/leitura, comunicação assíncrona ou em português. Descartar imediatamente se exigir fluência/avançado, calls diárias síncronas em inglês ou autorização de trabalho/residência fora do Brasil.

   * Faixa salarial estimada/compatível: $\ge$ **US\$ 2.500/mês** (aceita estimativa baseada em nível/stack/país quando omitido).

### 4.4. Trava de Reputação ("Sem Evidência de Clima, Sem Vaga")

* **Empresas na Whitelist (`TARGET_COMPANIES_BR_REMOTE` ou `TARGET_COMPANIES_PG`):** Já possuem aprovação prévia de reputação; avaliar aderência da vaga ao perfil e regras acima.

* **Empresas FORA da Whitelist (ex: descobertas remotas no exterior ou Brasil):**

  * O script **deve** acionar o `google_search` grounding do Gemini pesquisando: `"<Nome da Empresa>" (glassdoor OR reddit OR "work culture" OR layoffs OR reviews OR flexibilidade)`.

  * **Regra de Descarte Automático:** Se a busca não retornar informações públicas concretas e suficientes sobre o clima organizacional/reputação da empresa, ou se encontrar relatos recorrentes de toxicidade, atrasos ou microgerenciamento, definir obrigatoriamente `"match": false`. **Nunca aprovar empresa desconhecida no escuro.**

## 5. Especificação de Dados e Contratos

### 5.1. Listas de Empresas-Alvo (`config.py`)

* **`TARGET_COMPANIES_BR_REMOTE`:** `["Nubank", "iFood", "Accenture", "Itaú", "Sicredi", "Mercado Livre", "Tata Consultancy Services", "Asaas", "NTT Data", "Amazon", "99", "Google", "Felix", "Wellhub", "Globo", "Tractian", "Magalu", "OLX", "Uber", "nstech", "WWF", "Boston Consulting Group", "JP Morgan", "Anistia Internacional", "Food To Save", "Open Knowledge Brasil", "Revolut", "Paramount", "Apple", "Web Jasper", "Wildlife Conservation Society", "ONU", "Disney", "OIT Brasil", "Netflix", "UNICEF", "Water.org", "TNC", "Duolingo", "SaveFood", "Prosus", "Unimed", "HBO", "Meta", "Noh", "AlgorithmWatch", "OMS", "Fairwork Limited", "Meituan", "Keeta", "Decolar", "Boticário", "Binance", "Mercado Bitcoin", "Bitso", "Coinbase", "Kraken", "Chainalysis", "Ripio", "B3", "Inter", "Nomad", "Cora", "Warren", "PagBank", "PicPay", "Brex", "Stripe", "Wise", "Indicium", "QuintoAndar", "Databricks", "Snowflake", "dbt Labs", "RD Station", "Hotmart", "VTEX", "Zup Innovation", "CI&T", "Omie", "TOTVS", "Alice", "Dasa", "Spotify"]`

* **`TARGET_COMPANIES_PG`:** `["Zallpy", "Continental", "nstech", "Cargill", "Inbix", "AI Builders", "DataHolds", "Sicredi", "Águia Sistemas", "Webfloat", "Pellissari", "Rumo", "NTI", "São Camilo", "Frísia", "Unimed", "Castrolanda", "Agrocete", "Princesa dos Campos", "Ambev", "Espaço Smart", "Coca-Cola", "Heineken", "Bunge", "Gondaski"]`

### 5.2. Queries de Coleta

1. `("AI Engineer" OR "LLM" OR "GenAI" OR "LangChain" OR "AI Tech Lead")`

2. `("Analytics Engineer" OR "Data Engineer" OR "Engenheiro de Dados" OR "Data Tech Lead")`

3. `("AI Engineer" OR "Data Engineer" OR "Analytics Engineer" OR "Python") remote LATAM OR Brazil`

### 5.3. Contrato JSON de Saída do LLM (`gemini-2.5-flash`)

O modelo deve retornar estritamente o seguinte schema JSON:

```json
{
  "match": "boolean",
  "score": "integer (0 a 10)",
  "na_whitelist": "boolean",
  "categoria": "string ('BR Remoto CLT' | 'PG Presencial/Híbrido CLT' | 'Gringa Remoto' | 'Liderança Técnica' | 'Descartada')",
  "regime_detectado": "string ('CLT' | 'PJ' | 'Internacional/Contractor' | 'Não especificado')",
  "clima_e_cultura": "string (resumo das evidências encontradas na descrição e/ou busca web sobre clima, flexibilidade, ponto e benefícios)",
  "motivo": "string (1 frase objetiva explicando o porquê da aprovação ou descarte)",
  "destaque_cv": "string (1 recomendação prática de qual experiência/skill destacar na candidatura)"
}
```

* **Limiar de Notificação:** Apenas vagas com `match == true` e `score >= 7` disparam alerta no Telegram.

### 5.4. Template da Mensagem Telegram

```text
🎯 *{job_title}* — {employer_name}
📌 *Categoria:* {categoria} | *Regime:* {regime_detectado} | *Score:* {score}/10
🏢 *Clima & Cultura:* {clima_e_cultura}
💡 *Por que deu match:* {motivo}
📝 *Destaque no CV:* {destaque_cv}
🔗 [Aplicar na vaga]({job_apply_link})
```

## 6. Fluxo de Execução (`bot.py`)

1. **Inicialização:** Carregar variáveis de ambiente (`RAPIDAPI_KEY`, `GEMINI_API_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`) e ler `seen_jobs.json` para um `set` em memória.

2. **Coleta de Vagas:**

   * Executar queries 1 e 2 na JSearch API com `location="Ponta Grossa, Paraná, Brazil"` e `date_posted="today"`.

   * Executar queries 1, 2 e 3 na JSearch API com `location="Brazil"` e `date_posted="today"`.

   * Normalizar resultados em uma lista única de objetos de vaga contendo `job_id`, `job_title`, `employer_name`, `job_city`, `job_state`, `job_country`, `job_is_remote`, `job_description` e `job_apply_link`.

3. **Deduplicação Pré-LLM:**

   * Ignorar imediatamente qualquer `job_id` já presente em `seen_jobs.json`.

   * Adicionar o novo `job_id` ao `set` de vistos.

4. **Roteamento de Triagem (Whitelist vs. Investigação Web):**

   * Verificar (case-insensitive / substring match) se `employer_name` está em `TARGET_COMPANIES_BR_REMOTE` ou `TARGET_COMPANIES_PG`.

   * **Se estiver na whitelist:** Chamar `gemini-2.5-flash` com `response_mime_type="application/json"`.

   * **Se NÃO estiver na whitelist:** Chamar `gemini-2.5-flash` habilitando `tools=[{"google_search": {}}]` com instrução explícita de investigar Glassdoor/Reddit/notícias sobre clima, flexibilidade e estabilidade da empresa, aplicando a trava de descarte se não houver evidências concretas. *(Nota: como o Gemini API não permite combinar `google_search` + `response_mime_type="application/json"` na mesma chamada em algumas versões do SDK, fazer a busca com Grounding em texto e converter/extrair o JSON logo em seguida ou via regex/parser seguro).*

5. **Notificação:**

   * Se `match == True` e `score >= 7`, enviar mensagem formatada em Markdown para o Telegram (com escape seguro de caracteres especiais para não quebrar o parser).

6. **Persistência:**

   * Salvar os últimos 2.000 IDs no `seen_jobs.json`.

## 7. Requisitos Não-Funcionais e Resiliência

* **Segurança:** Nenhuma chave de API *hardcoded*; todas injetadas via `GitHub Secrets`.

* **Idempotência:** Rodar o script múltiplas vezes no mesmo dia nunca deve gerar alertas duplicados.

* **Tolerância a Falhas & Rate Limits:** Incluir backoff exponencial para `429 Too Many Requests` da API do Gemini e tratamento de exceções por vaga para que uma falha pontual não aborte o pipeline inteiro.

* **Dependências (`requirements.txt`):**

  ```text
  requests>=2.31.0
  google-genai>=1.0.0
  ```