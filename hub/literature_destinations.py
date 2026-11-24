"""Writes for the explicitly configured Obsidian and local Zotero destinations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from .config import is_guarded
from .literature_sources import norm_arxiv, norm_doi

SERVER = "http://127.0.0.1:23119/api/"
COLLECTION = re.compile(r"[A-Z0-9]{8}")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DestinationError(Exception):
    pass


def atomic_file(path, contents, *, replace=True):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        staged = Path(stream.name)
        stream.write(contents)
    try:
        if replace:
            os.replace(staged, path)
        else:
            os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _key_text(value):
    return " ".join(re.findall(r"\w+", str(value or "").casefold()))


def title_match(record, title, authors):
    return bool(record.authors and authors and _key_text(record.title) == _key_text(title)
                and _key_text(record.authors[0]).split()[-1:] == _key_text(authors[0]).split()[-1:])


def _under(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or is_guarded(resolved):
        raise DestinationError("Literature path escapes the configured vault")
    return resolved


def save_note(project, record, reason):
    if project.read_only or not project.brain or not project.brain.is_dir():
        raise DestinationError("Project Obsidian vault is unavailable")
    brain = project.brain.resolve()
    if is_guarded(brain):
        raise DestinationError("Project Obsidian vault is protected")
    literature = _under(brain / "02_Literature", brain)
    for path in literature.rglob("*.md"):
        if path.is_symlink() or is_guarded(path) or not path.resolve().is_relative_to(literature):
            continue
        with path.open(encoding="utf-8-sig", errors="replace") as stream:
            if stream.readline().strip() != "---":
                continue
            frontmatter = stream.read(12_000).split("\n---", 1)
        if len(frontmatter) != 2:
            continue
        try:
            meta = yaml.safe_load(frontmatter[0])
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict):
            continue
        authors = meta.get("authors", [])
        if isinstance(authors, str):
            authors = [authors]
        if (meta.get("research_hub_id") == record.identity
                or (record.doi and norm_doi(str(meta.get("doi") or "")) == record.doi)
                or (record.pmid and str(meta.get("pmid") or "") == record.pmid)
                or (record.arxiv and norm_arxiv(str(meta.get("arxiv") or "")) == record.arxiv)
                or (isinstance(authors, list) and title_match(record, meta.get("title"), authors))):
            return str(path.relative_to(brain).as_posix())
    folder = _under(literature / "Research Hub", brain)
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", record.title.lower()).strip("-")[:68].rstrip("-") or "paper"
    suffix = hashlib.sha256(record.identity.encode()).hexdigest()[:12]
    path = _under(folder / f"{slug}-{suffix}.md", folder)
    props = {"research_hub_id": record.identity, "title": record.title,
             "authors": list(record.authors), "published": record.published, "source": record.source,
             "evidence_type": record.kind, "url": record.url, "venue": record.venue,
             "doi": record.doi, "pmid": record.pmid, "arxiv": record.arxiv,
             "related_doi": record.related_doi, "related_pmid": record.related_pmid,
             "tags": ["literature", "research-hub", project.key]}
    lines = ["---", yaml.safe_dump(props, sort_keys=False, allow_unicode=True).rstrip(), "---",
             "", "## Project relevance", "", reason, "", "## Source", "", f"<{record.url}>", ""]
    try:
        atomic_file(path, "\n".join(lines).encode("utf-8"), replace=False)
    except FileExistsError as exc:
        raise DestinationError("An existing note uses the generated filename") from exc
    return path.relative_to(brain).as_posix()


def _dpapi(action, value):
    if os.name != "nt":
        raise DestinationError("Zotero credential storage requires Windows")
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$value = [Console]::In.ReadToEnd()
$scope = [Security.Cryptography.DataProtectionScope]::CurrentUser
if ('ACTION' -eq 'protect') {
    $bytes = [Text.Encoding]::UTF8.GetBytes($value)
    [Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect($bytes, $null, $scope))
} else {
    $bytes = [Convert]::FromBase64String($value)
    [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, $scope))
}
""".replace("ACTION", action)
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                input=value, text=True, capture_output=True, timeout=12, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DestinationError("Windows could not access Zotero credential storage") from exc
    if result.returncode or not result.stdout.strip():
        raise DestinationError("Windows could not access Zotero credential storage")
    return result.stdout.strip()


class LocalOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DestinationError("Zotero local API returned an unexpected redirect")


class Zotero:
    def __init__(self, directory):
        self.path = directory / "zotero-local.json"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalOnly())
        self.server = ""
        self.key = ""
        self.mapping = {}

    def request(self, route="", *, data=None, method=None, headers=None, timeout=8):
        req_headers = {"Zotero-API-Version": "3", "Accept": "application/json", **(headers or {})}
        if self.server:
            req_headers["Zotero-Server-ID"] = self.server
        if self.key:
            req_headers["Zotero-API-Key"] = self.key
        if data is not None:
            req_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(SERVER + route, data=None if data is None else json.dumps(data).encode(),
                                         method=method, headers=req_headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise DestinationError("Zotero response too large")
                result = json.loads(body) if body and "json" in response.headers.get("Content-Type", "") else None
                return result, response.headers
        except urllib.error.HTTPError as exc:
            message = {
                401: "Zotero write authorization expired",
                403: "Zotero local access is disabled or authorization was denied",
                404: "Zotero collection or endpoint is unavailable",
                409: "Zotero item changed during filing",
                412: "Zotero server or item changed during filing",
            }.get(exc.code, f"Zotero returned HTTP {exc.code}")
            raise DestinationError(message) from None
        except (TimeoutError, OSError, ValueError):
            raise DestinationError("Zotero desktop local API is unavailable") from None

    def identify(self):
        _, headers = self.request()
        server = headers.get("Zotero-Server-ID", "")
        if not server:
            raise DestinationError("Automatic filing requires the Zotero 10+ local write API")
        self.server = server

    def connect(self):
        if not self.path.exists():
            raise DestinationError("Zotero collections and write access need one-time setup")
        config = json.loads(self.path.read_text(encoding="utf-8"))
        self.identify()
        if config["server"] != self.server:
            raise DestinationError("Zotero library changed; run setup again")
        self.key = _dpapi("unprotect", config["credential"])
        self.mapping = config["collections"]

    def setup(self, projects, explicit, *, create=False):
        self.identify()
        collections = self.list_collections()
        missing = {}
        for project in projects:
            key = explicit.get(project.key)
            if key:
                if not COLLECTION.fullmatch(key) or key not in collections:
                    raise DestinationError(f"Invalid collection for {project.name}")
                self.mapping[project.key] = key
                continue
            names = {project.name.casefold(), f"research hub — {project.name.casefold()}"}
            matches = [k for k, value in collections.items() if value["name"].casefold() in names]
            if len(matches) == 1:
                self.mapping[project.key] = matches[0]
            elif len(matches) > 1:
                raise DestinationError(f"Multiple collections match {project.name}; provide its collection key")
            else:
                missing[project.key] = f"Research Hub — {project.name}"
        if missing and not create:
            raise DestinationError("Missing Zotero collections for " + ", ".join(missing) + "; use --create or explicit mappings")
        grant, _ = self.request("local/authorize", data={"appName": "Research Hub"}, timeout=120)
        if not isinstance(grant, dict) or not grant.get("remember") or not grant.get("key"):
            raise DestinationError("Choose Always Allow in Zotero to enable unattended filing")
        self.key = grant["key"]
        if missing:
            values, _ = self.request("users/0/collections", data=[{"name": name} for name in missing.values()])
            for idx, key in enumerate(missing):
                self.mapping[key] = written_key(values, idx)
        config = {"server": self.server, "credential": _dpapi("protect", self.key), "collections": self.mapping}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_file(self.path, json.dumps(config, indent=2).encode("utf-8"))
        return self.mapping

    def list_collections(self):
        return {row["key"]: row["data"] for row in self.pages("users/0/collections")}

    def pages(self, route, **params):
        for start in range(0, 10000, 100):
            query = urllib.parse.urlencode({**params, "start": start, "limit": 100})
            data, _ = self.request(f"{route}?{query}")
            if not isinstance(data, list):
                raise DestinationError("Zotero returned an invalid collection or item list")
            yield from data
            if len(data) < 100:
                return
        raise DestinationError("Zotero search exceeded the item limit")

    def save(self, project, record):
        collection = self.mapping.get(project.key, "")
        if project.read_only or not COLLECTION.fullmatch(collection):
            raise DestinationError("Project Zotero collection is not mapped")
        self.request(f"users/0/collections/{collection}")
        matches = self.matches(record)
        if len(matches) > 1:
            raise DestinationError("Multiple Zotero items match this paper")
        if matches:
            existing = matches[0]
            data = existing["data"]
            if collection not in data.get("collections", []):
                self.request(f"users/0/items/{existing['key']}",
                             data={"collections": sorted({*data.get("collections", []), collection})},
                             method="PATCH", headers={"If-Unmodified-Since-Version": str(existing["version"])})
            return existing["key"]
        payload = zotero_item(record, collection)
        # Persistent markers and lookups reconcile delayed or uncertain results across restarts.
        token = hashlib.sha256((record.identity + "|" + collection).encode()).hexdigest()[:28]
        result, _ = self.request("users/0/items", data=[payload], headers={"Zotero-Write-Token": token})
        return written_key(result, 0)

    def matches(self, record):
        matches = {}
        for query in dict.fromkeys(filter(None, (record.doi, record.pmid, record.arxiv, record.title))):
            for row in self.pages("users/0/items/top", q=query, qmode="everything"):
                data = row["data"]
                if data.get("itemType") in {"attachment", "note"}:
                    continue
                extra = data.get("extra", "")
                authors = [c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}" for c in data.get("creators", [])]
                if (f"Research Hub ID: {record.identity}" in extra.splitlines()
                        or (record.doi and norm_doi(data.get("DOI", "")) == record.doi)
                        or (record.pmid and (f"PMID: {record.pmid}" in extra.splitlines() or data.get("url") == record.url))
                        or (record.arxiv and (norm_arxiv(data.get("url", "")) == record.arxiv
                            or f"arXiv: {record.arxiv}" in extra.splitlines()))
                        or (title_match(record, data.get("title"), authors)
                            and (data.get("itemType") == "preprint") == (record.kind == "Preprint")
                            and (not record.doi or not data.get("DOI") or norm_doi(data["DOI"]) == record.doi))):
                    matches[row["key"]] = row
        return list(matches.values())


def written_key(response, index):
    index = str(index)
    value = (response or {}).get("success", {}).get(index)
    if not value:
        value = (response or {}).get("successful", {}).get(index, {}).get("key")
    if not value or not COLLECTION.fullmatch(value):
        failure = (response or {}).get("failed", {}).get(index, {}).get("code")
        raise DestinationError(f"Zotero did not confirm the write{f' (code {failure})' if failure else ''}")
    return value


def zotero_item(record, collection):
    creators = []
    for name in record.authors:
        first, sep, last = name.rpartition(" ")
        creators.append({"creatorType": "author", "firstName": first, "lastName": last}
                        if sep else {"creatorType": "author", "name": name})
    extra = [f"Research Hub ID: {record.identity}"]
    if record.pmid:
        extra.append(f"PMID: {record.pmid}")
    if record.arxiv:
        extra.append(f"arXiv: {record.arxiv}")
    if record.related_doi:
        extra.append(f"Related journal DOI: {record.related_doi}")
    pre = record.kind == "Preprint"
    fields = {"repository": (record.venue or record.source)} if pre else {
        "publicationTitle": record.venue, "volume": record.volume, "issue": record.issue,
        "pages": record.pages, "ISSN": record.issn,
    }
    return {**fields, "itemType": "preprint" if pre else "journalArticle",
            "title": record.title, "date": record.published[:10], "url": record.url, "DOI": record.doi,
            "abstractNote": record.excerpt, "creators": creators, "collections": [collection],
            "tags": [{"tag": "research-hub"}], "extra": "\n".join(extra)}


def ris(records):
    blocks = []
    for record in records:
        lines = ["TY  - " + ("UNPB" if record.kind == "Preprint" else "JOUR"), f"TI  - {record.title}"]
        lines.extend(f"AU  - {author}" for author in record.authors)
        for tag, value in (("PY", record.published[:4]), ("JO", record.venue), ("DO", record.doi),
                           ("UR", record.url), ("AN", record.pmid), ("AB", record.excerpt)):
            if value:
                lines.append(f"{tag}  - {' '.join(value.splitlines())}")
        blocks.append("\r\n".join([*lines, "ER  - ", ""]))
    return "\r\n".join(blocks)
