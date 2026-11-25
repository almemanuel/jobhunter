"""Orquestrador do Job Hunter Bot: coleta, deduplicação, triagem LLM e alertas Telegram."""

from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests
from google import genai
from google.genai import types

import auto_apply
import config
import cv_generator
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("job-hunter-bot")


# =============================================================================
# Persistência de Estado (Deduplicação)
# =============================================================================


def load_seen_jobs(path: str | Path = config.SEEN_JOBS_PATH) -> tuple[list[str], set[str]]:
    """Carrega IDs de vagas já processadas preservando a ordem cronológica."""
    file_path = Path(path)
    if not file_path.exists():
        return [], set()

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            logger.warning("seen_jobs.json não contém uma lista válida. Reiniciando estado.")
            return [], set()
        seen_list: list[str] = []
        seen_set: set[str] = set()
        for item in raw:
            job_id = str(item).strip()
            if job_id and job_id not in seen_set:
                seen_list.append(job_id)
                seen_set.add(job_id)
        return seen_list, seen_set
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Erro ao ler %s (%s). Iniciando com lista vazia.", path, exc)
        return [], set()


def save_seen_jobs(
    seen_list: list[str],
    path: str | Path = config.SEEN_JOBS_PATH,
    max_items: int = config.MAX_SEEN_JOBS,
) -> None:
    """Persiste os últimos `max_items` IDs no arquivo JSON."""
    trimmed = seen_list[-max_items:]
    file_path = Path(path)
    file_path.write_text(
        json.dumps(trimmed, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Estado persistido em %s (%d IDs).", file_path, len(trimmed))


# =============================================================================
# Normalização e Verificação de Whitelist
# =============================================================================


def _normalize_text(text: str) -> str:
    """Remove acentos e normaliza para lowercase."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def _matches_company_list(employer_name: str, companies: list[str]) -> str | None:
    """Retorna o nome da empresa da whitelist se houver match seguro por palavra/substring."""
    norm_employer = _normalize_text(employer_name)
    if not norm_employer:
        return None

    for company in companies:
        norm_company = _normalize_text(company)
        if not norm_company:
            continue
        # Usa fronteira de palavra para evitar falsos positivos em nomes curtos (ex: "99", "ONU", "Noh")
        pattern = rf"(?<![a-z0-9]){re.escape(norm_company)}(?![a-z0-9])"
        if re.search(pattern, norm_employer):
            return company
    return None


def check_whitelist(employer_name: str) -> dict[str, Any]:
    """Verifica se a empresa está em TARGET_COMPANIES_BR_REMOTE ou TARGET_COMPANIES_PG."""
    br_match = _matches_company_list(employer_name, config.TARGET_COMPANIES_BR_REMOTE)
    pg_match = _matches_company_list(employer_name, config.TARGET_COMPANIES_PG)
    return {
        "in_whitelist": bool(br_match or pg_match),
        "in_br_remote": bool(br_match),
        "in_pg": bool(pg_match),
        "matched_name": br_match or pg_match,
    }


# =============================================================================
# Coleta de Vagas (JSearch + Gupy)
# =============================================================================


def _is_blacklisted_domain(url: str) -> bool:
    """Verifica se a URL pertence a um domínio na blacklist (ex: talent.com / agregadores pagos)."""
    if not url:
        return True
    low = url.lower()
    host = (urlparse(low).netloc or low).lower()
    return any(bad in host or bad in low for bad in config.BLACKLISTED_APPLY_DOMAINS)


def _is_preferred_ats(url: str) -> bool:
    """Verifica se a URL é de um ATS oficial ou LinkedIn."""
    host = (urlparse(url.lower()).netloc or "").lower()
    return any(ats in host for ats in config.PREFERRED_ATS_DOMAINS)


def _matches_employer_domain(url: str, employer_name: str) -> bool:
    """Verifica se o domínio da URL contém o nome normalizado da empresa."""
    host = (urlparse(url.lower()).netloc or "").lower()
    tokens = [t for t in _normalize_text(employer_name).split() if len(t) >= 3]
    return any(tok in host for tok in tokens)


def _build_linkedin_search_url(employer_name: str, job_title: str) -> str:
    """Gera URL direta de busca no LinkedIn Jobs (universal, nunca dá 404 e escala para qualquer empresa)."""
    keywords = quote_plus(f"{job_title} {employer_name}".strip())
    return f"https://www.linkedin.com/jobs/search/?keywords={keywords}&location=Brazil"


def _is_url_reachable(url: str) -> bool:
    """Validação leve (HEAD/GET) para evitar enviar links quebrados (404/410) ao usuário."""
    if not url or not url.startswith("http"):
        return False
    # LinkedIn bloqueia HEAD sem cookies com 999, mas URLs /jobs/ são válidas
    if "linkedin.com" in url.lower():
        return True
    try:
        resp = requests.head(
            url,
            allow_redirects=True,
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        return resp.status_code < 400 or resp.status_code in (403, 405, 429)
    except Exception:
        return False


def select_best_apply_link(raw: dict[str, Any]) -> tuple[str, str]:
    """Seleciona link oficial de candidatura (site da empresa > ATS oficial/LinkedIn).

    Links de sites não oficiais / agregadores NUNCA são usados como link principal:
    quando a JSearch só retorna agregadores, usa busca direta no LinkedIn Jobs
    (universal e sem links hardcoded que quebram), deixando o agregador só como fallback.
    """
    employer_name = str(raw.get("employer_name") or "").strip()
    job_title = str(raw.get("job_title") or "").strip()

    candidates: list[tuple[str, bool]] = []
    seen_urls: set[str] = set()

    def _add(url: Any, is_direct: bool = False) -> None:
        cleaned = str(url or "").strip()
        if cleaned and cleaned not in seen_urls:
            seen_urls.add(cleaned)
            candidates.append((cleaned, bool(is_direct)))

    # 1. Lista completa de opções retornada pela JSearch (apply_options)
    for opt in raw.get("apply_options") or []:
        if isinstance(opt, dict):
            _add(opt.get("apply_link"), bool(opt.get("is_direct", False)))

    # 2. Campos padrão
    _add(raw.get("job_apply_link"), bool(raw.get("job_apply_is_direct", False)))
    _add(raw.get("job_google_link"), False)

    # Filtra estritamente links oficiais (site da empresa, is_direct não-blacklisted ou ATS/LinkedIn)
    official_candidates: list[tuple[int, str]] = []
    fallback_candidates: list[str] = []

    for url, is_direct in candidates:
        if _is_blacklisted_domain(url):
            fallback_candidates.append(url)
            continue
        if _matches_employer_domain(url, employer_name):
            official_candidates.append((0, url))
        elif is_direct:
            official_candidates.append((1, url))
        elif _is_preferred_ats(url):
            official_candidates.append((2, url))
        else:
            fallback_candidates.append(url)

    if official_candidates:
        official_candidates.sort(key=lambda item: item[0])
        return official_candidates[0][1], ""

    # Nenhum link oficial direto na JSearch -> busca direta no LinkedIn Jobs (universal, zero 404)
    linkedin_url = _build_linkedin_search_url(employer_name, job_title)
    fallback_url = fallback_candidates[0] if fallback_candidates else ""
    return linkedin_url, fallback_url


def resolve_official_job_url(job: dict[str, Any]) -> tuple[str, str]:
    """Para vagas aprovadas sem link oficial direto, tenta encontrar e validar a URL exata em ATS/LinkedIn."""
    primary = str(job.get("job_apply_link") or "")
    fallback = str(job.get("job_fallback_link") or "")
    if not fallback:
        return primary, ""

    employer = str(job.get("employer_name") or "").strip()
    title = str(job.get("job_title") or "").strip()
    query = (
        f'"{employer}" "{title}" '
        "(linkedin.com/jobs/view OR gupy.io OR inhire.app OR greenhouse.io OR lever.co OR ashbyhq.com)"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    }

    search_sources = [
        ("ddg", "https://html.duckduckgo.com/html/", "post", {"q": query}),
        ("bing", "https://www.bing.com/search", "get", {"q": query}),
    ]
    for source_name, endpoint, method, params in search_sources:
        try:
            resp = (
                requests.post(endpoint, data=params, headers=headers, timeout=12)
                if method == "post"
                else requests.get(endpoint, params=params, headers=headers, timeout=12)
            )
            if resp.status_code != 200:
                continue
            pattern = (
                r'class="result__a"[^>]*href="([^"]+)"'
                if source_name == "ddg"
                else r'<h2[^>]*><a[^>]*href="(https?://[^"]+)"'
            )
            hrefs = re.findall(pattern, resp.text)
            for href in hrefs[:8]:
                url = html.unescape(href)
                if "uddg=" in url:
                    parsed_qs = parse_qs(urlparse(url).query)
                    url = unquote(parsed_qs.get("uddg", [url])[0])
                if not url.startswith("http") or _is_blacklisted_domain(url):
                    continue
                if (_is_preferred_ats(url) or _matches_employer_domain(url, employer)) and _is_url_reachable(url):
                    logger.info("Link oficial resolvido (%s) para %s — %s: %s", source_name, title, employer, url)
                    return url, ""
        except Exception as exc:
            logger.debug("Falha ao resolver URL oficial (%s) para %s: %s", source_name, employer, exc)

    return primary, fallback


def normalize_jsearch_job(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza um registro retornado pela JSearch API para o contrato interno."""
    job_id = str(raw.get("job_id") or "").strip()
    if not job_id:
        return None

    apply_link, fallback_link = select_best_apply_link(raw)

    return {
        "job_id": job_id,
        "job_title": str(raw.get("job_title") or "Sem título").strip(),
        "employer_name": str(raw.get("employer_name") or "Empresa não informada").strip(),
        "job_city": str(raw.get("job_city") or "").strip(),
        "job_state": str(raw.get("job_state") or "").strip(),
        "job_country": str(raw.get("job_country") or "").strip(),
        "job_is_remote": bool(raw.get("job_is_remote", False)),
        "job_description": str(raw.get("job_description") or "").strip(),
        "job_apply_link": apply_link,
        "job_fallback_link": fallback_link,
        "source": "jsearch",
    }


def fetch_jsearch_jobs(rapidapi_key: str) -> list[dict[str, Any]]:
    """Executa os planos de busca configurados na JSearch API (RapidAPI)."""
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": config.JSEARCH_API_HOST,
    }
    collected: list[dict[str, Any]] = []

    for plan in config.JSEARCH_PLANS:
        query_text = f"{plan['query']} in {plan['location']}"
        params = {
            "query": query_text,
            "page": "1",
            "num_pages": "1",
            "date_posted": plan["date_posted"],
            "country": "br",
        }
        try:
            resp = requests.get(
                config.JSEARCH_API_URL,
                headers=headers,
                params=params,
                timeout=config.HTTP_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            payload = resp.json()
            raw_data = payload.get("data") or []
            items = raw_data.get("jobs", []) if isinstance(raw_data, dict) else raw_data
            logger.info(
                "JSearch [%s]: %d vagas retornadas.",
                plan["location"],
                len(items),
            )
            for item in items:
                normalized = normalize_jsearch_job(item)
                if normalized:
                    collected.append(normalized)
        except Exception as exc:
            logger.error(
                "Falha ao consultar JSearch (%s | %s): %s",
                plan["location"],
                plan["query"][:40],
                exc,
            )

    return collected


def _is_within_last_24h(iso_date: str | None) -> bool:
    """Verifica se uma data ISO-8601 está dentro das últimas 24 horas."""
    if not iso_date:
        return False
    try:
        cleaned = iso_date.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        return dt >= cutoff
    except ValueError:
        return False


def normalize_gupy_job(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza um registro da API pública da Gupy para o contrato interno."""
    raw_id = raw.get("id")
    if raw_id is None:
        return None

    workplace_type = str(raw.get("workplaceType") or "").lower()
    is_remote = bool(raw.get("isRemoteWork")) or workplace_type == "remote"

    return {
        "job_id": f"gupy:{raw_id}",
        "job_title": str(raw.get("name") or "Sem título").strip(),
        "employer_name": str(raw.get("careerPageName") or "Empresa não informada").strip(),
        "job_city": str(raw.get("city") or "").strip(),
        "job_state": str(raw.get("state") or "").strip(),
        "job_country": str(raw.get("country") or "Brasil").strip(),
        "job_is_remote": is_remote,
        "job_description": str(raw.get("description") or "").strip(),
        "job_apply_link": str(raw.get("jobUrl") or "").strip(),
        "source": "gupy",
    }


def fetch_gupy_jobs() -> list[dict[str, Any]]:
    """Consulta a API pública da Gupy filtrando últimas 24h e empresas da whitelist."""
    if not config.ENABLE_GUPY:
        return []

    collected: list[dict[str, Any]] = []
    headers = {"Accept": "application/json", "User-Agent": "job-hunter-bot/1.0"}

    for term in config.GUPY_SEARCH_TERMS:
        params = {"jobName": term, "limit": 50, "offset": 0}
        try:
            resp = requests.get(
                config.GUPY_API_URL,
                headers=headers,
                params=params,
                timeout=config.HTTP_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            items = resp.json().get("data") or []
            matched_count = 0
            for item in items:
                if not _is_within_last_24h(item.get("publishedDate")):
                    continue
                company_name = str(item.get("careerPageName") or "")
                if not check_whitelist(company_name)["in_whitelist"]:
                    continue
                normalized = normalize_gupy_job(item)
                if normalized:
                    collected.append(normalized)
                    matched_count += 1
            logger.info("Gupy [%s]: %d vagas recentes da whitelist.", term, matched_count)
        except Exception as exc:
            logger.warning("Falha ao consultar Gupy (%s): %s", term, exc)

    return collected


def _strip_html(raw_html: str) -> str:
    """Remove tags HTML de descrições retornadas por APIs de ATS."""
    text = html.unescape(raw_html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ats_job_matches_filters(title: str, location: str) -> bool:
    """Pré-filtro local rápido para vagas de ATS oficiais (título + Brasil/LATAM/Remoto)."""
    norm_title = _normalize_text(title)
    if not any(kw in norm_title for kw in config.OFFICIAL_ATS_TITLE_KEYWORDS):
        return False

    norm_loc = _normalize_text(location)
    if not norm_loc:
        return True
    has_br_or_latam = any(k in norm_loc for k in ("brazil", "brasil", "latam", "latin america", "south america", "ponta grossa"))
    if has_br_or_latam:
        return True
    if any(excl in norm_loc for excl in config.OFFICIAL_ATS_EXCLUDED_LOCATIONS):
        return False
    return any(kw in norm_loc for kw in config.OFFICIAL_ATS_LOCATION_KEYWORDS)


def fetch_official_ats_jobs() -> list[dict[str, Any]]:
    """Coleta vagas diretamente das APIs oficiais de ATS (Greenhouse, Lever e Ashby)."""
    if not config.ENABLE_OFFICIAL_ATS:
        return []

    collected: list[dict[str, Any]] = []

    # 1. Greenhouse
    for slug, company_name in config.OFFICIAL_GREENHOUSE_BOARDS.items():
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        try:
            resp = requests.get(url, params={"content": "true"}, timeout=15)
            if resp.status_code != 200:
                continue
            jobs_list = resp.json().get("jobs") or []
            matched = 0
            for item in jobs_list:
                title = str(item.get("title") or "").strip()
                loc = str((item.get("location") or {}).get("name") or "").strip()
                if not _ats_job_matches_filters(title, loc):
                    continue
                is_remote = any(w in loc.lower() for w in ("remote", "remoto", "anywhere"))
                collected.append(
                    {
                        "job_id": f"ats:gh:{slug}:{item.get('id')}",
                        "job_title": title,
                        "employer_name": company_name,
                        "job_city": loc,
                        "job_state": "",
                        "job_country": "BR" if any(w in loc.lower() for w in ("brazil", "brasil")) else "",
                        "job_is_remote": is_remote,
                        "job_description": _strip_html(str(item.get("content") or "")),
                        "job_apply_link": str(item.get("absolute_url") or "").strip(),
                        "job_fallback_link": "",
                        "source": f"greenhouse:{slug}",
                    }
                )
                matched += 1
            if matched:
                logger.info("ATS Oficial [Greenhouse/%s]: %d vagas aderentes.", company_name, matched)
        except Exception as exc:
            logger.debug("Falha ao consultar Greenhouse/%s: %s", slug, exc)

    # 2. Lever
    for slug, company_name in config.OFFICIAL_LEVER_BOARDS.items():
        url = f"https://api.lever.co/v0/postings/{slug}"
        try:
            resp = requests.get(url, params={"mode": "json"}, timeout=15)
            if resp.status_code != 200:
                continue
            jobs_list = resp.json() if isinstance(resp.json(), list) else []
            matched = 0
            for item in jobs_list:
                title = str(item.get("text") or "").strip()
                cats = item.get("categories") or {}
                loc = str(cats.get("location") or "").strip()
                workplace = str(item.get("workplaceType") or "").lower()
                if not _ats_job_matches_filters(title, loc):
                    continue
                is_remote = workplace == "remote" or any(w in loc.lower() for w in ("remote", "remoto"))
                collected.append(
                    {
                        "job_id": f"ats:lever:{slug}:{item.get('id')}",
                        "job_title": title,
                        "employer_name": company_name,
                        "job_city": loc,
                        "job_state": "",
                        "job_country": "BR" if any(w in loc.lower() for w in ("brazil", "brasil")) else "",
                        "job_is_remote": is_remote,
                        "job_description": str(item.get("descriptionPlain") or _strip_html(str(item.get("description") or ""))),
                        "job_apply_link": str(item.get("hostedUrl") or item.get("applyUrl") or "").strip(),
                        "job_fallback_link": "",
                        "source": f"lever:{slug}",
                    }
                )
                matched += 1
            if matched:
                logger.info("ATS Oficial [Lever/%s]: %d vagas aderentes.", company_name, matched)
        except Exception as exc:
            logger.debug("Falha ao consultar Lever/%s: %s", slug, exc)

    # 3. Ashby
    for slug, company_name in config.OFFICIAL_ASHBY_BOARDS.items():
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                continue
            jobs_list = resp.json().get("jobs") or []
            matched = 0
            for item in jobs_list:
                title = str(item.get("title") or "").strip()
                loc = str(item.get("location") or "").strip()
                if not _ats_job_matches_filters(title, loc):
                    continue
                is_remote = bool(item.get("isRemote")) or any(w in loc.lower() for w in ("remote", "remoto"))
                collected.append(
                    {
                        "job_id": f"ats:ashby:{slug}:{item.get('id')}",
                        "job_title": title,
                        "employer_name": company_name,
                        "job_city": loc,
                        "job_state": "",
                        "job_country": "BR" if any(w in loc.lower() for w in ("brazil", "brasil")) else "",
                        "job_is_remote": is_remote,
                        "job_description": str(item.get("descriptionPlain") or _strip_html(str(item.get("descriptionHtml") or ""))),
                        "job_apply_link": str(item.get("jobUrl") or item.get("applyUrl") or "").strip(),
                        "job_fallback_link": "",
                        "source": f"ashby:{slug}",
                    }
                )
                matched += 1
            if matched:
                logger.info("ATS Oficial [Ashby/%s]: %d vagas aderentes.", company_name, matched)
        except Exception as exc:
            logger.debug("Falha ao consultar Ashby/%s: %s", slug, exc)

    return collected


def _job_signature(job: dict[str, Any]) -> str:
    """Assinatura estável (empresa + título) para evitar reenvio quando a JSearch rotaciona o job_id."""
    emp = _normalize_text(job.get("employer_name", ""))
    title = _normalize_text(job.get("job_title", ""))
    return f"sig:{emp}|{title}" if emp and title else ""


def deduplicate_batch(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicatas dentro da mesma execução por job_id, URL, hash da descrição e empresa+título."""
    unique: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    company_titles: dict[str, list[str]] = {}

    for job in jobs:
        job_id = str(job.get("job_id") or "").strip()
        if job_id in seen_ids:
            continue

        norm_url = storage.normalize_apply_url(str(job.get("job_apply_link") or ""))
        if norm_url and norm_url in seen_urls:
            continue

        desc_hash = storage.description_fingerprint(str(job.get("job_description") or ""))
        if desc_hash and desc_hash in seen_hashes:
            continue

        comp = storage.canonical_company(str(job.get("employer_name") or ""))
        title = storage.canonical_title(str(job.get("job_title") or ""))
        if comp and title and any(
            storage.titles_are_similar(title, prev) for prev in company_titles.get(comp, ())
        ):
            continue

        seen_ids.add(job_id)
        if norm_url:
            seen_urls.add(norm_url)
        if desc_hash:
            seen_hashes.add(desc_hash)
        if comp and title:
            company_titles.setdefault(comp, []).append(title)
        unique.append(job)

    return unique


# =============================================================================
# Motor de Triagem e Investigação LLM (Gemini 2.5 Flash)
# =============================================================================


class DailyQuotaExceededError(RuntimeError):
    """Indica esgotamento da cota diária de um modelo Gemini."""


class ModelUnavailableError(RuntimeError):
    """Indica modelo inexistente/descontinuado (404) ou incompatível."""


def _is_daily_quota_error(exc: Exception) -> bool:
    """Detecta erro de cota diária esgotada no Gemini Free Tier."""
    msg = str(exc).lower()
    return "per day" in msg or "generaterequestsperday" in msg


def _is_model_unavailable_error(exc: Exception) -> bool:
    """Detecta modelo inexistente, descontinuado ou sem suporte a ferramenta."""
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in (
            "404",
            "not_found",
            "not found",
            "no longer available",
            "is not supported",
        )
    )


def _is_retryable_error(exc: Exception) -> bool:
    """Identifica erros transitórios (429 por minuto, 503 indisponível)."""
    if _is_daily_quota_error(exc) or _is_model_unavailable_error(exc):
        return False
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "429",
            "resource_exhausted",
            "too many requests",
            "quota",
            "503",
            "unavailable",
            "deadline exceeded",
        )
    )


