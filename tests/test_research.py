"""Offline research contracts, citations, eligibility and profile boundaries."""

from __future__ import annotations

import copy
import json
import shutil
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from job_pipeline.config import load_config
from job_pipeline.llm import LLMError
from job_pipeline.llm.api_client import AnthropicAPIClient
from job_pipeline.llm.research import (
    WebEvidence,
    WebSource,
    _Collector,
    _fetch_text,
    _search_links,
    safe_url,
    web_research,
)
from job_pipeline.research.models import Finding
from job_pipeline.research.service import (
    ResearchRequest,
    build_report,
    candidate_evidence,
    candidates,
    job_key,
    list_reports,
    save_report,
    validate_references,
)
from job_pipeline.store import JobStore
from job_pipeline.ui.server import create_app
from tests.conftest import EXAMPLE_PROFILE
from tests.test_ui_server import _make_job, _write_isolated_config


def comparison(key, refs=None):
    finding = {
        "rating": "around market",
        "explanation": "Comparable local base pay ranges.",
        "source_ids": refs or ["web-2"],
        "confidence": "medium",
        "cohort": "L4 backend engineers, SF Bay Area, base pay, example.org",
        "evidence_quote": "Published ranges for L4 backend engineers",
    }
    prospects = {
        "signal": "mixed",
        "reasons": ["Relevant backend experience"],
        "blockers": ["Applicant pool unknown"],
        "next_steps": ["Prepare an API example"],
    }
    return {
        "summary": "Compare verified terms before applying.",
        "methodology": "Similar role and location; a small sample, not a percentile.",
        "missing_information": ["Equity details"],
        "rows": [
            {
                "job_key": key,
                "salary": finding,
                "benefits": copy.deepcopy(finding),
                "company_reviews": copy.deepcopy(finding),
                "interview_prospects": prospects,
                "hiring_prospects": copy.deepcopy(prospects),
                "tradeoff": "Relevant experience, but interview performance is unknown.",
            }
        ],
    }


def prep():
    return {
        "company_brief": "Company context from public sources.",
        "role_priorities": ["Reliable backend APIs"],
        "process": "Confirm with recruiter.",
        "source_ids": ["web-2"],
        "questions": [
            {
                "question": q,
                "kind": "practice",
                "purpose": "Explain your decisions",
                "answer_outline": "Use your real API example; explain context, action and result.",
                "evidence_ids": ["experience/platform_co/b1"],
                "source_ids": [],
                "gap_to_acknowledge": "Do not claim tools you haven't used.",
            }
            for q in [
                "How did you improve API performance?",
                "Describe a design tradeoff.",
                "How do you handle an unfamiliar tool?",
            ]
        ],
        "study_plan": [
            {
                "session": "Day 1",
                "focus": "API story",
                "tasks": ["Rehearse a two-minute answer"],
                "success_check": "Explain the result without adding claims",
            }
        ],
        "questions_to_ask": ["What would success in the first 90 days look like?"],
        "missing_information": ["Exact interview format"],
    }


class FakeClient:
    auth_mode = "api_key"

    def __init__(self, response):
        self.response = response
        self.prompt = ""

    def structured(self, prompt, schema, **kwargs):
        self.prompt = prompt
        return schema.model_validate(self.response)


@pytest.fixture
def context(tmp_path):
    path = _write_isolated_config(tmp_path)
    shutil.copy(EXAMPLE_PROFILE / "resume_content.yaml", tmp_path / "resume_content.yaml")
    cfg = load_config(path)
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    store.upsert(_make_job(fit_verdict="apply", fit_score=8.5))
    store.save()
    return cfg, tmp_path, candidates(cfg, "comparison")


def research(*args, **kwargs):
    return WebEvidence(
        notes="Public salary benchmarks.",
        sources=[
            WebSource(
                url="https://example.org/market",
                title="Market benchmark",
                excerpt="Published ranges for L4 backend engineers in SF: $180k–$220k base.",
                kind="page",
            )
        ],
    )


