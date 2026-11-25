"""Motor de auto-preenchimento de candidaturas em portais ATS abertos (Greenhouse, Lever, Ashby)."""

from __future__ import annotations

import logging
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("job-hunter-bot")

_LOGIN_REQUIRED_DOMAINS: tuple[str, ...] = (
    "gupy.io",
    "myworkdayjobs.com",
    "workday.com",
    "linkedin.com",
    "inhire.app",
    "solides.com.br",
    "taleo.net",
    "icims.com",
)

_CAPTCHA_MARKERS: tuple[str, ...] = (
    "recaptcha",
    "g-recaptcha",
    "hcaptcha",
    "h-captcha",
    "turnstile",
    "cf-turnstile",
    "verify you are human",
    "i am not a robot",
    "não sou um robô",
)

# Regras de mapeamento de campos de formulário -> chave em `candidate_answers`
_FIELD_PATTERNS: list[tuple[str, str]] = [
    (r"\b(first\s*name|primeiro\s*nome|nome\s*pr[oó]prio)\b", "first_name"),
    (r"\b(last\s*name|surname|sobrenome|apelido)\b", "last_name"),
    (r"\b(full\s*name|nome\s*completo|^nome$|^name$)\b", "full_name"),
    (r"\b(e-?mail)\b", "email"),
    (r"\b(phone|telefone|celular|whatsapp|mobile)\b", "phone"),
    (r"\b(linkedin)\b", "linkedin_url"),
    (r"\b(github)\b", "github_url"),
    (r"\b(portfolio|website|site\s*pessoal)\b", "portfolio_url"),
    (r"\b(current\s*company|empresa\s*atual|org|organization)\b", "current_company"),
    (r"\b(city|cidade)\b", "city"),
    (r"\b(location|localiza[cç][aã]o|endere[cç]o)\b", "location"),
    (r"\b(notice\s*period|aviso\s*pr[eé]vio|disponibilidade|start\s*date|when\s*can\s*you\s*start)\b", "notice_period"),
    (r"\b(university|universidade|school|institui[cç][aã]o|faculdade)\b", "education_school"),
    (r"\b(degree|curso|forma[cç][aã]o|gradua[cç][aã]o)\b", "education_degree"),
    (r"\b(graduation\s*year|ano\s*de\s*formatura|conclus[aã]o)\b", "graduation_year"),
    (r"\b(sponsorship|visa|visto)\b", "needs_visa_sponsorship"),
    (r"\b(authorized\s*to\s*work|autoriza[cç][aã]o\s*para\s*trabalhar)\b", "work_authorization_br"),
    (r"\b(english|ingl[eê]s)\b", "english_level"),
]


@dataclass
class ApplyResult:
    status: str  # submitted | dry_run_ok | pending_info | blocked_captcha | requires_login | unsupported_portal | failed
    reason: str = ""
    missing_fields: list[dict[str, str]] = field(default_factory=list)


