"""Gathers everything an installed Vyum ships into one folder (build/resources), for the macOS and Windows packagers.

    python scripts/package/assemble.py            # uses ../genoffice next to this repo

Layout (the engine finds it through VYUM_RESOURCES, see engine/config.py):
    runtime/python/   Python 3.12 (python-build-standalone, via uv) with the pinned packages installed
    engine/           Vyum's engine (Python sources)
    genoffice/        GenOffice's built app (Apache-2.0): GenOffice.app on macOS, the unpacked folder on Windows.
                      Genspark's cloud CLI (resources/gsk) is removed: Vyum never uses cloud services.
    pdfhost/          The PDF editor: host.cjs + vyum-bridge.js (built from pdfhost/), GenOffice's PDF screen
                      (renderer/), an Electron runtime (electron/), and the wasm packages the host loads (go/)
    licenses/         Licence texts of what is shipped
Models are not here: the app downloads them on first launch (engine/setup.py).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GO = Path(os.environ.get("GENOFFICE_DIR", ROOT.parent / "genoffice"))
OUT = ROOT / "build" / "resources"
MAC, WIN = sys.platform == "darwin", sys.platform == "win32"
PY_VERSION = "3.12"


def run(*cmd, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True, **kw)


def copytree(src: Path, dst: Path, ignore=None):
    """Copies keeping symlinks (macOS app bundles rely on them)."""
    if MAC:
        dst.parent.mkdir(parents=True, exist_ok=True)
        run("ditto", src, dst)
    else:
        shutil.copytree(src, dst, symlinks=True, ignore=ignore, dirs_exist_ok=True)


def python_runtime():
    """A relocatable Python (python-build-standalone, the build uv manages) with the pinned packages."""
    run("uv", "python", "install", PY_VERSION)
    exe = Path(subprocess.run(["uv", "python", "find", PY_VERSION], capture_output=True, text=True,
                              check=True).stdout.strip())
    home = exe.parent.parent if MAC else exe.parent  # macOS: <home>/bin/python3.12; Windows: <home>/python.exe
    dst = OUT / "runtime" / "python"
    copytree(home, dst)
    py = dst / ("python.exe" if WIN else "bin/python3")
    lock = ROOT / "scripts/package" / f"requirements-{'windows' if WIN else 'mac'}.lock"
    run("uv", "pip", "install", "--python", py, "--break-system-packages", "--link-mode=copy", "--compile-bytecode",
        "-r", lock)
    prune(dst)
    # the OCR models (Latin + Indian scripts, Apache-2.0, ~40 MB) ship inside the rapidocr package: fetched now,
    # so the installed app never writes into its own (read-only) folder
    env = {k: v for k, v in os.environ.items() if not k.startswith("VYUM_")}
    run(py, "-c", "from engine import ocr; ocr.preload()", cwd=ROOT, env=env)
    marker = next(dst.glob("lib/python3*/EXTERNALLY-MANAGED"), None) or (dst / "Lib" / "EXTERNALLY-MANAGED")
    if marker.exists():
        marker.unlink()


def prune(py_home: Path):
    """C headers, CMake files and test suites the app never uses: smaller, and no paths long enough to trip the
    260-character limit of older Windows installs (torch's headers nest deep)."""
    site = next(py_home.glob("lib/python3*/site-packages"), None) or py_home / "Lib" / "site-packages"
    for rel in ("torch/include", "torch/share", "pyarrow/include", "numpy/_core/include"):
        shutil.rmtree(site / rel, ignore_errors=True)
    for pkg in ("numpy", "pandas", "scipy", "sklearn", "pyarrow", "torch", "sympy", "networkx", "skimage"):
        for d in list((site / pkg).rglob("tests")) if (site / pkg).exists() else []:
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
    for junk in site.rglob("*.opt-?.pyc"):
        junk.unlink()
    # Windows wheels carry static/import libraries and debug symbols for building C++ extensions (torch's alone are
    # hundreds of MB); nothing loads them at run time, and they push the installer past NSIS's 2 GB limit
    for pattern in ("*.lib", "*.pdb", "*.a"):
        for junk in site.rglob(pattern):
            junk.unlink()


def engine():
    dst = OUT / "engine"
    shutil.copytree(ROOT / "engine", dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "it2_worker.py"),
                    dirs_exist_ok=True)


