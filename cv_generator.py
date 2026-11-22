"""Gerador de currículo ATS-friendly adaptado por vaga em PDF (via Gemini + fpdf2)."""

from __future__ import annotations

import logging
import re
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from google import genai
from google.genai import types

import config

logger = logging.getLogger("job-hunter-bot")

CV_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "resumo": {"type": "STRING"},
        "competencias": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "experiencias": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "cargo": {"type": "STRING"},
                    "empresa": {"type": "STRING"},
                    "periodo": {"type": "STRING"},
                    "bullets": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
                "required": ["cargo", "empresa", "periodo", "bullets"],
            },
        },
        "formacao": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "destaque_extra": {"type": "STRING"},
    },
    "required": [
        "headline",
        "resumo",
        "competencias",
        "experiencias",
        "formacao",
        "destaque_extra",
    ],
}


def _sanitize_latin1(text: str) -> str:
    """Normaliza caracteres Unicode para Helvetica/latin-1 seguro no FPDF."""
    replacements = {
        "—": "-",
        "–": "-",
        "•": "|",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
        "\u00a0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def _build_cv_prompt(job: dict[str, Any], evaluation: dict[str, Any]) -> str:
    """Cria prompt para adaptar o currículo do Master Profile à vaga específica."""
    description = (job.get("job_description") or "")[:6000]
    return (
        "Você é um especialista em currículos técnicos ATS-friendly para vagas de AI Engineering, "
        "Data Engineering, Analytics Engineering e Tech Lead.\n"
        "Adapte o currículo do candidato abaixo especificamente para a vaga alvo.\n\n"
        "### REGRAS INEGOCIÁVEIS DE FIDELIDADE\n"
        "1. USE EXCLUSIVAMENTE experiências, cargos, datas e tecnologias reais do Master Profile.\n"
        "2. NUNCA invente métricas, empresas, cargos ou tecnologias.\n"
        "3. Sobre a seção 'Roadmap de Aprofundamento & TCC': ferramentas que o candidato já usa na prática "
        "(FastAPI, Prefect, PostgreSQL, Docker, GitHub Actions, RAG/Embeddings, Structured Outputs) podem constar "
        "normalmente; para itens que ainda estão apenas em estudo/TCC (ex: Terraform, RabbitMQ, Langfuse), "
        "não afirme experiência produtiva corporativa prévia — cite apenas como 'TCC em andamento' no campo `destaque_extra` se for muito relevante.\n"
        "4. Selecione as 4 a 5 experiências mais aderentes à vaga e escreva de 2 a 4 bullets objetivos e ricos em palavras-chave ATS por experiência.\n"
        "5. REGRA ESTRATÉGICA SOBRE VALENCE TECH / CALVARIUM: NÃO inclua Valence Tech/Calvarium como cargo "
        "de 'Fundador' em vagas CLT corporativas/tradicionais ou posições puramente técnicas (IC), para evitar receio "
        "de conflito de dedicação ou flight risk. Em vez disso, reflita essas competências (planejamento técnico/de produto, "
        "orientação e mentoria de devs, definição de roadmap, priorização de backlog e gestão ágil) na seção de competências "
        "e no resumo. Só cite o projeto Calvarium explicitamente se a vaga for de Tech Lead/Team Lead ou startup founder-friendly, "
        "sempre enquadrado como projeto paralelo de mentoria fora do expediente.\n"
        "6. Idioma do currículo: escreva em **Português** se a vaga for em português/Brasil, ou em **Inglês** se a vaga for internacional em inglês.\n"
        "7. Seja conciso para caber perfeitamente em 1 a 2 páginas limpas.\n\n"
        f"### Master Profile do Candidato\n{config.CANDIDATE_PROFILE}\n\n"
        f"### Vaga Alvo\n"
        f"- Título: {job.get('job_title')}\n"
        f"- Empresa: {job.get('employer_name')}\n"
        f"- Categoria: {evaluation.get('categoria')}\n"
        f"- Destaque sugerido na triagem: {evaluation.get('destaque_cv')}\n"
        f"- Descrição:\n{description}\n\n"
        "Retorne exclusivamente o JSON estruturado no schema solicitado."
    )


def render_cv_pdf(cv_data: dict[str, Any]) -> bytes:
    """Renderiza o JSON do currículo adaptado em um PDF limpo e ATS-friendly."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_margins(15, 14, 15)
    pdf.add_page()
    usable_w = pdf.epw

    # Cabeçalho
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(20, 30, 50)
    pdf.cell(usable_w, 8, _sanitize_latin1("Emanuel de Almeida Souza"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    headline = _sanitize_latin1(str(cv_data.get("headline") or "").strip())
    if headline:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(55, 80, 140)
        pdf.multi_cell(usable_w, 5.5, headline, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    contact_line = (
        "Ponta Grossa, PR - Brasil | emanuel.almeida1998@outlook.com | "
        "(42) 98423-1561 | linkedin.com/in/almeida-emanuel"
    )
    pdf.multi_cell(usable_w, 5, _sanitize_latin1(contact_line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    def _section_title(title: str) -> None:
        pdf.ln(2)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(20, 30, 50)
        pdf.cell(usable_w, 6, _sanitize_latin1(title.upper()), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y = pdf.get_y()
        pdf.set_draw_color(55, 80, 140)
        pdf.set_line_width(0.35)
        pdf.line(pdf.l_margin, y, pdf.l_margin + usable_w, y)
        pdf.ln(2)

    # Resumo
    resumo = _sanitize_latin1(str(cv_data.get("resumo") or "").strip())
    if resumo:
        _section_title("Resumo")
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(usable_w, 4.8, resumo, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Competências
    competencias = [str(c).strip() for c in (cv_data.get("competencias") or []) if str(c).strip()]
    if competencias:
        _section_title("Competencias Tecnicas")
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 9.2)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(usable_w, 4.8, _sanitize_latin1(" | ".join(competencias)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Experiências
    experiencias = cv_data.get("experiencias") or []
    if experiencias:
        _section_title("Experiencia")
        for exp in experiencias:
            cargo = _sanitize_latin1(str(exp.get("cargo") or "").strip())
            empresa = _sanitize_latin1(str(exp.get("empresa") or "").strip())
            periodo = _sanitize_latin1(str(exp.get("periodo") or "").strip())

            pdf.set_x(pdf.l_margin)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(20, 30, 50)
            pdf.multi_cell(usable_w, 5, f"{cargo} - {empresa}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            if periodo:
                pdf.set_x(pdf.l_margin)
                pdf.set_font("Helvetica", "I", 8.8)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(usable_w, 4.2, periodo, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            pdf.set_font("Helvetica", "", 9.2)
            pdf.set_text_color(30, 30, 30)
            indent = 3
            for bullet in exp.get("bullets") or []:
                clean_b = _sanitize_latin1(str(bullet).strip().lstrip("-• "))
                if clean_b:
                    pdf.set_x(pdf.l_margin + indent)
                    pdf.multi_cell(
                        usable_w - indent,
                        4.6,
                        f"- {clean_b}",
                        new_x=XPos.LMARGIN,
                        new_y=YPos.NEXT,
                    )
            pdf.ln(1.5)

    # Formação
    formacao = [str(f).strip() for f in (cv_data.get("formacao") or []) if str(f).strip()]
    if formacao:
        _section_title("Formacao Academica")
        pdf.set_font("Helvetica", "", 9.2)
        pdf.set_text_color(30, 30, 30)
        for item in formacao:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(usable_w, 4.8, _sanitize_latin1(f"- {item}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Destaque Extra (Pesquisa / Premiações / TCC)
    extra = _sanitize_latin1(str(cv_data.get("destaque_extra") or "").strip())
    if extra:
        _section_title("Pesquisa, Lideranca & Projetos")
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 9.2)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(usable_w, 4.8, extra, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


def build_cv_filename(job: dict[str, Any]) -> str:
    """Gera um nome de arquivo limpo para o CV adaptado."""
    employer = re.sub(r"[^a-zA-Z0-9]+", "_", str(job.get("employer_name") or "empresa")).strip("_")
    title = re.sub(r"[^a-zA-Z0-9]+", "_", str(job.get("job_title") or "vaga")).strip("_")
    slug = f"{employer}_{title}"[:50].strip("_")
    return f"CV_Emanuel_Almeida_{slug}.pdf"


def generate_tailored_cv_pdf(
    client: genai.Client,
    job: dict[str, Any],
    evaluation: dict[str, Any],
    model_name: str,
    extract_json_fn,
    call_with_backoff_fn,
) -> tuple[bytes, str]:
    """Gera um currículo PDF sob medida para a vaga aprovada."""
    prompt = _build_cv_prompt(job, evaluation)
    response = call_with_backoff_fn(
        client.models.generate_content,
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CV_RESPONSE_SCHEMA,
            temperature=0.2,
        ),
    )
    cv_data = extract_json_fn(response.text or "")
    if not cv_data:
        raise ValueError("Resposta JSON inválida ao gerar currículo adaptado.")

    pdf_bytes = render_cv_pdf(cv_data)
    filename = build_cv_filename(job)
    return pdf_bytes, filename
