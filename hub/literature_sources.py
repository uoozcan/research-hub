"""Bounded readers for public bibliographic records and newsletter feeds."""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ARXIV = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
DOI = re.compile(r"10\.\d{4,9}/\S+", re.I)
ARXIV_ID = re.compile(r"(?:[a-z.-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", re.I)
DATE_PARTS = ("Year", "Month", "Day")
MAX_BYTES = 8 * 1024 * 1024


class SourceError(Exception):
    """Safe, user-facing source failure without request parameters."""


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def clean(value, limit=3600):
    parser = Text()
    parser.feed(html.unescape(value or ""))
    return " ".join(" ".join(parser.parts).split())[:limit]


def xml(data):
    # The public endpoints use UTF-8 and do not require any XML declarations for entities.
    data = data.decode("utf-8-sig")
    # PubMed emits an external DTD reference. Strip it; reject internal declarations.
    data = re.sub(r'<!DOCTYPE\s+\w+\s+(?:PUBLIC\s+"[^"]*"\s+|SYSTEM\s+)"[^"]*"\s*>', "", data, flags=re.I)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.I):
        raise SourceError("Unexpected XML document type")
    return ET.fromstring(data)


def norm_doi(value):
    value = value.strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value if DOI.fullmatch(value) else ""


def norm_arxiv(value):
    value = re.sub(r"^(?:https?://(?:www\.|export\.)?arxiv\.org/abs/|arxiv:\s*)", "", value.strip(), flags=re.I)
    return re.sub(r"v\d+$", "", value, flags=re.I).lower() if ARXIV_ID.fullmatch(value) else ""


def iso(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return ""
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC).isoformat(timespec="seconds")


def _text(node, path):
    child = node.find(path)
    return " ".join("".join(child.itertext()).split()) if child is not None else ""


def safe_url(value):
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password and parts.port in (None, 443):
            return urllib.parse.urlunsplit(parts._replace(fragment=""))
    except (ValueError, UnicodeError):
        pass
    return ""


@dataclass
class Record:
    identity: str
    source: str
    title: str
    url: str
    kind: str
    published: str = ""
    updated: str = ""
    excerpt: str = ""
    authors: tuple[str, ...] = ()
    venue: str = ""
    doi: str = ""
    pmid: str = ""
    arxiv: str = ""
    related_doi: str = ""
    related_pmid: str = ""
    issn: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""

    def json(self):
        return asdict(self)