def genoffice():
    if MAC:
        src = GO / "apps/shell/release/mac-arm64/GenOffice.app"
        dst = OUT / "genoffice" / "GenOffice.app"
        res = dst / "Contents/Resources"
    else:
        src = GO / "apps/shell/release/win-unpacked"
        dst = OUT / "genoffice" / "GenOffice"
        res = dst / "resources"
    if not src.exists():
        sys.exit(f"GenOffice is not built at {src}. Run scripts/build_genoffice.sh (macOS) or the Windows workflow.")
    copytree(src, dst)
    shutil.rmtree(res / "gsk", ignore_errors=True)  # Genspark's cloud CLI: not part of an offline app


def pdfhost():
    dst = OUT / "pdfhost"
    dist = ROOT / "pdfhost" / "dist"
    if not (dist / "host.cjs").exists():
        run("node", ROOT / "pdfhost/build.mjs", GO)
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("host.cjs", "vyum-bridge.js"):
        shutil.copy2(dist / f, dst / f)
    copytree(GO / "apps/pdf/out/renderer", dst / "renderer")
    copytree(GO / "node_modules/electron/dist", dst / "electron")
    go = dst / "go"
    (go / "apps/pdf/src/main").mkdir(parents=True, exist_ok=True)  # the host resolves its wasm from here
    copytree(GO / "node_modules/@embedpdf/pdfium", go / "node_modules/@embedpdf/pdfium")
    copytree(GO / "apps/pdf/node_modules/harfbuzzjs", go / "node_modules/harfbuzzjs")


def licenses():
    dst = OUT / "licenses"
    dst.mkdir(parents=True, exist_ok=True)
    for name, src in [("GenOffice-LICENSE.txt", GO / "LICENSE"), ("GenOffice-NOTICE.txt", GO / "NOTICE"),
                      ("Electron-LICENSE.txt", GO / "node_modules/electron/dist/LICENSE")]:
        if src.exists():
            shutil.copy2(src, dst / name)
    (dst / "README.txt").write_text(
        "Vyum ships: Python (PSF), its packages (see runtime/python), GenOffice (Apache-2.0), Electron (MIT).\n"
        "Downloaded on first launch: Ollama (MIT), Qwen3 and Qwen2.5-VL (Apache-2.0), BGE-M3 (MIT),\n"
        "bge-reranker-v2-m3 (Apache-2.0), Docling layout (Apache-2.0) and TableFormer (CDLA-Permissive-2.0),\n"
        "RapidOCR / PP-OCR models (Apache-2.0).\n", encoding="utf-8")


def main():
    steps = sys.argv[1:] or ["python", "engine", "genoffice", "pdfhost", "licenses"]
    if "python" in steps:
        shutil.rmtree(OUT / "runtime", ignore_errors=True)
    for step in steps:
        print(f"== {step}", flush=True)
        {"python": python_runtime, "engine": engine, "genoffice": genoffice, "pdfhost": pdfhost,
         "licenses": licenses}[step]()
    report()
    print(f"assembled {OUT}")


def report():
    """Sizes of what ships (the Windows installer must stay under NSIS's 2 GB)."""
    def size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
    total = 0
    for d in sorted(OUT.iterdir()):
        n = size(d)
        total += n
        print(f"{n / 1e6:9.0f} MB  {d.name}")
    site = next(OUT.glob("runtime/python/lib/python3*/site-packages"), None) or OUT / "runtime/python/Lib/site-packages"
    if site.exists():
        biggest = sorted(((size(p), p.name) for p in site.iterdir() if p.is_dir()), reverse=True)[:8]
        print("  largest packages: " + ", ".join(f"{n} {b / 1e6:.0f} MB" for b, n in biggest))
    print(f"{total / 1e6:9.0f} MB  total")


if __name__ == "__main__":
    main()
