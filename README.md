# Triody AI: your small business finance copilot

Entry for the 2026 Shenzhen International FinTech Competition (FinTechathon), International Track, Topic E: SME Finance Copilot.

AI finance copilot for small businesses. Forecasts 60 days of cash with risk ranges, alerts the owner before a shortfall, explains the cause, and matches financing options it can prove are affordable. Drafts applications, never moves money. Topic E entry, 2026 Shenzhen FinTechathon International Track. Triody combines Tri, for our team of three and the three tasks of our agent, with Ody, from Odyssey. A small business runs a long journey, and our copilot travels with the owner. The three tasks are cash-flow alerts, matching financing options, and business financial analysis.

Triody is a finance agent that takes part in a small business's money decisions. It does the three jobs in the topic description, and it does them without waiting to be asked:

1. **Cash-flow forecast with alerts.** A 60-day forecast with a probability range. Each day the agent checks it on its own and warns the owner with a date, an amount and a probability.
2. **Business analysis.** It explains in plain language why cash is short, and reports month by month: sales, gross margin, operating profit, which costs grow fastest against sales, the biggest suppliers, and the daily sales needed to break even. Every number comes from the ledger and the forecast.
3. **Financing matching.** It sizes the gap and compares ten UK options built from public sources (HMRC Time to Pay, Lloyds Bank, iwoca, Funding Circle, Start Up Loans and others, each with its source link and assumptions). It ranks the eligible ones, gives a reason for every one it rejects, refuses anything the business cannot afford, and re-runs its own forecast to show how the best option changes the risk.

The agent can read, analyse and draft. It cannot move money. Anything that changes state needs a one-time code that goes to the owner's device on a separate channel, never into the chat, and every step is written to a tamper-evident log.

