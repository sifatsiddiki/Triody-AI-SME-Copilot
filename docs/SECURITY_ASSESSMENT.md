# Security self-assessment

Scope: the Triody AI code in this repository, run locally on synthetic data. This is a self-assessment by the project team. No external review or penetration test was done.

## 1. Assets and threats

| Asset | Why it matters |
|---|---|
| Money and obligations of the owner | A wrong or forced action could cost real money in a real deployment |
| Transaction data | Contains supplier names, amounts and free-text memos |
| Confirmation codes | They authorise state-changing actions |
| Audit log | Evidence of what the agent did |

Threats considered: a manipulated language model, malicious text inside data (prompt injection), a user or script trying to skip confirmation, replay of an approval, tampering with the log, a malicious web page talking to the local dashboard, bad input to tools, invented numbers in replies, an agent that acts on its own (the daily check) doing more than it should.

## 2. Permission tiers

Defined in `triody/policy.py`. Each tool is registered with one tier in `triody/tools.py`.

| Tier | Meaning | Tools | Rule enforced in code |
|---|---|---|---|
| READ | Analyse only | `get_balance_summary`, `forecast_cash`, `explain_shortfall`, `analyse_business`, `match_financing`, `check_plan`, `recent_transactions`, `period_report`, `data_coverage` | Always allowed. Arguments are checked. |
| PROPOSE | Create a draft. Nothing leaves the sandbox. | `draft_financing_application`, `draft_supplier_delay` | Always allowed. Eligibility and supplier name are checked again. |
| EXECUTE | Change state (simulated here) | `submit_financing_application`, `schedule_supplier_delay` | Only with `origin="owner"` and a redeemed one-time code. Refused for the agent. |
| PROHIBITED | Never allowed | `transfer_funds` | Refused for every origin, even with a code. The function body raises if it is ever reached. |

Tools that a language model is offered (`Copilot.tool_schemas`) are only READ and PROPOSE. Two tests check that EXECUTE and PROHIBITED tools are absent from that list, one of them on the payload actually sent to the model.

The daily check (`Copilot.briefing`) is the one place where the agent acts without a message from the owner. It calls READ tools and at most one PROPOSE tool, then stops. It runs once per session. It never redeems a code: the owner must confirm the draft like any other.

## 3. Controls

| Control | Where | Tested by |
|---|---|---|
| Tier check before every call | `agent.run_tool`, `policy.check_call` | `PermissionTests` |
| One-time code, single use | `policy.redeem` | `test_confirm_executes_once_only` |
| Code sent on a separate channel (owner device), never in the chat, a model request or the audit log | `agent._request_approval`, `agent.handle`, `webapi.state` | `OwnerChannelTests` (10 tests) |
| Five wrong codes lock every waiting draft | `agent.confirm` | `test_guessing_codes_locks_the_drafts` |
| Tool calls written as text by small local models pass the same gate | `brain._tool_calls_in_text` | `TextToolCallTests` |
| Catalogue file named by a shop profile cannot point outside the package | `region.catalog_path` | `test_catalogue_name_cannot_escape_the_package` |
| Audit chain head written outside the host (optional, Sepolia test network) | `tools/anchor_audit.py` | `AnchorTests` (fake chain) |
| Code expires after 10 minutes | `policy.redeem` | `test_code_expires` |
| Code bound to the exact action | `policy.action_hash` | `test_code_is_bound_to_its_action` |
| Code is random, 48 bits | `secrets.token_hex(6)` | not tested (standard library) |
| Cancel revokes a code | `policy.revoke` | `test_cancel_prevents_execution` |
| Amount limit, £50,000 | `policy.check_limits` | `test_amount_limit` |
| Daily limit, 3 executed actions | `policy.check_limits` | `test_daily_action_limit` |
| Argument schema check | `tools.validate_args` | `ValidationTests` |
| Untrusted memo text hidden from a model | `ToolResult.model_view` | `test_model_view_hides_memo_text` |
| System prompt tells a model to treat data as data | `brain.SYSTEM_PROMPT` | not testable, and not relied on |
| Number grounding on model text | `agent.numbers_grounded` | `test_ungrounded_numbers_are_replaced` |
| Daily check drafts once and cannot execute | `agent.briefing` | `DailyCheckTests` |
| Model offline: rule planner answers, event logged | `agent.handle` | `test_falls_back_to_rules_when_model_is_offline` |
| Same policy gate for both model APIs | `brain.LLMBrain` | `test_llm_brain_cannot_bypass_policy`, `test_openai_path_cannot_bypass_policy` |
| Hash-chained audit log | `audit.AuditLog` | `AuditTests` |
| Masking of e-mail addresses and long digit strings | `audit.redact` | `test_sensitive_values_are_redacted` |
| Tool exceptions hidden from user and model | `agent.run_tool` | by review |
| Local-only server, fixed file list, Host check, client header, body limit, CSP | `server.py` | `ServerTests` |
| Same request checks in the browser version (method, size, JSON shape) | `webapi.py` | `TestDispatch` |
| Dashboard writes text with `textContent`, never `innerHTML` | `web/app.js` | by review |
| Other devices sign in with a single-use 6-digit code from the computer's screen; 5 wrong tries lock the device out for 5 minutes; 10 in total replace the code | `access.py` | `PairingTests` |
| Session cookie is random, HttpOnly, SameSite=Strict, 30 days; only its SHA-256 hash is stored | `access.py`, `server.py` | `test_store_keeps_hashes_not_tokens`, `test_pairing_flow_sets_an_httponly_cookie` |
| Requests from other devices need a session; the pairing code is shown only to 127.0.0.1 | `server.py` | `RemoteDeviceTests` |
| With `--lan`, Host must be localhost or a plain IP, which still blocks DNS rebinding | `server._host_ok` | `test_ip_host_is_allowed_on_lan_but_names_are_not` |
| Browser lock: passkey signature checked against the saved public key (challenge, origin, user-verified flag), or PIN with PBKDF2 and a growing wait | `web/app.js` | by review and a browser run with a virtual authenticator |
| Any unexpected error returns a plain message, logs only the error type, and changes nothing | `webapi.dispatch` | `test_internal_error_returns_a_plain_message` |
| Figure, data and unclear questions can only reach READ tools; transfer words are checked before and after typo fixes | `intent.classify`, `brain.RuleBrain` | `ReadOnlyRouteTests` |
| A financing plan must keep repayments within 50% of free cash flow; Time to Pay is never shown as money received | `financing.match`, `tools.plan_commitments` | `FinancingFixTests` |

