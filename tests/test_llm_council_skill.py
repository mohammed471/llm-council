"""Tests for the llm-council skill script and build script.

The OpenRouter API is fully mocked: no network access or API key is needed.
Run with:  python3 -m unittest discover -s tests -v   (or: python3 -m pytest tests)
"""

import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "llm-council"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


council = _load("llm_council_script", SKILL_DIR / "scripts" / "council.py")
build_skill = _load("llm_council_build", REPO_ROOT / "scripts" / "build_skill.py")

MODELS = ["m/alpha", "m/beta", "m/gamma"]
CHAIRMAN = "m/chair"


class FakeClient:
    """Deterministic stand-in for OpenRouterClient."""

    def __init__(self, fail=(), rankings=None, chairman_fails=False):
        self.fail = set(fail)
        self.rankings = rankings or {}
        self.chairman_fails = chairman_fails
        self.calls = []
        self.errors = {}

    def query_model(self, model, messages):
        self.calls.append((model, messages))
        if model in self.fail or (model == CHAIRMAN and self.chairman_fails):
            self.errors[model] = "network error: mocked"
            return None
        prompt = messages[-1]["content"]
        if model == CHAIRMAN:
            return {"content": "CHAIRMAN SYNTHESIS", "reasoning_details": None}
        if "FINAL RANKING:" in prompt:
            ranking = self.rankings.get(model, ["Response A", "Response B", "Response C"])
            body = "Evaluation of each response.\n\nFINAL RANKING:\n" + "\n".join(
                f"{i}. {lbl}" for i, lbl in enumerate(ranking, 1)
            )
            return {"content": body, "reasoning_details": None}
        return {"content": f"answer from {model}", "reasoning_details": None}

    def query_models_parallel(self, models, messages):
        return {m: self.query_model(m, messages) for m in models}


def make_config(**overrides):
    cfg = {"council_models": list(MODELS), "chairman_model": CHAIRMAN,
           "api_url": "https://example.invalid/api", "timeout_seconds": 5}
    cfg.update(overrides)
    return cfg


class ParseRankingTests(unittest.TestCase):
    def test_numbered_final_ranking(self):
        text = "Response A is ok. Response C is great.\n\nFINAL RANKING:\n1. Response C\n2. Response A\n3. Response B"
        self.assertEqual(council.parse_ranking_from_text(text), ["Response C", "Response A", "Response B"])

    def test_ignores_mentions_before_header(self):
        text = "Response B Response B\nFINAL RANKING:\n1. Response A\n2. Response B"
        self.assertEqual(council.parse_ranking_from_text(text), ["Response A", "Response B"])

    def test_unnumbered_section_fallback(self):
        text = "FINAL RANKING:\nResponse B, then Response A"
        self.assertEqual(council.parse_ranking_from_text(text), ["Response B", "Response A"])

    def test_no_header_fallback(self):
        self.assertEqual(council.parse_ranking_from_text("I prefer Response B over Response A"),
                         ["Response B", "Response A"])

    def test_arabic_evaluation_text(self):
        text = "Response A جيدة لكنها مختصرة.\nResponse B أفضل.\n\nFINAL RANKING:\n1. Response B\n2. Response A"
        self.assertEqual(council.parse_ranking_from_text(text), ["Response B", "Response A"])


