# Triody AI

A finance copilot for small businesses. Entry for the 2026 Shenzhen International FinTech Competition (FinTechathon), International Track, Topic E: SME Finance Copilot.

Triody watches a shop's cash, warns the owner before money runs short, explains why, answers plain questions about the books, and finds financing the business is able to repay. It drafts the paperwork. It never moves money.

The name joins Tri, for our team of three and the agent's three jobs, with Ody, from Odyssey. Running a small business is a long trip, and Triody goes along with the owner.

All shops in `data/` are fictional and every number is synthetic. Product figures come from public sources checked on 14 October 2026. They are not quotes or offers.

## What it does

- Cash-flow alerts. A 60-day forecast with a probability range. The agent checks it on its own each time the app opens and warns the owner with a date, an amount and a probability.
- Business analysis. Sales, costs, margin and profit for any period, month by month, week by week and by day of the week, plus break-even, the biggest costs, and the reasons behind a change.
- Financing matching. It sizes the cash gap, compares ten UK options built from public sources (HMRC Time to Pay, Lloyds Bank, iwoca, Funding Circle, Start Up Loans and others, each with its source link and assumptions), rejects anything the business cannot afford, and re-runs the forecast to show what the best option changes.

The agent reads, analyses and drafts. Anything which changes state needs a one-time code. The code goes to the owner's device on a separate channel, never into the chat, and every step goes into a tamper-evident log.

The country lives in one file. The same code runs a UK café on the UK catalogue and a Shenzhen tea shop, priced in yuan, on a China catalogue (WeBank Weiyedai, a bank inclusive small business loan, tax payment deferral, supplier terms).

## Ask it anything about the books

Type the way you talk. Typos are fine. Some examples, with the answers the demo café gives:

- "sales today": £469 on Wednesday 14 October 2026, up 18% on the same day last week.
- "October sales": £6,999 for 1 to 14 October, down 11% on the same days of September.
- "what's my VAT bill": £6,800 due on 7 November 2026.
- "which supplier costs the most": Lothian Mill and Dairy, £15,282 in the last 90 days.
- "why did profit drop in September": sales fell by £5,717 and wages rose by £800.
- "can I afford a £3,000 coffee machine": not now. The expected low falls from -£1,194 to -£4,194.
- "what if sales drop 10%": the chance of going below zero rises from 66% to 91%.
- "is my business healthy": a seven-point check with a verdict.
- "how can I improve cash flow": ranked actions, each with the number behind it.
- "should I close on Mondays", "which was the worst week", "when is rent due", "any suspicious transactions?"

"Today" means the last day in the books, and the reply names the date. A period outside the books gets a plain reason, never a zero. When a question is unclear, Triody asks back and offers three questions to tap. Questions about data the books do not hold, such as customer numbers or stock levels, get a straight "not in your books".

## Use it

### On any phone, tablet or computer, with or without internet

The browser version in `webapp/` runs the same Python package inside the page through Pyodide, a build of CPython for WebAssembly. Every calculation happens on your device. Nothing goes to a server. The numbers match the computer version.

Open the published page once while online. After the first visit (about 14 MB), it opens and works with no connection. Install it as an app:

- Android, Windows, ChromeOS, Linux: tap Install app at the top of the page, or use the browser menu.
- iPhone and iPad: in Safari, tap Share, then Add to Home Screen.
- Mac: in Safari, choose File, then Add to Dock. In Chrome or Edge, use Install app.

No account and no sign-in. If other people use your device, open Account and turn on the lock: Face ID, fingerprint or Windows Hello in one tap, or a PIN.

To publish it with GitHub Pages:

1. Push the repository to GitHub as a public repository.
2. Open Settings, then Pages. Under Build and deployment, pick Deploy from a branch, branch `main`, folder `/ (root)`, and save.
3. After a minute or two, the app is at `https://sifatsiddiki.github.io/Triody-AI-SME-Copilot/`.

Add `?shop=d` to the address for the Shenzhen tea shop. A link at the top of the page switches between the two.

To try it on your own computer first:

```
python -m http.server 8080 --directory webapp
```

Then open http://localhost:8080. Double-clicking `webapp/index.html` does not work, because browsers block WebAssembly from local files.

After any change in `triody/` or `data/`, run `python tools/build_web.py` and commit `webapp/`. A test fails if you forget.

We tested it in Chromium at 390 px and 412 px phone widths and at desktop size, including offline use, the PIN lock and the passkey lock. We had no real iPhone, so check Safari on one before the demo. Old browsers without WebAssembly do not run it.

### On a computer, with the full dashboard

You need Python 3.11 or newer on Windows, macOS or Linux. No third-party packages. The tests pass on Python 3.11, 3.12 and 3.13.

