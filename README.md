# vtk-archive

A read-only, statically hosted archive of VTK's GitLab history: issues, merge
requests, review threads, the code change on each merge request, and the
discussion around all of it.

VTK's development record lives at `gitlab.kitware.com/vtk/vtk`. The source is
mirrored widely and in no danger. The ~236,000 comments explaining *why* the
code looks the way it does exist in one place. This archives that.

It also exists to take load off Kitware's server. Coding agents now crawl
issue threads on every prompt, and a static site on a CDN can absorb that at
no cost to the project.

## What it holds

| | |
|---|---|
| Issues | 5,226 |
| Merge requests | 13,870 |
| Comments | 236,000+ |
| Merge requests with diffs | 13,518 |
| Attachments | 3,457 |
| Recovered Mantis comments | 4,569, on 1,893 pre-2015 issues |

The Mantis comments appear on no GitLab page: the 2015 migration carried each
report but not the discussion under it, and the original tracker's front page
now 404s.

## Use

```bash
uv sync
uv run vtk-archive check-access     # what this credential actually reaches
uv run vtk-archive skeleton         # every issue/MR with its body
uv run vtk-archive notes            # comment threads (needs a token)
uv run vtk-archive diffs            # the code change on each merge request
uv run vtk-archive attachments      # files referenced from bodies
uv run vtk-archive mantis           # the pre-2015 tracker's lost discussion
uv run vtk-archive commits          # which SHAs the GitHub mirror holds
uv run vtk-archive build            # render dist/, re-rendering only what changed
uv run vtk-archive index            # build the search index
uv run vtk-archive preview          # serve dist/ the way the host will
uv run vtk-archive status
```

`check-access` is the gate: it probes each endpoint and prints what the
current credential reaches, so a token is proven rather than assumed.

For the update cycle and what each pass considers stale, see
[docs/MAINTENANCE.md](docs/MAINTENANCE.md).

## Access

The host splits access by authentication, not permission: the comment
endpoints answer `401 Unauthorized`, not `403 Forbidden`.

| Tier | Needs | Unlocks |
|---|---|---|
| 1 | A `read_api` token on your own gitlab.kitware.com account | Comments, discussions, labels, milestones |
| 2 | An Anubis User-Agent allowlist entry from Kitware | GitLab's own rendered HTML, via GraphQL |

Tier 1 needs no coordination with anyone: signup is open and VTK is a public
project. Tier 2 is a fidelity upgrade; without it the archive renders the
markdown itself. See [docs/ACCESS.md](docs/ACCESS.md) and
[docs/SOURCES.md](docs/SOURCES.md).

**We do not spoof an allowlisted User-Agent to get past Anubis.**

## Load

The crawler is sequential, identified, and never concurrent, with exponential
backoff on `429` and `5xx`. Uncoordinated parallel scraping is why this host
became hard to reach; a crawler that reproduced the cause would defeat its own
purpose. Having a token does not license going faster.

## Layout

| | |
|---|---|
| `crawler/` | The passes. Sequential, resumable, each with a staleness rule. |
| `builder/` | Renders the corpus into `dist/`. |
| `data/` | The corpus, 1.4 GB. **Not in git** — it lives in R2. |
| `attachments/` | 1.1 GB of files from issue threads. Also R2. |
| `dist/` | ~86,000 files, 4.2 GB. Built, deployed, never stored. |
| `scripts/corpus.sh` | Moves the corpus between a checkout and R2. |
| `scripts/rotate-r2-token.sh` | Replaces the R2 credential without printing it. |

`.github/workflows/refresh.yml` runs the whole cycle daily: restore the corpus,
crawl what changed, rebuild, reindex, deploy. Hosting, the partial-deploy
mechanism and the limits that bind are in [docs/HOSTING.md](docs/HOSTING.md).

## Scope

Archival and read-only. Not a fork, not a mirror you can file issues into, not
a place VTK development happens. Every page links to its canonical
`gitlab.kitware.com` URL, which remains authoritative.

The tooling is MIT-licensed. The archived discussion is the work of the people
who wrote it; see [NOTICE](NOTICE).