def _discover_flash_models(api_key: str) -> list[str]:
    """Lista e ranqueia modelos Gemini Flash ativos via API (inspirado no cashbot)."""
    try:
        resp = requests.get(f"{config.GEMINI_MODELS_URL}?key={api_key}", timeout=8)
        if resp.status_code != 200:
            return []
        candidates: list[str] = []
        for meta in resp.json().get("models", []):
            name = str(meta.get("name", "")).replace("models/", "")
            low = name.lower()
            methods = meta.get("supportedGenerationMethods", [])
            if any(bad in low for bad in ("preview", "exp", "tts", "image", "audio")):
                continue
            if "flash" in low and "generateContent" in methods:
                candidates.append(name)

        def _score(model_name: str) -> float:
            m = re.search(r"gemini-(\d+(?:\.\d+)?)", model_name.lower())
            ver = float(m.group(1)) if m else 2.0
            # Prioriza modelos lite para maximizar cota gratuita diária
            lite_bonus = 0.05 if "lite" in model_name.lower() else 0.0
            return ver + lite_bonus

        return sorted(dict.fromkeys(candidates), key=_score, reverse=True)
    except Exception as exc:
        logger.debug("Não foi possível listar modelos Gemini automaticamente: %s", exc)
        return []


def get_candidate_models(api_key: str | None = None) -> list[str]:
    """Monta fila de prioridade de modelos Gemini (env -> defaults -> descoberta automática)."""
    candidates: list[str] = []

    env_model = os.getenv("GEMINI_MODEL", "").strip()
    if env_model:
        candidates.append(env_model)

    env_fallbacks = [
        m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()
    ]
    candidates.extend(env_fallbacks)
    candidates.extend(config.GEMINI_DEFAULT_MODELS)

    if api_key:
        candidates.extend(_discover_flash_models(api_key))

    unique = list(dict.fromkeys(candidates))
    return unique or [config.GEMINI_MODEL]