class AggregateTests(unittest.TestCase):
    def test_average_and_sort(self):
        mapping = {"Response A": "x", "Response B": "y", "Response C": "z"}
        stage2 = [
            {"ranking": "FINAL RANKING:\n1. Response B\n2. Response A\n3. Response C"},
            {"ranking": "FINAL RANKING:\n1. Response B\n2. Response C\n3. Response A"},
            {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"},
        ]
        agg = council.calculate_aggregate_rankings(stage2, mapping)
        self.assertEqual([a["model"] for a in agg], ["y", "x", "z"])
        self.assertEqual(agg[0], {"model": "y", "average_rank": 1.33, "rankings_count": 3})
        self.assertEqual(agg[1]["average_rank"], 2.0)
        self.assertEqual(agg[2]["average_rank"], 2.67)

    def test_unknown_labels_ignored(self):
        agg = council.calculate_aggregate_rankings(
            [{"ranking": "FINAL RANKING:\n1. Response Z\n2. Response A"}], {"Response A": "x"})
        self.assertEqual(agg, [{"model": "x", "average_rank": 2.0, "rankings_count": 1}])


class PromptTests(unittest.TestCase):
    def test_ranking_prompt_is_anonymous(self):
        stage1 = [{"model": m, "response": f"ans {i}"} for i, m in enumerate(MODELS)]
        prompt, mapping = council.build_ranking_prompt("Q?", stage1)
        self.assertEqual(mapping, {"Response A": "m/alpha", "Response B": "m/beta", "Response C": "m/gamma"})
        for m in MODELS:
            self.assertNotIn(m, prompt)
        self.assertIn("Response C:\nans 2", prompt)
        self.assertIn('FINAL RANKING:', prompt)

    def test_stage1_has_language_rule(self):
        msgs = council.build_stage1_messages("ما هي عاصمة مصر؟")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Arabic", msgs[0]["content"])
        self.assertEqual(msgs[-1]["content"], "ما هي عاصمة مصر؟")

    def test_chairman_prompt_contains_everything(self):
        stage1 = [{"model": "m/alpha", "response": "A1"}]
        stage2 = [{"model": "m/alpha", "ranking": "R1"}]
        p = council.build_chairman_prompt("Q?", stage1, stage2)
        for part in ("Original Question: Q?", "STAGE 1", "A1", "STAGE 2", "R1", "same language"):
            self.assertIn(part, p)


class FullCouncilTests(unittest.TestCase):
    def test_three_stages_in_order(self):
        client = FakeClient(rankings={"m/alpha": ["Response B", "Response A", "Response C"],
                                      "m/beta": ["Response B", "Response C", "Response A"],
                                      "m/gamma": ["Response A", "Response B", "Response C"]})
        result = council.run_full_council(client, make_config(), "What is 2+2?")
        self.assertEqual([r["model"] for r in result["stage1"]], MODELS)
        self.assertEqual(len(result["stage2"]), 3)
        self.assertEqual(result["stage3"], {"model": CHAIRMAN, "response": "CHAIRMAN SYNTHESIS"})
        self.assertEqual(result["metadata"]["aggregate_rankings"][0]["model"], "m/beta")
        # 3 stage-1 calls, 3 stage-2 calls, 1 chairman call
        self.assertEqual(len(client.calls), 7)
        self.assertEqual(client.calls[-1][0], CHAIRMAN)

    def test_graceful_degradation(self):
        client = FakeClient(fail={"m/gamma"},
                            rankings={m: ["Response B", "Response A"] for m in MODELS})
        result = council.run_full_council(client, make_config(), "Q")
        self.assertEqual([r["model"] for r in result["stage1"]], ["m/alpha", "m/beta"])
        self.assertEqual(set(result["metadata"]["label_to_model"]), {"Response A", "Response B"})
        self.assertEqual(result["metadata"]["aggregate_rankings"][0]["model"], "m/beta")

    def test_all_fail_raises_unavailable(self):
        client = FakeClient(fail=set(MODELS))
        with self.assertRaises(council.CouncilError) as ctx:
            council.run_full_council(client, make_config(), "Q")
        self.assertEqual(ctx.exception.exit_code, council.EXIT_UNAVAILABLE)
        self.assertIn("simulated", str(ctx.exception))

    def test_chairman_failure_message(self):
        result = council.run_full_council(FakeClient(chairman_fails=True), make_config(), "Q")
        self.assertIn("Unable to generate final synthesis", result["stage3"]["response"])


class FakeHTTPResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class OpenRouterClientTests(unittest.TestCase):
    def test_request_shape_and_parse(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse(json.dumps(
                {"choices": [{"message": {"content": "مرحبا", "reasoning_details": None}}]}).encode())

        client = council.OpenRouterClient("test-key", "https://example.invalid/api", 7)
        with mock.patch.object(council.urllib.request, "urlopen", fake_urlopen):
            resp = client.query_model("m/alpha", [{"role": "user", "content": "hi"}])
        self.assertEqual(resp["content"], "مرحبا")
        self.assertEqual(captured["url"], "https://example.invalid/api")
        self.assertEqual(captured["auth"], "Bearer test-key")
        self.assertEqual(captured["body"]["model"], "m/alpha")
        self.assertEqual(captured["timeout"], 7)

    def test_network_error_returns_none(self):
        client = council.OpenRouterClient("k")
        with mock.patch.object(council.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("no route")), redirect_stderr(io.StringIO()):
            self.assertIsNone(client.query_model("m/alpha", []))
        self.assertIn("network error", client.errors["m/alpha"])

    def test_http_error_returns_none(self):
        client = council.OpenRouterClient("k")
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        with mock.patch.object(council.urllib.request, "urlopen", side_effect=err), \
                redirect_stderr(io.StringIO()):
            self.assertIsNone(client.query_model("m/alpha", []))
        self.assertEqual(client.errors["m/alpha"], "HTTP 401")

    def test_parallel_preserves_order(self):
        client = council.OpenRouterClient("k")
        with mock.patch.object(client, "query_model", side_effect=lambda m, msgs: {"content": m}):
            out = client.query_models_parallel(MODELS, [])
        self.assertEqual(list(out), MODELS)


class FormatTests(unittest.TestCase):
    def _result(self, question):
        client = FakeClient(rankings={m: ["Response B", "Response A", "Response C"] for m in MODELS})
        return council.run_full_council(client, make_config(), question)

    def test_english_markdown_sections(self):
        md = council.format_markdown(self._result("Which is better?"))
        order = ["# LLM Council", "## Stage 1", "## Stage 2", "## Aggregate ranking", "## Stage 3"]
        positions = [md.index(h) for h in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("**m/beta**", md)  # de-anonymized for display
        self.assertIn("CHAIRMAN SYNTHESIS", md)
        self.assertIn("| 1 | m/beta | 1.0 | 3 |", md)

    def test_arabic_detection_and_headings(self):
        self.assertEqual(council.detect_language("ما هو أفضل كتاب في التاريخ؟"), "ar")
        self.assertEqual(council.detect_language("What is the best book?"), "en")
        self.assertEqual(council.detect_language("123"), "en")
        md = council.format_markdown(self._result("ما هو أفضل كتاب في التاريخ؟"))
        self.assertIn("مجلس النماذج اللغوية", md)
        self.assertIn("المرحلة 3", md)

    def test_lang_override(self):
        md = council.format_markdown(self._result("Hello"), lang="ar")
        self.assertIn("المرحلة 1", md)


class ConfigTests(unittest.TestCase):
    def _write(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_bundled_config_is_valid(self):
        cfg = council.load_config(str(SKILL_DIR / "config.json"))
        self.assertGreaterEqual(len(cfg["council_models"]), 2)
        self.assertTrue(cfg["chairman_model"])

    def test_rejects_api_key_in_config(self):
        path = self._write({"council_models": ["a"], "chairman_model": "a", "api_key": "x"})
        with self.assertRaises(council.CouncilError):
            council.load_config(path)

    def test_rejects_empty_models(self):
        with self.assertRaises(council.CouncilError):
            council.load_config(self._write({"council_models": [], "chairman_model": "a"}))

    def test_defaults_applied(self):
        cfg = council.load_config(self._write({"council_models": ["a"], "chairman_model": "a"}))
        self.assertEqual(cfg["api_url"], council.DEFAULT_API_URL)


class CLITests(unittest.TestCase):
    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = council.main(argv, client_factory=lambda key, url, t: FakeClient())
        return code, out.getvalue(), err.getvalue()

    def test_missing_key_exits_2(self):
        code, _, err = self.run_main(["Q?"], {})
        self.assertEqual(code, council.EXIT_NO_KEY)
        self.assertIn("simulated", err)

    def test_check(self):
        code, out, _ = self.run_main(["--check"], {"OPENROUTER_API_KEY": "secret-value"})
        self.assertEqual(code, 0)
        self.assertNotIn("secret-value", out)

    def test_full_run_with_overrides_and_json(self):
        code, out, _ = self.run_main(
            ["--json", "--models", "m/alpha,m/beta", "--chairman", CHAIRMAN, "Q?"],
            {"OPENROUTER_API_KEY": "k"})
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([r["model"] for r in data["stage1"]], ["m/alpha", "m/beta"])
        self.assertEqual(data["stage3"]["response"], "CHAIRMAN SYNTHESIS")

    def test_question_file_arabic(self):
        with tempfile.TemporaryDirectory() as d:
            q = Path(d) / "q.txt"
            q.write_text("ما معنى الحياة؟", encoding="utf-8")
            outfile = Path(d) / "out.md"
            code, out, _ = self.run_main(["--question-file", str(q), "--output", str(outfile)],
                                         {"OPENROUTER_API_KEY": "k"})
            self.assertEqual(code, 0)
            self.assertIn("ما معنى الحياة؟", out)
            self.assertIn("المرحلة 2", outfile.read_text(encoding="utf-8"))

    def test_no_question(self):
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            code, _, _ = self.run_main([], {"OPENROUTER_API_KEY": "k"})
        self.assertEqual(code, council.EXIT_USAGE)


class SkillPackageTests(unittest.TestCase):
    def test_skill_md_frontmatter(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        front = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL).group(1)
        self.assertIn("name: llm-council", front)
        self.assertIn("description:", front)
        for needed in ("Simulated mode", "Stage 1", "Stage 2", "Stage 3", "FINAL RANKING", "OPENROUTER_API_KEY"):
            self.assertIn(needed, text)

    def test_no_hardcoded_keys_in_skill(self):
        for path in SKILL_DIR.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".md", ".json"}:
                content = path.read_text(encoding="utf-8")
                for pattern in build_skill.SECRET_PATTERNS:
                    self.assertIsNone(pattern.search(content), f"secret-like string in {path}")

    def test_build_zip(self):
        with tempfile.TemporaryDirectory() as d:
            out = build_skill.build(Path(d) / "llm-council.zip")
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
                self.assertIn("llm-council/SKILL.md", names)
                self.assertIn("llm-council/scripts/council.py", names)
                self.assertIn("llm-council/config.json", names)
                self.assertTrue(all(n.startswith("llm-council/") for n in names))
                self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))
                json.loads(zf.read("llm-council/config.json"))

    def test_build_rejects_secret(self):
        with tempfile.TemporaryDirectory() as d:
            skill = Path(d) / "llm-council"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: llm-council\ndescription: test skill\n---\nbody\n")
            (skill / "leak.json").write_text('{"k": "sk-or-v1-' + "a" * 40 + '"}')
            with self.assertRaises(SystemExit):
                build_skill.build(Path(d) / "x.zip", skill_dir=skill)


if __name__ == "__main__":
    unittest.main()