def test_comparison_grounding_and_search_privacy(context):
    cfg, folder, jobs = context
    cfg.profile.highlights = ["PRIVATE_CANDIDATE_EXAMPLE"]
    prompts = []

    def search(client, prompt, **kwargs):
        prompts.append(prompt)
        return research()

    client = FakeClient(comparison(jobs[0]["job_key"]))
    report = build_report(
        cfg,
        folder,
        ResearchRequest(kind="comparison", job_keys=[jobs[0]["job_key"]]),
        jobs,
        client,
        progress=lambda _: None,
        researcher=search,
    )
    assert report["sources"][1]["url"] == "https://example.org/market"
    assert "PRIVATE_CANDIDATE_EXAMPLE" not in prompts[0]
    assert cfg.profile.name not in prompts[0]
    assert "PRIVATE_CANDIDATE_EXAMPLE" in client.prompt
    assert "jordan.rivera@example.com" not in client.prompt
    assert report["content"]["rows"][0]["salary"]["rating"] == "around market"


def test_research_failure_uses_unknown_not_fake_market_rating(context):
    cfg, folder, jobs = context

    def fail(*args, **kwargs):
        raise RuntimeError("SECRET token must not appear")

    report = build_report(
        cfg,
        folder,
        ResearchRequest(kind="comparison", job_keys=[jobs[0]["job_key"]]),
        jobs,
        FakeClient(comparison(jobs[0]["job_key"], ["posting-1"])),
        progress=lambda _: None,
        researcher=fail,
    )
    assert report["content"]["rows"][0]["salary"]["rating"] == "unknown"
    assert report["warnings"]
    assert "SECRET" not in json.dumps(report)


def test_interview_answers_link_exact_resume_evidence(context):
    cfg, folder, jobs = context
    result = build_report(
        cfg,
        folder,
        ResearchRequest(kind="interview", job_keys=["a"]),
        jobs,
        FakeClient(prep()),
        progress=lambda _: None,
        researcher=research,
    )
    assert "2M+" in result["evidence"]["experience/platform_co/b1"]
    assert "~30%" in result["evidence"]["experience/platform_co/b1"]
    assert result["content"]["questions"][0]["kind"] == "practice"


@pytest.mark.parametrize(
    "field,reference", [("source_ids", "invented"), ("evidence_ids", "invented")]
)
def test_unrecognized_references_rejected(field, reference):
    with pytest.raises(LLMError, match="unsupported citation"):
        validate_references({"nested": [{field: [reference]}]}, {"source"}, {"bullet"})


def test_unsourced_reported_question_becomes_practice(context):
    cfg, folder, jobs = context
    payload = prep()
    payload["questions"][0]["kind"] = "reported"
    out = build_report(
        cfg, folder, ResearchRequest(kind="interview", job_keys=["a"]), jobs,
        FakeClient(payload), progress=lambda _: None, researcher=research,
    )
    q = out["content"]["questions"][0]
    assert q["kind"] == "practice" and "couldn't be read" in q["purpose"]


def test_reported_question_from_listing_only_becomes_practice(context):
    cfg, folder, jobs = context

    def listing(*a, **k):
        return WebEvidence(sources=[WebSource(url="https://example.org/glassdoor",
                                              title="Interview reviews")])

    payload = prep()
    payload["questions"][0].update(kind="reported", source_ids=["web-2"])
    out = build_report(
        cfg, folder, ResearchRequest(kind="interview", job_keys=["a"]), jobs,
        FakeClient(payload), progress=lambda _: None, researcher=listing,
    )
    assert out["content"]["questions"][0]["kind"] == "practice"
    assert any("only search listings" in w for w in out["warnings"])


def test_invented_figure_in_answer_outline_is_flagged(context):
    cfg, folder, jobs = context
    payload = prep()
    payload["questions"][0]["answer_outline"] = (
        "Say you cut latency ~30% and served 2M+ requests; also mention 45% cost savings."
    )
    out = build_report(
        cfg, folder, ResearchRequest(kind="interview", job_keys=["a"]), jobs,
        FakeClient(payload), progress=lambda _: None, researcher=research,
    )
    note = out["content"]["questions"][0]["verify_note"]
    assert "45%" in note and "30%" not in note and "2M" not in note
    assert out["content"]["questions"][1]["verify_note"] == ""


@pytest.mark.parametrize("change,why", [
    ({"evidence_quote": "Salaries are well above average"}, "isn't in a cited page"),
    ({"evidence_quote": ""}, "no supporting quote"),
    ({"cohort": ""}, "cohort wasn't stated"),
])
def test_market_rating_needs_verifiable_quote_and_cohort(context, change, why):
    cfg, folder, jobs = context
    payload = comparison(jobs[0]["job_key"])
    payload["rows"][0]["salary"].update(change)
    out = build_report(
        cfg, folder, ResearchRequest(kind="comparison", job_keys=[jobs[0]["job_key"]]), jobs,
        FakeClient(payload), progress=lambda _: None, researcher=research,
    )
    salary = out["content"]["rows"][0]["salary"]
    assert salary["rating"] == "unknown" and why in salary["explanation"]
    assert out["content"]["rows"][0]["benefits"]["rating"] == "around market"  # untouched