def _call_with_backoff(fn, *args, **kwargs):
    """Executa chamada à API Gemini com backoff exponencial para 429/503 transitórios."""
    last_exc: Exception | None = None
    for attempt in range(config.GEMINI_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if _is_daily_quota_error(exc):
                raise DailyQuotaExceededError(str(exc)) from exc
            if _is_model_unavailable_error(exc):
                raise ModelUnavailableError(str(exc)) from exc
            last_exc = exc
            if not _is_retryable_error(exc) or attempt == config.GEMINI_MAX_RETRIES - 1:
                if "429" in str(exc) or "resource_exhausted" in str(exc).lower():
                    raise DailyQuotaExceededError(str(exc)) from exc
                raise
            delay = config.GEMINI_BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0.2, 1.0)
            logger.warning(
                "Rate limit/erro transitório no Gemini (tentativa %d/%d). Aguardando %.1fs...",
                attempt + 1,
                config.GEMINI_MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
    raise last_exc  # pragma: no cover


def search_company_reputation_web(
    employer_name: str,
    cache: dict[str, str] | None = None,
) -> str:
    """Busca pública gratuita (DuckDuckGo HTML) sobre reputação/clima da empresa (fallback Free Tier)."""
    norm_key = _normalize_text(employer_name)
    if cache is not None and norm_key in cache:
        return cache[norm_key]

    query = f'"{employer_name}" (glassdoor OR reddit OR "work culture" OR layoffs OR reviews OR flexibilidade)'
    snippets: list[str] = []
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            raw_html = resp.text
            titles = re.findall(
                r'class="result__a"[^>]*>(.*?)</a>',
                raw_html,
                flags=re.DOTALL,
            )
            bodies = re.findall(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>',
                raw_html,
                flags=re.DOTALL,
            )
            for idx, (raw_title, raw_body) in enumerate(zip(titles[:5], bodies[:5]), start=1):
                clean_title = re.sub(r"<[^>]+>", "", html.unescape(raw_title)).strip()
                clean_body = re.sub(r"<[^>]+>", "", html.unescape(raw_body)).strip()
                if clean_title or clean_body:
                    snippets.append(f"{idx}. {clean_title}: {clean_body}")
    except Exception as exc:
        logger.warning("Falha na busca web gratuita para '%s': %s", employer_name, exc)

    result_text = "\n".join(snippets)
    if cache is not None:
        cache[norm_key] = result_text
    return result_text


def _build_stage_b_web_snippets_prompt(
    job: dict[str, Any],
    whitelist_info: dict[str, Any],
    web_snippets: str,
) -> str:
    """Prompt do Estágio B combinando busca web gratuita + conhecimento consolidado do LLM."""
    evidence_block = (
        web_snippets
        if web_snippets.strip()
        else "Nenhum snippet adicional retornado pela busca web nesta tentativa."
    )
    return (
        "Você é um recrutador técnico sênior e investigador de reputação corporativa.\n"
        "Esta empresa NÃO está na whitelist do candidato. Use as evidências da busca web abaixo "
        "combinadas com o seu conhecimento consolidado sobre o mercado de tecnologia antes de decidir.\n\n"
        f"### Evidências Públicas Encontradas na Web (Glassdoor / Reddit / Reviews)\n{evidence_block}\n\n"
        "### Trava de Reputação (REGRA CRÍTICA)\n"
        "- Se a empresa for **amplamente conhecida e consolidada no mercado** (ex: grandes empresas de "
        "tecnologia, bancos, varejistas ou scale-ups reconhecidas), você PODE opinar sobre clima, "
        "cultura, flexibilidade e estabilidade com base no seu conhecimento prévio + descrição da vaga, "
        "mesmo que a busca web tenha vindo vazia.\n"
        "- Se a empresa for **desconhecida, obscura, recrutadora genérica ou sem histórico público "
        "confiável** (nem na busca web nem no seu conhecimento), defina OBRIGATORIAMENTE `match: false`.\n"
        "- Se houver relatos conhecidos ou sinais de toxicidade, microgerenciamento, atrasos ou "
        "pressão extrema, defina OBRIGATORIAMENTE `match: false`.\n"
        "- Para vagas no Brasil (Cenário A), se não for 100% remota CLT ou se houver qualquer dúvida sobre "
        "ser PJ/híbrido fora de Ponta Grossa, defina `match: false`.\n\n"
        f"{config.CANDIDATE_PROFILE}\n\n"
        f"{config.BUSINESS_RULES}\n\n"
        f"{_format_job_context(job, whitelist_info)}\n\n"
        "Retorne exclusivamente um objeto JSON aderente ao schema especificado, com `na_whitelist: false`. "
        "IMPORTANTE: seja ultra-direto — `clima_e_cultura`, `motivo` e `destaque_cv` devem ter no máximo 1 frase curta (até ~120 caracteres cada)."
    )


def _format_job_context(job: dict[str, Any], whitelist_info: dict[str, Any]) -> str:
    """Formata os metadados e a descrição da vaga para o prompt do LLM."""
    description = job["job_description"][:8000]
    location_parts = [p for p in (job["job_city"], job["job_state"], job["job_country"]) if p]
    location_str = ", ".join(location_parts) or "Não informada"

    return (
        f"### Dados da Vaga\n"
        f"- **Título:** {job['job_title']}\n"
        f"- **Empresa:** {job['employer_name']}\n"
        f"- **Na Whitelist:** {'Sim' if whitelist_info['in_whitelist'] else 'Não'} "
        f"(BR Remoto: {whitelist_info['in_br_remote']}, Ponta Grossa: {whitelist_info['in_pg']})\n"
        f"- **Localização:** {location_str}\n"
        f"- **Remoto:** {'Sim' if job['job_is_remote'] else 'Não / Não informado'}\n"
        f"- **Link:** {job['job_apply_link']}\n\n"
        f"### Descrição da Vaga\n{description}\n"
    )


def _build_stage_a_prompt(job: dict[str, Any], whitelist_info: dict[str, Any]) -> str:
    """Prompt para Estágio A (empresa já aprovada na whitelist)."""
    return (
        "Você é um recrutador técnico sênior e conselheiro de carreira especializado em "
        "AI Engineering, LLM Systems, Data Engineering e Analytics Engineering.\n"
        "Avalie a vaga abaixo para o candidato seguindo ESTRITAMENTE todas as regras de negócio.\n"
        "A empresa JÁ ESTÁ NA WHITELIST pré-aprovada de reputação; portanto, foque em avaliar "
        "aderência técnica, regime contratual, localização, faixa salarial estimada, idioma "
        "e sinais culturais presentes na própria descrição da vaga.\n\n"
        f"{config.CANDIDATE_PROFILE}\n\n"
        f"{config.BUSINESS_RULES}\n\n"
        f"{_format_job_context(job, whitelist_info)}\n\n"
        "Retorne exclusivamente um objeto JSON aderente ao schema especificado, com `na_whitelist: true`. "
        "IMPORTANTE: seja ultra-direto — `clima_e_cultura`, `motivo` e `destaque_cv` devem ter no máximo 1 frase curta (até ~120 caracteres cada)."
    )


def _build_stage_b_prompt(job: dict[str, Any], whitelist_info: dict[str, Any]) -> str:
    """Prompt para Estágio B (empresa fora da whitelist — investigação obrigatória via Google Search)."""
    employer = job["employer_name"]
    search_query = (
        f'"{employer}" (glassdoor OR reddit OR "work culture" OR layoffs OR reviews OR flexibilidade)'
    )
    return (
        "Você é um recrutador técnico sênior e investigador de reputação corporativa.\n"
        "Esta empresa NÃO está na whitelist do candidato. Você DEVE obrigatoriamente usar a "
        "ferramenta Google Search antes de decidir.\n\n"
        f"### Investigação Obrigatória (Google Search)\n"
        f"Pesquise sobre a empresa usando a consulta sugerida:\n`{search_query}`\n"
        "Investigue Glassdoor, Reddit, notícias recentes, relatos de clima organizacional, "
        "flexibilidade de horários, controle de ponto, benefícios, estabilidade e histórico de layoffs/toxicidade.\n\n"
        "### Trava de Reputação (REGRA CRÍTICA — SEM EXCEÇÕES)\n"
        "- Se a busca NÃO retornar informações públicas concretas e suficientes sobre o clima "
        "organizacional/reputação da empresa, defina OBRIGATORIAMENTE `\"match\": false`.\n"
        "- Se encontrar relatos recorrentes de toxicidade, microgerenciamento, atrasos ou pressão extrema, "
        "defina OBRIGATORIAMENTE `\"match\": false`.\n"
        "- NUNCA aprove empresa desconhecida no escuro.\n"
        "- Para vagas no Brasil (Cenário A), se não for 100% remota CLT ou se houver qualquer dúvida sobre "
        "ser PJ/híbrido fora de Ponta Grossa, defina `\"match\": false`.\n\n"
        f"{config.CANDIDATE_PROFILE}\n\n"
        f"{config.BUSINESS_RULES}\n\n"
        f"{_format_job_context(job, whitelist_info)}\n\n"
        "Ao final da sua investigação, retorne EXCLUSIVAMENTE um bloco JSON válido com exatamente estas chaves:\n"
        "```json\n"
        "{\n"
        '  "match": true/false,\n'
        '  "score": 0 a 10,\n'
        '  "na_whitelist": false,\n'
        '  "categoria": "BR Remoto CLT" | "PG Presencial/Híbrido CLT" | "Gringa Remoto" | "Liderança Técnica" | "Descartada",\n'
        '  "regime_detectado": "CLT" | "PJ" | "Internacional/Contractor" | "Não especificado",\n'
        '  "clima_e_cultura": "resumo objetivo das evidências encontradas na busca web e na descrição",\n'
        '  "motivo": "1 frase objetiva explicando o porquê da aprovação ou descarte",\n'
        '  "destaque_cv": "1 recomendação prática de qual experiência/skill destacar na candidatura"\n'
        "}\n"
        "```"
    )


def extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Extrai o primeiro objeto JSON válido de um texto (inclusive dentro de blocos markdown)."""
    if not text:
        return None

    # 1. Tentativa direta
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 2. Bloco ```json ... ``` ou ``` ... ```
    fenced_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    for candidate in fenced_matches:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    # 3. Primeiro par balanceado de chaves { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def _normalize_evaluation(raw: dict[str, Any], na_whitelist: bool) -> dict[str, Any]:
    """Garante tipos corretos, consistência match/score e presença de todas as chaves do contrato JSON."""
    is_match = bool(raw.get("match", False))
    score_raw = raw.get("score", 0)
    try:
        score = max(0, min(10, int(score_raw)))
    except (TypeError, ValueError):
        score = 0

    # Garante consistência: vaga descartada nunca pode exibir score >= 7
    if not is_match and score >= config.MIN_MATCH_SCORE:
        score = config.MIN_MATCH_SCORE - 1

    return {
        "match": is_match,
        "score": score,
        "na_whitelist": bool(raw.get("na_whitelist", na_whitelist)),
        "categoria": str(raw.get("categoria") or "Descartada").strip(),
        "regime_detectado": str(raw.get("regime_detectado") or "Não especificado").strip(),
        "clima_e_cultura": str(raw.get("clima_e_cultura") or "Sem informações suficientes.").strip(),
        "motivo": str(raw.get("motivo") or "Sem justificativa informada.").strip(),
        "destaque_cv": str(raw.get("destaque_cv") or "N/A").strip(),
    }


def evaluate_job(
    client: genai.Client,
    job: dict[str, Any],
    whitelist_info: dict[str, Any],
    model_name: str = config.GEMINI_MODEL,
    grounding_state: dict[str, bool] | None = None,
    web_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Avalia uma vaga com o modelo Gemini informado (Estágio A whitelist ou Estágio B com busca web)."""
    if whitelist_info["in_whitelist"]:
        prompt = _build_stage_a_prompt(job, whitelist_info)
        response = _call_with_backoff(
            client.models.generate_content,
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=config.EVALUATION_RESPONSE_SCHEMA,
                temperature=0.2,
            ),
        )
        parsed = extract_json_from_text(response.text or "")
        if not parsed:
            raise ValueError(f"Resposta JSON inválida no Estágio A: {(response.text or '')[:200]}")
        return _normalize_evaluation(parsed, na_whitelist=True)

    # Estágio B: tenta Google Search Grounding nativo; se bloqueado (Free Tier 429), usa busca web gratuita + JSON
    use_native_grounding = not (grounding_state and grounding_state.get("disabled", False))
    if use_native_grounding:
        prompt = _build_stage_b_prompt(job, whitelist_info)
        try:
            search_response = _call_with_backoff(
                client.models.generate_content,
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.2,
                ),
            )
            raw_text = search_response.text or ""
            parsed = extract_json_from_text(raw_text)

            if not parsed:
                conversion_prompt = (
                    "Converta a análise de triagem abaixo estritamente para o schema JSON solicitado, "
                    "preservando fielmente todas as conclusões (inclusive `match: false` caso não haja "
                    "evidências concretas de reputação/clima).\n\n"
                    f"### Análise Original\n{raw_text}"
                )
                structured_response = _call_with_backoff(
                    client.models.generate_content,
                    model=model_name,
                    contents=conversion_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=config.EVALUATION_RESPONSE_SCHEMA,
                        temperature=0.0,
                    ),
                )
                parsed = extract_json_from_text(structured_response.text or "")

            if not parsed:
                raise ValueError(f"Não foi possível extrair JSON no Estágio B: {raw_text[:200]}")
            return _normalize_evaluation(parsed, na_whitelist=False)
        except DailyQuotaExceededError:
            if grounding_state is not None:
                grounding_state["disabled"] = True
                logger.warning(
                    "Google Search Grounding indisponível no Free Tier (429). "
                    "Ativando fallback de busca web gratuita (DuckDuckGo) para o Estágio B."
                )
            else:
                raise

    # Fallback Estágio B: busca web gratuita (DuckDuckGo) + Structured Output JSON no Gemini
    web_snippets = search_company_reputation_web(job["employer_name"], cache=web_cache)
    prompt = _build_stage_b_web_snippets_prompt(job, whitelist_info, web_snippets)
    response = _call_with_backoff(
        client.models.generate_content,
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=config.EVALUATION_RESPONSE_SCHEMA,
            temperature=0.2,
        ),
    )
    parsed = extract_json_from_text(response.text or "")
    if not parsed:
        raise ValueError(f"Resposta JSON inválida no Estágio B (web fallback): {(response.text or '')[:200]}")
    return _normalize_evaluation(parsed, na_whitelist=False)


