"""Configurações, whitelists, queries e regras de negócio do Job Hunter Bot."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Carrega variáveis de um arquivo .env local sem sobrescrever variáveis já definidas."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


load_dotenv()

# =============================================================================
# Parâmetros Gerais e APIs
# =============================================================================

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_DEFAULT_MODELS: list[str] = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
]
GEMINI_MODELS_URL: str = "https://generativelanguage.googleapis.com/v1beta/models"
MIN_MATCH_SCORE: int = 7
MAX_SEEN_JOBS: int = 2000
SEEN_JOBS_PATH: str = "seen_jobs.json"

JSEARCH_API_URL: str = "https://jsearch.p.rapidapi.com/search-v2"
JSEARCH_API_HOST: str = "jsearch.p.rapidapi.com"

GUPY_API_URL: str = "https://portal.api.gupy.io/api/v1/jobs"
ENABLE_GUPY: bool = os.getenv("ENABLE_GUPY", "false").lower() in ("1", "true", "yes")
ENABLE_OFFICIAL_ATS: bool = os.getenv("ENABLE_OFFICIAL_ATS", "true").lower() in ("1", "true", "yes")
MAX_NEW_ATS_JOBS_PER_RUN: int = int(os.getenv("MAX_NEW_ATS_JOBS_PER_RUN", "15"))

# APIs oficiais de ATS (100% diretas das empresas, zero agregadores/paywalls)
OFFICIAL_GREENHOUSE_BOARDS: dict[str, str] = {
    "gympass": "Wellhub",
    "quintoandar": "QuintoAndar",
    "vtex": "VTEX",
    "inter": "Inter",
    "brex": "Brex",
    "databricks": "Databricks",
    "stripe": "Stripe",
    "coinbase": "Coinbase",
    "duolingo": "Duolingo",
    "wise": "Wise",
    "anthropic": "Anthropic",
}

OFFICIAL_LEVER_BOARDS: dict[str, str] = {
    "spotify": "Spotify",
    "binance": "Binance",
    "ciandt": "CI&T",
}

OFFICIAL_ASHBY_BOARDS: dict[str, str] = {
    "snowflake": "Snowflake",
    "kraken": "Kraken",
}

OFFICIAL_ATS_TITLE_KEYWORDS: tuple[str, ...] = (
    "ai engineer",
    "llm",
    "genai",
    "generative ai",
    "analytics engineer",
    "data engineer",
    "engenheiro de dados",
    "engenharia de dados",
    "data platform",
    "machine learning",
    "ml engineer",
    "applied ai",
    "tech lead",
    "team lead",
)

OFFICIAL_ATS_LOCATION_KEYWORDS: tuple[str, ...] = (
    "brazil",
    "brasil",
    "latam",
    "latin america",
    "south america",
    "americas",
    "remote",
    "remoto",
    "anywhere",
    "global",
    "ponta grossa",
)

OFFICIAL_ATS_EXCLUDED_LOCATIONS: tuple[str, ...] = (
    "united states only",
    "us only",
    "usa only",
    "u.s. only",
    "uk only",
    "emea only",
    "apac only",
    "san francisco",
    "new york",
    "seattle",
    "london",
    "dublin",
    "berlin",
    "amsterdam",
    "singapore",
    "tokyo",
    "bengaluru",
    "bangalore",
    "india",
    "canada",
    "australia",
)

HTTP_TIMEOUT_SECONDS: int = 30
GEMINI_MAX_RETRIES: int = 2
GEMINI_BASE_BACKOFF_SECONDS: float = 2.0

# Domínios agregadores/paywall evitados como link principal (usados só como último fallback)
BLACKLISTED_APPLY_DOMAINS: list[str] = [
    "talent.com",
    "jobleads",
    "jobhunter",
    "jooble.org",
    "adzuna.com",
    "neuvoo.com",
    "bebee.com",
    "whatjobs.com",
    "jobrapido.com",
]

# Portais oficiais / ATS priorizados logo após o site direto da empresa
PREFERRED_ATS_DOMAINS: list[str] = [
    "gupy.io",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "breezy.hr",
    "inhire.app",
    "solides.com.br",
    "recruitee.com",
    "workable.com",
    "icims.com",
    "taleo.net",
    "linkedin.com",
]

# =============================================================================
# Whitelists de Empresas-Alvo (Seção 5.1)
# =============================================================================

TARGET_COMPANIES_BR_REMOTE: list[str] = [
    "Nubank",
    "iFood",
    "Accenture",
    "Itaú",
    "Sicredi",
    "Mercado Livre",
    "Tata Consultancy Services",
    "Asaas",
    "NTT Data",
    "Amazon",
    "99",
    "Google",
    "Felix",
    "Wellhub",
    "Globo",
    "Tractian",
    "Magalu",
    "OLX",
    "Uber",
    "nstech",
    "WWF",
    "Boston Consulting Group",
    "JP Morgan",
    "Anistia Internacional",
    "Food To Save",
    "Open Knowledge Brasil",
    "Revolut",
    "Paramount",
    "Apple",
    "Web Jasper",
    "Wildlife Conservation Society",
    "ONU",
    "Disney",
    "OIT Brasil",
    "Netflix",
    "UNICEF",
    "Water.org",
    "TNC",
    "Duolingo",
    "SaveFood",
    "Prosus",
    "Unimed",
    "HBO",
    "Meta",
    "Noh",
    "AlgorithmWatch",
    "OMS",
    "Fairwork Limited",
    "Meituan",
    "Keeta",
    "Decolar",
    "Boticário",
    # Cripto & Web3
    "Binance",
    "Mercado Bitcoin",
    "Bitso",
    "Coinbase",
    "Kraken",
    "Chainalysis",
    "Ripio",
    # Investimentos, Bancos Digitais & Fintechs
    "B3",
    "Inter",
    "Nomad",
    "Cora",
    "Warren",
    "PagBank",
    "PicPay",
    "Brex",
    "Stripe",
    "Wise",
    # Dados, Analytics Engineering & IA
    "Indicium",
    "QuintoAndar",
    "Databricks",
    "Snowflake",
    "dbt Labs",
    "RD Station",
    "Hotmart",
    "VTEX",
    "Zup Innovation",
    "CI&T",
    "Omie",
    "TOTVS",
    # Healthtech & Mídia
    "Alice",
    "Dasa",
    "Spotify",
]

TARGET_COMPANIES_PG: list[str] = [
    "Zallpy",
    "Continental",
    "nstech",
    "Cargill",
    "Inbix",
    "AI Builders",
    "DataHolds",
    "Sicredi",
    "Águia Sistemas",
    "Webfloat",
    "Pellissari",
    "Rumo",
    "NTI",
    "São Camilo",
    "Frísia",
    "Unimed",
    "Castrolanda",
    "Agrocete",
    "Princesa dos Campos",
    "Ambev",
    "Espaço Smart",
    "Coca-Cola",
    "Heineken",
    "Bunge",
    "Gondaski",
]

# =============================================================================
# Queries de Coleta (Seções 5.2 e 6.2)
# =============================================================================

QUERY_AI: str = '("AI Engineer" OR "LLM" OR "GenAI" OR "LangChain" OR "AI Tech Lead")'
QUERY_DATA: str = (
    '("Analytics Engineer" OR "Data Engineer" OR "Engenheiro de Dados" OR "Data Tech Lead")'
)
QUERY_REMOTE_INTL: str = (
    '("AI Engineer" OR "Data Engineer" OR "Analytics Engineer" OR "Python") '
    "remote LATAM OR Brazil"
)

JSEARCH_PLANS: list[dict[str, str]] = [
    # Ponta Grossa, PR (queries 1 e 2)
    {"query": QUERY_AI, "location": "Ponta Grossa, Paraná, Brazil", "date_posted": "today"},
    {"query": QUERY_DATA, "location": "Ponta Grossa, Paraná, Brazil", "date_posted": "today"},
    # Brasil / Remoto (queries 1, 2 e 3)
    {"query": QUERY_AI, "location": "Brazil", "date_posted": "today"},
    {"query": QUERY_DATA, "location": "Brazil", "date_posted": "today"},
    {"query": QUERY_REMOTE_INTL, "location": "Brazil", "date_posted": "today"},
]

# Termos de busca na API pública da Gupy (focados em empresas da whitelist BR)
GUPY_SEARCH_TERMS: list[str] = [
    "AI Engineer",
    "LLM",
    "GenAI",
    "Analytics Engineer",
    "Data Engineer",
    "Engenheiro de Dados",
    "Tech Lead",
]

# =============================================================================
# Perfil do Candidato (Master Profile) e Regras de Negócio (Seção 4)
# =============================================================================

PROFILE_PATH: str = os.getenv("PROFILE_PATH", "profile.md")
ENABLE_TAILORED_CV: bool = os.getenv("ENABLE_TAILORED_CV", "true").lower() in ("1", "true", "yes")
AUTO_APPLY_ENABLED: bool = os.getenv("AUTO_APPLY_ENABLED", "true").lower() in ("1", "true", "yes")
AUTO_APPLY_DRY_RUN: bool = os.getenv("AUTO_APPLY_DRY_RUN", "true").lower() in ("1", "true", "yes")

# Respostas padrão do candidato (pré-populam `candidate_answers` no Neon sem sobrescrever edições manuais)
DEFAULT_CANDIDATE_ANSWERS: dict[str, tuple[str, str, str]] = {
    "full_name": ("Nome completo", "Emanuel de Almeida Souza", "personal"),
    "first_name": ("Primeiro nome", "Emanuel", "personal"),
    "last_name": ("Sobrenome", "de Almeida Souza", "personal"),
    "email": ("E-mail", "emanuel.almeida1998@outlook.com", "personal"),
    "phone": ("Telefone / WhatsApp", "+55 42 98423-1561", "personal"),
    "city": ("Cidade", "Ponta Grossa", "location"),
    "state": ("Estado", "Paraná", "location"),
    "country": ("País", "Brasil", "location"),
    "location": ("Localização", "Ponta Grossa, Paraná, Brasil", "location"),
    "linkedin_url": ("LinkedIn", "https://www.linkedin.com/in/almeida-emanuel", "links"),
    "github_url": ("GitHub", "https://github.com/almemanuel", "links"),
    "portfolio_url": ("Portfólio / Website", "https://github.com/almemanuel", "links"),
    "current_company": ("Empresa atual", "Faculdade UNIGUAÇU", "experience"),
    "current_role": ("Cargo atual", "Analista de Dados / Analytics Engineer Pleno", "experience"),
    "education_school": ("Universidade", "Universidade Tecnológica Federal do Paraná (UTFPR)", "education"),
    "education_degree": ("Curso / Formação", "Bacharelado em Ciência da Computação", "education"),
    "graduation_year": ("Ano de formatura", "2026", "education"),
    "salary_clt_remote_brl": ("Pretensão salarial CLT Remoto (R$)", "5500", "compensation"),
    "salary_clt_pg_brl": ("Pretensão salarial CLT Ponta Grossa (R$)", "6000", "compensation"),
    "salary_intl_usd_monthly": ("Pretensão salarial Internacional (US$/mês)", "2500", "compensation"),
    "notice_period": ("Disponibilidade de início / Aviso prévio", "30 dias", "availability"),
    "work_authorization_br": ("Autorização para trabalhar no Brasil", "Sim", "legal"),
    "needs_visa_sponsorship": ("Precisa de visto/sponsorship", "Não", "legal"),
    "english_level": ("Nível de inglês", "Técnico / Leitura (Básico em conversação)", "languages"),
}

_FALLBACK_CANDIDATE_PROFILE: str = """
### Perfil Técnico e Histórico do Candidato
- **Nome:** Emanuel de Almeida Souza
- **Posicionamento:** AI Engineer | Data Platform Engineer | Analytics Engineer | Tech Lead
- **Experiências:** Grupo UNIGUAÇU (Analytics Engineer Pleno), Valence Tech (Fundador / PM & PO do produto Calvarium, liderando 2 devs), iFood (Estagiário de Dados — Databricks/PySpark/Agentes de IA), UTFPR (IC PII com OpenCV/OCR/LLMs), Princesa dos Campos (Analytics Engineer — Prefect/Airflow/DuckDB/PySpark/Flask), Orion Aerospace Design (2º lugar INPE Cube Design, Gerente/Diretor).
""".strip()


def load_candidate_profile(path: str | Path = PROFILE_PATH) -> str:
    """Carrega o Master Profile completo de `profile.md` (fallback embutido se ausente)."""
    profile_file = Path(path)
    if profile_file.is_file():
        content = profile_file.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _FALLBACK_CANDIDATE_PROFILE


CANDIDATE_PROFILE: str = load_candidate_profile()

BUSINESS_RULES: str = """
### Perfil Comportamental e Inegociáveis de Cultura (Hard Criteria)
1. **Saúde Mental, Família e Flexibilidade Real (Inegociável):**
   - Respeito estrito a horários e vida pessoal/familiar.
   - Pode haver registro de ponto (padrão CLT), mas é obrigatório que exista flexibilidade real de horário e foco em entregas (outcome-based), além de pacote de benefícios estruturado.
   - Descartar imediatamente vagas com sinais de microgerenciamento, cobrança rígida de minuto a minuto, monitoramento de presença online constante, "ritmo frenético/pressão extrema" ou cultura tóxica.
