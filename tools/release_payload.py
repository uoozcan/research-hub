"""Assemble distribution notices and an exact uninstall list for the built payload."""

import argparse
import importlib.metadata
import os
import re
import shutil
import sys
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ["fastapi", "uvicorn", "mammoth", "pyyaml", "tzdata", "colorama", "pyinstaller"]
LICENSE_NAME = re.compile(r"^(license|licence|licensing|copying|notice|third[ ._-]*party[ ._-]*notices)(?:[._-]|\b)", re.I)


def python_licenses(target):
    todo = RUNTIME.copy()
    handled = set()
    while todo:
        dist = importlib.metadata.distribution(todo.pop())
        key = dist.metadata["Name"].lower()
        if key in handled:
            continue
        handled.add(key)
        destination = target / f"{key}-{dist.version}"
        for filename in dist.files or ():
            if LICENSE_NAME.match(filename.name) and ".dist-info/" in filename.as_posix():
                file = Path(dist.locate_file(filename))
                if file.is_file():
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, destination / filename.name)
        for dependency in dist.requires or ():
            req = Requirement(dependency)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                todo.append(req.name)


def other_licenses(target, nsis):
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", target / "CPython.txt")
    packages = Path(os.environ["NUGET_PACKAGES"])
    for package in ("microsoft.netcore.app.runtime.win-x64", "microsoft.windowsdesktop.app.runtime.win-x64",
                    "microsoft.aspnetcore.app.runtime.win-x64", "microsoft.web.webview2"):
        files = [path for version in (packages / package).iterdir()
                 for path in version.iterdir() if path.is_file() and LICENSE_NAME.match(path.name)]
        if not files:
            raise RuntimeError(f"No distribution notice found for {package}")
        for path in files:
            destination = target / f"{package}-{path.parent.name}"
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination / path.name)
    compiler = Path(shutil.which(nsis) or nsis)
    for candidate in (compiler.parent / "COPYING", compiler.parent.parent / "COPYING"):
        if candidate.is_file():
            shutil.copyfile(candidate, target / "NSIS.txt")
            break
    else:
        raise RuntimeError("No NSIS license found next to the compiler")
    shutil.copytree(ROOT / "static/vendor/licenses", target / "JavaScript")


def uninstall_list(payload):
    def name(path):
        return str(path.relative_to(payload)).replace("/", "\\").replace("$", "$$")

    files = sorted(p for p in payload.rglob("*") if p.is_file())
    dirs = sorted((p for p in payload.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts))
    commands = [f'Delete "$INSTDIR\\{name(path)}"' for path in files]
    commands += [f'RMDir "$INSTDIR\\{name(path)}"' for path in dirs]
    (payload.parent / "uninstall-files.nsh").write_text("\n".join(commands) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("--nsis", required=True)
    args = parser.parse_args()
    target = args.payload / "ThirdPartyLicenses"
    target.mkdir()
    python_licenses(target)
    other_licenses(target, args.nsis)
    uninstall_list(args.payload)