The confirmation flow is the main control. The test `test_llm_brain_cannot_bypass_policy` scripts a model that asks for a transfer and then for a direct submit. Both are denied and no file is written.

## 4. Known risks

Likelihood and impact are the team's own estimates for a real deployment, not measured values. "Open" means the risk remains in this version.

| # | Risk | Likelihood | Impact | Status | Mitigation now | What a real deployment needs |
|---|---|---|---|---|---|---|
| 1 | Sign-in is by place, not by person: the computer running Triody is trusted, other devices pair with a code. On `--lan` the traffic is plain http inside the local network. | Low | High | Reduced | Local by default. LAN devices need a pairing code and a session; codes are single use and rate limited; sessions are hashed and can all be revoked | Per-user accounts, TLS, a server the user cannot edit |
| 2 | Audit log is protected by a hash chain only. Someone with file access can rewrite the whole file with a valid chain. Deleting entries from the end is not detected. | Medium | Medium | Reduced if anchoring is used | Detects edits and deleted middle entries. `tools/anchor_audit.py` writes the chain head to the Sepolia test network; `verify` then detects a rewrite of anything up to that point. Not run by default: it needs a test wallet and an RPC endpoint | Anchor on a schedule, sign entries, or use append-only storage |
| 3 | Approval codes. Found in review: the code used to be printed in the agent's own chat message, so anyone who saw the chat could use it, and a real bank would send it on a separate channel. | Low | Medium | Reduced | The code now goes only to the owner device. The chat, the model, the conversation memory and the audit log never hold it (tested). Five wrong codes lock all drafts. Short life, single use, bound to one action. Remaining gap: in this prototype the owner device is a panel on the same dashboard page, so someone at the owner's screen still sees both | A real second channel: SMS, or a push message in the bank's app, ideally with a device-bound key |
| 4 | Prompt injection through free text (memos, supplier names) | Medium | High | Reduced | Policy gate in code, memo hidden from a model, owner-only EXECUTE | Red-team against a live model, more fields treated as untrusted |
| 5 | Language-model planner was not run against a live model in this repository | n/a | Medium | Open | Tested with scripted fake models in both API formats. `python -m triody llm-check` runs the demo plus six attack prompts against a real model and records every step, including whether an approval code ever appeared in a reply | Run `llm-check` on the chosen model, keep the output in `evidence/`, extend the attack list |
| 6 | Rule-based planner uses rules. Odd phrasing can still be misread. | Medium | Low | Reduced | Typo fixing, period parsing and 50 tested phrasings; an unclear message gets a question back and never an action | A tested language-model planner with the same gate |
| 7 | A wrong forecast leads to a wrong decision | Medium | Medium | Open | Ranges, stated assumptions, backtest shown | Validate on real businesses, at least 12 months of history |
| 8 | Forecast settings were tuned on shop A. Ranges now run wide on shops A, B and D (84% to 98% against 80%), which can warn too early, and still a little narrow on shop C at day 14 (about 71%) | Medium | Medium | Reduced, disclosed | Four synthetic shops, self-calibrating range width, one-off handling, results in `EVALUATION.md` | Hold-out validation on real businesses with 12 months of history |
| 9 | Catalogue figures go out of date, and some products are costed at an advertised "from" rate or a market index | High | Medium | Reduced, disclosed | Every product lists its source, the date checked and its assumptions. Replies say when a rate is a best case | Live provider quotes, a scheduled re-check, compliance review |
| 10 | Output could be taken as regulated advice | Medium | High | Open | Wording says information, not advice | Legal review under the relevant regime |
| 11 | Real transaction data would be personal and commercial data | n/a | High | Not in scope | Synthetic data only | Data protection assessment, retention rules, encryption at rest |
| 12 | Grounding check ignores numbers below 100 and years, and matches any number a tool returned | Medium | Low | Open | Applies only to model-written text | Structured outputs where figures are filled from fields |
| 13 | Single global lock in the server. One slow request blocks others. | Low | Low | Accepted | Requests are fast | Proper concurrency |
| 14 | No general rate limit on the server | Low | Low | Reduced | Pairing attempts are limited per device and per code; everything else needs a session | Rate limiting per session |
| 15 | Simulated execution cannot show failure modes of real APIs (timeouts, partial success, duplicates) | Certain | Medium | Open | Draft status prevents a second submit of the same draft | Idempotency keys and reconciliation against a real provider |
| 16 | Daily limit counts a redeemed code even if the tool then fails | Low | Low | Open | Errs on the side of fewer actions | Count only successful executions |
| 17 | Browser version runs all logic on the visitor's device. A visitor can change the code or state in their own copy of the page. | Low | Low | Accepted | Each visitor gets a separate in-memory copy that resets on reload, no secrets or API keys in the page, the language-model path is disabled there, CSP meta tag allows only same-site scripts plus WebAssembly compilation | A real deployment keeps policy, tokens and the audit log on a server the user cannot edit |
| 18 | Browser version depends on Pyodide, a third-party runtime | Low | Medium | Accepted | Pinned version 0.27.7 copied unmodified into the repository, served from the same site, no CDN | Subresource integrity hashes and a scheduled update check |
| 19 | The owner approves the daily check's draft without reading it | Medium | Medium | Open | The draft states product, amount, term and cost; the tested plan is shown above it; amount and daily limits still apply | Show the draft's full terms on the confirm screen, add a short cooling-off delay |
| 20 | API key for the model provider leaks | Low | Medium | Reduced | Key read from the environment only, `.env` in `.gitignore`, the browser version never uses a key | Secret manager, per-environment keys |
| 21 | A local model through Ollama is weaker at following rules than a large hosted model | Medium | Low | Reduced | Same policy gate, grounding check and fallback apply to every model. Tool calls written as text are read only for offered tools | Pick the model by its `llm-check` result, not by size |
| 22 | The China catalogue has four products, fewer checks, and one product (WeBank) is costed at an assumed rate inside a wide published range (2.88% to 24%) | High | Medium | Disclosed | Each product lists its source and assumptions; replies say when a rate is assumed or a market average | Provider quotes and a local compliance review before any use in China |
| 23 | The anchoring tool needs a private key for a test wallet | Low | Low | Reduced | Key read from the environment only, Sepolia only (the tool refuses other networks), only the 32-byte hash is sent | A hardware or cloud key store; a dedicated wallet per deployment |
| 24 | The browser lock runs in the page. Someone with full control of the browser can skip it. | Medium | Low | Accepted | It guards a shared screen. Resetting it wipes the session, and the browser build holds only synthetic data | Real data and policy on a server, with sign-in there |
| 25 | The offline copy can serve an old version until the next online visit | Medium | Low | Reduced | The cache name is a hash of every file, so any change replaces the whole copy on the next visit | Show "update ready" in the app |
| 26 | "Today" is the last day in the books, which can differ from the calendar date | Certain with demo data | Low | Disclosed | Every "today" reply names the date and says it is the latest day in the books | A live bank feed |

## 5. Compliance notes

- The software gives information. It is not regulated financial advice and does not make credit decisions.
- Products shown use published figures and stated assumptions. They are not quotes or offers.
- All data is synthetic. No personal data is processed.
- Third-party components: the Python code uses the standard library only. The optional anchoring tool needs `eth-account` (MIT licence) for signing. The browser version ships an unmodified copy of Pyodide 0.27.7 (Mozilla Public License 2.0).
- The optional language-model planner would send tool results (without memo text) to the model provider. That needs a data processing review before use with real data.

## 6. Review checklist before any real use

1. Add per-user accounts and TLS (pairing codes cover one owner on a home or shop network).
2. Anchor the audit log outside the host on a schedule (the tool exists; run it daily).
3. Run an adversarial test suite against the language-model planner.
4. Replace published and index rates with live provider quotes.
5. Validate the forecast on real data with a hold-out period.
6. Get legal and data protection review.