2. **Cultura Escrita e Espaço para Produzir:**
   - Preferência por times remote-first com forte cultura de documentação escrita e comunicação assíncrona (poucas reuniões, blocos livres para execução profunda).
   - Prefere, mas não exige atuar de ponta a ponta (end-to-end) em tudo: valoriza atuar em um escopo específico bem definido desde que haja espaço para fazer um trabalho técnico bem feito e com qualidade.
3. **Voz Ativa e Liderança Técnica:**
   - Busca ambientes onde tenha voz ativa nas decisões de arquitetura e engenharia.
   - Considerar também vagas de Tech Lead / Team Lead (Dados ou IA) quando a stack e a cultura forem aderentes.

### Regras Eliminatórias de Local, Regime e Salário
1. **Cenário A — Brasil Remoto:**
   - Regime exclusivamente CLT (descartar imediatamente PJ, Cooperado, Associado ou Temporário).
   - Modalidade 100% Remota (descartar híbrido ou presencial fora de Ponta Grossa).
   - Prioridade/exigência de pertencimento à whitelist TARGET_COMPANIES_BR_REMOTE (para empresas fora da whitelist no Brasil, aplicar rigor máximo na trava de reputação e garantir CLT + 100% remoto).
   - Faixa salarial estimada/compatível: >= R$ 5.000,00 CLT.