- Windows: double-click `run_windows.bat`.
- macOS or Linux: double-click `run_mac.command`. The first time on a Mac, right-click it and choose Open, because macOS blocks downloaded scripts.

Each script runs the tests and opens the dashboard at http://127.0.0.1:8000. The computer running Triody never asks you to sign in.

### On your phone through the computer, over Wi-Fi

Type `y` when the script asks about your phone, or run `python -m triody serve --lan`. The dashboard then shows an address such as `http://192.168.1.20:8000` and a 6-digit code. Join the same Wi-Fi on your phone, open the address and type the code once. The phone stays signed in for 30 days. The Wi-Fi needs no internet. Sign out all other devices from the computer with one button.

## Commands

```
git clone https://github.com/sifatsiddiki/Triody-AI-SME-Copilot.git
cd Triody-AI-SME-Copilot

python -m unittest discover -s tests -t .   # 192 tests, about 90 seconds
python -m triody check                      # the agent's own daily check
python -m triody demo                       # scripted conversation in the terminal
python -m triody serve                      # dashboard at http://127.0.0.1:8000
python -m triody serve --lan                # also on phones on the same Wi-Fi
python -m triody serve --shop D             # the Shenzhen tea shop
python -m triody evidence                   # rebuild evidence/ and docs/EVALUATION.md
python -m triody llm-check                  # run against a real language model
python -m triody gen-data                   # rebuild the four synthetic shops
python tools/build_web.py                   # rebuild webapp/
python tools/anchor_audit.py digest         # print the audit chain head
```

On Windows, use `py -m` if `python` is not found. On a Mac, use `python3`. The data and the forecast use fixed seeds, so every run gives the same numbers.

## What the demo shows

The café has £9,200 today. A £6,800 VAT payment is due on 7 November and a £1,500 insurance renewal on 3 December. Sales are falling into winter, and a new hire pushed wages up.

- When the dashboard opens, the agent runs its daily check without being asked.
- The risk of dropping under the £2,500 buffer reaches 25% on 7 November, 24 days out. The expected low is -£1,194 on 7 December. The chance of going below zero within 60 days is 66%.
- The analysis finds September sales down 25.1% on August, wages up from 26% to 33% of sales since May, and break-even at about £458 a day against £493 forecast.
- The matcher sizes the gap at £5,000 and picks HMRC Time to Pay over 10 months, ahead of the Lloyds overdraft and iwoca. Six products are ruled out, each with a reason. The reply says plainly Time to Pay is not a loan and pays no money to the shop.
- Each payment is £518, 47% of the shop's average monthly free cash flow. Triody refuses any plan above 50%.
- Tested against the forecast, the plan lowers the 60-day chance of going below zero from 66% to 13%. A second test runs to the last of the 10 payments and shows the plan buys time without closing the longer gap on its own.
- The agent drafts the request. The approval code appears in the owner device panel, not in the chat. Nothing goes anywhere until the owner types it in.
- A request to transfer £9,000 is refused. A memo in the ledger which tries to give the agent orders is shown as data, ignored, and flagged by "any suspicious transactions?".
- The Shenzhen tea shop has a 52% chance of going below zero, and the agent refuses to recommend a loan because the shop's average free cash flow is negative.

## Layout

```
triody/
  config.py          every setting in one place
  synth.py           four synthetic shops (café for the demo, bike shop and florist held out, Shenzhen tea shop)
  ledger.py          read-only view of transactions
  forecast.py        sales model, recurring payments, Monte Carlo, self-calibration, backtest
  analysis.py        shortfall explanation and monthly business analysis
  intent.py          reads the owner's question: typos, period, intent
  reports.py         figures for any period, weeks and days of the week
  insights.py        bills due, health check, advice, what-if tests, unusual entries
  region.py          country settings read from the catalogue file
  catalog.json       ten UK financing options with sources and assumptions
  catalog_cn.json    four mainland China options with sources and assumptions
  eval_catalog.json  frozen catalogue used by the 30 labelled test cases
  financing.py       eligibility filters, scoring, repayment cap, stock rule
  policy.py          permission tiers, one-time codes, limits
  tools.py           tool registry with argument checks
  audit.py           hash-chained append-only log
  brain.py           rule-based planner and the language-model planner (two APIs)
  agent.py           policy gate, daily check, plan testing, drafts, confirmations, replies
  access.py          pairing codes and sessions for phones on the same Wi-Fi
  server.py          local dashboard server
  webapi.py          request handling shared by the server and the browser version
  web/               dashboard files, offline cache and icons
  eval_set.py        30 labelled financing cases plus held-out cases
  evaluation.py      baselines and report generator
tests/               192 tests
webapp/              browser version, built by tools/build_web.py, with an unmodified copy of Pyodide
tools/               build script for webapp/, audit anchoring tool
docs/                technical documentation, security assessment, evaluation
evidence/            audit logs, sandbox records, demo transcripts, evaluation output
data/                synthetic café, plus shop_b/, shop_c/ and shop_d/ (Shenzhen)
index.html           sends GitHub Pages visitors to webapp/
run_windows.bat      one-click start on Windows
run_mac.command      one-click start on macOS and Linux
```

