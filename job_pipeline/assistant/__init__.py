"""Jobi — the in-app helper chat.

Jobi can read your profile, resume, companies, searches and run history,
diagnose problems, and PROPOSE changes. Nothing it proposes is saved until
you click Apply in the chat window (see proposals.py). It runs on Claude
Opus 5.5 by default (`llm.assistant_model` in config.yaml).

Modules:
  proposals.py  the change cards: create, store, apply through the same
                writers the forms use
  commands.py   the fixed allowlist of maintenance commands Jobi may suggest
  tools.py      the tools Claude sees (read, diagnose, propose, handoff)
  agent.py      the chat loop for both auth modes (API key / Claude Code)
"""
