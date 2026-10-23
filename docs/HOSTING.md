# Hosting and deploying

The archive is about 86,000 files and 4.2 GB, and the corpus that produces it is
another 2.5 GB that cannot live in git. Both facts shape the deploy: the site
is on Netlify and the corpus is in Cloudflare R2.

## Why `index.html`

Every page is written to `<url>/index.html`. That is the one layout every
static host resolves at the bare URL with no configuration, no redirect rules
and no edge function in the request path. An earlier scheme stored pages under
their exact key for a flat object store; it worked on exactly one host.

## Partial deploys on Netlify

Netlify's file-digest deploy is natively incremental, which is what makes a
4.2 GB site practical:

1. `POST /api/v1/sites/{site_id}/deploys` with a map of every published path to
   its SHA1.
2. The response's `required` array lists only the hashes Netlify does not
   already hold.
3. `PUT` just those files.

So unchanged files are never re-uploaded. The *digest* must still cover every
file on every deploy, because deploys are atomic and Netlify uses the full
checksum list to refuse a stale or partial file set. That has one consequence
worth planning around: **the machine that deploys must hold the complete
`dist/`**, so it must be able to rebuild from the corpus.

Pass `"async": true` in the digest. API requests over 30 seconds are
terminated, and a digest of 86,000 files is well past that. `netlify deploy`
sets `async` itself for anything over 100 files.

### What bandwidth costs

Where the 4.3 GB actually sits, because that is the bill:

| | Size | Files |
|---|---|---|
| Merge-request diff pages | 2.43 GB | 13,518 |
| Attachments | 1.18 GB | 3,457 |
| HTML pages | 0.45 GB | 24,650 |
| Markdown twins | 0.19 GB | 24,322 |
| Pagefind index | 0.07 GB | 19,853 |

The Netlify team plan includes 1 TiB a month and accumulates overage rather
than cutting off, so roughly 250 complete crawls of everything fit inside the
allowance. Ordinary agent reads are nowhere near that: a page averages 18 KB.
The exposure is the diff pages, which are 56% of the bytes and the part a
reader least needs, since that code is in the git history already.

`builder/build.HEADERS` is written to `dist/_headers`, which Netlify reads out
of the publish directory. An upload URL carries the file's own digest, so those
bytes can never change and are cached for a year; everything else is rebuilt
daily and gets an hour with `stale-while-revalidate`. Netlify serves `ETag` on
static files by itself, so a conditional re-crawl costs a 304.

R2 is free at this size: 2.4 GB against a 10 GB free tier, a few thousand Class
A operations a month against a million, and no egress charge at all.

If a single crawler ever does become the bill, the team plan carries Firewall
Traffic Rules (5 rules, IP and country) but **not** rate limiting, which sits
behind Advanced Web Security and is off on this plan.

### Turn Netlify's Pretty URLs off

This is a **site setting and lives in no file here**, so it has to be set again
on any new site:

```
netlify api updateSite --data '{"site_id":"<id>","body":{"processing_settings":{"html":{"pretty_urls":false}}}}'
```

On by default, it answers every slash-less path with a `301` to the
trailing-slash form. Each page is written as `<url>/index.html`, so that is
every page on the site, and the sitemap and llms.txt advertise exactly the
slash-less form — 19,365 URLs, each costing a redirect before it serves, on an
archive whose purpose is to be cheap for agents to read. With it off, both
forms return `200` directly. Measured on a draft deploy before production,
because the plausible failure was the opposite one: slash-less paths answering
`404` instead.

### A pasted log

Netlify's own guidance is 10 MB per uploaded file. One page was past it:
merge request 4298 carries two notes of 7.2 MB each, the same build log pasted
twice, which rendered to a 12.7 MB HTML page. Eleven items in the corpus hold
a body over 200 KB.

`builder/render.BODY_CAP` cuts the *HTML* rendering of one body at 200,000
characters and says so on the page. The corpus keeps every byte and so does
the `.md` twin, which is what an agent reads and what the notice points at.
The cap is a rendering decision, never a preservation one.

Merge-request diff pages are separately capped per file and per file count
(`builder/diff.py`), and the widest of them is about 10 MB. That is the cost
of showing a 1,000-file change and it stays.

### The first deploy is the slow one

Read off `netlify-cli@27`'s own `utils/deploy/constants.js`, because these
govern the one step that cannot be rehearsed without a live site:

