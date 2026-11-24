from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hub import config, literature
from hub.literature_ai import SynthError, validate
from hub.literature_destinations import DestinationError, Zotero, save_note, zotero_item
from hub.literature_sources import Batch, Record, SourceError, parse_arxiv, parse_pubmed, rss

FIXTURES = Path(__file__).with_name("fixtures")
NOW = datetime.fromisoformat("2026-11-24T08:30:00+02:00")


def fixture(name):
    return (FIXTURES / name).read_bytes()


def paper(source_id="100", **fields):
    return Record(**{
        "identity": f"pmid:{source_id}", "pmid": source_id, "source": "PubMed",
        "kind": "Journal article", "title": f"HLA typing across populations ({source_id})",
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{source_id}/",
        "authors": ("A. Smith",), "excerpt": "The benchmark evaluated a reference panel.",
        **fields,
    })


def config_for(tmp_path):
    cfg = config.load(path=config.ROOT / "hub.example.toml")
    hla = cfg.projects["methods"]
    hla.brain = tmp_path / "methods"
    hla.brain.mkdir()
    cfg.literature.projects = {
        "methods": config.LiteratureProfile("HLA typing", ["HLA typing"], ["benchmark"])
    }
    cfg.literature.filing_enabled = True
    cfg.literature.feeds = {}
    cfg.literature.auto_limit = 1
    config.load.cache_clear()
    return cfg


def test_source_parsers_and_labels():
    record, = parse_pubmed(fixture("pubmed.xml"))
    assert (record.pmid, record.doi, record.kind, record.published) == ("100", "10.1234/test.hla", "Journal article", "2026-11-23")
    assert record.excerpt == "RESULTS: A benchmark evaluated a reference panel."
    assert record.authors == ("Avery Smith",)
    preprint, = parse_arxiv(fixture("arxiv.xml"))
    assert (preprint.arxiv, preprint.kind, preprint.related_doi) == ("2611.01234", "Preprint", record.doi)
    assert preprint.updated == "2026-11-24T04:00:00+00:00"
    items = rss(type("Feed", (), {"fetch": lambda self, url: fixture("newsletter.xml")})(),
                "https://example.org/feed", "Newsletter example", NOW - timedelta(days=1)).records
    assert [(item.kind, item.title, item.url) for item in items] == [
        ("Newsletter", "New methods", "https://example.org/posts/methods"),
    ]
    with pytest.raises(SourceError):
        list(parse_pubmed(b'<!DOCTYPE data [<!ENTITY x "boom">]><PubmedArticleSet/>'))


def test_partial_run_recovers_without_duplicate_notes_or_refetching_healthy_source(tmp_path, monkeypatch):
    cfg = config_for(tmp_path)
    target = tmp_path / "data"
    calls = []
    ready = False

    def sources(settings, http, checkpoints, now):
        def get():
            calls.append("arXiv")
            return Batch([next(parse_arxiv(fixture("arxiv.xml")))])
        def get_pubmed():
            calls.append("PubMed")
            if not ready:
                raise SourceError("HTTP 503")
            return Batch([paper(doi="10.1234/test.hla"), paper("101"), paper("101", identity="alternate:101")])
        yield "PubMed", get_pubmed
        yield "arXiv", get

    monkeypatch.setattr(literature, "source_jobs", sources)
    monkeypatch.setattr(Zotero, "connect", lambda self: (_ for _ in ()).throw(DestinationError("Closed")))
    fallback = lambda *_: (_ for _ in ()).throw(SynthError("Offline model"))
    first = literature.run(cfg, target, now=NOW, codex=fallback)
    assert first["state"] == "partial"
    assert first["ai"]["status"] == "unavailable"
    assert literature.view(target, cfg)["counts"] == {"review": 0, "pending": 1, "filed": 0}
    notes = list(cfg.projects["methods"].brain.rglob("*.md"))
    note, = notes
    note.write_text(note.read_text(encoding="utf-8") + "\nUser note.\n", encoding="utf-8")

    ready = True
    synth = lambda *_: {"summaries": {}, "ideas": []}
    second = literature.run(cfg, target, now=NOW, codex=synth)
    assert second["state"] == "complete"
    assert second["total"] == 3
    assert calls == ["PubMed", "arXiv", "PubMed"]
    related = {item["identity"]: item["related"] for item in second["projects"]["methods"]}
    assert related["pmid:100"][0]["id"] == "arxiv:2611.01234"
    assert note.read_text(encoding="utf-8").endswith("User note.\n")
    assert len(list(cfg.projects["methods"].brain.rglob("*.md"))) == 1
    assert literature.view(target, cfg)["counts"] == {"review": 2, "pending": 1, "filed": 0}
    assert "TI  - " in literature.export_ris(target, "methods")
    assert literature.export_ris(target, "benchmark") == ""

    monkeypatch.setattr(Zotero, "connect", lambda self: None)
    monkeypatch.setattr(Zotero, "save", lambda self, project, record: "AAAATEST")
    literature.retry(cfg, directory=target)
    literature.retry(cfg, directory=target)
    assert literature.view(target, cfg)["counts"] == {"review": 2, "pending": 0, "filed": 1}
    literature.decide(cfg, "methods", "pmid:100", "save", target)
    literature.decide(cfg, "methods", "pmid:101", "dismiss", target)
    assert literature.view(target, cfg)["counts"] == {"review": 0, "pending": 0, "filed": 2}


