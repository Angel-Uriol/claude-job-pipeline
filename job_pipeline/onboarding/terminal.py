"""Terminal versions of the Setup page: `job-pipeline auth` and `setup`.

Same engine as the web UI (interview.py / companies.py / writer.py) — just
input()/print() instead of HTTP. Handy over SSH or for people who'd rather
not run the Next.js dev server.
"""

from __future__ import annotations

import getpass
import sys
import textwrap
from pathlib import Path

from .. import paths
from ..config import LLMSettings, load_config, load_secrets
from ..llm import LLMError, build_llm, self_check
from .companies import suggest_companies, verify_companies
from .env_file import mask, set_env_values
from .interview import InterviewState, prefill_from_resume, step
from .resume_intake import parse_resume_file
from .writer import build_files, write_files

TERMS_NOTE = """\
Note on "Claude Code login" mode: it runs YOUR locally installed, logged-in
Claude Code CLI on YOUR machine, for your own job search. Anthropic's terms
ask third-party tools to use API keys rather than claude.ai logins, so don't
host this app for other people in this mode."""


def _models() -> LLMSettings:
    """Model choices: from the profile config if it exists, else defaults."""
    if paths.is_configured():
        try:
            return load_config().llm
        except Exception:  # noqa: BLE001 - a broken config shouldn't block setup
            pass
    return LLMSettings()


def _say(text: str) -> None:
    print("\n" + textwrap.fill(text, width=88) + "\n")


def run_auth(mode: str | None = None) -> int:
    """Pick an auth mode, save it to .env, and run a live self-check."""
    current = load_secrets()
    print(f"Current mode: {current.auth_mode}"
          + (f" (key {mask(current.anthropic_api_key)})" if current.anthropic_api_key else ""))
    if mode is None:
        print("\nHow should this tool talk to Claude?\n"
              "  1) Anthropic API key   (recommended)\n"
              "  2) My Claude Code login (uses your local `claude` CLI)\n")
        choice = input("Choose 1 or 2 [1]: ").strip() or "1"
        mode = "claude_code" if choice == "2" else "api_key"

    values = {"JOB_PIPELINE_AUTH_MODE": mode}
    if mode == "api_key":
        key = getpass.getpass("Paste your Anthropic API key (input hidden; Enter keeps the "
                              "saved one): ").strip()
        if key:
            values["ANTHROPIC_API_KEY"] = key
        elif not current.anthropic_api_key:
            print("No key given. Create one at https://console.anthropic.com/settings/keys")
            return 1
    else:
        print("\n" + TERMS_NOTE + "\n")
        print("Make sure the CLI is installed and logged in: run `claude` once and use /login.")
    set_env_values(values)

    print("Checking the connection …")
    info = self_check(_models().fast_model)
    print(("OK — " if info["ok"] else "FAILED — ") + info["detail"])
    return 0 if info["ok"] else 1


def run_setup(resume_path: str | None = None, restart: bool = False) -> int:
    """Interactive questionnaire → profile files."""
    models = _models()
    try:
        llm = build_llm()
    except LLMError as e:
        print(f"Claude isn't connected yet: {e}\nRun `job-pipeline auth` first.")
        return 1

    state = InterviewState.reset() if restart else InterviewState.load()
    if state.messages and not restart:
        print("Resuming your saved setup (use --restart to start over).")

    # 1. resume
    if state.resume_content is None:
        path = resume_path or input(
            "Path to your resume (PDF/DOCX/TXT), or Enter to skip: "
        ).strip()
        if path:
            print("Reading your resume with Claude …")
            try:
                content = parse_resume_file(Path(path).expanduser(), llm=llm,
                                            model=models.smart_model)
            except (ValueError, OSError, LLMError) as e:
                print(f"Couldn't parse that resume: {e}")
                return 1
            prefill_from_resume(state, content)
            exps = content.get("experiences") or {}
            print(f"Got it — {len(exps)} roles, "
                  f"{sum(len(e.get('bullets') or []) for e in exps.values())} bullets.")
            state.save()

    # 2. interview
    print("\nA few questions so the search fits you. Type 'skip' to skip an optional one.\n")
    if not state.messages or state.messages[-1].role == "user":
        state = step(state, None, llm=llm, model=models.fast_model)
    while not state.done:
        print(f"Claude: {state.messages[-1].text}")
        try:
            answer = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaved — run `job-pipeline setup` again to continue.")
            return 1
        if not answer:
            continue
        state = step(state, answer, llm=llm, model=models.fast_model)
    print(f"Claude: {state.messages[-1].text}\n")

    # 3. companies
    if not state.companies:
        print("Finding companies that fit your profile …")
        state.companies = suggest_companies(state.draft, llm=llm, model=models.smart_model)
        state.save()
    for i, c in enumerate(state.companies, 1):
        print(f"  {i:>2}. [{c['tier']}] {c['name']} — {c.get('why', '')}")
    drop = input("\nNumbers to REMOVE (comma-separated), or Enter to keep all: ").strip()
    if drop:
        idx = {int(x) for x in drop.replace(" ", "").split(",") if x.isdigit()}
        for i, c in enumerate(state.companies, 1):
            c["selected"] = i not in idx
    extra = input("Companies to ADD (comma-separated), or Enter: ").strip()
    for name in (x.strip() for x in extra.split(",") if x.strip()):
        state.companies.append({"name": name, "tier": 1, "why": "added by you", "selected": True})
    print("Checking which companies have a public job board …")
    state.companies = verify_companies(state.companies)
    state.save()
    found = [c for c in state.companies if c.get("selected", True) and c.get("platform")]
    print(f"{len(found)} companies can be scraped directly; the rest get Chrome Claude prompts.")

    # 4. review + write
    files = build_files(state)
    print("\n--- profile/config.yaml (profile section) ---")
    print(files["config.yaml"].split("scrapers:")[0])
    if input("Write your profile files now? [Y/n]: ").strip().lower() in ("", "y", "yes"):
        for p in write_files(state):
            print(f"  wrote {p}")
        print("\nAll set. Next: `make dev` (web UI) or `job-pipeline daily`.")
        return 0
    print("Not written. Your answers are saved — run setup again any time.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_setup())
