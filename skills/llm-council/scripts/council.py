#!/usr/bin/env python3
"""LLM Council - self-contained runner for the llm-council Agent Skill.

Ports the 3-stage workflow of karpathy/llm-council (backend/council.py):

  Stage 1  First opinions   - every council model answers the question.
  Stage 2  Peer review      - every council model ranks the *anonymized*
                              answers ("Response A", "Response B", ...).
  Stage 3  Synthesis        - the chairman model writes the final answer
                              from all answers + all peer reviews.

Uses only the Python standard library (no pip install needed).
The OpenRouter key is read from the OPENROUTER_API_KEY environment variable
and is never written to disk or printed.

Exit codes:
  0  success
  1  bad usage / bad config
  2  OPENROUTER_API_KEY is not set          -> use simulated mode
  3  no council model could be reached      -> use simulated mode
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config.json"
DEFAULT_API_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY_ENV = "OPENROUTER_API_KEY"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_KEY = 2
EXIT_UNAVAILABLE = 3


class CouncilError(Exception):
    """Raised when the council cannot run; carries an exit code."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load council config. Precedence: explicit path > $LLM_COUNCIL_CONFIG > bundled config.json."""
    config_path = Path(path or os.environ.get("LLM_COUNCIL_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError:
        raise CouncilError(f"Config file not found: {config_path}", EXIT_USAGE)
    except json.JSONDecodeError as exc:
        raise CouncilError(f"Invalid JSON in {config_path}: {exc}", EXIT_USAGE)

    if "api_key" in config or "openrouter_api_key" in config:
        raise CouncilError(
            "Do not put API keys in the config file. Set the OPENROUTER_API_KEY "
            "environment variable instead.",
            EXIT_USAGE,
        )

    models = config.get("council_models")
    if not isinstance(models, list) or not models or not all(isinstance(m, str) and m for m in models):
        raise CouncilError("config 'council_models' must be a non-empty list of model ids", EXIT_USAGE)
    if len(models) > 26:
        raise CouncilError("config 'council_models' supports at most 26 models (labels A-Z)", EXIT_USAGE)
    if not isinstance(config.get("chairman_model"), str) or not config["chairman_model"]:
        raise CouncilError("config 'chairman_model' must be a model id string", EXIT_USAGE)

    config.setdefault("api_url", DEFAULT_API_URL)
    config.setdefault("timeout_seconds", 120)
    return config


# --------------------------------------------------------------------------
# OpenRouter client (port of backend/openrouter.py, stdlib only)
# --------------------------------------------------------------------------

QueryFn = Callable[[str, List[Dict[str, str]]], Optional[Dict[str, Any]]]


class OpenRouterClient:
    def __init__(self, api_key: str, api_url: str = DEFAULT_API_URL, timeout: float = 120.0):
        self.api_key = api_key
        self.api_url = api_url
        self.timeout = timeout
        self.errors: Dict[str, str] = {}

    def query_model(self, model: str, messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        """Query one model. Returns {'content', 'reasoning_details'} or None on failure."""
        payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-Title": "LLM Council Skill",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            message = data["choices"][0]["message"]
            return {
                "content": message.get("content") or "",
                "reasoning_details": message.get("reasoning_details"),
            }
        except urllib.error.HTTPError as exc:
            self.errors[model] = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.errors[model] = f"network error: {getattr(exc, 'reason', exc)}"
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self.errors[model] = f"unexpected response: {exc}"
        print(f"[llm-council] Error querying model {model}: {self.errors[model]}", file=sys.stderr)
        return None

    def query_models_parallel(
        self, models: List[str], messages: List[Dict[str, str]]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Query several models concurrently; keeps the input order."""
        with ThreadPoolExecutor(max_workers=max(1, len(models))) as pool:
            results = list(pool.map(lambda m: self.query_model(m, messages), models))
        return dict(zip(models, results))


# --------------------------------------------------------------------------
# Prompts (identical to backend/council.py, plus a language instruction)
# --------------------------------------------------------------------------

LANGUAGE_RULE = (
    "Write your answer in the same language as the user's question "
    "(for example, if the question is in Arabic, answer in Arabic)."
)


def build_stage1_messages(user_query: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": LANGUAGE_RULE},
        {"role": "user", "content": user_query},
    ]


def build_ranking_prompt(user_query: str, stage1_results: List[Dict[str, Any]]) -> Tuple[str, Dict[str, str]]:
    labels = [chr(65 + i) for i in range(len(stage1_results))]
    label_to_model = {f"Response {label}": r["model"] for label, r in zip(labels, stage1_results)}
    responses_text = "\n\n".join(
        f"Response {label}:\n{r['response']}" for label, r in zip(labels, stage1_results)
    )
    prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

Write your evaluations in the same language as the question, but keep the labels "Response A", "Response B", etc. and the "FINAL RANKING:" header exactly in English.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""
    return prompt, label_to_model


def build_chairman_prompt(
    user_query: str, stage1_results: List[Dict[str, Any]], stage2_results: List[Dict[str, Any]]
) -> str:
    stage1_text = "\n\n".join(f"Model: {r['model']}\nResponse: {r['response']}" for r in stage1_results)
    stage2_text = "\n\n".join(f"Model: {r['model']}\nRanking: {r['ranking']}" for r in stage2_results)
    return f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

{LANGUAGE_RULE}

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""


# --------------------------------------------------------------------------
# Ranking parsing / aggregation (verbatim logic from backend/council.py)
# --------------------------------------------------------------------------

def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """Extract the ordered labels from the 'FINAL RANKING:' section."""
    if "FINAL RANKING:" in ranking_text:
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            numbered = re.findall(r"\d+\.\s*Response [A-Z]", ranking_section)
            if numbered:
                return [re.search(r"Response [A-Z]", m).group() for m in numbered]
            return re.findall(r"Response [A-Z]", ranking_section)
    return re.findall(r"Response [A-Z]", ranking_text)


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]], label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Average rank position per model across all peer reviews (lower is better)."""
    positions: Dict[str, List[int]] = defaultdict(list)
    for ranking in stage2_results:
        for pos, label in enumerate(parse_ranking_from_text(ranking["ranking"]), start=1):
            if label in label_to_model:
                positions[label_to_model[label]].append(pos)
    aggregate = [
        {"model": model, "average_rank": round(sum(p) / len(p), 2), "rankings_count": len(p)}
        for model, p in positions.items()
        if p
    ]
    aggregate.sort(key=lambda x: x["average_rank"])
    return aggregate


# --------------------------------------------------------------------------
# The three stages
# --------------------------------------------------------------------------

def stage1_collect_responses(client, models: List[str], user_query: str) -> List[Dict[str, Any]]:
    responses = client.query_models_parallel(models, build_stage1_messages(user_query))
    return [
        {"model": model, "response": resp.get("content", "")}
        for model, resp in responses.items()
        if resp is not None
    ]


def stage2_collect_rankings(
    client, models: List[str], user_query: str, stage1_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    prompt, label_to_model = build_ranking_prompt(user_query, stage1_results)
    responses = client.query_models_parallel(models, [{"role": "user", "content": prompt}])
    results = []
    for model, resp in responses.items():
        if resp is not None:
            text = resp.get("content", "")
            results.append({"model": model, "ranking": text, "parsed_ranking": parse_ranking_from_text(text)})
    return results, label_to_model


def stage3_synthesize_final(
    client, chairman: str, user_query: str,
    stage1_results: List[Dict[str, Any]], stage2_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    prompt = build_chairman_prompt(user_query, stage1_results, stage2_results)
    resp = client.query_model(chairman, [{"role": "user", "content": prompt}])
    if resp is None:
        return {"model": chairman, "response": "Error: Unable to generate final synthesis."}
    return {"model": chairman, "response": resp.get("content", "")}


def run_full_council(client, config: Dict[str, Any], user_query: str) -> Dict[str, Any]:
    models = config["council_models"]
    stage1 = stage1_collect_responses(client, models, user_query)
    if not stage1:
        details = "; ".join(f"{m}: {e}" for m, e in getattr(client, "errors", {}).items())
        raise CouncilError(
            "All council models failed to respond" + (f" ({details})" if details else "")
            + ". Fall back to simulated mode.",
            EXIT_UNAVAILABLE,
        )
    stage2, label_to_model = stage2_collect_rankings(client, models, user_query, stage1)
    aggregate = calculate_aggregate_rankings(stage2, label_to_model)
    stage3 = stage3_synthesize_final(client, config["chairman_model"], user_query, stage1, stage2)
    return {
        "question": user_query,
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
        "metadata": {"label_to_model": label_to_model, "aggregate_rankings": aggregate},
    }


# --------------------------------------------------------------------------
# Output formatting (English / Arabic headings)
# --------------------------------------------------------------------------

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

STRINGS = {
    "en": {
        "title": "LLM Council",
        "mode": "Mode: real models via OpenRouter",
        "members": "Council",
        "question": "Question",
        "stage1": "Stage 1 — Council members' answers",
        "stage2": "Stage 2 — Anonymous peer review & rankings",
        "stage2_note": "Reviewers saw only anonymous labels; model names are shown in bold for readability.",
        "reviewer": "Reviewer",
        "extracted": "Extracted ranking",
        "aggregate": "Aggregate ranking (average position, lower is better)",
        "rank": "Rank", "model": "Model", "avg": "Average position", "votes": "Votes",
        "stage3": "Stage 3 — Chairman's final answer",
        "chairman": "Chairman",
        "none": "(no ranking could be parsed)",
    },
    "ar": {
        "title": "مجلس النماذج اللغوية",
        "mode": "الوضع: نماذج حقيقية عبر OpenRouter",
        "members": "الأعضاء",
        "question": "السؤال",
        "stage1": "المرحلة 1 — إجابات أعضاء المجلس",
        "stage2": "المرحلة 2 — المراجعة المجهولة والترتيب",
        "stage2_note": "رأى المراجعون تسميات مجهولة فقط؛ أسماء النماذج بالخط العريض للتوضيح فقط.",
        "reviewer": "المُراجِع",
        "extracted": "الترتيب المستخرج",
        "aggregate": "الترتيب الإجمالي (متوسط المركز، الأقل أفضل)",
        "rank": "المركز", "model": "النموذج", "avg": "متوسط المركز", "votes": "عدد الأصوات",
        "stage3": "المرحلة 3 — الإجابة النهائية لرئيس المجلس",
        "chairman": "رئيس المجلس",
        "none": "(تعذّر استخراج الترتيب)",
    },
}


def detect_language(text: str) -> str:
    """'ar' if the text is predominantly Arabic script, else 'en'."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    arabic = sum(1 for c in letters if ARABIC_RE.match(c))
    return "ar" if arabic / len(letters) >= 0.3 else "en"


def format_markdown(result: Dict[str, Any], lang: Optional[str] = None) -> str:
    lang = lang or detect_language(result["question"])
    t = STRINGS.get(lang, STRINGS["en"])
    label_to_model = result["metadata"]["label_to_model"]

    def deanon(text: str) -> str:
        return re.sub(r"Response [A-Z](?![A-Za-z])",
                      lambda m: f"**{label_to_model[m.group()]}**" if m.group() in label_to_model else m.group(),
                      text)

    members = ", ".join(r["model"] for r in result["stage1"])
    out = [f"# {t['title']}", "",
           f"_{t['mode']} · {t['members']}: {members} · {t['chairman']}: {result['stage3']['model']}_", "",
           f"**{t['question']}:** {result['question']}", "", f"## {t['stage1']}", ""]
    model_to_label = {v: k for k, v in label_to_model.items()}
    for r in result["stage1"]:
        out += [f"### {r['model']} ({model_to_label.get(r['model'], '')})", "", r["response"].strip(), ""]

    out += [f"## {t['stage2']}", "", f"_{t['stage2_note']}_", ""]
    for r in result["stage2"]:
        out += [f"### {t['reviewer']}: {r['model']}", "", deanon(r["ranking"].strip()), ""]
        if r["parsed_ranking"]:
            ranked = [f"{i}. {label_to_model.get(lbl, lbl)} ({lbl})" for i, lbl in enumerate(r["parsed_ranking"], 1)]
            out += [f"**{t['extracted']}:**", ""] + ranked + [""]
        else:
            out += [f"**{t['extracted']}:** {t['none']}", ""]

    out += [f"## {t['aggregate']}", "", f"| {t['rank']} | {t['model']} | {t['avg']} | {t['votes']} |",
            "|---|---|---|---|"]
    for i, a in enumerate(result["metadata"]["aggregate_rankings"], 1):
        out.append(f"| {i} | {a['model']} | {a['average_rank']} | {a['rankings_count']} |")
    out += ["", f"## {t['stage3']}", "", f"**{t['chairman']}:** {result['stage3']['model']}", "",
            result["stage3"]["response"].strip(), ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the 3-stage LLM Council via OpenRouter.")
    p.add_argument("question", nargs="?", help="The question (or use --question-file / stdin).")
    p.add_argument("--question-file", help="Read the question from a UTF-8 file (best for Arabic/multiline).")
    p.add_argument("--config", help="Path to a config JSON (default: bundled config.json).")
    p.add_argument("--models", help="Comma-separated council model ids (overrides config).")
    p.add_argument("--chairman", help="Chairman model id (overrides config).")
    p.add_argument("--json", action="store_true", help="Print the raw JSON result instead of Markdown.")
    p.add_argument("--lang", choices=["en", "ar"], help="Force output heading language.")
    p.add_argument("--output", help="Also write the output to this file.")
    p.add_argument("--check", action="store_true", help="Only check that an API key is configured.")
    return p


def read_question(args) -> str:
    if args.question_file:
        return Path(args.question_file).read_text(encoding="utf-8").strip()
    if args.question:
        return args.question.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def main(argv: Optional[List[str]] = None, client_factory=OpenRouterClient) -> int:
    args = build_parser().parse_args(argv)
    try:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        config = load_config(args.config)
        if args.models:
            config["council_models"] = [m.strip() for m in args.models.split(",") if m.strip()]
        if args.chairman:
            config["chairman_model"] = args.chairman.strip()

        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            raise CouncilError(
                f"{API_KEY_ENV} is not set. Use the simulated council mode described in SKILL.md.",
                EXIT_NO_KEY,
            )
        if args.check:
            print(f"OK: {API_KEY_ENV} is set; council = {', '.join(config['council_models'])}; "
                  f"chairman = {config['chairman_model']}")
            return EXIT_OK

        question = read_question(args)
        if not question:
            raise CouncilError("No question given.", EXIT_USAGE)

        client = client_factory(api_key, config["api_url"], float(config["timeout_seconds"]))
        result = run_full_council(client, config, question)
    except CouncilError as exc:
        print(f"[llm-council] {exc}", file=sys.stderr)
        return exc.exit_code

    text = json.dumps(result, ensure_ascii=False, indent=2) if args.json else format_markdown(result, args.lang)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
