---
name: llm-council
description: Convene an "LLM Council" to answer a question through a 3-stage deliberation - several models each answer independently, then anonymously peer-review and rank each other's answers, then a chairman model synthesizes the final answer. Use when the user asks for an LLM council, a panel/committee of models, multiple AI opinions, a second opinion from several models, a debate or cross-review between models, "ask several models and compare", or wants a high-stakes question answered by consensus; also for Arabic requests such as "مجلس النماذج" or "مجلس الذكاء الاصطناعي" or "اسأل عدة نماذج" or "آراء متعددة". Runs real models via OpenRouter when OPENROUTER_API_KEY is set and network is available; otherwise Claude simulates the council itself following the same 3 stages. Answers in the user's language (Arabic supported).
---

# LLM Council

A port of karpathy/llm-council as a skill. The workflow is always the same three stages:

1. **Stage 1 – First opinions.** Every council member answers the question independently.
2. **Stage 2 – Anonymous peer review.** The answers are relabelled `Response A`, `Response B`, … (identities hidden). Every member evaluates each response (strengths and weaknesses), then gives a `FINAL RANKING:` from best to worst. Ranks are averaged into an **aggregate ranking** (lower average position = better).
3. **Stage 3 – Chairman synthesis.** The chairman reads all answers and all reviews and writes one final answer that represents the council's collective wisdom.

**Language rule:** detect the language of the user's question and write every answer, review, justification and the final synthesis in that language (Arabic question → Arabic output, with Arabic section headings). Keep only the literal labels `Response A`… and `FINAL RANKING:` in English inside Stage 2 reviews so rankings stay parseable.

## Step 0 – Pick the mode

- **Real mode** if you can run shell commands, `OPENROUTER_API_KEY` is set, and the network is reachable (typically Claude Code).
- **Simulated mode** otherwise (typically claude.ai, or no key / no network). Also use it if the user explicitly asks for a simulated council.

Quick check (real mode candidates only):

```bash
python3 <skill-dir>/scripts/council.py --check
```

`<skill-dir>` is the folder containing this SKILL.md (e.g. `~/.claude/skills/llm-council`). Exit code `2` = no API key → simulated mode. Never ask the user to paste an API key into the chat, and never write a key into any file.

## Real mode (OpenRouter)

Write the question to a UTF-8 file (safest for Arabic and multi-line text), then run:

```bash
python3 <skill-dir>/scripts/council.py --question-file /tmp/council_question.txt --output /tmp/council_result.md
```

Options: `--models a/x,b/y` and `--chairman c/z` override the council for one run; `--config path.json` uses another config; `--json` prints raw JSON; `--lang ar|en` forces heading language. Defaults live in `<skill-dir>/config.json` (`council_models`, `chairman_model`, `api_url`, `timeout_seconds`) – edit that file to change the council permanently.

The script prints the full report in the output format below. Show it to the user (you may trim very long Stage 2 reviews to their key points, but keep every member's answer, every ranking, the aggregate table and the full chairman answer). If the script exits with code `3` (no model reachable), tell the user in one line and switch to simulated mode. If some models fail, the script continues with the rest – mention which ones were missing.

## Simulated mode (no API key / no network)

You run the whole council yourself. Do it honestly and visibly – tell the user at the top that this is a **simulated council** (Claude role-playing distinct members), not real external models.

**Members.** Use 4 members unless the user asks otherwise (3–6 allowed). Give each a distinct, named perspective so the answers genuinely differ, for example:

| Member | Perspective |
|---|---|
| The Analyst | rigorous, evidence-first, numbers and definitions, flags uncertainty |
| The Pragmatist | practical, actionable steps, costs/trade-offs, real-world constraints |
| The Skeptic | challenges assumptions, looks for risks, edge cases and counter-arguments |
| The Visionary | big picture, creative alternatives, long-term and second-order effects |

Pick perspectives that fit the question (e.g. for medical/legal/finance include a cautious domain expert). If the user names models ("simulate GPT, Gemini, Grok"), you may adopt those names but say they are imitations.

**Stage 1.** Write each member's answer *independently* – as if the member had not seen the others. Vary structure, emphasis and conclusions; do not make them paraphrases of one another. Each member may make different (even wrong-ish but plausible) choices; that is what review is for. Keep each answer focused (roughly 80–250 words unless the question needs more).

**Stage 2.** Shuffle the answers and assign anonymous labels `Response A`, `Response B`, … (the label order must NOT simply follow the member order; record the mapping). Then, for **each member as reviewer**, in that member's voice:
- evaluate every response in 1–2 sentences (what it does well / poorly) – this is the short justification;
- end with exactly:
  ```
  FINAL RANKING:
  1. Response X
  2. Response Y
  ...
  ```
  Judge on accuracy and insight only. Reviewers must not favour a response because they guess it is their own; a reviewer may rank its own response first only if it is genuinely the best.

**Aggregate.** For each response, average its position across all reviewers (round to 2 decimals) and sort ascending. Show the arithmetic table; then de-anonymize (reveal which member wrote which label).

**Stage 3.** As the **Chairman** (a neutral role, distinct from all members), synthesize one final answer: build on the highest-ranked content, fix errors the reviewers found, resolve disagreements explicitly, and keep valuable minority points. The final answer must stand on its own – the user should be able to read only this section.

## Output format (both modes)

Use these sections in this order (translate headings into the user's language; Arabic headings shown in brackets):

1. `# LLM Council` [`# مجلس النماذج اللغوية`] – one line: mode (real/simulated), members, chairman.
2. `## Stage 1 — Council members' answers` [`## المرحلة 1 — إجابات أعضاء المجلس`] – one `###` subsection per member with their full answer.
3. `## Stage 2 — Anonymous peer review & rankings` [`## المرحلة 2 — المراجعة المجهولة والترتيب`] – one `###` per reviewer: short justification per response + their `FINAL RANKING`. Model/member names may be shown in **bold** next to labels for readability; note that reviewers only saw anonymous labels.
4. `## Aggregate ranking` [`## الترتيب الإجمالي`] – table: rank | member/model | average position | votes, plus the label→member mapping.
5. `## Stage 3 — Chairman's final answer` [`## المرحلة 3 — الإجابة النهائية لرئيس المجلس`] – the synthesized answer.

See `references/output-example.md` for a complete short example in English and Arabic.

## Notes

- For quick factual questions with one obvious answer, the council is overkill – still run it if the user asked, but keep each stage short.
- Do not reveal or log the API key; the script only reads it from the `OPENROUTER_API_KEY` environment variable.
- Real mode costs OpenRouter credits: (N members × 2 calls) + 1 chairman call per question.