@dataclass
class Batch:
    records: list[Record]
    finish: bool = True


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_url(newurl) or urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            raise SourceError("Unexpected source redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicHTTP:
    def __init__(self):
        self.opener = urllib.request.build_opener(SameHostRedirect())
        self.next_request = {}

    def fetch(self, url, params=None, *, spacing=0):
        host = urllib.parse.urlsplit(url).hostname
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(3):
            time.sleep(max(0, self.next_request.get(host, 0) - time.monotonic()))
            self.next_request[host] = time.monotonic() + spacing
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ResearchHubLiterature/1.0"})
                with self.opener.open(req, timeout=20) as response:
                    data = response.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise SourceError("Source response exceeded size limit")
                    return data
            except urllib.error.HTTPError as exc:
                delay = exc.headers.get("Retry-After", "") if exc.headers else ""
                retry_at = iso(delay) if not delay.isdigit() else ""
                seconds = int(delay) if delay.isdigit() else (
                    max(0, (datetime.fromisoformat(retry_at) - datetime.now(UTC)).total_seconds())
                    if retry_at else 2 ** attempt
                )
                if attempt == 2 or exc.code not in (429, 500, 502, 503, 504) or seconds > 25:
                    raise SourceError(f"HTTP {exc.code}") from None
                time.sleep(seconds)
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                if attempt == 2:
                    detail = getattr(exc, "reason", exc)
                    if isinstance(detail, TimeoutError):
                        raise SourceError("Connection timed out") from None
                    raise SourceError(f"Connection failed ({type(detail).__name__})") from None
                time.sleep(2 ** attempt)


def _pubmed_date(article):
    for node in article.findall("./ArticleDate") + article.findall("./Journal/JournalIssue/PubDate"):
        year, month, day = (_text(node, p) for p in DATE_PARTS)
        if not year:
            medline = re.search(r"\b\d{4}\b", _text(node, "MedlineDate"))
            return medline.group() if medline else ""
        if month and not month.isdigit():
            try:
                month = str(datetime.strptime(month[:3], "%b").month)
            except ValueError:
                return year
        if month:
            try:
                return date(int(year), int(month), int(day or 1)).isoformat() if day else f"{year}-{int(month):02}"
            except ValueError:
                pass
        return year
    return ""


def parse_pubmed(data):
    root = xml(data)
    if root.find("ERROR") is not None or root.tag != "PubmedArticleSet":
        raise SourceError("Unexpected PubMed record response")
    for paper in root.findall("PubmedArticle"):
        citation = paper.find("MedlineCitation")
        article = paper.find("MedlineCitation/Article")
        if citation is None or article is None:
            continue
        pmid, title = _text(citation, "PMID"), clean(_text(article, "ArticleTitle"), 500)
        if not pmid.isascii() or not pmid.isdigit() or not title:
            continue
        ids = {node.get("IdType"): (node.text or "") for node in paper.findall("PubmedData/ArticleIdList/ArticleId")}
        types = {_text(node, ".").casefold() for node in article.findall("PublicationTypeList/PublicationType")}
        if types & {"retracted publication", "retraction of publication", "published erratum", "comment", "editorial", "news", "expression of concern"}:
            continue
        relations = citation.findall("CommentsCorrectionsList/CommentsCorrections")
        if any(n.get("RefType") in {"RetractionIn", "ExpressionOfConcernIn"} for n in relations):
            continue
        preprint = "preprint" in types
        kind = "Preprint" if preprint else ("Guideline" if types & {"guideline", "practice guideline"} else "Journal article")
        related = next((_text(n, "PMID") for n in relations if n.get("RefType") in {"UpdateIn", "UpdateOf", "PreprintIn", "PreprintOf"}), "")
        authors = tuple(
            _text(a, "CollectiveName") or " ".join(filter(None, (_text(a, "ForeName") or _text(a, "Initials"), _text(a, "LastName"))))
            for a in article.findall("AuthorList/Author")
        )
        doi = norm_doi(ids.get("doi", ""))
        if not doi:
            doi = next((norm_doi(n.text or "") for n in article.findall("ELocationID") if n.get("EIdType") == "doi"), "")
        yield Record(
            identity=f"pmid:{pmid}", source="PubMed", title=title, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            kind=kind, published=_pubmed_date(article), authors=tuple(filter(None, authors)),
            excerpt=clean(" ".join(
                ((n.get("Label") + ": ") if n.get("Label") else "") + _text(n, ".")
                for n in article.findall("Abstract/AbstractText")
            )),
            doi=doi, pmid=pmid, venue=clean(_text(article, "Journal/Title"), 200),
            related_pmid=related if related.isascii() and related.isdigit() else "",
            issn=_text(article, "Journal/ISSN"), volume=_text(article, "Journal/JournalIssue/Volume"),
            issue=_text(article, "Journal/JournalIssue/Issue"), pages=_text(article, "Pagination/MedlinePgn"),
        )


def pubmed(http, query, since, until):
    auth = {"tool": "ResearchHubLiterature"}
    for env, param in (("RESEARCH_HUB_NCBI_EMAIL", "email"), ("RESEARCH_HUB_NCBI_API_KEY", "api_key")):
        if os.environ.get(env):
            auth[param] = os.environ[env]
    result = []
    size = 200
    dates = dict(datetype="edat", mindate=since.strftime("%Y/%m/%d"), maxdate=until.strftime("%Y/%m/%d"))
    for start in range(0, 1600, size):
        data = json.loads(http.fetch(NCBI + "/esearch.fcgi",
            dict(auth, **dates, db="pubmed", term=query, sort="pub_date", retmode="json", retmax=size, retstart=start),
            spacing=0.4))
        search = data.get("esearchresult", {})
        if "count" not in search or "idlist" not in search:
            raise SourceError("Invalid PubMed search response")
        ids = search["idlist"]
        for index in range(0, len(ids), 100):
            result.extend(parse_pubmed(http.fetch(NCBI + "/efetch.fcgi",
                dict(auth, db="pubmed", id=",".join(ids[index:index + 100]), retmode="xml"), spacing=0.4)))
        if not ids or start + len(ids) >= int(search["count"]):
            return Batch(result)
    return Batch(result, finish=False)


def parse_arxiv(data):
    root = xml(data)
    if root.tag != ATOM + "feed":
        raise SourceError("Unexpected arXiv response")
    for entry in root.findall(ATOM + "entry"):
        raw = _text(entry, ATOM + "id")
        if "/api/errors" in raw:
            raise SourceError("arXiv rejected the search query")
        arc = norm_arxiv(raw)
        title = clean(_text(entry, ATOM + "title"), 500)
        if not arc or not title:
            continue
        yield Record(
            identity=f"arxiv:{arc}", source="arXiv", title=title, url=f"https://arxiv.org/abs/{arc}",
            kind="Preprint", excerpt=clean(_text(entry, ATOM + "summary")),
            published=iso(_text(entry, ATOM + "published")), updated=iso(_text(entry, ATOM + "updated")),
            authors=tuple(_text(a, ATOM + "name") for a in entry.findall(ATOM + "author")),
            arxiv=arc, related_doi=norm_doi(_text(entry, ARXIV_NS + "doi")),
        )


def arxiv(http, query, since, _until):
    result = []
    size = 100
    boundary = since.astimezone(UTC).isoformat(timespec="seconds")
    for start in range(0, 1000, size):
        batch = list(parse_arxiv(http.fetch(ARXIV,
            dict(search_query=query, start=start, max_results=size, sortBy="lastUpdatedDate", sortOrder="descending"),
            spacing=3.1)))
        recent = [r for r in batch if (r.updated or r.published) >= boundary]
        result.extend(recent)
        if len(batch) < size or len(recent) < len(batch):
            return Batch(result)
    return Batch(result, finish=False)


def rss(http, url, name, since):
    if not safe_url(url):
        raise SourceError("Feed must be a public HTTPS URL")
    root = xml(http.fetch(url))
    if root.tag not in {"rss", ATOM + "feed"}:
        raise SourceError("Expected RSS or Atom feed")
    result = []
    boundary = since.astimezone(UTC).isoformat(timespec="seconds")
    for item in root.findall("./channel/item") + root.findall(ATOM + "entry"):
        atom = item.tag == ATOM + "entry"
        link = next((x.get("href", "") for x in item.findall(ATOM + "link") if x.get("rel", "alternate") == "alternate"), "") if atom else _text(item, "link")
        url = safe_url(link)
        title = clean(_text(item, ATOM + "title" if atom else "title"), 500)
        published = iso(_text(item, ATOM + "published" if atom else "pubDate"))
        updated = iso(_text(item, ATOM + "updated")) if atom else published
        if not url or not title or ((updated or published) and (updated or published) < boundary):
            continue
        # Newsletter query strings are tracking data, not article identity.
        canonical = urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(query=""))
        author = _text(item, ATOM + "author/" + ATOM + "name" if atom else DC_CREATOR)
        result.append(Record(f"url:{canonical}", name, title, canonical, "Newsletter", published,
                             updated, clean(_text(item, ATOM + "summary" if atom else "description"), 900),
                             (author,) if author else (), name))
    return Batch(result)


def source_jobs(settings, http, checkpoints, now):
    def since(key):
        last = checkpoints.get(key)
        return datetime.fromisoformat(last) - timedelta(hours=48) if last else now - timedelta(hours=72)

    if settings.pubmed_query:
        yield "PubMed", lambda: pubmed(http, settings.pubmed_query, since("PubMed"), now)
    if settings.arxiv_query:
        yield "arXiv", lambda: arxiv(http, settings.arxiv_query, since("arXiv"), now)
    for name, url in settings.feeds.items():
        yield name, lambda name=name, url=url: rss(http, url, name, since(name))