def test_arxiv_revision_and_lock(tmp_path):
    with literature.database(tmp_path) as db:
        old, = parse_arxiv(fixture("arxiv.xml"))
        literature._save_record(db, old, "2026-11-24")
        literature._save_record(db, old, "2026-11-25")
        new = literature.replace(old, updated="2026-11-26T05:00:00+00:00", excerpt="Revised methods")
        literature._save_record(db, new, "2026-11-26")
        assert db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
        assert [r["day"] for r in db.execute("SELECT day FROM seen ORDER BY day")] == ["2026-11-24", "2026-11-26"]
    with literature.locked(tmp_path), pytest.raises(literature.Busy), literature.locked(tmp_path):
        pass


def test_existing_obsidian_note_by_doi_is_preserved(tmp_path):
    project = config_for(tmp_path).projects["methods"]
    folder = project.brain / "02_Literature"
    folder.mkdir()
    existing = folder / "User's note.md"
    markup = "---\ndoi: https://doi.org/10.1234/test.hla\n---\nMy annotations.\n"
    existing.write_text(markup, encoding="utf-8")
    assert save_note(project, paper(doi="10.1234/test.hla"), "Fit") == "02_Literature/User's note.md"
    assert existing.read_text(encoding="utf-8") == markup
    assert list(folder.glob("*.md")) == [existing]


def test_zotero_links_existing_item_with_version_precondition(tmp_path, monkeypatch):
    z = Zotero(tmp_path)
    project = config_for(tmp_path).projects["methods"]
    z.mapping[project.key] = "ABCDEFG1"
    matched = {"key": "ALREADY1", "version": 13, "data": {"collections": ["ORIGINAL"]}}
    monkeypatch.setattr(z, "matches", lambda record: [matched])
    writes = []
    monkeypatch.setattr(z, "request", lambda route, **opts: writes.append((route, opts)))
    assert z.save(project, paper()) == "ALREADY1"
    assert writes[1] == ("users/0/items/ALREADY1", {
        "data": {"collections": ["ABCDEFG1", "ORIGINAL"]}, "method": "PATCH",
        "headers": {"If-Unmodified-Since-Version": "13"},
    })
    assert zotero_item(next(parse_arxiv(fixture("arxiv.xml"))), "ABCDEFG1")["itemType"] == "preprint"


def test_synthesis_requires_supplied_references_and_project():
    payload = {"records": [{"id": "pmid:100"}], "project_briefs": {"methods": "HLA typing"}}
    with pytest.raises(SynthError):
        validate('{"ideas":[],"summaries":[{"id":"unknown","finding":"x","relevance":"y","limitation":"z"}]}', payload)
    valid = validate('{"ideas":[],"summaries":[{"id":"pmid:100","finding":"x","relevance":"y","limitation":"z"}]}', payload)
    assert valid["summaries"]["pmid:100"]["limitation"] == "z"
