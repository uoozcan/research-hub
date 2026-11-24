# Research Hub

A local-first workspace for research projects. Track recorded work, review decisions and blockers, and keep a literature briefing alongside project notes.

- **Overview** brings together priorities, a daily activity calendar, and the morning brief.
- **Literature** collects public PubMed, arXiv, or RSS records when configured, with optional synthesis and library filing.
- **Timeline, Progress, and Second brain** connect indexed events with local notes, summaries, and figures.

## Windows desktop

Download the installer or portable ZIP from [GitHub Releases](https://github.com/uoozcan/research-hub/releases). The first release targets Windows 11 x64. Run per-user setup and launch **Research Hub** from Start, or extract the ZIP and run **ResearchHub.exe**. Release binaries are unsigned. Compare the SHA-256 digest with the release checksums.

The app uses Microsoft Edge WebView2 Evergreen Runtime. If it is missing, the app offers Microsoft's download page. It opens with an offline example workspace. Use **File → Open workspace folder** to find your configuration. The default is `%LOCALAPPDATA%\ResearchHub\workspace`. Launch `ResearchHub.exe --workspace "C:\path\to\workspace"` to use a separate directory. The native window uses loopback port `18765` by default. Use `--port` if it is occupied.

## Run from source

Use Python 3.13. Git is needed only to index Git repositories.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m hub serve
```

The browser launch binds to `127.0.0.1`. Set `RESEARCH_HUB_HOME` to choose a workspace directory.

## Configure your workspace

Edit your private `hub.toml` and restart the app. Use `hub.example.toml` as a starting point. Set `demo = false` when adding your own data. Paths may be absolute or relative to the workspace configuration.

```toml
[hub]
timezone = "UTC"
demo = false

[projects.benchmark]
name = "Benchmark project"
repos = ["C:/Research/benchmark"]
brain = "C:/Research/notes"
reports = ["C:/Research/benchmark/reports"]

[literature]
pubmed_query = "\"reproducibility\"[Title/Abstract]"
synthesis_enabled = false
filing_enabled = false
auto_limit = 0
```

Select **Refresh** to index configured sources. Optional `claude_logs` and `codex_sessions` directories enable local AI-session indexing. A separate `synthesis_enabled = true` setting allows the signed-in Codex CLI to receive curated public excerpts and short project briefs. Keep those briefs suitable for external model processing.

Add project profiles under `[literature.projects.<project>]` with `brief`, `direct`, and `context` fields, and public newsletter feed URLs under `[literature.feeds]`. Literature collection starts on request. Set `filing_enabled = true` to allow library writes. Obsidian uses each project's `brain` folder. Automatic Zotero filing requires Zotero 10+ and one-time `python -m hub literature zotero-setup`. RIS export is available for pending Zotero entries. To opt into scheduling, use Windows Task Scheduler with the intended workspace and `python -m hub literature run --log` or the bundled backend's equivalent command.

Import into a new directory with `python -m hub workspace import --from SOURCE` after setting `RESEARCH_HUB_HOME` to the destination. The command copies configuration, SQLite history, and workspace documents. Enable write-capable integrations separately after importing. App upgrades and uninstall leave workspace data on disk.

## Build and test

Use Python 3.13 x64, .NET 10 SDK, and NSIS 3.13.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe -m pytest -q
pwsh -File tools/build_windows.ps1 -Python .venv/Scripts/python.exe -Makensis "C:/Tools/NSIS/makensis.exe" -OutputDir artifacts
```

See the [MIT license](LICENSE) and [third-party notices](THIRD_PARTY.md).
