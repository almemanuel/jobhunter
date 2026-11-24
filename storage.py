"""Camada de persistência e deduplicação em 4 camadas (Neon PostgreSQL + fallback JSON)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import ssl
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import config

logger = logging.getLogger("job-hunter-bot")

# Aliases conhecidos para unificar variações de nome entre ATS oficial e JSearch
_COMPANY_ALIASES: dict[str, str] = {
    "ci&t": "ciandt",
    "ci t": "ciandt",
    "cit": "ciandt",
    "gympass": "wellhub",
    "grupo boticario": "boticario",
    "o boticario": "boticario",
    "expresso princesa dos campos": "princesa dos campos",
    "itau unibanco": "itau",
    "banco inter": "inter",
    "inter co": "inter",
    "livelo brasil": "livelo",
    "stone co": "stone",
    "stone pagamentos": "stone",
    "mercado libre": "mercado livre",
    "meli": "mercado livre",
    "magazine luiza": "magalu",
}

_LEGAL_SUFFIXES = re.compile(
    r"\b(ltda|s\.?a\.?|inc\.?|llc|corp\.?|co\.?|limited|ltd|brasil|brazil|latam|grupo|group)\b",
    flags=re.IGNORECASE,
)

_TITLE_NOISE = re.compile(
    r"(\[\s*(?:job\s*-?\s*)?\d+\s*\]|"
    r"job\s*id\s*:\s*[a-z0-9_-]+|"
    r"ref#?\s*[a-z0-9_-]+|"
    r"\((?:brazil|brasil|latam|remote|remoto|women applicants only)\)|"
    r"\b(?:remote|remoto|brazil|brasil|latin america|latam|remote work)\b)",
    flags=re.IGNORECASE,
)


def _norm(text: str) -> str:
    """Remove acentos, converte para lowercase e colapsa espaços."""
    nf = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in nf if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def canonical_company(name: str) -> str:
    """Normaliza nome de empresa unificando aliases (ex: CI&T == Ciandt, Stone Co. == Stone)."""
    base = _norm(name)
    if base in _COMPANY_ALIASES:
        return _COMPANY_ALIASES[base]
    cleaned = _LEGAL_SUFFIXES.sub(" ", base)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return _COMPANY_ALIASES.get(cleaned, cleaned or base)


def canonical_title(title: str) -> str:
    """Normaliza título removendo códigos internos ([Job - 32496], REF#..., Brazil, Remote)."""
    base = _norm(title)
    cleaned = _TITLE_NOISE.sub(" ", base)
    cleaned = re.sub(r"[^a-z0-9+#]+", " ", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned)


def normalize_apply_url(url: str) -> str:
    """Remove query params de tracking (utm_*, gh_src) e barra final para deduplicação de URL."""
    cleaned = (url or "").strip()
    if not cleaned or not cleaned.startswith("http"):
        return ""
    if "linkedin.com/jobs/search" in cleaned.lower():
        return ""  # URL de busca genérica não serve como chave única de vaga
    parsed = urlparse(cleaned)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def description_fingerprint(description: str) -> str:
    """Gera SHA-256 estável dos primeiros 800 caracteres normalizados da descrição."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", _norm(description)).strip()
    if len(cleaned) < 120:
        return ""
    return hashlib.sha256(cleaned[:800].encode("utf-8")).hexdigest()


def titles_are_similar(title_a: str, title_b: str, threshold: float = 0.84) -> bool:
    """Compara dois títulos canônicos por igualdade ou similaridade textual (>= 84%)."""
    if not title_a or not title_b:
        return False
    if title_a == title_b:
        return True
    return SequenceMatcher(None, title_a, title_b).ratio() >= threshold


class JobStorage:
    """Gerencia persistência e deduplicação em 4 camadas (Neon PostgreSQL ou JSON local)."""

    def __init__(
        self,
        database_url: str = "",
        json_path: str | Path = config.SEEN_JOBS_PATH,
        max_json_items: int = config.MAX_SEEN_JOBS,
    ) -> None:
        self.database_url = (database_url or "").strip()
        self.json_path = Path(json_path)
        self.max_json_items = max_json_items
        self._conn = None

        # Índices em memória para checagem O(1) durante a execução
        self._seen_ids: set[str] = set()
        self._seen_urls: set[str] = set()
        self._seen_desc_hashes: set[str] = set()
        self._company_titles: dict[str, list[str]] = {}
        self._json_list: list[str] = []

    @property
    def using_postgres(self) -> bool:
        return self._conn is not None

    def _open_postgres_connection(self):
        """Abre conexão PostgreSQL via pg8000 (100% Python puro, compatível com Windows App Control e Linux)."""
        import pg8000.dbapi  # type: ignore

        parsed = urlparse(self.database_url)
        ssl_context = ssl.create_default_context()
        conn = pg8000.dbapi.connect(
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            database=(parsed.path or "").lstrip("/"),
            ssl_context=ssl_context,
        )
        conn.autocommit = True
        return conn

    def connect(self) -> None:
        """Inicializa conexão com Neon PostgreSQL (ou carrega JSON local como fallback)."""
        if self.database_url:
            try:
                self._conn = self._open_postgres_connection()
                self._ensure_schema()
                self._load_from_postgres()
                self._seed_from_json_if_empty()
                logger.info(
                    "Persistência Neon PostgreSQL ativa (%d vagas no histórico recente).",
                    len(self._seen_ids),
                )
                return
            except Exception as exc:
                logger.error(
                    "Falha ao conectar no PostgreSQL (%s). Usando fallback local %s.",
                    exc,
                    self.json_path,
                )
                self._conn = None

        self._load_from_json()
        logger.info(
            "Persistência local JSON ativa (%d entradas em %s).",
            len(self._json_list),
            self.json_path,
        )

    def close(self) -> None:
        """Finaliza conexão ou salva arquivo JSON local."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        else:
            self._save_to_json()

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        statements = [
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id BIGSERIAL PRIMARY KEY,
                job_id TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                employer_name TEXT NOT NULL DEFAULT '',
                canonical_company TEXT NOT NULL DEFAULT '',
                job_title TEXT NOT NULL DEFAULT '',
                canonical_title TEXT NOT NULL DEFAULT '',
                apply_url TEXT NOT NULL DEFAULT '',
                normalized_url TEXT NOT NULL DEFAULT '',
                description_hash TEXT NOT NULL DEFAULT '',
                match BOOLEAN NOT NULL DEFAULT FALSE,
                score SMALLINT NOT NULL DEFAULT 0,
                categoria TEXT NOT NULL DEFAULT '',
                regime_detectado TEXT NOT NULL DEFAULT '',
                clima_e_cultura TEXT NOT NULL DEFAULT '',
                motivo TEXT NOT NULL DEFAULT '',
                model_used TEXT NOT NULL DEFAULT '',
                notified BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_jobs_canonical_comp_title ON jobs (canonical_company, canonical_title)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_normalized_url ON jobs (normalized_url) WHERE normalized_url <> ''",
            "CREATE INDEX IF NOT EXISTS idx_jobs_description_hash ON jobs (description_hash) WHERE description_hash <> ''",
            "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at DESC)",
        ]
        cur = self._conn.cursor()
        try:
            for stmt in statements:
                cur.execute(stmt)
        finally:
            cur.close()

    def _load_from_postgres(self) -> None:
        assert self._conn is not None
        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                SELECT job_id, canonical_company, canonical_title, normalized_url, description_hash
                FROM jobs
                WHERE created_at >= NOW() - INTERVAL '45 days'
                """
            )
            for job_id, comp, title, norm_url, desc_hash in cur.fetchall():
                if job_id:
                    self._seen_ids.add(job_id)
                if norm_url:
                    self._seen_urls.add(norm_url)
                if desc_hash:
                    self._seen_desc_hashes.add(desc_hash)
                if comp and title:
                    self._company_titles.setdefault(comp, []).append(title)
        finally:
            cur.close()

    def _seed_from_json_if_empty(self) -> None:
        """Migra IDs já processados de seen_jobs.json na primeira conexão com o banco vazio."""
        if self._seen_ids or not self.json_path.is_file():
            return
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list) or not raw:
                return
            assert self._conn is not None
            migrated = 0
            cur = self._conn.cursor()
            try:
                for item in raw:
                    val = str(item).strip()
                    if not val:
                        continue
                    comp, title = "", ""
                    if val.startswith("sig:") and "|" in val:
                        parts = val[4:].split("|", 1)
                        comp = canonical_company(parts[0])
                        title = canonical_title(parts[1])
                    cur.execute(
                        """
                        INSERT INTO jobs (job_id, source, canonical_company, canonical_title)
                        VALUES (%s, 'migrated_json', %s, %s)
                        ON CONFLICT (job_id) DO NOTHING
                        """,
                        (val, comp, title),
                    )
                    self._seen_ids.add(val)
                    if comp and title:
                        self._company_titles.setdefault(comp, []).append(title)
                    migrated += 1
            finally:
                cur.close()
            logger.info("Migrados %d registros de %s para o Neon PostgreSQL.", migrated, self.json_path)
        except Exception as exc:
            logger.warning("Não foi possível migrar %s para o banco: %s", self.json_path, exc)

    def _load_from_json(self) -> None:
        if not self.json_path.is_file():
            return
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return
            for item in raw:
                val = str(item).strip()
                if not val or val in self._seen_ids:
                    continue
                self._json_list.append(val)
                self._seen_ids.add(val)
                if val.startswith("sig:") and "|" in val:
                    parts = val[4:].split("|", 1)
                    comp = canonical_company(parts[0])
                    title = canonical_title(parts[1])
                    if comp and title:
                        self._company_titles.setdefault(comp, []).append(title)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Erro ao ler %s (%s). Iniciando vazio.", self.json_path, exc)

    def _save_to_json(self) -> None:
        trimmed = self._json_list[-self.max_json_items :]
        self.json_path.write_text(
            json.dumps(trimmed, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("Estado persistido em %s (%d entradas).", self.json_path, len(trimmed))

    def is_duplicate(self, job: dict[str, Any]) -> tuple[bool, str]:
        """Verifica duplicidade em 4 camadas: job_id -> URL -> description_hash -> empresa+título."""
        job_id = str(job.get("job_id") or "").strip()
        if job_id and job_id in self._seen_ids:
            return True, "job_id"

        norm_url = normalize_apply_url(str(job.get("job_apply_link") or ""))
        if norm_url and norm_url in self._seen_urls:
            return True, "url"

        desc_hash = description_fingerprint(str(job.get("job_description") or ""))
        if desc_hash and desc_hash in self._seen_desc_hashes:
            return True, "description_hash"

        comp = canonical_company(str(job.get("employer_name") or ""))
        title = canonical_title(str(job.get("job_title") or ""))
        if comp and title:
            for existing_title in self._company_titles.get(comp, ()):
                if titles_are_similar(title, existing_title):
                    return True, "company_title"

        return False, ""

    def record_job(
        self,
        job: dict[str, Any],
        evaluation: dict[str, Any],
        model_used: str = "",
        notified: bool = False,
    ) -> None:
        """Persiste a vaga avaliada no banco (ou JSON local) e atualiza os índices em memória."""
        job_id = str(job.get("job_id") or "").strip()
        comp = canonical_company(str(job.get("employer_name") or ""))
        title = canonical_title(str(job.get("job_title") or ""))
        apply_url = str(job.get("job_apply_link") or "").strip()
        norm_url = normalize_apply_url(apply_url)
        desc_hash = description_fingerprint(str(job.get("job_description") or ""))

        if job_id:
            self._seen_ids.add(job_id)
        if norm_url:
            self._seen_urls.add(norm_url)
        if desc_hash:
            self._seen_desc_hashes.add(desc_hash)
        if comp and title:
            self._company_titles.setdefault(comp, []).append(title)

        if self._conn is not None:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO jobs (
                        job_id, source, employer_name, canonical_company,
                        job_title, canonical_title, apply_url, normalized_url,
                        description_hash, match, score, categoria,
                        regime_detectado, clima_e_cultura, motivo, model_used, notified
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id) DO NOTHING
                    """,
                    (
                        job_id,
                        str(job.get("source") or ""),
                        str(job.get("employer_name") or ""),
                        comp,
                        str(job.get("job_title") or ""),
                        title,
                        apply_url,
                        norm_url,
                        desc_hash,
                        bool(evaluation.get("match", False)),
                        int(evaluation.get("score", 0)),
                        str(evaluation.get("categoria") or ""),
                        str(evaluation.get("regime_detectado") or ""),
                        str(evaluation.get("clima_e_cultura") or ""),
                        str(evaluation.get("motivo") or ""),
                        model_used,
                        bool(notified),
                    ),
                )
            finally:
                cur.close()
        else:
            if job_id and job_id not in self._json_list:
                self._json_list.append(job_id)
            if comp and title:
                sig = f"sig:{comp}|{title}"
                if sig not in self._json_list:
                    self._json_list.append(sig)