def test_search_listing_is_not_market_evidence(context):
    cfg, folder, jobs = context

    def listing(*a, **k):
        return WebEvidence(sources=[WebSource(
            url="https://example.org/market", title="Market benchmark",
            excerpt="")])

    out = build_report(
        cfg, folder, ResearchRequest(kind="comparison", job_keys=[jobs[0]["job_key"]]), jobs,
        FakeClient(comparison(jobs[0]["job_key"])), progress=lambda _: None, researcher=listing,
    )
    assert out["content"]["rows"][0]["salary"]["rating"] == "unknown"


def test_wrong_job_in_comparison_rejected(context):
    cfg, folder, jobs = context
    with pytest.raises(LLMError, match="selected jobs"):
        build_report(
            cfg,
            folder,
            ResearchRequest(kind="comparison", job_keys=["a"]),
            jobs,
            FakeClient(comparison("wrong")),
            progress=lambda _: None,
            researcher=research,
        )


def test_market_rating_requires_citation():
    with pytest.raises(ValidationError):
        Finding(rating="above market", explanation="No evidence", source_ids=[])


def test_saved_posting_is_not_market_evidence(context):
    cfg, folder, jobs = context
    out = build_report(
        cfg,
        folder,
        ResearchRequest(kind="comparison", job_keys=["a"]),
        jobs,
        FakeClient(comparison(jobs[0]["job_key"], ["posting-1"])),
        progress=lambda _: None,
        researcher=research,
    )
    assert all(
        out["content"]["rows"][0][k]["rating"] == "unknown"
        for k in ("salary", "benefits", "company_reviews")
    )


def test_comparison_eligibility_and_company_key_collisions(context):
    cfg, _, _ = context
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    store.upsert(_make_job(company="Other Co", fit_verdict="consider", fit_score=6.0))
    store.upsert(_make_job(job_id="applied", status="applied", fit_verdict="apply"))
    store.upsert(_make_job(job_id="skip", fit_verdict="skip"))
    store.save()
    jobs = candidates(cfg, "comparison")
    assert len(jobs) == 2
    assert jobs[0]["job_key"] != jobs[1]["job_key"]
    assert job_key("A", "1") != job_key("B", "1")


def test_interview_only_after_application(context, seed_tracker_db):
    cfg, folder, _ = context
    seed_tracker_db(folder / "proj")
    assert [j["job_key"] for j in candidates(cfg, "interview")] == ["jb"]


def test_atomic_storage_and_profile_isolation(tmp_path):
    record = dict(id=str(uuid.uuid4()), created_at="2026-11-04", status="complete")
    save_report(tmp_path / "one", record)
    assert list_reports(tmp_path / "one") == [record]
    assert list_reports(tmp_path / "two") == []
    assert not list((tmp_path / "one").rglob("*.tmp"))
    with pytest.raises(ValueError):
        save_report(tmp_path, dict(record, id="../../escape"))


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://u:p@host"])
def test_unsafe_sources_rejected(url):
    assert not safe_url(url)
    col = _Collector()
    col.add(url, excerpt="x")
    assert col.sources() == []


# Shapes below were recorded from a live Claude Code 2.1.x session (Nov 2026).
LIVE_SEARCH_TEXT = (
    'Web search results for query: "Stripe software engineer salary"\n\n'
    'Links: [{"title":"Stripe L5 Software Engineer Salary","url":"https://www.levels.fyi/a"},'
    '{"title":"Stripe Software Engineer Salary","url":"https://www.levels.fyi/b"}]\n\n'
    "Based on the search results, Stripe pays well."
)


def test_search_links_from_structured_result_and_text():
    structured = {"query": "q", "results": [
        {"tool_use_id": "srv", "content": [{"title": "A", "url": "https://example.org/a"}]},
        "Some summary text",
    ]}
    assert _search_links("", structured) == [("https://example.org/a", "A")]
    assert _search_links(LIVE_SEARCH_TEXT, None) == [
        ("https://www.levels.fyi/a", "Stripe L5 Software Engineer Salary"),
        ("https://www.levels.fyi/b", "Stripe Software Engineer Salary"),
    ]