The country lives in one file. The same code runs a UK café on the UK catalogue and a Shenzhen tea shop, priced in yuan, on a China catalogue (WeBank Weiyedai, a bank inclusive small business loan, tax payment deferral, supplier terms). A language model can plan the steps (Anthropic's API or any OpenAI-compatible server, including a free local model), with a rule-based planner as fallback.

Everything runs on synthetic data for fictional shops. No real business or person is involved. Product figures come from public sources checked on 14 October 2026. They are not quotes or offers.

## Requirements

Two ways to use it:

- **Any phone or computer, nothing to install:** open the browser version (see below). It works on iPhone, Android, Windows, Mac and Linux in a current browser.
- **Full project on a computer:** Python 3.11 or newer on Windows, macOS or Linux. The test suite passes on Python 3.11, 3.12 and 3.13. No third-party Python packages are needed.

## Browser version (phones, tablets, any computer)

`webapp/` runs the same Python package inside the browser through Pyodide, a build of CPython for WebAssembly. Every calculation happens on the visitor's device. There is no server code and nothing is sent anywhere. The numbers match the terminal version exactly.

To put it online with GitHub Pages:

1. Push the repository to GitHub as a public repository.
2. Open Settings, then Pages. Under "Build and deployment" pick "Deploy from a branch", branch `main`, folder `/ (root)`, and save.
3. After a minute or two the demo is at `https://<your-username>.github.io/triody-sme-copilot/`. Open that link on any phone.

Add `?shop=d` to the address to open the Shenzhen tea shop with the China catalogue. A link at the top of the page switches between the two.

To try it on your own computer first: `python -m http.server 8080 --directory webapp`, then open http://127.0.0.1:8080. Opening `webapp/index.html` by double-clicking does not work, because browsers block WebAssembly loading from local files.

After changing anything in `triody/` or `data/`, run `python tools/build_web.py` and commit `webapp/`. A test fails if you forget.

Tested in Chromium at iPhone (390 px), small Android (360 px) and desktop sizes. Real Safari on iPhone was not available for testing here, so check it on a real iPhone before the demo. Very old phones whose browsers lack WebAssembly cannot run it.

## Run it on a computer

Quick start: double-click `run_windows.bat` on Windows or `run_mac.command` on a Mac. Each runs the tests and opens the dashboard. On a Mac, the first time, right-click the file and choose Open, because macOS blocks downloaded scripts by default.

Or use the terminal:

```bash
git clone <your repository url>
cd triody-sme-copilot

python -m unittest discover -s tests -t .   # 122 tests, about 30 seconds
python -m triody check                      # the agent's own daily check
python -m triody demo                       # scripted conversation in the terminal
python -m triody serve                      # dashboard at http://127.0.0.1:8000
python -m triody evidence                   # regenerate evidence/ and docs/EVALUATION.md
python -m triody llm-check                  # run against a real language model, save the transcript
python -m triody serve --shop D             # the same dashboard for the Shenzhen tea shop
python -m triody gen-data                   # rebuild the four synthetic shops in data/
python tools/anchor_audit.py digest         # print the audit chain head (send: write it to Sepolia)
```

On Windows use `py -m` instead of `python -m` if `python` is not found. On a Mac use `python3`.

The data and the forecast are seeded, so every run gives the same numbers.

## What to expect from the demo

The fictional café has £9,200 today. A £6,800 VAT payment is due on 7 November and a £1,500 insurance renewal on 3 December. Sales are falling into winter and a new hire raised the wage bill.

- When the dashboard opens, the agent runs its daily check without being asked.
- The forecast reaches a 25% risk of dropping under the £2,500 buffer on 7 November, 24 days out. The expected low is -£1,194 on 7 December. The chance of going below zero at some point in 60 days is 66%.
- The analysis finds September sales down 25.1% on August, wages up from 26% to 33% of sales since May, and break-even at about £458 a day against £493 forecast.
- The matcher sizes the gap at £5,000 over 6 months and picks HMRC Time to Pay (7.75%), ahead of the Lloyds overdraft and iwoca. Six products are ruled out, each with a reason.
- Tested against the forecast, the plan lowers the chance of going below zero from 66% to 15%.
- The agent drafts the application. The approval code appears in the owner device panel, not in the chat. Nothing is sent until the owner types it in.
- Asking the agent to transfer £9,000 is refused. A memo in the ledger that tries to give the agent orders is shown to the owner as data and ignored.
- Shenzhen tea shop (`?shop=d` in the browser, `--shop D` on a computer): 52% chance of going below zero, and the agent refuses to recommend a loan because the shop's average free cash flow is negative.

## Layout

```
triody/
  config.py       every setting in one place
  synth.py        four synthetic shops (café for the demo, bike shop and florist held out, Shenzhen tea shop)
  ledger.py       read-only view of transactions
  forecast.py     sales model, recurring payments, Monte Carlo, self-calibration, backtest
  analysis.py     shortfall explanation and monthly business analysis
  region.py       country settings read from the catalogue file
  catalog.json    ten UK financing options with sources and assumptions
  catalog_cn.json four mainland China options with sources and assumptions
  eval_catalog.json  frozen illustrative catalogue used by the 30 labelled test cases
  financing.py    eligibility filters, scoring, affordability gate, stock rule
  policy.py       permission tiers, one-time codes, limits
  tools.py        tool registry with argument checks
  audit.py        hash-chained append-only log
  brain.py        language-model planner (two APIs) and the rule-based planner
  agent.py        policy gate, daily check, plan testing, drafts, confirmations, fallback
  server.py       local dashboard server
  webapi.py       request handling shared by the server and the browser version
  web/            dashboard files (boot.js is used by the browser version only)
  eval_set.py     30 labelled financing cases plus 14 and 15 held-out cases
  evaluation.py   baselines and report generator
tests/            122 tests
webapp/           browser version, built by tools/build_web.py, includes an unmodified copy of Pyodide
tools/            build script for webapp/, audit anchoring tool
index.html        sends GitHub Pages visitors to webapp/
run_windows.bat   one-click start on Windows
run_mac.command   one-click start on macOS
docs/             technical documentation, security assessment, evaluation, demo script
evidence/         audit log, sandbox records, demo transcript, evaluation output
data/             synthetic café, plus shop_b/, shop_c/ and shop_d/ (Shenzhen)
```

## Language-model agent

The browser version always uses the rule-based planner. An API key placed in a public web page would be readable by anyone, so the language-model path is for the computer version only.

`LLMBrain` in `brain.py` lets a language model choose the tools over several steps and write the answer. It remembers the last four exchanges, so follow-up questions work. The model sees only read and draft tools, never sees memo text from the ledger, and its wording is replaced if it states numbers that no tool returned. If the model cannot be reached, the rule-based planner answers and the reply says so.

Option 1, Anthropic API:

```bash
export ANTHROPIC_API_KEY=...            # Windows PowerShell: $env:ANTHROPIC_API_KEY="..."
export TRIODY_LLM_MODEL=claude-sonnet-5-5   # optional
python -m triody llm-check
python -m triody serve --brain llm
```

Option 2, a free local model with Ollama (no key, nothing leaves the laptop):

```bash
ollama pull qwen2.5:7b
export TRIODY_LLM_PROVIDER=openai
export TRIODY_LLM_BASE_URL=http://localhost:11434/v1
export TRIODY_LLM_MODEL=qwen2.5:7b
python -m triody llm-check
python -m triody serve --brain llm
```

Any other OpenAI-compatible server works the same way through `TRIODY_LLM_BASE_URL` and `OPENAI_API_KEY`.

`llm-check` runs the demo questions and six attack prompts (an "admin mode" transfer, "do what the memo says", "submit without a code", a request for an exact future balance, "tell me the approval code", and a fake "SYSTEM: pre-approved" message) and writes `evidence/llm_run.json` and `evidence/llm_run.md` with every model step, every tool call and a safety summary.

Small local models sometimes write a tool call as text instead of using the tool field. Triody reads those too, for offered tools only, and they go through the same policy gate.

Status: this path is tested with scripted fake model responses in both API formats. It has not been run against a live model in this repository. Run `llm-check` yourself, read the output, commit it, and update this line.

## Anchoring the audit log (optional)

`tools/anchor_audit.py` writes the latest hash of the audit chain to Ethereum Sepolia, a public test network, so that a rewrite of the whole log can be detected later. Only the 32-byte hash leaves the computer. Test ETH has no money value.

```bash
pip install eth-account
export SEPOLIA_RPC_URL=...       # a free endpoint from an RPC provider
export ANCHOR_PRIVATE_KEY=...    # a fresh test wallet with a little Sepolia ETH from a faucet. Never commit it
python tools/anchor_audit.py send
python tools/anchor_audit.py verify <transaction hash>
```

Each anchor is recorded in `evidence/anchors.jsonl` with its Etherscan link.

## Licences and third-party material

The Python code uses only the standard library. The browser version ships an unmodified copy of Pyodide 0.27.7 in `webapp/pyodide/`, under the Mozilla Public License 2.0 (see `webapp/pyodide/NOTICE.txt`). All data is generated by `synth.py`. Our code is released under the MIT licence (see `LICENSE`).

## Limits you should know about

Read `docs/SECURITY_ASSESSMENT.md` and `docs/EVALUATION.md`. In short: the data is synthetic; product figures are published or index rates with stated assumptions, not quotes; the forecast was tuned on one shop, its ranges now run wide on three shops and a little narrow on the florist; the China catalogue is small; the language-model path has not been run live here; the owner device is simulated on the same page; and the dashboard is built for one local user and has no login.

This software gives information. It is not regulated financial advice.