| Constant | Value | What it means here |
|---|---|---|
| `DEFAULT_CONCURRENT_UPLOAD` | 5 | every uploaded file, five at a time, and no CLI flag raises it |
| `DEFAULT_CONCURRENT_HASH` | 100 | hashing `dist/` is not the bottleneck |
| `DEFAULT_SYNC_LIMIT` | 100 | over 100 files the CLI sets `async` itself |
| `DEFAULT_DEPLOY_TIMEOUT` | 20 min | caps the async-diff wait *and* the go-live wait, so it is raised |
| `DEFAULT_MAX_RETRY` | 5 | fibonacci backoff, 5 s to 90 s, per file |

Measured, on this site:

| Deploy | Uploaded | Wall clock |
|---|---|---|
| First, empty site | 85,788 assets | 32m 47s |
| Second, nothing changed | **0 assets** | 41m 47s |

So the digest really is incremental: the second deploy uploaded nothing. What
it did not do is finish faster, because the cost is Netlify diffing 86,000
hashes server-side, not the upload. 31s of that was local CPU.

That is why `refresh.yml` passes `--timeout 3600`. The CLI's 20-minute default
would have failed the deploy that uploaded nothing at all, and every scheduled
run should be expected to sit in this step for half an hour or more.

If a deploy does fall over, the fallback is the digest API directly (the three
steps above), where the upload loop is ours and the concurrency is a number we
choose. Nothing else in the pipeline changes.

### Limits that actually bind

| Limit | Value | Us |
|---|---|---|
| Files per directory | 54,000 | 27,742 at the widest |
| Files per ZIP extraction | 25,000 | ~86,000, so **ZIP deploys are out** |
| Deploys via the API | 3/min, 100/day | a scheduled daily run is fine |
| Other API requests | 500/min | shapes the first upload, not later ones |

The per-directory headroom is comfortable. `merge_requests/` grows by about
two entries per merge request, and VTK opens on the order of 700 a year.

Free-plan allowances moved to a credit pool; read the current terms off
Netlify's pricing page rather than trusting a number written here. The
relevant one is bandwidth, because absorbing agent traffic is the point of the
archive, and bandwidth is what that spends.

## Why not Cloudflare Pages

The corpus lives in R2, so Pages is the obvious pairing, and the file count is
what rules it out. Pages allows 20,000 files per site on the free plan and
100,000 on a paid one, and the archive is ~86,000 files growing by about 2,100 a
year. That is a paid plan from day one and a ceiling inside a decade.

Netlify's constraint is per directory, not per site: 54,000, against 27,742 in
the widest directory here. Same CDN economics, no cap to grow into, and the
file-digest deploy below is genuinely incremental.

A single asset over 25 MiB would also have to come off Pages and be served from
R2 directly. Nothing in the archive is that large today, but an attachment
could be.

## Where the corpus lives

R2, because egress is free and a scheduled run pulls the whole 2.5 GB every
time. `scripts/corpus.sh pull|push` moves it with rclone, configured entirely
from the environment so no credential is written to disk.

It uses `rclone copy`, never `sync`: sync deletes on the far side to match the
near side, so one failed pull followed by a push would empty the bucket.
Nothing in the corpus is ever meant to be deleted.

At R2's rates the whole thing is about four cents a month in storage, and the
operations a daily run spends are rounding error against that.

## The shape of a scheduled run

1. Restore `data/` and `attachments/` from R2.
2. Run the crawl passes (see MAINTENANCE.md).
3. Write `data/` and `attachments/` back, *before* building.
4. `vtk-archive build` — seconds when little changed.
5. `vtk-archive index`.
6. Deploy `dist/` by digest.

Steps 1 and 3 exist because the corpus is 2.5 GB. Step 3 comes before the build
so that a build or deploy failure never costs a crawl. Everything else assumes
a machine with the whole tree on disk.

`.github/workflows/refresh.yml` is this list.

## The `?uri=` gateway

`/?uri=<any gitlab.kitware.com URL>` lands on that page's archived copy. It
runs on the home page rather than as a redirect rule because no static host
can strip an origin off a query value.

It follows a URL only when the host is `gitlab.kitware.com` *and* the path
starts with a single slash. A path of `//evil.example.com/x` is
protocol-relative, so following it would leave the site entirely — an open
redirect on a feature whose whole purpose is to accept pasted URLs.
