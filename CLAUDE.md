# CLAUDE.md

## Project Purpose

This repository is for a production-grade hospital logo collection pipeline.

The system will process an uploaded Excel workbook containing more than 6,000 hospital accounts and produce one verified, correctly named PNG logo per hospital whenever a reliable match can be found.

This is not a prototype or sample-only project. The system must be designed to process the full workbook, survive interruptions, resume safely, and produce final downloadable deliverables.

## Core Working Rules

1. Do not stop after planning.
2. Do not stop after building a prototype.
3. Do not process only a sample unless testing is necessary before a full run.
4. After testing, continue into full-workbook execution.
5. Preserve completed work across interruptions.
6. Never restart completed rows unnecessarily.
7. Never silently skip a workbook row.
8. Never fabricate a hospital logo.
9. Never use a generic medical icon as a substitute for a real hospital or health-system logo.
10. Route uncertain matches to manual review instead of guessing.

## Engineering Standards

Build the system as a maintainable production pipeline with:

* clear modular architecture
* persistent checkpoints
* resumable processing
* structured logs
* retry handling
* rate-limit handling
* failure isolation
* cached search and download results
* deterministic filename generation
* validation before completion
* final QA before packaging

Prefer reliable, understandable code over unnecessary complexity.

Use environment variables for credentials, API keys, browser settings, or provider-specific configuration.

Never hardcode secrets into the repository.

Provide an `.env.example` when environment variables are required.

## Repository Behavior

Before making major changes:

1. Inspect the current repository.
2. Understand existing files and prior progress.
3. Reuse completed work where appropriate.
4. Avoid rebuilding working components without a clear reason.

When resuming the project:

1. Read the current checkpoint and manifest.
2. Determine which rows are already complete.
3. Continue from the next unresolved row.
4. Do not redownload or overwrite verified logos unless necessary.

## Data Integrity

The Excel workbook is the source of truth.

The hospital ID must be preserved exactly.

Every original workbook row must appear exactly once in the final manifest.

Each row must end in one documented status:

* `pending`
* `processing`
* `completed`
* `manual_review`
* `failed`

No row may disappear from tracking.

## Logo Quality Rules

A completed logo must:

* belong to the correct hospital or verified parent health system
* be a PNG
* be square
* be at least 250x250 pixels
* be centered
* preserve aspect ratio
* avoid stretching
* avoid obvious cropping
* avoid watermarks
* avoid screenshots and irrelevant page elements
* use transparency or a clean white background where appropriate

Prefer official and high-resolution sources.

## Source Reliability

Prioritize sources in this order:

1. official hospital website
2. official parent health-system website
3. official media or brand asset page
4. official social-media profile
5. authoritative healthcare directory
6. Wikimedia Commons or another reliable secondary source
7. image-search discovery followed by independent verification

Do not approve a logo based only on filename similarity.

Use account name, address, city, state, and ZIP code to verify facility identity.

## Manual Review

Send a row to manual review when:

* identity is ambiguous
* multiple hospitals plausibly match
* branding continuity after a rename or acquisition is unclear
* the only image is too low quality
* no authoritative logo can be found
* source reliability is insufficient
* confidence is below the acceptance threshold

Manual-review entries must include useful notes, attempted searches, candidate URLs, and the reason the row was not approved.

## Execution and Progress

Checkpoint frequently.

Progress updates should report:

* total rows
* completed
* manual review
* failed
* remaining
* current processing rate
* notable blockers

Do not claim the full task is complete until every workbook row is accounted for.

## Final Deliverables

The completed project must generate:

1. a folder containing all accepted PNG logos
2. a ZIP containing the accepted PNG logos
3. a full manifest covering every workbook row
4. a manual-review file
5. a summary report
6. reusable source code
7. checkpoint files needed for safe resume

Before final delivery, run validation for:

* missing IDs
* duplicate IDs
* duplicate filenames
* incorrect filenames
* corrupt images
* images under 250x250
* non-square images
* non-PNG outputs
* blank or near-blank images
* unresolved rows missing from manual review
* completed rows missing source evidence

## Decision Principle

Accuracy is more important than artificially achieving a 100% automatic completion rate.

A well-documented manual-review result is better than a confidently wrong logo.
