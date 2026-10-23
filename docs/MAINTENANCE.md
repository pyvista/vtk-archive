# Keeping the archive current

The archive is meant to run without anyone watching it. `.github/workflows/refresh.yml`
runs the whole cycle daily. That only works if every pass can tell stale from
fresh, and if the passes that cannot are named here rather than assumed.

## The update cycle

```bash
scripts/corpus.sh pull                # restore data/ and attachments/ from R2
vtk-archive skeleton --incremental    # new and changed items, from the watermark
vtk-archive notes                     # comment threads for anything stale
vtk-archive diffs                     # the code change on anything stale
vtk-archive mantis                    # retries any page that failed before
vtk-archive attachments               # files referenced from bodies
vtk-archive commits                   # which SHAs the GitHub mirror now holds
vtk-archive scrub                     # refuses if any address survived
scripts/corpus.sh push                # write the corpus back before building
vtk-archive build                     # re-render only what changed
vtk-archive index                     # rebuild the search index
```

Order matters in two places. `skeleton` refreshes `updated_at` and every other
pass compares against it, so running `notes` first finds nothing new. And the
corpus goes back to R2 before the build, so a build or deploy failure never
costs a crawl.

## Staying current

Each pass decides what to fetch by comparing GitLab's `updated_at` against its
own `*_fetched_at` stamp. Four rules make that trustworthy, and each one is a
bug that shipped.

**Compare instants, never strings.** GitLab stamps in its own offset (`-04:00`)
and we stamp in UTC. `2026-09-30T21:00:00-04:00` is *later* than
`2026-10-01T00:00:00+00:00` and sorts *earlier* as text, so a string compare
skips a changed record forever. Everything goes through `crawler/times.py`.

**Stamp before the request, not after.** A thread spanning several pages takes
seconds. A comment posted inside that window carries an `updated_at` below an
after-the-fact stamp, so it is invisible on every future run.

**Presence is not freshness.** `"notes" in rec` and `iid in held` both answer
"did we ever fetch this", which is a different question. The `notes` pass
fetched each item exactly once for its whole life because of this, and `diffs`
had the same bug against 357 open merge requests.

**A failure is not an answer.** One Mantis timeout used to write an error stub
that satisfied the "have we got this" test, so no later run ever looked at that
id again. `mantis.retryable` names what gets another chance: an `error` always,
an `empty` only under `--refetch`, because the id really does hold nothing.

A record that cannot prove its own freshness is re-fetched. One wrong refetch
costs a request; one wrong skip loses the content permanently, because nothing
revisits it.

### Why the skeleton pass does not page by offset

The walk is ordered by `updated_at`, which is the column the walk is racing. An
item edited mid-crawl moves to the end of the list, every row behind it shifts
back one slot, and the row that crosses a page boundary is never returned. Its
`updated_at` then sits below the watermark, so no later `--incremental` run
revisits it either. The record is gone, and nothing reports it.

GitLab's own `pagination=keyset` is not the fix: `/issues` honours it and
returns a real cursor, and `/merge_requests` answers `200` while silently
serving ordinary `page=` links. `Rest.walk_updated` carries the cursor itself,
re-anchoring `updated_after` on the newest stamp each page. `updated_after` is
inclusive, so the row carrying the cursor comes back rather than its neighbours
being skipped; the corpus is keyed by iid, so a duplicate costs one request.
`tests/test_pagination.py` holds both halves, including the offset walk
dropping a row.

### One writer per corpus

Every pass reads a whole corpus into memory, changes some records and writes it
back through one temp path. Two passes over the same corpus lose each other's
records outright and can interleave inside the replace and leave a torn file.
`store.lock` takes one `flock` per corpus and refuses rather than waiting,
naming the holder. `diffs` writes `mr_diffs` and only reads the merge-request
corpus, so it still runs beside `notes`.

## What the state file reports

`data/state.json` carries the numbers that can go red:

| | means |
|---|---|
| `notes_stale` | records whose comments changed since we fetched them |
| `notes_failed` | records carrying a `notes_error` |
| `mantis.errors` | Mantis pages standing on an error stub |
| `commits.in_mirror` | SHAs resolvable on the GitHub mirror |

`notes_complete` is beside them and is not a signal: it reads 100% over an
arbitrarily stale corpus.

A per-item failure is recorded on the record as `notes_error` and the pass
continues. Before that, one deleted or confidential item raised out of the pass
at the same point on every run, and nothing after it was ever fetched.

`commits` fetches the clone by default. A clone that is never refreshed answers
"not in the mirror" for every commit pushed since it was made, and that answer
routes the link back to gitlab.kitware.com. `resolve` raises if git exits
non-zero or answers for fewer SHAs than it was asked about; it used to map all
82,152 to absent and say nothing.

## Incremental builds

`vtk-archive build` re-renders a page only when something feeding it changed.
The fingerprint covers the record, the merge request's diff, the recovered
Mantis discussion, whether the head commit is in the GitHub mirror, the site
URL, and a hash of every file under `builder/` — code, templates and stylesheet
alike.

