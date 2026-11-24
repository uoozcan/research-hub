"""python -m hub refresh | literature | serve [--no-browser] | facts [--project P] [--period week] [--key K | --missing]"""

import sys

from . import workspace


def facts(argv: list[str]) -> None:
    """Print the facts for one period, or list periods that have activity but no summary yet.

    Used by the /hub-summary command so Claude Code or Codex can write narratives without the server.
    """
    import argparse
    import json

    from . import app, config

    ap = argparse.ArgumentParser(prog="python -m hub facts")
    ap.add_argument("--project", default="all")
    ap.add_argument("--period", default="week", choices=["day", "week", "month"])
    ap.add_argument("--key", help="e.g. 2026-10-15, 2026-W42 or 2026-10")
    ap.add_argument("--missing", action="store_true", help="list periods with activity and no summary")
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args(argv)
    keys = list(config.load().projects) if a.project == "all" else [a.project]
    out = []
    for k in keys:
        rows = app.progress(project=k, period=a.period, limit=400)
        for r in rows:
            if a.key and r["key"] != a.key:
                continue
            if a.missing:
                f = config.WORKSPACE / "summaries" / k / a.period / f"{r['key']}.md"
                if f.exists():
                    continue
                r = {"project": k, "key": r["key"], "counts": r["counts"],
                     "file_to_write": str(f)}
            else:
                r = {"project": k, **r}
            out.append(r)
    out = out[: a.limit] if a.missing else out
    print(json.dumps(out, indent=1, ensure_ascii=False))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "workspace":
        import argparse
        from pathlib import Path
        from . import config

        parser = argparse.ArgumentParser(prog="ResearchHubBackend workspace")
        parser.add_argument("action", choices=["init", "import"])
        parser.add_argument("--from", dest="source", type=Path)
        args = parser.parse_args(sys.argv[2:])
        if args.action == "import":
            if not args.source:
                parser.error("--from is required")
            workspace.import_workspace(args.source, config.WORKSPACE)
        else:
            workspace.prepare()
        print(config.WORKSPACE)
        return
    if cmd == "desktop-server":
        from .desktop_server import run

        run()
        return
    if cmd not in {"refresh", "literature", "facts", "serve"}:
        sys.exit(__doc__)
    workspace.prepare()
    if cmd == "refresh":
        from . import config
        if config.load().demo:
            from .demo import seed
            seed(refresh=True)
            return
        from .refresh import run
        if "--log" in sys.argv:  # scheduled runs use pythonw (no console), so write to a file
            import datetime
            log = sys.argv[sys.argv.index("--log") + 1]
            with open(log, "a", encoding="utf-8") as fh:
                sys.stdout = sys.stderr = fh
                print(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M} scheduled refresh")
                try:
                    run()
                except Exception as e:  # keep the log useful even when a run fails
                    print(f"FAILED: {type(e).__name__}: {e}")
                    raise
        else:
            run()
    elif cmd == "literature":
        from .literature import cli
        sys.exit(cli(sys.argv[2:]))
    elif cmd == "facts":
        facts(sys.argv[2:])
    elif cmd == "serve":
        import threading
        import webbrowser

        import uvicorn

        from .config import load
        port = load().port
        if "--no-browser" not in sys.argv:
            threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        uvicorn.run("hub.app:app", host="127.0.0.1", port=port, log_level="warning", use_colors=False)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
