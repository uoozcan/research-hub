"""Create Windows release artifacts from the tested application payload."""

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEM = "ResearchHub-0.1.0-windows-x64"


def package(payload: Path, output: Path, nsis: str):
    payload, output = payload.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    installer = output / f"{STEM}-setup.exe"
    with tempfile.TemporaryDirectory(dir=payload.parent, prefix="nsis-") as build_temp:
        # NSIS can spill sizable buffers to temporary files. Keep them on the build volume.
        environment = dict(os.environ, TMP=build_temp, TEMP=build_temp)
        subprocess.run(
            [nsis, "/V2", f"/DPAYLOAD={payload}", f"/DREMOVE_LIST={payload.parent / 'uninstall-files.nsh'}",
             f"/DOUTFILE={installer}", str(ROOT / "desktop/installer.nsi")],
            cwd=ROOT, env=environment, check=True,
        )
    archive = Path(shutil.make_archive(str(output / f"{STEM}-portable"), "zip",
                                       root_dir=payload.parent, base_dir=payload.name))
    checksums = []
    for file in (installer, archive):
        with file.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}")
    (output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii", newline="\n")
    print(f"Payload={payload}\nRelease={output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--nsis", required=True)
    args = parser.parse_args()
    package(args.payload, args.output, args.nsis)
