"""Testes unitários para o Job Hunter Bot."""

from __future__ import annotations

import json
import sys
import tempfile
import types as pytypes
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Permite executar testes unitários mesmo em ambientes sem google-genai/requests instalados
if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["requests"] = MagicMock()

if "google.genai" not in sys.modules:
    try:
        from google import genai  # noqa: F401
    except ModuleNotFoundError:
        class _DummyObj:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        google_mod = pytypes.ModuleType("google")
        genai_mod = pytypes.ModuleType("google.genai")
        genai_types_mod = pytypes.ModuleType("google.genai.types")
        genai_types_mod.GenerateContentConfig = _DummyObj
        genai_types_mod.Tool = _DummyObj
        genai_types_mod.GoogleSearch = _DummyObj
        genai_mod.types = genai_types_mod
        genai_mod.Client = MagicMock
        google_mod.genai = genai_mod
        sys.modules["google"] = google_mod
        sys.modules["google.genai"] = genai_mod
        sys.modules["google.genai.types"] = genai_types_mod

import bot
import config
import cv_generator


class TestSeenJobsPersistence(unittest.TestCase):
    def test_load_and_save_respects_max_items_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen_jobs.json"
            ids = [f"job-{i}" for i in range(10)]
            bot.save_seen_jobs(ids, path=path, max_items=5)

            loaded_list, loaded_set = bot.load_seen_jobs(path=path)
            self.assertEqual(loaded_list, ["job-5", "job-6", "job-7", "job-8", "job-9"])
            self.assertEqual(loaded_set, set(loaded_list))

    def test_load_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded_list, loaded_set = bot.load_seen_jobs(Path(tmp) / "missing.json")
            self.assertEqual(loaded_list, [])
            self.assertEqual(loaded_set, set())

    def test_load_dotenv_sets_missing_and_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                '# comentário\nJOBOT_TEST_A="valor_a"\nJOBOT_TEST_B=valor_b\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"JOBOT_TEST_B": "ja_definido"}, clear=False):
                config.load_dotenv(env_file)
                import os
                self.assertEqual(os.environ.get("JOBOT_TEST_A"), "valor_a")
                self.assertEqual(os.environ.get("JOBOT_TEST_B"), "ja_definido")
                os.environ.pop("JOBOT_TEST_A", None)


class TestWhitelistMatching(unittest.TestCase):
    def test_matches_br_remote_and_pg_case_and_accent_insensitive(self) -> None:
        self.assertTrue(bot.check_whitelist("Itau Unibanco")["in_br_remote"])
        self.assertTrue(bot.check_whitelist("AGUIA SISTEMAS S.A.")["in_pg"])
        self.assertTrue(bot.check_whitelist("nstech")["in_br_remote"])
        self.assertTrue(bot.check_whitelist("nstech")["in_pg"])

    def test_short_names_do_not_false_positive(self) -> None:
        self.assertFalse(bot.check_whitelist("1999 Tech Solutions")["in_whitelist"])
        self.assertFalse(bot.check_whitelist("Nohon Labs")["in_whitelist"])
        self.assertTrue(bot.check_whitelist("99 Tecnologia")["in_br_remote"])


