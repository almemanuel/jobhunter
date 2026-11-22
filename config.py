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

HTTP_TIMEOUT_SECONDS: int = 30
GEMINI_MAX_RETRIES: int = 2
GEMINI_BASE_BACKOFF_SECONDS: float = 2.0

# Domínios agregadores/paywall evitados como link principal (usados só como último fallback)
BLACKLISTED_APPLY_DOMAINS: list[str] = [
    "talent.com",
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