def test_failed_fetch_is_dropped_even_without_is_error():
    empty = ("I don't have any content to analyze. The web page content section in your "
             "message appears to be empty.")
    assert _fetch_text(empty, {"bytes": 0, "code": 202, "result": empty})[1] == "empty page"
    assert _fetch_text(empty, None)[1] == "no readable content"
    assert _fetch_text("x", {"code": 404, "bytes": 10})[1] == "HTTP 404"
    text, problem = _fetch_text("Base pay $180k–$220k.", {"code": 200, "bytes": 5000,
                                                           "result": "Base pay $180k–$220k."})
    assert problem == "" and "180k" in text


def test_collector_prefers_page_text_over_listing():
    col = _Collector()
    col.add("https://example.org/a", title="A")
    col.add("https://example.org/b", title="B")
    col.add("https://example.org/a", excerpt="Quoted text")
    col.add("https://example.org/a", excerpt="More text")
    first, second = col.sources()
    assert first.url.endswith("/a") and first.kind == "page"
    assert "Quoted text" in first.excerpt and "More text" in first.excerpt
    assert second.kind == "listing"


def test_api_search_bounds_and_tool_errors(monkeypatch):
    client = object.__new__(AnthropicAPIClient)
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            model_dump=lambda: {
                "content": [
                    {
                        "type": "text",
                        "text": "Unverified https://invented.example",
                        "citations": None,
                    },
                    {
                        "type": "web_search_tool_result",
                        "content": [
                            {"type": "web_search_result", "url": "https://example.org/real",
                             "title": "Real", "encrypted_content": "…", "page_age": "May 2026"},
                        ],
                    },
                    {
                        "type": "web_search_tool_result",
                        "content": {"type": "web_search_tool_result_error",
                                    "error_code": "max_uses_exceeded"},
                    },
                    {
                        "type": "text",
                        "text": "Base pay is around $200k.",
                        "citations": [{"type": "web_search_result_location",
                                       "url": "https://example.org/real", "title": "Real",
                                       "cited_text": "Base pay ranges from $180k to $220k."}],
                    },
                ]
            },
        )

    monkeypatch.setattr(client, "_create", create)
    result = web_research(client, "Public role only", model="test")
    assert seen["tools"][0]["max_uses"] == 6
    assert [s.url for s in result.sources] == ["https://example.org/real"]  # no invented link
    src = result.sources[0]
    assert src.kind == "page" and "180k" in src.excerpt and src.published == "May 2026"
    assert "1 search(es) failed" in result.warning


def test_routes_validate_selection_and_recover_interrupted(context):
    cfg, folder, jobs = context
    with TestClient(create_app(config_path=folder / "config.yaml")) as client:
        assert client.get("/api/research/options?kind=comparison").json()["jobs"][0]["job_key"]
        assert (
            client.post("/api/research", json={"kind": "comparison", "job_keys": []}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/research", json={"kind": "comparison", "job_keys": ["nope"]}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/research", json={"kind": "interview", "job_keys": ["a", "b"]}
            ).status_code
            == 422
        )
        key = jobs[0]["job_key"]
        assert (
            client.post(
                "/api/research", json={"kind": "comparison", "job_keys": [key, key]}
            ).status_code
            == 422
        )
        save_report(folder, dict(id=str(uuid.uuid4()), created_at="now", status="running"))
        assert client.get("/api/research").json()["reports"][0]["status"] == "failed"


def test_background_run_rejects_overlap_and_persists(context, monkeypatch):
    _, folder, jobs = context
    entered, release = threading.Event(), threading.Event()

    def build(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"content": comparison(jobs[0]["job_key"]), "sources": [], "warnings": []}

    monkeypatch.setattr("job_pipeline.ui.research_routes.build_llm", lambda: object())
    monkeypatch.setattr("job_pipeline.ui.research_routes.build_report", build)
    app = create_app(config_path=folder / "config.yaml")
    body = {"kind": "comparison", "job_keys": [jobs[0]["job_key"]]}
    with TestClient(app) as client:
        try:
            assert client.post("/api/research", json=body).status_code == 202
            assert entered.wait(3)
            assert client.post("/api/research", json=body).status_code == 409
            assert client.post("/api/run/daily").status_code == 409
        finally:
            release.set()
        for _ in range(100):
            rows = client.get("/api/research").json()["reports"]
            if rows[0]["status"] == "complete":
                break
            time.sleep(0.01)
        assert rows[0]["status"] == "complete"
        assert app.state.pipeline_state.running is None


