# ADR-0001: Package on GitHub + launcher notebook

**Status:** Accepted
**Date:** 2026-10-27
**Decided by:** Adriel

## Context

The studio lived entirely inside a 9-cell Colab notebook. The shared library (~1,200 lines) and the pipeline
(~1,700 lines) were text inside cells 1 and 7, written to disk when run. Editing that code in Colab was
awkward, there was no change history, the tests lived outside the project, and every fix meant downloading
and replacing the whole notebook.

## Decision

The code moves to a Python package (`studio/`) in a public GitHub repository. The notebook becomes an 8-cell
launcher: Cell 1 clones or updates the repository and the other cells only import and call one function.

## Options considered

### A: Self-contained notebook (as before)

| Dimension | Assessment |
|---|---|
| Complexity | Low to use, high to maintain |
| Updating | Replace the whole `.ipynb` |
| Tests | Outside the project |
| External dependency | None |

**For:** a single file; works without reaching GitHub.
**Against:** code as text, no history, changes hard to review.

### B: Repository with cells 7 and 8 only

**Against:** they depend on Cell 1's library; the repository would be incomplete.

### C: Full package + launcher (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Medium: one package, one short notebook |
| Updating | Run Cell 1 again |
| Tests | In the repository, with CI on every change |
| External dependency | GitHub (with a backup copy in Drive) |

**For:** history and versions (`VERSION = "v3.0.0"`), automatic tests, readable cells with their options in
sight.
**Against:** depends on GitHub at start; being public, the code is visible to anyone.

## Analysis

The GitHub dependency is mitigated by a copy of the code in `Drive/YT-Channel-Studio/code`, which Cell 1 uses
when GitHub does not answer. A public repository exposes no data: keys live only in `api_keys.toml` in the
user's Drive and the code contains none.

## Consequences

- **Easier:** reviewing changes, going back to an earlier version, getting fixes without replacing the
  notebook, testing every change.
- **Harder:** paths can no longer be fixed to `/content`; they come from `STUDIO_CONTENT_DIR` and
  `STUDIO_BASE_DIR`.
- **To revisit:** if a `git pull` changes a tool's dependencies, its cell has to be run again today; this could
  be detected by storing a dependency version in each venv.