class TestNormalizationAndDates(unittest.TestCase):
    def test_is_within_last_24h(self) -> None:
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        self.assertTrue(bot._is_within_last_24h(recent))
        self.assertFalse(bot._is_within_last_24h(old))
        self.assertFalse(bot._is_within_last_24h(None))

    def test_deduplicate_batch(self) -> None:
        jobs = [
            {"job_id": "1", "job_title": "AI Engineer", "employer_name": "Nubank"},
            {"job_id": "1", "job_title": "AI Engineer", "employer_name": "Nubank"},
            {"job_id": "2", "job_title": "AI Engineer", "employer_name": "nubank"},
            {"job_id": "3", "job_title": "Data Engineer", "employer_name": "iFood"},
        ]
        unique = bot.deduplicate_batch(jobs)
        self.assertEqual([j["job_id"] for j in unique], ["1", "3"])

    def test_select_best_apply_link_prefers_company_and_blacklists_talent(self) -> None:
        raw = {
            "job_id": "j1",
            "job_title": "Analytics Engineer",
            "employer_name": "Omie",
            "job_apply_link": "https://br.talent.com/view?id=646793688390964837",
            "apply_options": [
                {"apply_link": "https://br.talent.com/view?id=646793688390964837", "is_direct": False},
                {"apply_link": "https://www.linkedin.com/jobs/view/123", "is_direct": False},
                {"apply_link": "https://omie.gupy.io/jobs/999", "is_direct": True},
            ],
        }
        primary, fallback = bot.select_best_apply_link(raw)
        self.assertEqual(primary, "https://omie.gupy.io/jobs/999")
        self.assertEqual(fallback, "")

    def test_select_best_apply_link_uses_company_search_when_only_blacklisted(self) -> None:
        raw = {
            "job_id": "j2",
            "job_title": "AI Engineer",
            "employer_name": "Indicium",
            "job_apply_link": "https://br.talent.com/view?id=646793688390964837",
            "apply_options": [
                {"apply_link": "https://br.talent.com/view?id=646793688390964837", "is_direct": False},
            ],
        }
        primary, fallback = bot.select_best_apply_link(raw)
        self.assertIn("linkedin.com/jobs/search/", primary)
        self.assertIn("Indicium", primary)
        self.assertEqual(fallback, "https://br.talent.com/view?id=646793688390964837")

    def test_select_best_apply_link_demotes_unofficial_aggregator_to_fallback(self) -> None:
        raw = {
            "job_id": "j3",
            "job_title": "Data Engineer",
            "employer_name": "Empresa Desconhecida XYZ",
            "job_apply_link": "https://www.simplyhired.com.br/job/xyz",
            "apply_options": [
                {"apply_link": "https://www.simplyhired.com.br/job/xyz", "is_direct": False},
            ],
        }
        primary, fallback = bot.select_best_apply_link(raw)
        self.assertIn("linkedin.com/jobs/search/", primary)
        self.assertEqual(fallback, "https://www.simplyhired.com.br/job/xyz")

    def test_ats_job_matches_filters(self) -> None:
        self.assertTrue(bot._ats_job_matches_filters("Senior Analytics Engineer", "Brazil - Remote"))
        self.assertTrue(bot._ats_job_matches_filters("AI Engineer", "Remote - LATAM"))
        self.assertFalse(bot._ats_job_matches_filters("Senior Analytics Engineer", "San Francisco, CA"))
        self.assertFalse(bot._ats_job_matches_filters("Account Executive", "Brazil - Remote"))


class TestJsonExtractionAndTelegram(unittest.TestCase):
    def test_extract_json_from_fenced_markdown(self) -> None:
        text = 'Pesquisa concluída.\n```json\n{"match": true, "score": 8}\n```'
        parsed = bot.extract_json_from_text(text)
        self.assertEqual(parsed, {"match": True, "score": 8})

    def test_normalize_evaluation_caps_score_when_rejected(self) -> None:
        normalized = bot._normalize_evaluation({"match": False, "score": 10}, na_whitelist=False)
        self.assertFalse(normalized["match"])
        self.assertEqual(normalized["score"], 6)

    def test_format_telegram_message_escapes_markdown(self) -> None:
        job = {
            "job_title": "AI_Engineer *Senior*",
            "employer_name": "Nubank [BR]",
            "job_apply_link": "https://example.com/job(1)",
        }
        evaluation = {
            "categoria": "BR Remoto CLT",
            "regime_detectado": "CLT",
            "score": 9,
            "clima_e_cultura": "Flexível_e_remoto",
            "motivo": "Aderente ao perfil",
            "destaque_cv": "RAG e LangChain",
        }
        msg = bot.format_telegram_message(job, evaluation)
        self.assertIn(r"🎯 *AI\_Engineer \*Senior\** — Nubank \[BR]", msg)
        self.assertIn("📌 BR Remoto CLT · CLT · *9/10*", msg)
        self.assertIn("https://example.com/job(1%29", msg)


