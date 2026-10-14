# Submission checklist

Deadline: 20 October 2026, 23:59 Beijing time, which is 16:59 in Edinburgh. You can update the submission until then. Nothing is accepted after.

| # | Required item (from the rules) | Where it is | Done |
|---|---|---|---|
| 01 | Technical documentation: system architecture, core algorithm, security design | `docs/TECHNICAL_DOCUMENTATION.md` (export to PDF or Word if you prefer) | |
| 02 | Presentation deck, 10 minutes at most | `deck/Triody_AI_presentation.pptx`, 12 slides with speaker notes, charts, screenshots and an architecture diagram | |
| 03 | Demo video, optional, 5 minutes at most | Record it from `docs/DEMO_SCRIPT.md`, with the language-model segment. A silent draft is in the team package (`video/`). For our topic it is part of the execution evidence, so do not skip it | |
| 04 | Source code with README, GitHub link, deployment instructions, test cases | GitHub repository link, `README.md`, `tests/`, plus the GitHub Pages link to the browser version | |
| 05 | Security self-assessment: permission tiers and known-risk list | `docs/SECURITY_ASSESSMENT.md` | |
| 06 | Execution evidence: sandbox operation logs and demo video | `evidence/` folder: UK and China demo transcripts, audit logs, sandbox records, backtests, matcher results. Add `llm_run.json` and `llm_run.md` from your own model run, and `anchors.jsonl` if you anchor the log. Plus the video | |

## Before you send

1. Run `python -m unittest discover -s tests -t .` on a clean computer and read the output yourself.
2. Turn on GitHub Pages and open the browser version on at least one iPhone and one Android phone. Run the four quick buttons, Apply, and Confirm.
3. Run `python -m triody evidence` again so the evidence files carry your own run.
4. Run `python -m triody llm-check` with a real model (an API key, or Ollama on a laptop for free). Read `evidence/llm_run.md`. If the safety summary shows any EXECUTE tool run without a code, stop and fix it before submitting. Commit the result either way.
5. Replace "Triody AI team" in `LICENSE` with your names if you want.
6. Check names, university and team name are spelled the same everywhere and match your passports.
7. Open every document once and fix anything that does not match what you built.
8. Send the email from the team leader's address. Keep the sent message.
9. Ask the organisers two questions in writing (draft in `email_drafts.txt` in the team package): may AI coding tools be used, and does the on-chain challenge bonus apply to non-on-chain topics. Keep the reply.
10. Only if the bonus applies: run `python tools/anchor_audit.py send` with a test wallet and list the transaction hash in the submission.
11. Check the official site for the current submission address and read the full competition rules (Appendix 2), in particular any rule on independent work and on the use of AI tools. If anything is unclear, ask WANG Yiran before the deadline and keep the reply.