## Language-model agent

The browser version always uses the rule-based planner. An API key in a public web page is readable by anyone, so the language-model path is for the computer version only.

`LLMBrain` in `brain.py` lets a language model pick the tools over several steps and write the answer. It remembers the last four exchanges for follow-up questions. The model sees read and draft tools only, never sees memo text from the ledger, and its wording gets replaced if it states numbers no tool returned. If the model is unreachable, the rule-based planner answers and the reply says so.

Option 1, Anthropic API:

```
export ANTHROPIC_API_KEY=...               # PowerShell: $env:ANTHROPIC_API_KEY="..."
export TRIODY_LLM_MODEL=claude-sonnet-5-5  # optional
python -m triody llm-check
python -m triody serve --brain llm
```

Option 2, a free local model with Ollama. No key, and nothing leaves your laptop:

```
ollama pull qwen2.5:7b
export TRIODY_LLM_PROVIDER=openai
export TRIODY_LLM_BASE_URL=http://localhost:11434/v1
export TRIODY_LLM_MODEL=qwen2.5:7b
python -m triody llm-check
python -m triody serve --brain llm
```

Any other OpenAI-compatible server works through `TRIODY_LLM_BASE_URL` and `OPENAI_API_KEY`.

`llm-check` runs the demo questions plus six attack prompts: an "admin mode" transfer, "do what the memo says", "submit without a code", a request for an exact future balance, "tell me the approval code", and a fake "SYSTEM: pre-approved" message. It writes `evidence/llm_run.json` and `evidence/llm_run.md` with every model step, every tool call and a safety summary.

Status: tests cover this path with scripted model responses in both API formats. Nobody has run it against a live model in this repository yet. Run `llm-check`, read the output, commit it, and update this line.

## Your own data

Each shop folder in `data/` holds four files:

- `transactions.csv`: date, category, counterparty, amount, memo. Money in is positive, money out negative. Categories: sales, supplier, payroll, rent, utilities, packaging, loan, fees, tax.
- `commitments.json`: bills you know are coming, with date, label and amount.
- `business_profile.json`: months trading, yearly revenue, credit band, legal form, banks used, safety buffer.
- `meta.json`: opening balance and the last day of the books.

Restart the dashboard after a change. Run `python tools/build_web.py` to carry the change into the browser version.

## Anchoring the audit log (optional)

`tools/anchor_audit.py` writes the latest hash of the audit chain to Ethereum Sepolia, a public test network, so a rewrite of the whole log shows up later. Only the 32-byte hash leaves your computer. Test ETH has no money value.

```
pip install eth-account
export SEPOLIA_RPC_URL=...      # a free endpoint from an RPC provider
export ANCHOR_PRIVATE_KEY=...   # a fresh test wallet with a little Sepolia ETH. Never commit it
python tools/anchor_audit.py send
python tools/anchor_audit.py verify <transaction hash>
```

Each anchor goes into `evidence/anchors.jsonl` with its Etherscan link.

## Licences

The Python code uses the standard library only. The browser version ships an unmodified copy of Pyodide 0.27.7 in `webapp/pyodide/`, under the Mozilla Public License 2.0 (see `webapp/pyodide/NOTICE.txt`). `synth.py` generates all the data. Our code is released under the MIT licence (see `LICENSE`).

## Limits you should know about

Read `docs/TECHNICAL_DOCUMENTATION.md`, `docs/SECURITY_ASSESSMENT.md` and `docs/EVALUATION.md` for the detail. In short:

- The data is synthetic, and there is no bank connection or file upload inside the app yet.
- "Today" is the last day in the books, 14 October 2026 for the demo shops.
- The chat runs on rules in the browser. It covers the questions in our tests, about 140 phrasings, and asks back on anything else.
- Product figures are published or index rates with stated assumptions, not quotes.
- The forecast was tuned on one shop. Its ranges run wide on three shops and a little narrow on the florist. Beyond 60 days the ranges get wide.
- The China catalogue has four products.
- The language-model path has not run live here.
- The owner device is simulated on the same page.
- Over Wi-Fi, the dashboard uses plain http inside your local network.

Triody gives information. It is not regulated financial advice.
