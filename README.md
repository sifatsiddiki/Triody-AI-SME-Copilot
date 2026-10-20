# Triody AI

**2026 Shenzhen International FinTech Competition, International Track. Topic E: SME Finance Copilot.**

Team Triody AI, Edinburgh Napier University: AL SIFAT SIDDIKI (team leader), Md Rezuan Islami Rokib, Atif Azeem.

| What | Where |
|---|---|
| Demo video | [paste video link here] |
| Technical documentation | [docs/Triody_AI_Technical_Documentation.pdf](docs/Triody_AI_Technical_Documentation.pdf) |
| Security self-assessment | [docs/SECURITY_ASSESSMENT.md](docs/SECURITY_ASSESSMENT.md) |
| Evaluation results | [docs/EVALUATION.md](docs/EVALUATION.md) |
| Sandbox logs and demo transcripts | [evidence/](evidence/) |
| Test cases | [tests/](tests/) (192 tests) |

A finance copilot for a small business owner. It watches the cash, warns before money runs short, explains why, answers questions about sales and costs for any period, compares financing from public UK sources (and a smaller China catalogue), tests the best option against the forecast and drafts the paperwork. The owner approves every action with a one-time code. It never moves money.

All shops in `data/` are fictional and their numbers are synthetic.

## Start it

### On a computer, with the full dashboard

Needs Python 3.11 or newer. No other packages.

- Windows: double-click `run_windows.bat`.
- macOS or Linux: double-click `run_mac.command` (or run `./run_mac.command`).

The script runs the tests, then opens http://127.0.0.1:8000. The computer itself never asks you to sign in.

You can also start it by hand from the project folder:

```
python3 -m unittest discover -s tests -t .
python3 -m triody serve
```

Then open http://127.0.0.1:8000 in a browser.

### On your phone through the computer, over Wi-Fi

Answer `y` when the script asks about your phone, or run `python3 -m triody serve --lan`. The dashboard then shows an address such as `http://192.168.1.20:8000` and a 6-digit code. On the phone, join the same Wi-Fi, open that address and type the code once. The phone stays signed in for 30 days. The Wi-Fi does not need internet access. **Sign out all other devices** on the computer ends every phone session.

### Any phone, tablet or computer, with or without internet

Serve the `webapp/` folder over https (for example with GitHub Pages) and open it once while online. Then install it:

- Android, Windows, ChromeOS, Linux: tap **Install app** at the top, or use the browser menu and choose Install.
- iPhone and iPad: in Safari tap Share, then **Add to Home Screen**.
- Mac: in Safari choose File, then **Add to Dock**. In Chrome or Edge use **Install app**.

After the first visit it opens and works with no connection. Everything runs on the device. No account is needed. To stop other people using it on a shared device, open **Account** and turn on the lock: Face ID, fingerprint or Windows Hello in one tap, or a PIN.

## What you can ask

Plain questions, typos and all. Some examples:

- Figures for any period: "sales today", "this year sales", "October sales", "costs last month", "profit in Q3", "wages this year", "how much did I pay Lothian Mill this year", "sales on Saturday", "best day this month", "how many days was I open last month".
- Comparisons and trends: "compare this month to last month", "July vs August", "which month had the highest sales", "are sales going up or down", "which was the worst week", "sales by day of the week".
- Why: "why did profit drop in September?", "why are my wages so high?", "why is cash tight?"
- Bills and cash: "what's my VAT bill?", "when is rent due?", "do I have enough cash for payroll?", "what will my balance be at the end of November?", "how long will my cash last?"
- Decisions: "can I afford a £3,000 coffee machine?", "can I hire someone for £900 a month?", "what if sales drop 10%?", "what if I raise prices 5%?", "should I close on Mondays?"
- The big picture: "is my business healthy?", "how can I improve cash flow?", "help me plan for next month", "any suspicious transactions?"
- Financing: "what financing fits?", then "apply". "What happens if I pay VAT late?"
- Terms: "what is gross margin?", "what does APR mean?"

"Today" means the last day in the books, and the reply names that date. A period outside the books gets a plain reason, not zeros. If a question is unclear, Triody asks back and offers three questions to tap. It never moves money, and nothing is sent without the code from the owner device.

## Commands

```
python3 -m triody serve [--lan] [--shop A|B|C|D] [--port 8000]   dashboard
python3 -m triody check                                         the daily check, in the terminal
python3 -m triody demo                                          scripted demo in the terminal
python3 -m triody evidence                                      rebuild evidence/ and docs/EVALUATION.md
python3 -m triody gen-data                                      rebuild the four synthetic shops
python3 -m unittest discover -s tests -t .                      all tests
python3 tools/build_web.py                                      rebuild webapp/ after changing triody/ or data/
```

On Windows, type `python` instead of `python3`.

Shops: A is the Edinburgh café (default), B a bike shop, C a florist, D a Shenzhen tea shop on the China catalogue. In the browser build add `?shop=d` to the address for shop D.

## Language-model agent

By default a rule-based planner answers. To let a language model plan the tool calls and write the replies, set environment variables before `serve`, `demo` or `check`:

```
ANTHROPIC_API_KEY=...                         use Anthropic's API
TRIODY_LLM_PROVIDER=openai                    or any OpenAI-compatible server
TRIODY_LLM_BASE_URL=http://localhost:11434/v1 for example Ollama on this computer
TRIODY_LLM_MODEL=qwen2.5:7b                   model name
```

The model passes the same permission gate as the rules, never sees memo text or approval codes, and any figure it writes that no tool returned gets its wording replaced. If the model cannot be reached, the rules answer and the reply says so. `python3 -m triody llm-check` runs the demo plus attack prompts against the model and saves the transcript in `evidence/`. The browser build never uses a model, because a key in a public page would be readable.

## Your own data

Each shop folder in `data/` holds four files:

- `transactions.csv`: `date,category,counterparty,amount,memo`. Money in is positive, money out negative. Categories: sales, supplier, payroll, rent, utilities, packaging, loan, fees, tax.
- `commitments.json`: bills you know are coming, with date, label and amount.
- `business_profile.json`: months trading, yearly revenue, credit band, legal form, banks used, safety buffer.
- `meta.json`: opening balance and the last day of the books.

Restart the dashboard after a change. Run `python3 tools/build_web.py` to put the change into the browser build.

## Folders

```
triody/          the package: intent.py (reads questions), reports.py (period figures), forecast.py,
                 analysis.py, financing.py, tools.py, policy.py, agent.py, brain.py, audit.py,
                 access.py (pairing and sessions), server.py, webapi.py, web/ (dashboard files)
webapp/          browser build: the same package running in the page through Pyodide, plus the
                 offline cache (sw.js) and app manifest
data/            four synthetic shops
tests/           unit, integration and safety tests
tools/           build_web.py, anchor_audit.py (optional: audit chain head on the Sepolia test network)
docs/            technical documentation, security assessment, evaluation
evidence/        demo transcripts, audit logs and evaluation output made by `python3 -m triody evidence`
```

Read [docs/Triody_AI_Technical_Documentation.pdf](docs/Triody_AI_Technical_Documentation.pdf) for how it works and [docs/SECURITY_ASSESSMENT.md](docs/SECURITY_ASSESSMENT.md) for the controls and known risks. Triody gives information, not regulated financial advice.