def test_background_failure_is_retryable(context, monkeypatch):
    _, folder, jobs = context

    def fail():
        raise RuntimeError("SECRET")

    monkeypatch.setattr("job_pipeline.ui.research_routes.build_llm", fail)
    app = create_app(config_path=folder / "config.yaml")
    with TestClient(app) as client:
        response = client.post(
            "/api/research", json={"kind": "comparison", "job_keys": [jobs[0]["job_key"]]}
        )
        assert response.status_code == 202
        for _ in range(100):
            rows = client.get("/api/research").json()["reports"]
            if rows[0]["status"] == "failed":
                break
            time.sleep(0.01)
        assert rows[0]["status"] == "failed"
        assert "SECRET" not in json.dumps(rows)
        assert app.state.pipeline_state.running is None


def test_resume_missing_uses_profile_highlights(context):
    cfg, folder, _ = context
    (folder / "resume_content.yaml").unlink()
    cfg.profile.highlights = ["Built APIs"]
    assert candidate_evidence(cfg, folder) == {"highlight-1": "Built APIs"}


def test_cli_research_has_only_web_tools_and_enforces_caps(monkeypatch):
    import asyncio

    import claude_agent_sdk as sdk

    from job_pipeline.llm.research import _cli_research

    async def query(*, prompt, options):
        assert options.tools == ["WebSearch", "WebFetch"]
        assert options.allowed_tools == ["WebSearch", "WebFetch"]
        assert options.setting_sources == []
        assert options.model == "sonnet"
        hook = options.hooks["PreToolUse"][0].hooks[0]
        for name, limit in [("WebSearch", 6), ("WebFetch", 8)]:
            for _ in range(limit):
                assert await hook({"tool_name": name}, None, {"signal": None}) == {}
            denied = await hook({"tool_name": name}, None, {"signal": None})
            assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        yield sdk.AssistantMessage(content=[
            sdk.ToolUseBlock(id="s1", name="WebSearch", input={"query": "q"})], model="sonnet")
        yield sdk.UserMessage(content=[sdk.ToolResultBlock(
            tool_use_id="s1", content=LIVE_SEARCH_TEXT)])
        yield sdk.AssistantMessage(content=[
            sdk.ToolUseBlock(id="f1", name="WebFetch",
                             input={"url": "https://www.levels.fyi/a", "prompt": "pay"}),
            sdk.ToolUseBlock(id="f2", name="WebFetch",
                             input={"url": "https://blocked.example/x", "prompt": "pay"})],
            model="sonnet")
        yield sdk.UserMessage(
            content=[sdk.ToolResultBlock(tool_use_id="f1", content="L5 base $200k–$240k.")],
            tool_use_result={"code": 200, "bytes": 9000, "result": "L5 base $200k–$240k."})
        yield sdk.UserMessage(
            content=[sdk.ToolResultBlock(tool_use_id="f2",
                                         content="I don't have any content to analyze.")],
            tool_use_result={"code": 202, "bytes": 0, "result": ""})
        yield sdk.AssistantMessage(content=[sdk.TextBlock(text="Research notes")], model="sonnet")

    monkeypatch.setattr(sdk, "query", query)
    client = SimpleNamespace(cli_path="/fake/claude", _child_env=lambda: {})
    result = asyncio.run(_cli_research(client, "Public job only", "claude-sonnet-test"))
    by = {s.url: s for s in result.sources}
    assert set(by) == {"https://www.levels.fyi/a", "https://www.levels.fyi/b"}
    assert by["https://www.levels.fyi/a"].kind == "page"           # fetched → has text
    assert "200k" in by["https://www.levels.fyi/a"].excerpt
    assert by["https://www.levels.fyi/a"].title == "Stripe L5 Software Engineer Salary"
    assert by["https://www.levels.fyi/b"].kind == "listing"        # only searched
    assert "blocked.example" in result.warning                     # failed fetch reported
    assert result.notes == "Research notes"