That last part is read off disk rather than from a version constant, because a
constant someone forgets to bump serves stale markup with nothing reporting it.

Measured on the full corpus: a cold build is about two minutes, and a build
with nothing changed is about twenty seconds and renders zero pages.

`dist/` is kept between runs. Anything under it that the build did not claim is
deleted, so a page whose record disappeared does not linger. The search index
is deleted and rebuilt, because Pagefind adds fragments and keeps the ones it
no longer references.

### What an incremental build can miss

Three things depend on the whole corpus rather than on the page's own record:
whether `#19500` renders as a link, whether `@someone` resolves to a profile,
and whether a commit link goes to the mirror or to GitLab. Folding the corpus
into every fingerprint would make each new issue a full rebuild, which is most
days, so these are left to `--full`.

The worst case is a reference rendering as plain text instead of a link. Never
a wrong or missing record. `refresh.yml` carries a second Sunday cron so the
build step can tell that day apart and pass `--full`; a full rebuild of
unchanged records produces the same bytes, so it costs a rebuild and uploads
nothing.

## Pinning the actions

`scripts/check-actions.sh` resolves every `uses:` ref against the GitHub API and
CI runs it, because nothing else asks whether an action exists. `actionlint`
checks syntax, so `astral-sh/setup-uv@v10` passed every local check and failed
every real run with "unable to find version", on a repository whose owner was
reading green local output.

`astral-sh/setup-uv` publishes no moving major alias past `v7` even though its
releases are past `v10`, so it is pinned to an exact `v10.3.2`. A paginated tag
listing is what hid this: `tags?per_page=100` never reached `v10`, so a tag that
does not exist looked like one that does. Ask for the ref itself.

## Rotating the R2 credential

`scripts/rotate-r2-token.sh` mints a replacement, proves it against the live
bucket, installs it into the GitHub secrets and `~/.config/vtk-archive/env`,
revokes the old token, and revokes the bootstrap token it used. It prints
lengths and pass/fail only, so the credential reaches no terminal, no log and
no agent transcript. That is the point of the script: a value pasted into a
session is a value in a file on disk somewhere.

It needs a short-lived Cloudflare token carrying **Account API Tokens Write**,
passed as `CF_BOOTSTRAP_TOKEN` with its id as `BOOTSTRAP_TOKEN_ID`. Give it an
`expires_on` an hour out, because a token that can mint tokens is the one worth
bounding. `wrangler`'s OAuth login cannot do this: its scopes carry neither R2
nor API-token write.

The replacement is scoped to the one bucket with Object Read & Write
(`Workers R2 Storage Bucket Item Read`/`Write`), not account-wide R2. Its S3
Access Key ID is the token id and its Secret Access Key is the SHA-256 of the
token value, which is why the script derives the secret locally and discards
the value.

Nothing is replaced until the probe passes, so a token scoped to the wrong
bucket or to read-only fails with the working credential untouched.

## The snapshot stamp

The yellow banner carries the snapshot's date and time on its right, in red
when the archive has gone stale, linking to the workflow run that built it.

It is filled in by a 15-line script from `/snapshot.json` rather than written
into the page, and that is a consequence of the section above: an item page is
reused until its record changes, so a stamp baked into the HTML would be the
date *that page* last changed. Only pages rebuilt every run can carry a literal
date, which is what the `snapshot` template flag gates.

`snapshot.json` holds the build time, the staleness threshold, and the run URL.
`STALE_AFTER_HOURS` is 48 in `builder/build.py`: the cron is daily, so one
missed run is a slow day and two is a failure. On a runner the link resolves to
that specific run through `GITHUB_RUN_ID`, which is the page worth opening when
the stamp is red; off a runner it falls back to the workflow's run list.

A reader with JavaScript off sees no stamp in the banner. The literal date in
the footer of every rebuilt-every-run page is the fallback.

## Where the corpus lives

Not in git. `data/merge_requests.ndjson` is 831 MB and `data/mr_diffs.ndjson`
is 544 MB, against GitHub's hard 100 MB per-file limit on push, so the
repository holds code only.

| | size | where |
|---|---|---|
| `data/` | 1.4 GB | R2, restored before a run |
| `attachments/` | 1.1 GB | R2 |
| `dist/` | 4.2 GB | built, deployed, never stored |
| `.cache/` | 87 MB | a bare VTK clone, rebuilt on demand |

Every pass is resumable and writes atomically, so a run that dies mid-way loses
at most one checkpoint and converges on the next.

## Secrets the scheduled run needs

| | what |
|---|---|
| `GITLAB_TOKEN` | a `read_api` token on a gitlab.kitware.com account |
| `R2_ACCOUNT_ID` | the subdomain of your `r2.cloudflarestorage.com` endpoint |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | an R2 token with Object Read & Write |
| `NETLIFY_AUTH_TOKEN` / `NETLIFY_SITE_ID` | the deploy target |

Locally they live in `~/.config/vtk-archive/env`, never in the repository.