def evaluate_job_with_fallback(
    client: genai.Client,
    job: dict[str, Any],
    whitelist_info: dict[str, Any],
    candidate_models: list[str],
    exhausted_models: set[str],
    grounding_state: dict[str, bool] | None = None,
    web_cache: dict[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    """Tenta avaliar a vaga percorrendo a fila de prioridade de modelos Gemini."""
    active_models = [m for m in candidate_models if m not in exhausted_models]
    if not active_models:
        raise DailyQuotaExceededError("Todos os modelos Gemini da fila de prioridade esgotaram a cota/indisponíveis.")

    last_exc: Exception | None = None
    for model_name in active_models:
        try:
            result = evaluate_job(
                client,
                job,
                whitelist_info,
                model_name=model_name,
                grounding_state=grounding_state,
                web_cache=web_cache,
            )
            return result, model_name
        except (DailyQuotaExceededError, ModelUnavailableError) as exc:
            exhausted_models.add(model_name)
            logger.warning(
                "Modelo %s indisponível/cota esgotada (%s). Alternando para o próximo modelo da fila...",
                model_name,
                str(exc)[:140],
            )
            last_exc = exc
            continue

    raise DailyQuotaExceededError(str(last_exc) if last_exc else "Nenhum modelo Gemini disponível.")


# =============================================================================
# Notificação Telegram
# =============================================================================


def escape_markdown(text: str) -> str:
    """Escapa caracteres especiais do Telegram Markdown (v1) para evitar erros de parsing."""
    if not text:
        return ""
    for char in ("_", "*", "`", "["):
        text = text.replace(char, f"\\{char}")
    return text


def format_telegram_message(job: dict[str, Any], evaluation: dict[str, Any]) -> str:
    """Formata a mensagem de alerta de forma curta e direta para leitura rápida no Telegram."""
    job_title = escape_markdown(job["job_title"])
    employer_name = escape_markdown(job["employer_name"])
    categoria = escape_markdown(evaluation["categoria"])
    regime = escape_markdown(evaluation["regime_detectado"])
    score = evaluation["score"]
    clima = escape_markdown(evaluation["clima_e_cultura"])
    motivo = escape_markdown(evaluation["motivo"])
    apply_link = job["job_apply_link"].replace(")", "%29")
    fallback_link = str(job.get("job_fallback_link") or "").replace(")", "%29")

    if fallback_link:
        link_line = (
            f"🔗 [Buscar no site da empresa]({apply_link}) · "
            f"[Fallback]({fallback_link})"
        )
    else:
        link_line = f"🔗 [Aplicar na vaga]({apply_link})"

    return (
        f"🎯 *{job_title}* — {employer_name}\n"
        f"📌 {categoria} · {regime} · *{score}/10*\n"
        f"🏢 {clima}\n"
        f"💡 {motivo}\n"
        f"{link_line}"
    )


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    """Envia mensagem ao Telegram com fallback para texto puro em caso de erro de Markdown."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=config.HTTP_TIMEOUT_SECONDS)
    if resp.status_code == 400 and "parse" in resp.text.lower():
        logger.warning("Erro de parsing Markdown no Telegram; reenviando em texto puro.")
        payload.pop("parse_mode", None)
        resp = requests.post(url, json=payload, timeout=config.HTTP_TIMEOUT_SECONDS)
    resp.raise_for_status()


def send_telegram_document(
    token: str,
    chat_id: str,
    document_bytes: bytes,
    filename: str,
    caption: str = "",
) -> None:
    """Envia um arquivo PDF (currículo adaptado) para o chat do Telegram."""
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    files = {"document": (filename, document_bytes, "application/pdf")}
    data = {"chat_id": chat_id, "caption": caption[:1024]}
    resp = requests.post(url, data=data, files=files, timeout=config.HTTP_TIMEOUT_SECONDS)
    resp.raise_for_status()


def sync_telegram_answers(token: str, chat_id: str, store: storage.JobStorage) -> int:
    """Lê comandos `/set <chave> <valor>` enviados pelo usuário no Telegram e salva no banco."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    saved = 0
    try:
        resp = requests.get(url, params={"limit": 30, "timeout": 2}, timeout=10)
        if resp.status_code != 200:
            return 0
        updates = resp.json().get("result") or []
        max_update_id = 0
        for upd in updates:
            upd_id = int(upd.get("update_id") or 0)
            max_update_id = max(max_update_id, upd_id)
            msg = upd.get("message") or {}
            if str((msg.get("chat") or {}).get("id") or "") != str(chat_id):
                continue
            text = str(msg.get("text") or "").strip()
            match = re.match(r"^/set\s+([a-zA-Z0-9_]+)\s+(.+)$", text)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip()
            store.upsert_candidate_answer(key, value)
            saved += 1
            try:
                send_telegram_message(
                    token,
                    chat_id,
                    f"✅ Dado salvo no banco: `{escape_markdown(key)}` = {escape_markdown(value)}",
                )
            except Exception:
                pass
        # Marca updates como lidos no Telegram para não reprocessar na próxima execução
        if max_update_id > 0:
            requests.get(url, params={"offset": max_update_id + 1, "limit": 1}, timeout=5)
    except Exception as exc:
        logger.debug("Não foi possível sincronizar comandos /set do Telegram: %s", exc)
    return saved


# =============================================================================
# Pipeline Principal
# =============================================================================


def _get_required_env() -> dict[str, str]:
    """Carrega e valida as variáveis de ambiente obrigatórias."""
    required = ("RAPIDAPI_KEY", "GEMINI_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
    env_values = {name: os.getenv(name, "").strip() for name in required}
    missing = [name for name, val in env_values.items() if not val]
    if missing:
        raise EnvironmentError(
            f"Variáveis de ambiente obrigatórias ausentes: {', '.join(missing)}"
        )
    return env_values


def run() -> None:
    """Executa o pipeline completo de coleta, deduplicação (4 camadas), triagem e notificação."""
    env = _get_required_env()
    store = storage.JobStorage(
        database_url=os.getenv("DATABASE_URL", ""),
        json_path=config.SEEN_JOBS_PATH,
        max_json_items=config.MAX_SEEN_JOBS,
    )
    store.connect()

    try:
        # 0. Sincroniza respostas novas enviadas pelo usuário via `/set <chave> <valor>` no Telegram
        synced = sync_telegram_answers(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], store)
        if synced:
            logger.info("Sincronizados %d novos dados do candidato via Telegram /set.", synced)

        # 1. Coleta de vagas (fontes oficiais de ATS primeiro, depois JSearch e Gupy)
        ats_jobs = fetch_official_ats_jobs()
        new_ats_jobs = [
            j for j in deduplicate_batch(ats_jobs) if not store.is_duplicate(j)[0]
        ]
        if len(new_ats_jobs) > config.MAX_NEW_ATS_JOBS_PER_RUN:
            logger.info(
                "Limitando vagas novas de ATS nesta execução para %d (de %d encontradas).",
                config.MAX_NEW_ATS_JOBS_PER_RUN,
                len(new_ats_jobs),
            )
            new_ats_jobs = new_ats_jobs[: config.MAX_NEW_ATS_JOBS_PER_RUN]

        raw_jobs: list[dict[str, Any]] = []
        raw_jobs.extend(new_ats_jobs)
        raw_jobs.extend(fetch_jsearch_jobs(env["RAPIDAPI_KEY"]))
        raw_jobs.extend(fetch_gupy_jobs())

        jobs = deduplicate_batch(raw_jobs)
        logger.info("Total de vagas únicas coletadas nesta execução: %d.", len(jobs))

        # 2. Deduplicação pré-LLM em 4 camadas (job_id, URL, hash de descrição, empresa+título)
        new_jobs: list[dict[str, Any]] = []
        dup_reasons: dict[str, int] = {}
        for job in jobs:
            is_dup, reason = store.is_duplicate(job)
            if is_dup:
                dup_reasons[reason] = dup_reasons.get(reason, 0) + 1
            else:
                new_jobs.append(job)

        logger.info(
            "Vagas novas para triagem: %d (ignoradas: %d | motivos: %s).",
            len(new_jobs),
            len(jobs) - len(new_jobs),
            dup_reasons or "nenhum",
        )

        if not new_jobs:
            logger.info("Nenhuma vaga nova encontrada. Encerrando.")
            return

        # Prioriza vagas da whitelist (Estágio A) antes de descoberta (Estágio B)
        new_jobs.sort(key=lambda j: not check_whitelist(j["employer_name"])["in_whitelist"])

        # api_version="v1beta" é obrigatório para Google Search Grounding no google-genai>=3.x
        client = genai.Client(
            api_key=env["GEMINI_API_KEY"],
            http_options=types.HttpOptions(api_version="v1beta"),
        )
        candidate_models = get_candidate_models(env["GEMINI_API_KEY"])
        exhausted_models: set[str] = set()
        grounding_state: dict[str, bool] = {"disabled": False}
        web_cache: dict[str, str] = {}
        logger.info("Fila de prioridade de modelos Gemini: %s", candidate_models)

        notified_count = 0
        evaluated_count = 0

        for job in new_jobs:
            is_dup, reason = store.is_duplicate(job)
            if is_dup:
                logger.debug("Ignorando duplicata tardia (%s): %s — %s", reason, job["job_title"], job["employer_name"])
                continue

            job_id = job["job_id"]
            whitelist_info = check_whitelist(job["employer_name"])
            stage_label = "Estágio A (Whitelist)" if whitelist_info["in_whitelist"] else "Estágio B (Busca Web)"
            logger.info(
                "Avaliando [%s] %s — %s (%s)",
                stage_label,
                job["job_title"],
                job["employer_name"],
                job_id,
            )

            try:
                evaluation, used_model = evaluate_job_with_fallback(
                    client,
                    job,
                    whitelist_info,
                    candidate_models,
                    exhausted_models,
                    grounding_state=grounding_state,
                    web_cache=web_cache,
                )
                evaluated_count += 1
                was_notified = False

                logger.info(
                    "Resultado [%s]: match=%s | score=%d | categoria=%s | motivo=%s",
                    used_model,
                    evaluation["match"],
                    evaluation["score"],
                    evaluation["categoria"],
                    evaluation["motivo"],
                )

                if evaluation["match"] and evaluation["score"] >= config.MIN_MATCH_SCORE:
                    if job.get("job_fallback_link"):
                        resolved_primary, resolved_fallback = resolve_official_job_url(job)
                        job["job_apply_link"] = resolved_primary
                        job["job_fallback_link"] = resolved_fallback

                    # Gera o CV adaptado em PDF antes do auto-apply (para anexar no formulário e/ou no Telegram)
                    pdf_bytes: bytes | None = None
                    pdf_name: str = ""
                    if config.ENABLE_TAILORED_CV:
                        try:
                            pdf_bytes, pdf_name = cv_generator.generate_tailored_cv_pdf(
                                client=client,
                                job=job,
                                evaluation=evaluation,
                                model_name=used_model,
                                extract_json_fn=extract_json_from_text,
                                call_with_backoff_fn=_call_with_backoff,
                            )
                        except Exception as cv_exc:
                            logger.warning(
                                "Falha ao gerar CV adaptado para %s — %s: %s",
                                job["job_title"],
                                job["employer_name"],
                                cv_exc,
                            )

                    apply_res: auto_apply.ApplyResult | None = None
                    if config.AUTO_APPLY_ENABLED:
                        apply_res = auto_apply.try_auto_apply(
                            job=job,
                            pdf_bytes=pdf_bytes,
                            pdf_name=pdf_name,
                            candidate_answers=store.get_candidate_answers(),
                            dry_run=config.AUTO_APPLY_DRY_RUN,
                        )
                        store.record_application(
                            job=job,
                            status=apply_res.status,
                            reason=apply_res.reason,
                            missing_fields=apply_res.missing_fields,
                            cv_filename=pdf_name,
                        )

                    # Regra 1: candidatura enviada -> só confirmação curta no Telegram
                    if apply_res and apply_res.status == "submitted":
                        confirm_msg = (
                            f"✅ *Candidatura enviada:* {escape_markdown(job['job_title'])} — "
                            f"{escape_markdown(job['employer_name'])} (*{evaluation['score']}/10*)"
                        )
                        send_telegram_message(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], confirm_msg)
                        notified_count += 1
                        was_notified = True
                        logger.info("Candidatura enviada automaticamente: %s — %s", job["job_title"], job["employer_name"])
                    else:
                        # Regra 2 & 3: não conseguiu enviar ou faltou dado -> alerta + motivo + pedido de /set + PDF
                        message = format_telegram_message(job, evaluation)
                        if apply_res:
                            if apply_res.status == "pending_info" and apply_res.missing_fields:
                                missing_lines = "\n".join(
                                    f"  • {escape_markdown(f['label'])} → `/set {f['key']} <valor>`"
                                    for f in apply_res.missing_fields
                                )
                                message += f"\n\n❓ *Faltam dados para envio automático:*\n{missing_lines}"
                            elif apply_res.reason:
                                message += f"\n⚠️ *Auto-apply:* {escape_markdown(apply_res.reason)}"

                        send_telegram_message(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], message)
                        notified_count += 1
                        was_notified = True
                        logger.info("Alerta enviado no Telegram para: %s — %s", job["job_title"], job["employer_name"])

                        if pdf_bytes and pdf_name:
                            try:
                                send_telegram_document(
                                    env["TELEGRAM_TOKEN"],
                                    env["TELEGRAM_CHAT_ID"],
                                    pdf_bytes,
                                    pdf_name,
                                    caption=f"📄 Currículo adaptado para {job['job_title']} — {job['employer_name']}",
                                )
                                logger.info("Currículo adaptado enviado no Telegram: %s", pdf_name)
                            except Exception as doc_exc:
                                logger.warning("Falha ao enviar PDF no Telegram: %s", doc_exc)

                store.record_job(job, evaluation, model_used=used_model, notified=was_notified)
            except DailyQuotaExceededError:
                logger.warning(
                    "Todos os modelos Gemini atingiram cota/indisponibilidade após %d avaliações. "
                    "Encerrando execução graciosamente (vagas restantes serão avaliadas no próximo ciclo).",
                    evaluated_count,
                )
                break
            except Exception as exc:
                logger.error(
                    "Erro ao processar vaga %s (%s — %s): %s",
                    job_id,
                    job["job_title"],
                    job["employer_name"],
                    exc,
                )

        logger.info(
            "Execução finalizada. Avaliadas: %d | Notificadas: %d.",
            evaluated_count,
            notified_count,
        )
    finally:
        store.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        logger.critical("Erro fatal no Job Hunter Bot: %s", exc)
        sys.exit(1)