def test_interrupted_status_is_persisted(context):
    _, folder, _ = context
    rid = str(uuid.uuid4())
    save_report(folder, dict(id=rid, created_at="now", status="running"))
    with TestClient(create_app(config_path=folder / "config.yaml")) as client:
        assert client.get("/api/research").json()["reports"][0]["status"] == "failed"
    assert list_reports(folder)[0]["status"] == "failed"  # written back, not just shown


def test_resume_only_jobs_are_comparable(context):
    cfg, _, _ = context
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    store.upsert(_make_job(job_id="rz", status="resume_only", fit_verdict="apply",
                           fit_score=9.0))
    store.save()
    assert any(j["title"] for j in candidates(cfg, "comparison") if j["fit_score"] == 9.0)


def test_reports_round_trip_through_profile_export(tmp_path, monkeypatch):
    from job_pipeline import paths, profiles
    from job_pipeline.research.service import import_reports

    monkeypatch.delenv("JOB_PIPELINE_PROFILE_DIR", raising=False)
    monkeypatch.setenv("JOB_PIPELINE_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(paths, "LEGACY_PROFILE_DIR", tmp_path / "profile")
    profiles.create("Sam")
    d = paths.profile_dir_for("sam")
    for name in ("config.yaml", "resume_content.yaml", "target_companies.yaml"):
        shutil.copy(EXAMPLE_PROFILE / name, d / name)
    done = dict(id=str(uuid.uuid4()), kind="comparison", status="complete",
                created_at="2026-11-04", content={"summary": "x"})
    save_report(d, done)
    save_report(d, dict(id=str(uuid.uuid4()), kind="interview", status="running",
                        created_at="2026-11-05"))
    bundle = profiles.export_profile("sam")
    assert [r["id"] for r in bundle["research"]] == [done["id"]]  # running one skipped
    assert "research" not in profiles.export_profile("sam", include_history=False)
    out = profiles.import_bundle(json.loads(json.dumps(bundle)), name="Sam copy")
    assert out["restored"]["research"] == 1
    assert list_reports(paths.profile_dir_for("sam-copy"))[0]["id"] == done["id"]
    # Malformed entries are skipped, never written outside the folder.
    bad = [{"id": "../../evil", "kind": "comparison", "status": "complete"},
           {"id": str(uuid.uuid4()), "kind": "nope", "status": "complete"}, "junk"]
    assert import_reports(tmp_path / "x", bad) == 0
    assert not (tmp_path / "evil.json").exists()


def test_live_copy_of_the_posting_is_not_market_evidence(context):
    """Regression from the first live run: the agent re-fetched the job ad and
    quoted its own pay range as a 'market' comparison."""
    cfg, folder, jobs = context
    url = jobs[0]["url"]

    def refetch(*a, **k):
        return WebEvidence(sources=[WebSource(
            url=url.replace("https://", "https://www.") + "?gh_src=x", title="Posting",
            excerpt="Annual Salary: $270,000 - $345,000 USD", kind="page")])

    payload = comparison(jobs[0]["job_key"])
    payload["rows"][0]["salary"].update(evidence_quote="Annual Salary: $270,000 - $345,000")
    out = build_report(
        cfg, folder, ResearchRequest(kind="comparison", job_keys=[jobs[0]["job_key"]]), jobs,
        FakeClient(payload), progress=lambda _: None, researcher=refetch,
    )
    assert out["sources"][1]["kind"] == "job posting"
    assert out["content"]["rows"][0]["salary"]["rating"] == "unknown"
    assert any("no market page" in w for w in out["warnings"])


def test_employer_page_alone_cannot_rate_market(context):
    """Regression from the second live run: benefits rated 'above market' from
    the employer's own careers page only."""
    cfg, folder, jobs = context
    company = jobs[0]["company"]

    def own_site(*a, **k):
        host = company.lower().replace(" ", "")
        return WebEvidence(sources=[WebSource(
            url=f"https://www.{host}.com/careers", title="Careers",
            excerpt="Published ranges for L4 backend engineers; 22 weeks of paid parental leave.",
            kind="page")])

    out = build_report(
        cfg, folder, ResearchRequest(kind="comparison", job_keys=[jobs[0]["job_key"]]), jobs,
        FakeClient(comparison(jobs[0]["job_key"])), progress=lambda _: None, researcher=own_site,
    )
    assert out["sources"][1]["kind"] == "employer page"
    assert out["content"]["rows"][0]["benefits"]["rating"] == "unknown"
    assert "independent market page" in out["content"]["rows"][0]["benefits"]["explanation"]