2. **Cenário B — Ponta Grossa-PR (Presencial ou Híbrido):**
   - Regime exclusivamente CLT.
   - Localização obrigatória em Ponta Grossa, PR (presencial ou híbrido).
   - Prioridade máxima para TARGET_COMPANIES_PG.
   - Faixa salarial estimada/compatível: >= R$ 6.000,00 CLT.
3. **Cenário C — Exterior (Remoto Internacional):**
   - Inglês não pode ser bloqueador: aceitar vagas com inglês técnico/leitura, comunicação assíncrona ou em português. Descartar imediatamente se exigir fluência/avançado, calls diárias síncronas em inglês ou autorização de trabalho/residência fora do Brasil.
   - Faixa salarial estimada/compatível: >= US$ 2.500/mês (aceita estimativa baseada em nível/stack/país quando omitido).

### Régua Rigorosa de Pontuação (`score` de 0 a 10 — SEJA CRÍTICO E REALISTA)
- **0 a 3 (`match: false`):** Violou critério eliminatório (PJ no Brasil, presencial/híbrido fora de Ponta Grossa, inglês avançado/fluente obrigatório, cultura tóxica ou empresa desconhecida sem reputação).
- **4 a 6 (`match: false`):** Sem violação grave, mas com gaps relevantes de senioridade, stack distante do perfil ou salário/regime incerto.
- **7 (`match: true`):** Boa vaga — atende às regras e à maioria da stack, mas possui 1 a 2 gaps técnicos ou pontos de atenção (ex: exige senioridade acima da média ou ferramenta secundária ainda em aprofundamento).
- **8 (`match: true`):** Muito boa — stack, nível, cultura e momento de carreira bem alinhados.
- **9 (`match: true`):** Excelente — encaixe raro em stack, cultura, flexibilidade e potencial de crescimento.
- **10 (`match: true`):** Vaga dos sonhos (RESERVAR APENAS PARA CASOS RARÍSSIMOS com 100% de aderência exata, benefícios/cultura excepcionais e zero gaps). NÃO inflacione notas para 10!
""".strip()

# =============================================================================
# Schema de Saída do LLM (Seção 5.3)
# =============================================================================

EVALUATION_RESPONSE_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "match": {"type": "BOOLEAN"},
        "score": {"type": "INTEGER"},
        "na_whitelist": {"type": "BOOLEAN"},
        "categoria": {
            "type": "STRING",
            "enum": [
                "BR Remoto CLT",
                "PG Presencial/Híbrido CLT",
                "Gringa Remoto",
                "Liderança Técnica",
                "Descartada",
            ],
        },
        "regime_detectado": {
            "type": "STRING",
            "enum": [
                "CLT",
                "PJ",
                "Internacional/Contractor",
                "Não especificado",
            ],
        },
        "clima_e_cultura": {"type": "STRING"},
        "motivo": {"type": "STRING"},
        "destaque_cv": {"type": "STRING"},
    },
    "required": [
        "match",
        "score",
        "na_whitelist",
        "categoria",
        "regime_detectado",
        "clima_e_cultura",
        "motivo",
        "destaque_cv",
    ],
}
