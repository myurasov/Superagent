# Image format policy

[Do not change manually — managed by Superagent]

This rule governs which image formats Superagent is allowed to land under `workspace/Sources/`. It applies whenever the agent imports a photo or scan into the source library — most commonly from `workspace/Inbox/`, but also from any direct save (email attachment, AirDrop, screenshot).

The policy is forward-only: it constrains what the agent **creates, files, or renames into** under `Sources/`. It does NOT trigger retroactive walks of pre-existing trees; one-time clean-ups happen via the `migrate` skill or an explicit user request.

---

## 1. No HEIC / HEIF under `Sources/`

When the agent files an incoming HEIC or HEIF file into `Sources/`, it MUST convert the image to an sRGB JPEG sibling first, then file ONLY the JPEG. The HEIC original is NOT retained alongside the JPEG.

Rationale:

- **Browser + viewer compatibility.** Most web viewers, markdown previewers, and downstream tools cannot render HEIC. A JPEG sibling guarantees every artifact in `Sources/` is openable without a per-format codec.
- **Display P3 black-frame bug.** macOS `sips`'s direct `HEIC -> JPEG` path produces an all-black file for Display P3 HEICs captured on recent iPhones (documented FasTrak IMG_3221 incident). The agent works around this by going through Quick Look (`qlmanage -t`) to PNG first, then `sips` PNG -> JPEG. The helper at `superagent/tools/heic_to_jpg.py` encapsulates this routine.
- **No information loss in practice.** Phone-captured HEICs at full Quick Look thumbnail size (`-s 2400`) produce JPEGs that preserve every detail the agent needs for downstream document extraction (account numbers, license plates, dates, barcodes).

## 2. Acceptable image formats under `Sources/`

- `.jpg` / `.jpeg` — preferred for photographs and most scans.
- `.png` — preferred for screenshots and synthetic graphics; acceptable for scans when a transparency channel is needed.
- `.pdf` — for multi-page scans and any artifact that arrives as a PDF in the first place. Single-page scans MAY stay as PDF if that is how they arrived.
- `.gif`, `.webp`, `.tiff` — accepted as-is when they arrive in that form; the agent does NOT re-encode them. The policy targets HEIC specifically.

## 3. Conversion helper

Use the framework helper rather than hand-rolling conversion in skills:

```bash
# Single file (deletes the HEIC after writing the sibling .jpg)
uv run python -m superagent.tools.heic_to_jpg convert <path/to/file.heic>

# Whole directory (recursive); --dry-run to preview
uv run python -m superagent.tools.heic_to_jpg convert-dir <path/to/dir>
```

Both forms accept `--keep` to retain the HEIC alongside the JPEG (rare; used only when the user explicitly asks for the original to be preserved off-Sources, e.g. archived to a personal cloud).

The helper:

1. Skips conversion if a `.jpg` sibling already exists at the same stem — the on-disk JPEG is treated as authoritative.
2. Uses `qlmanage` (Quick Look) HEIC -> PNG, then `sips` PNG -> JPEG, to dodge the Display P3 black-frame bug.
3. Removes the HEIC original after verifying the JPEG was written, unless `--keep` was passed.

## 4. What this rule does NOT cover

- HEIC files in `workspace/Inbox/` are fine while they are in transit. The conversion happens at the moment the agent files them into `Sources/`.
- HEIC files in `Outbox/` are not normally produced (the agent generates HTML / PDF / Markdown artifacts there); if a workflow ever does, the same JPEG-only policy applies before the artifact is published.
- Historical mentions of HEIC filenames in append-only logs (`interaction-log.yaml`, `history.md`, `_processed.yaml`) are NOT rewritten. Those entries document what happened at the time of import; the policy is forward-only.
- Cached external content under `Sources/_cache/<hash>/raw.*` follows the upstream source's format; the agent does not transcode cache payloads.
