# LLM Council — Claude Agent Skill

[English](#english) · [العربية](#العربية)

---

## English

`llm-council` packages the 3-stage workflow of this repo as a Claude Agent Skill:

1. **First opinions** — every council model answers the question independently.
2. **Anonymous peer review** — answers are relabelled `Response A, B, C…`; each member evaluates and ranks them; ranks are averaged into an aggregate ranking.
3. **Chairman synthesis** — the chairman model writes the final answer from all answers and reviews.

It works in two modes:

| Mode | When | What happens |
|---|---|---|
| **Real** | `OPENROUTER_API_KEY` is set and the network is reachable (usually Claude Code) | `scripts/council.py` queries the real models on OpenRouter |
| **Simulated** | No key or no network (usually claude.ai) | Claude role-plays distinct council members, does the anonymous review and ranking, then the chairman synthesis — same 3 stages, same output |

Output (both modes): each member's answer → each reviewer's rankings with short justifications → aggregate ranking table → chairman's final answer. Questions in Arabic are answered in Arabic (and any other language in that language).

### Skill layout

```
skills/llm-council/
├── SKILL.md                     # instructions + trigger description
├── config.json                  # council models, chairman, API URL, timeout
├── scripts/council.py           # self-contained runner (Python 3.8+, stdlib only)
└── references/output-example.md # format examples (English + Arabic)
```

### Build the zip

```bash
python3 scripts/build_skill.py
```

This validates `SKILL.md`, refuses to package anything that looks like an API key, and writes `dist/llm-council.zip` (a single `llm-council/` folder inside).

### Install in claude.ai

1. Build the zip (above) or download it.
2. In claude.ai open **Settings → Capabilities** and make sure **Code execution and file creation** is enabled (skills require it).
3. In the **Skills** section click **Upload skill** and pick `llm-council.zip`.
4. Turn the skill on, then ask e.g. *"Ask the LLM council: should I learn Rust or Go?"*.

claude.ai usually has no OpenRouter key or outbound access, so the skill runs in **simulated mode**. Don't paste your API key into the chat.

### Install in Claude Code

Personal (all projects):

```bash
python3 scripts/build_skill.py
```

```bash
mkdir -p ~/.claude/skills && unzip -o dist/llm-council.zip -d ~/.claude/skills/
```

(Or copy the folder directly: `cp -r skills/llm-council ~/.claude/skills/`. For one project only, use `.claude/skills/` inside that project.)

Enable real mode by exporting your key in your shell profile (`~/.bashrc`, `~/.zshrc`) — never commit it:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

Restart Claude Code, then ask *"Run the LLM council on: …"*. You can also run the script yourself:

```bash
python3 ~/.claude/skills/llm-council/scripts/council.py --check
```

```bash
python3 ~/.claude/skills/llm-council/scripts/council.py "What is the best way to learn statistics?"
```

### Configure the council

Edit `config.json` (or pass `--config my.json`, or set `LLM_COUNCIL_CONFIG`):

```json
{
  "council_models": ["openai/gpt-5.1", "google/gemini-3-pro-preview", "anthropic/claude-sonnet-4.5", "x-ai/grok-4"],
  "chairman_model": "google/gemini-3-pro-preview",
  "api_url": "https://openrouter.ai/api/v1/chat/completions",
  "timeout_seconds": 120
}
```

One-off overrides: `--models a/x,b/y --chairman c/z`. Other flags: `--question-file`, `--json`, `--output`, `--lang ar|en`. The config loader rejects any `api_key` field — the key only comes from the environment.

Exit codes: `0` ok · `1` usage/config error · `2` no API key · `3` no model reachable (codes 2 and 3 tell Claude to switch to simulated mode).

### Tests

```bash
python3 -m unittest discover -s tests -v
```

The OpenRouter API is fully mocked; no key or network needed.

---

<div dir="rtl">

## العربية

مهارة `llm-council` تحوّل سير العمل ذي المراحل الثلاث في هذا المستودع إلى مهارة (Agent Skill) لـ Claude:

1. **الآراء الأولى** — كل نموذج في المجلس يجيب عن السؤال بشكل مستقل.
2. **المراجعة المجهولة** — تُعاد تسمية الإجابات إلى `Response A, B, C…` دون أسماء النماذج، ثم يقيّم كل عضو الإجابات ويرتّبها، ويُحسب متوسط المراكز للحصول على الترتيب الإجمالي.
3. **تلخيص رئيس المجلس** — يكتب النموذج الرئيس الإجابة النهائية اعتماداً على كل الإجابات والمراجعات.

تعمل المهارة بوضعين:

| الوضع | متى | ماذا يحدث |
|---|---|---|
| **حقيقي** | عند وجود المتغير `OPENROUTER_API_KEY` واتصال بالشبكة (غالباً في Claude Code) | يستدعي السكربت `scripts/council.py` النماذج الحقيقية عبر OpenRouter |
| **مُحاكى** | عند عدم وجود مفتاح أو شبكة (غالباً في claude.ai) | يؤدي Claude أدوار أعضاء مختلفين، ثم المراجعة المجهولة والترتيب، ثم تلخيص الرئيس — بنفس المراحل الثلاث ونفس شكل المخرجات |

شكل المخرجات في الوضعين: إجابة كل عضو ← ترتيب كل مراجع مع مبررات قصيرة ← جدول الترتيب الإجمالي ← الإجابة النهائية لرئيس المجلس. الأسئلة العربية يُجاب عنها بالعربية (وأي لغة أخرى بلغتها).

### بناء ملف zip

</div>

```bash
python3 scripts/build_skill.py
```

<div dir="rtl">

يتحقق السكربت من ملف `SKILL.md`، ويرفض التغليف إذا وجد ما يشبه مفتاح API، وينتج الملف `dist/llm-council.zip` (يحتوي مجلداً واحداً `llm-council/`).

### التثبيت في claude.ai

1. ابنِ ملف zip (كما في الأعلى) أو نزّله.
2. في claude.ai افتح **Settings ← Capabilities** وتأكد من تفعيل **Code execution and file creation** (المهارات تتطلبه).
3. في قسم **Skills** اضغط **Upload skill** واختر الملف `llm-council.zip`.
4. فعّل المهارة ثم اسأل مثلاً: *«اسأل مجلس النماذج: هل أتعلم Rust أم Go؟»*.

غالباً لا يتوفر في claude.ai مفتاح OpenRouter ولا اتصال خارجي، لذا ستعمل المهارة **بالوضع المُحاكى**. لا تلصق مفتاح API في المحادثة.

### التثبيت في Claude Code

للاستخدام الشخصي في كل المشاريع:

</div>

```bash
python3 scripts/build_skill.py
```

```bash
mkdir -p ~/.claude/skills && unzip -o dist/llm-council.zip -d ~/.claude/skills/
```

<div dir="rtl">

(أو انسخ المجلد مباشرة: `cp -r skills/llm-council ~/.claude/skills/`. ولمشروع واحد فقط استخدم المجلد `.claude/skills/` داخل المشروع.)

لتفعيل الوضع الحقيقي صدّر المفتاح في ملف إعدادات الطرفية (`~/.bashrc` أو `~/.zshrc`) — ولا تضعه أبداً في المستودع:

</div>

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

<div dir="rtl">

أعد تشغيل Claude Code ثم اطلب: *«شغّل مجلس النماذج على: …»*. ويمكنك تشغيل السكربت بنفسك:

</div>

```bash
python3 ~/.claude/skills/llm-council/scripts/council.py --check
```

```bash
python3 ~/.claude/skills/llm-council/scripts/council.py "ما أفضل طريقة لتعلّم الإحصاء؟"
```

<div dir="rtl">

### إعداد المجلس

عدّل الملف `config.json` (أو استخدم `--config my.json` أو المتغير `LLM_COUNCIL_CONFIG`) لتغيير قائمة النماذج `council_models` والنموذج الرئيس `chairman_model`. وللتغيير لمرة واحدة: `--models a/x,b/y --chairman c/z`. خيارات أخرى: `--question-file` (الأفضل للنصوص العربية) و`--json` و`--output` و`--lang ar|en`. يرفض السكربت أي حقل `api_key` في ملف الإعداد — المفتاح يُقرأ من متغير البيئة فقط.

رموز الخروج: `0` نجاح · `1` خطأ في الاستخدام أو الإعداد · `2` لا يوجد مفتاح · `3` تعذّر الوصول لأي نموذج (الرمزان 2 و3 يخبران Claude بالانتقال إلى الوضع المُحاكى).

### الاختبارات

</div>

```bash
python3 -m unittest discover -s tests -v
```

<div dir="rtl">

واجهة OpenRouter مُحاكاة بالكامل في الاختبارات؛ لا حاجة لمفتاح أو اتصال بالشبكة.

</div>