def _norm(text: str) -> str:
    nf = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in nf if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def slugify_field_key(label: str) -> str:
    """Converte o rótulo de uma pergunta do formulário em uma chave curta para /set."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", _norm(label)).strip("_")
    return cleaned[:40] or "campo_extra"


def detect_portal(url: str) -> str:
    """Classifica o portal de candidatura a partir da URL."""
    host = (urlparse((url or "").lower()).netloc or "").lower()
    if not host:
        return "unsupported"
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if any(dom in host for dom in _LOGIN_REQUIRED_DOMAINS):
        return "requires_login"
    return "unsupported"


def match_answer_for_field(
    field_hint: str,
    answers: dict[str, str],
    job: dict[str, Any] | None = None,
) -> str | None:
    """Encontra a resposta cadastrada para um campo de formulário por padrão ou chave customizada."""
    norm_hint = _norm(field_hint)
    if not norm_hint:
        return None

    # 1. Match exato com chave customizada (ex: cadastrada via /set) tem prioridade máxima
    slug = slugify_field_key(norm_hint)
    if slug in answers and answers[slug]:
        return answers[slug]

    # 2. Pretensão salarial contextual (BRL vs USD)
    if re.search(r"\b(salary|sal[aá]rio|pretens[aã]o|compensation|remunera[cç][aã]o)\b", norm_hint):
        is_brl = any(w in norm_hint for w in ("r$", "brl", "reais", "clt"))
        is_usd = not is_brl and (
            any(w in norm_hint for w in ("usd", "dollar", "us$"))
            or bool(re.search(r"(?<![a-z])\$", norm_hint))
        )
        if is_usd:
            return answers.get("salary_intl_usd_monthly")
        loc_text = _norm(str((job or {}).get("job_city") or ""))
        if "ponta grossa" in loc_text:
            return answers.get("salary_clt_pg_brl")
        return answers.get("salary_clt_remote_brl")

    # 3. Padrões conhecidos
    for pattern, key in _FIELD_PATTERNS:
        if re.search(pattern, norm_hint):
            val = answers.get(key)
            if val:
                return val

    # 4. Match aproximado por slug com chaves customizadas
    for key, val in answers.items():
        if val and len(key) >= 4 and (key in slug or slug in key):
            return val

    return None


def _page_has_captcha(page) -> bool:
    """Detecta CAPTCHA/Cloudflare/verificação humana (nunca tenta burlar)."""
    try:
        html_lower = (page.content() or "").lower()
        if any(marker in html_lower for marker in _CAPTCHA_MARKERS):
            # Confirma se há iframe/widget visível de captcha
            captcha_loc = page.locator(
                "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile'], "
                ".g-recaptcha, .h-captcha, .cf-turnstile"
            )
            if captcha_loc.count() > 0:
                return True
    except Exception:
        pass
    return False


def _prepare_apply_url(portal: str, url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if portal == "lever" and not cleaned.endswith("/apply"):
        return f"{cleaned}/apply"
    return cleaned


def try_auto_apply(
    job: dict[str, Any],
    pdf_bytes: bytes | None,
    pdf_name: str,
    candidate_answers: dict[str, str],
    dry_run: bool = True,
) -> ApplyResult:
    """Tenta preencher (e opcionalmente enviar) candidatura em portais ATS abertos."""
    apply_url = str(job.get("job_apply_link") or "").strip()
    portal = detect_portal(apply_url)

    if portal == "requires_login":
        host = urlparse(apply_url).netloc or "portal"
        return ApplyResult(
            status="requires_login",
            reason=f"Exige login/conta própria em {host}.",
        )
    if portal == "unsupported":
        return ApplyResult(
            status="unsupported_portal",
            reason="Portal externo fora dos ATS automatizados (Greenhouse/Lever/Ashby).",
        )

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return ApplyResult(
            status="failed",
            reason="Playwright não instalado neste ambiente.",
        )

    target_url = _prepare_apply_url(portal, apply_url)
    tmp_pdf_path: Path | None = None

    try:
        if pdf_bytes:
            tmp_dir = Path(tempfile.mkdtemp(prefix="jobot_cv_"))
            tmp_pdf_path = tmp_dir / (pdf_name or "CV_Emanuel_Almeida.pdf")
            tmp_pdf_path.write_bytes(pdf_bytes)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale="pt-BR")
            page = context.new_page()
            page.set_default_timeout(15000)

            page.goto(target_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            # Ashby normalmente tem uma aba "Application"
            if portal == "ashby":
                app_tab = page.locator("button:has-text('Apply'), a:has-text('Application')").first
                if app_tab.count() > 0 and app_tab.is_visible():
                    app_tab.click()
                    page.wait_for_timeout(1000)

            if _page_has_captcha(page):
                browser.close()
                return ApplyResult(
                    status="blocked_captcha",
                    reason="Formulário protegido por CAPTCHA/verificação humana.",
                )

            # 1. Upload do CV personalizado em PDF
            if tmp_pdf_path and tmp_pdf_path.is_file():
                file_inputs = page.locator("input[type='file']")
                if file_inputs.count() > 0:
                    try:
                        file_inputs.first.set_input_files(str(tmp_pdf_path))
                    except Exception as exc:
                        logger.debug("Falha ao anexar CV no formulário: %s", exc)

            # 2. Preenchimento de campos de texto/textarea visíveis
            fields_info = page.evaluate(
                """() => {
                    const results = [];
                    const els = document.querySelectorAll('input, textarea, select');
                    els.forEach((el, idx) => {
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        if (['hidden', 'submit', 'button', 'file', 'image', 'reset'].includes(type)) return;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null) return;
                        let label = '';
                        if (el.id) {
                            const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                            if (lbl) label = lbl.innerText.trim();
                        }
                        if (!label) {
                            const parentLabel = el.closest('label');
                            if (parentLabel) label = parentLabel.innerText.trim();
                        }
                        if (!label) {
                            label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '';
                        }
                        const required = el.required || el.getAttribute('aria-required') === 'true' || label.includes('*');
                        el.setAttribute('data-jobot-idx', String(idx));
                        results.push({
                            idx,
                            tag: el.tagName.toLowerCase(),
                            type,
                            name: el.getAttribute('name') || '',
                            id: el.id || '',
                            label: label.replace(/\\s+/g, ' ').trim(),
                            required,
                            value: el.value || ''
                        });
                    });
                    return results;
                }"""
            )

            missing_fields: list[dict[str, str]] = []
            seen_missing_keys: set[str] = set()

            for info in fields_info:
                if info["value"]:
                    continue
                hint = f"{info['label']} {info['name']} {info['id']}"
                answer = match_answer_for_field(hint, candidate_answers, job=job)
                loc = page.locator(f"[data-jobot-idx='{info['idx']}']")

                if answer and info["tag"] in ("input", "textarea") and info["type"] not in ("checkbox", "radio"):
                    try:
                        loc.fill(answer)
                        continue
                    except Exception:
                        pass

                if info["required"] and info["type"] not in ("checkbox", "radio"):
                    clean_label = info["label"].rstrip("* ").strip() or info["name"] or "Campo obrigatório"
                    key = slugify_field_key(clean_label)
                    if key not in seen_missing_keys:
                        seen_missing_keys.add(key)
                        missing_fields.append({"key": key, "label": clean_label[:80]})

            if missing_fields:
                browser.close()
                return ApplyResult(
                    status="pending_info",
                    reason="Faltam dados obrigatórios no formulário.",
                    missing_fields=missing_fields[:4],
                )

            if dry_run:
                browser.close()
                return ApplyResult(
                    status="dry_run_ok",
                    reason="DRY_RUN: formulário preenchido com sucesso (envio real desativado).",
                )

            # 3. Envio real (somente quando dry_run=False e sem CAPTCHA/campos faltantes)
            submit_btn = page.locator(
                "button[type='submit'], input[type='submit'], button:has-text('Submit Application'), button:has-text('Enviar')"
            ).first
            if submit_btn.count() == 0:
                browser.close()
                return ApplyResult(
                    status="failed",
                    reason="Botão de envio não encontrado no formulário.",
                )

            submit_btn.click()
            page.wait_for_timeout(3000)

            if _page_has_captcha(page):
                browser.close()
                return ApplyResult(
                    status="blocked_captcha",
                    reason="CAPTCHA exibido ao tentar enviar a candidatura.",
                )

            body_text = (page.inner_text("body") or "").lower()
            success_markers = (
                "application has been submitted",
                "thank you for applying",
                "application submitted",
                "obrigado por se candidatar",
                "candidatura enviada",
                "we have received your application",
            )
            browser.close()
            if any(m in body_text for m in success_markers):
                return ApplyResult(status="submitted", reason="Candidatura submetida com sucesso.")
            return ApplyResult(
                status="failed",
                reason="Não foi possível confirmar o envio na página final (verifique manualmente).",
            )
    except Exception as exc:
        logger.warning("Erro no auto-apply para %s: %s", apply_url, exc)
        return ApplyResult(status="failed", reason=f"Erro técnico no preenchimento: {str(exc)[:100]}")
    finally:
        if tmp_pdf_path and tmp_pdf_path.exists():
            try:
                tmp_pdf_path.unlink()
                tmp_pdf_path.parent.rmdir()
            except OSError:
                pass
