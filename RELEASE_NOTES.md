## Vyum — offline document AI

Ask questions about your documents and get short, verified answers with citations. Edit PDFs, sheets and documents
by asking. Hindi and other Indian languages supported. Your files never leave your computer.

### Download

| Computer | File |
|---|---|
| Mac with Apple Silicon (M1–M5), macOS 14 Sonoma or later | `Vyum-…-mac-arm64.dmg` |
| Windows 10 or 11, 64-bit | `Vyum_…_x64-setup.exe` |

**Memory:** 16 GB or more uses Qwen3-8B. With 8 GB, Vyum picks the smaller Qwen3-4B automatically.
**Disk:** about 15 GB free (app plus models).

### First launch

The installer is small. On first launch, Vyum downloads its AI models once (about 9–12 GB, depending on memory).
The download resumes if it is interrupted, and the model server's download is checked against its published checksum.
After that, Vyum works fully offline.

### Opening a test build (not yet signed)

These first builds are not signed by Apple or Microsoft yet, so your computer will warn you once.

- **Mac:** Open the .dmg and drag Vyum to Applications. The first time, open Vyum; when macOS says it cannot verify
  the developer, go to **System Settings → Privacy & Security** and click **Open Anyway**.
- **Windows:** If SmartScreen shows "Windows protected your PC", click **More info → Run anyway**. Vyum installs
  for your user only (no administrator rights needed).

Each file has a matching `.sha256` checksum.

### Known limits of this version

- Web search is not included in the installed app (the dev version uses SearXNG, which is AGPL licensed).
- Hindi answers use the main model to translate, with Vyum's number and limit checks. The dedicated Hindi translator
  (IndicTrans2) is not included yet.
- Windows without an NVIDIA graphics card runs the model on the processor: answers are slower.

### What is inside

Open-source components (licences in the app's `licenses` folder): GenOffice (Apache-2.0), Electron (MIT),
Python and its packages, Ollama (MIT, downloaded on first launch). Models: Qwen3 and Qwen2.5-VL (Apache-2.0),
BGE-M3 (MIT), bge-reranker-v2-m3 (Apache-2.0), Docling layout (Apache-2.0) and TableFormer (CDLA-Permissive-2.0),
PP-OCR (Apache-2.0).