class TestEvaluationRouting(unittest.TestCase):
    def test_stage_a_uses_json_schema(self) -> None:
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(
            {
                "match": True,
                "score": 9,
                "na_whitelist": True,
                "categoria": "BR Remoto CLT",
                "regime_detectado": "CLT",
                "clima_e_cultura": "Empresa na whitelist com cultura remota.",
                "motivo": "Excelente fit técnico e cultural.",
                "destaque_cv": "Destacar agentes de IA no iFood.",
            }
        )
        client.models.generate_content.return_value = mock_resp

        job = {
            "job_id": "abc",
            "job_title": "AI Engineer",
            "employer_name": "Nubank",
            "job_city": "",
            "job_state": "",
            "job_country": "BR",
            "job_is_remote": True,
            "job_description": "CLT 100% remoto com LangChain e Python.",
            "job_apply_link": "https://nubank.com.br/jobs/1",
        }
        wl = bot.check_whitelist(job["employer_name"])
        result = bot.evaluate_job(client, job, wl)
        self.assertTrue(result["match"])
        self.assertEqual(result["score"], 9)
        call_kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], config.GEMINI_MODEL)
        self.assertEqual(call_kwargs["config"].response_mime_type, "application/json")

    def test_stage_b_uses_google_search_and_fallback_conversion(self) -> None:
        client = MagicMock()
        search_resp = MagicMock()
        search_resp.text = "Não encontrei evidências concretas sobre a empresa UnknownTech."
        structured_resp = MagicMock()
        structured_resp.text = json.dumps(
            {
                "match": False,
                "score": 2,
                "na_whitelist": False,
                "categoria": "Descartada",
                "regime_detectado": "Não especificado",
                "clima_e_cultura": "Sem evidências públicas suficientes.",
                "motivo": "Descartada pela trava de reputação.",
                "destaque_cv": "N/A",
            }
        )
        client.models.generate_content.side_effect = [search_resp, structured_resp]

        job = {
            "job_id": "xyz",
            "job_title": "AI Engineer",
            "employer_name": "UnknownTech",
            "job_city": "",
            "job_state": "",
            "job_country": "US",
            "job_is_remote": True,
            "job_description": "Remote AI role.",
            "job_apply_link": "https://unknowntech.example/jobs/1",
        }
        wl = bot.check_whitelist(job["employer_name"])
        result = bot.evaluate_job(client, job, wl)
        self.assertFalse(result["match"])
        self.assertEqual(client.models.generate_content.call_count, 2)
        first_config = client.models.generate_content.call_args_list[0].kwargs["config"]
        self.assertTrue(first_config.tools)

    @patch("bot.time.sleep", return_value=None)
    def test_backoff_retries_on_429(self, mock_sleep: MagicMock) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return "ok"

        self.assertEqual(bot._call_with_backoff(flaky), "ok")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(mock_sleep.call_count, 1)

    def test_daily_quota_error_does_not_retry(self) -> None:
        calls = {"n": 0}

        def exhausted() -> str:
            calls["n"] += 1
            raise RuntimeError("429 GenerateRequestsPerDayPerProjectPerModel-FreeTier")

        with self.assertRaises(bot.DailyQuotaExceededError):
            bot._call_with_backoff(exhausted)
        self.assertEqual(calls["n"], 1)

    def test_evaluate_job_with_fallback_switches_on_quota_or_404(self) -> None:
        client = MagicMock()
        ok_resp = MagicMock()
        ok_resp.text = json.dumps(
            {
                "match": True,
                "score": 8,
                "na_whitelist": True,
                "categoria": "BR Remoto CLT",
                "regime_detectado": "CLT",
                "clima_e_cultura": "Whitelist.",
                "motivo": "Bom fit.",
                "destaque_cv": "Python + LLMs",
            }
        )
        client.models.generate_content.side_effect = [
            RuntimeError("404 NOT_FOUND model discontinued"),
            RuntimeError("429 GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
            ok_resp,
        ]
        job = {
            "job_id": "fb1",
            "job_title": "AI Engineer",
            "employer_name": "Nubank",
            "job_city": "",
            "job_state": "",
            "job_country": "BR",
            "job_is_remote": True,
            "job_description": "CLT remoto.",
            "job_apply_link": "https://nubank.com.br/jobs/fb1",
        }
        exhausted: set[str] = set()
        result, used_model = bot.evaluate_job_with_fallback(
            client,
            job,
            bot.check_whitelist("Nubank"),
            ["model-404", "model-quota", "model-ok"],
            exhausted,
        )
        self.assertTrue(result["match"])
        self.assertEqual(used_model, "model-ok")
        self.assertEqual(exhausted, {"model-404", "model-quota"})


class TestMasterProfileAndCvGenerator(unittest.TestCase):
    def test_profile_md_loads_experiences_startup_and_roadmap(self) -> None:
        profile = config.load_candidate_profile("profile.md")
        self.assertIn("Valence Tech", profile)
        self.assertIn("Calvarium", profile)
        self.assertIn("PM/PO", profile)
        self.assertIn("Roadmap de Aprofundamento", profile)
        self.assertIn("dbt Core", profile)
        self.assertIn("FiqOn", profile)

    def test_render_cv_pdf_generates_valid_pdf_bytes(self) -> None:
        sample = {
            "headline": "AI Engineer | Data Platform & LLM Systems",
            "resumo": "Engenheiro focado em plataformas de dados e agentes de IA.",
            "competencias": ["Python", "SQL", "Databricks", "LangChain", "Prefect"],
            "experiencias": [
                {
                    "cargo": "Analista de Dados Pleno",
                    "empresa": "Faculdade UNIGUAÇU (Uniscale)",
                    "periodo": "09/2026 - Presente",
                    "bullets": ["Desenvolvimento de produtos de dados ponta a ponta com FastAPI e React."],
                }
            ],
            "formacao": ["Bacharelado em Ciência da Computação — UTFPR (2021 - 2026)"],
            "destaque_extra": "Iniciação Científica em anonimização de PII com OpenCV, OCR e LLMs.",
        }
        pdf_bytes = cv_generator.render_cv_pdf(sample)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 500)
        self.assertEqual(
            cv_generator.build_cv_filename({"employer_name": "Spotify", "job_title": "AI Engineer"}),
            "CV_Emanuel_Almeida_Spotify_AI_Engineer.pdf",
        )


if __name__ == "__main__":
    unittest.main()
