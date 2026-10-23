# Agent-facing search over the archive

A design proposal. Nothing here is built. The site stays static; whether anything
runs beside it is the question this document answers.

Already settled and not re-litigated here: Pagefind for the human search UI, a
`.md` twin of every page for token-efficient agent reads, `llms.txt` at the root.

Every number below is measured on this machine against this repo's `data/`, or
quoted from a linked vendor doc, or marked **unverified**. The diff crawl and a
`vtk-archive build` were both running while these were taken, so diff and `dist/`
figures are a snapshot of an incomplete corpus and are labelled with their
coverage.

## 1. What the corpus actually is

Measured 2026-10-22 from `data/*.ndjson`.

| Quantity | Measured | At full crawl |
|---|---|---|
| Issues | 5,226 (4,189 closed, 1,037 open) | same |
| Merge requests | 13,870 (11,812 merged, 1,699 closed, 359 open) | same |
| Human comments | 220,254, 77.0 MB of body text | same |
| System notes | 130,838, 19.8 MB | same |
| Comments anchored to a diff position | 50,817 | same |
| Items with notes fetched | 5,226/5,226 issues, 13,870/13,870 MRs | complete |
| MRs with diffs fetched | 6,755 of 13,870 (49%) | 13,870 |
| Diff hunks | 177,821 | ~365,000 |
| Changed files in diffs | 67,354 at 5,755 MRs | ~160,000 |
| Diff text | 185 MB at 5,755 MRs | ~380 MB |
| Searchable units (title+body+human comment) | 255,344 | same |
| Pages emitted (5,226 x 2 aliases + 13,870 + indexes) | ~24,500 | ~24,500 |
| Plain text as a fraction of rendered HTML | 0.43 (300-page sample) | same |

Comment length is extremely skewed: mean 350 characters, median 91, p90 481,
p99 1,773, max 7,248,194. Half of all "comments" are one line of GitLab
bookkeeping. Any ranking that treats a comment as a document has to cope with
both ends of that.

Top labels by count are `area:build` (520), `triage:confirmed` (325),
`area:third-party` (291), `lang:python` (271), `area:rendering` (176). 33
distinct milestones. These are the only pre-existing facets worth exposing.

### The three retrieval targets

| Target | Unit | Why an agent wants it |
|---|---|---|
| Discussion | one comment, with its thread | "why is this interface like this", "was this argued" |
| Item | issue or MR, title + body + labels + state | "has this been reported", "what shipped in 9.3" |
| Code change | one diff hunk, with path, line, SHA | "show me the change that caused this", "what did the fix look like" |

Comment-level is the unit that matters and the unit a page-level index cannot
return. A 300-comment MR page is one Pagefind document.

## 2. Track A: can this work with zero backend

Four mechanisms, each evaluated on bytes per query, whether an agent can drive
it, and the failure mode at this size.

### A1. Pagefind, driven from Node by the agent itself

Pagefind's Node API indexes; it does not search. Its exports are `createIndex`,
`close`, and on the index `addDirectory`, `addHTMLFile`, `addCustomRecord`,
`getFiles`, `writeFiles`, `deleteIndex`
([node-api](https://pagefind.app/docs/node-api/)). So "use the Node API to
search" is not a thing. What does work is driving the browser bundle from Node.

Measured: Pagefind v1.5.2 (Extended) over a 2,000-page sample of `dist/`
(36 MB of HTML), then queried from Node v24 by importing the emitted
`pagefind.js` from disk and setting `basePath` to an HTTP origin.

| Artifact | Measured at 2,000 pages | Linear projection to 24,500 |
|---|---|---|
| `index/*.pf_index` | 4.2 MB in 137 chunks, median 30 KB, max 60 KB | ~52 MB, ~1,700 chunks |
| `fragment/*.pf_fragment` | 3.3 MB, 2,000 files, mean 1.7 KB | ~40 MB |
| `*.pf_meta` | 16.6 KB | ~200 KB |
| `wasm.en.pagefind` | 72.7 KB | 72.7 KB (fixed) |
| `pagefind.js` | 45.5 KB | 45.5 KB (fixed) |
| Build time | 1.75 s | ~25 s |

One cold query (`depth peeling`) plus reading three results cost **8 HTTP
requests, 158 KB**: entry 174 B, wasm 72.7 KB, meta 16.6 KB, two index chunks
63 KB, three fragments 0.7 to 6.2 KB. Results were correct and ranked sensibly
(`!706 A fix for picking combined with depth peeling`, `#17418`, `#16824`).

- HTTP ESM import of `pagefind.js` fails (`ERR_UNSUPPORTED_ESM_URL_SCHEME`), so
  a client has to vendor or cache the bundle plus the wasm locally.
- Projected cold cost at full size is ~300 to 400 KB per query, dominated by
  `pf_meta` plus wasm. An agent process that exits after one query pays all of
  it every time; a long-lived MCP process pays it once.
- Where it breaks: granularity, not bytes. Pagefind indexes pages. A hit tells
  the agent "something on this 300-comment MR matched", and the excerpt is one
  windowed fragment. It cannot return the comment, the thread, the author, the
  date, or the diff hunk. Filters would have to be baked in as
  `data-pagefind-filter` attributes at page level, which is the wrong level.
- Indexing comments as `addCustomRecord` instead of pages fixes granularity and
  multiplies the fragment count by ~10 (255,344 records), which multiplies the
  deploy file count by the same factor. See the deploy cap in section 3.

Verdict: keep Pagefind for humans. It is not the agent index.

### A2. Static postings shards fetched by plain GET

Build the inverted index at build time, write it as content-addressed files,
let the client do the boolean work and the scoring. Measured from the real
vocabulary of the 255,344-document comment index.

| Measurement | Value |
|---|---|
| Distinct terms | 208,918 |
| Postings pairs (term, doc) | 6,266,784 |
| Document frequency | median 1, p90 6, p99 191, max 114,817 (`the`) |
| Terms with docfreq > 10,000 | 130, carrying 55% of all postings |
| Distinct terms starting `vtk` | 7,986, 120,196 postings |
| Whole postings set, 4-byte doc ids | 25 MB; 16 MB after a 130-term stoplist |

Sharding the stoplisted set by term hash:

| Buckets | Total | Median bucket | p90 | Max |
|---|---|---|---|---|
| 1,024 | 16 MB | 12 KB | 28 KB | 57 KB |
| 4,096 | 16 MB | 2 KB | 7 KB | 52 KB |
| 16,384 | 16 MB | under 1 KB | 1 KB | 48 KB |

A three-term query at 4,096 buckets is three GETs, typically 6 to 30 KB, worst
case 150 KB. That is an order of magnitude cheaper than A1 and it returns
comment ids, not page ids. Add a parallel bucket set over diff hunks (same
construction, roughly 2x the comment index by text volume) and a small
per-document metadata table (kind, iid, author, date, state, labels, path) so
the client can filter and score without fetching documents.

Costs and limits:

- The client implements scoring. BM25 needs document lengths and term document
  frequencies, both of which fit in a sidecar: 208,918 terms x ~12 B is ~2.5 MB
  for the full dictionary, cacheable once per agent process.
- Phrase and proximity queries need positions. Positions roughly triple the
  postings size (FTS5's own `detail=full` index is 36.4 MB against 14.8 MB for
  `detail=none`, a 2.5x ratio on the same corpus). Affordable: carry positions
  only for the comment index, not for hunks.
- Bucketing by hash means no prefix or wildcard queries. Substring identifier
  search needs the term dictionary, see section 6.
- 4,096 + 4,096 bucket files add ~8,200 files to the deploy. Tolerable. One file
  per term (208,918 files) is not, see section 3.

Verdict: this is the real zero-backend answer. It is also the artifact a backend
would serve if one is ever added, so building it is not a bet on staying static.

### A3. SQLite FTS5 over HTTP range requests

Measured against the real corpus, SQLite 3 via Python:

| Index | Size | Notes |
|---|---|---|
| Comments + titles, `content=doc`, `detail=full` | +36.4 MB over 255,344 docs | phrase and NEAR work |
| Same, `detail=none` | +14.8 MB | no phrase, no snippets |
| Stored document text (needed for snippets) | 119 MB | |
| Hunk text FTS at 49% diff coverage | +55.0 MB | ~113 MB at full coverage |
| Hunk text stored | 242 MB at 49% | ~490 MB at full |
| Contentless identifier index over hunks | +11.4 MB | ~23 MB at full; lookups 0 ms |
| Trigram index | 2.81 bytes per character of text | 270 MB for comments alone |

Query latency on a local file was 0 to 1 ms for `"depth peeling"` (159 hits),
`vtkIdList` (181), `"shared memory" AND crash` (3). Identifier lookups against
the contentless hunk index: `vtkOpenGLPolyDataMapper` 1,307 hunks, 0 ms;
`vtkSmartPointer` 4,646 hunks, 0 ms.

Range requests do work on the host. Measured 2026-10-23 against a Netlify-served
static asset:

```
curl -H 'Range: bytes=0-99' https://docs.netlify.com/_astro/print.DNXP8c50.css
HTTP/2 206
accept-ranges: bytes
content-range: bytes 0-99/3554
```

So `sql.js-httpvfs` or an equivalent VFS can target the deployed asset. What
breaks:

- `sql.js-httpvfs` is explicitly a demonstration project: "this library was
  mainly written for small personal projects of mine and as a demonstration",
  "the virtual file system part doesn't have any tests", and there is no cache
  eviction, so RAM grows with bytes read
  ([README](https://github.com/phiresky/sql.js-httpvfs)). It recommends
  `page_size=1024` to trade request count against overhead, and warns that a
  `SCAN TABLE` means downloading the table.
- `sqlite-wasm-http` exists as an alternative with an HTTP VFS, but its public
  signals are thin (2 contributors, ~86 weekly downloads on
  [Snyk's listing](https://snyk.io/advisor/npm-package/sqlite-wasm-http)) and I
  did not verify it against this corpus. **Unverified.**
- Request count is the cost, not bytes. An FTS5 term lookup is a B-tree descent
  plus a doclist read: order 5 to 20 pages at 1 KB each for a selective term,
  which is cheap, but a `bm25()` ordering reads `_docsize` and `_data` rows for
  every hit, and a snippet read pulls content pages. I did not instrument a
  range-backed run, so per-query request counts here are **estimates**.
- A 600 MB single asset on the Netlify CDN is untested by me. Sharding the DB
  (the VFS supports chunked databases) avoids the question.

Verdict: viable, and strictly worse than A2 for an agent client, because it buys
SQL expressiveness at the price of an unmaintained wasm VFS in the hot path. It
becomes the right answer the moment a server process exists, because then the
same file is opened locally, see section 3.

### A4. llms.txt plus URL construction

The URL space is derivable: `/vtk/vtk/-/issues/<iid>`,
`/vtk/vtk/-/merge_requests/<iid>`, both aliased, every page with a `.md` twin.
An agent that already has an iid, a reference like `!2754`, or a GitLab URL from
a stack trace or a commit message needs no search at all: one fetch, and at
ratio 0.43 the `.md` twin of a median page is ~2.6 KB.

This is free, it should exist regardless, and it is not search. Issue iids are
sparse (imported from Mantis, so 5,000 is a 404), so enumeration is not a
substitute for an index. `llms.txt` should carry the URL grammar, the facet
vocabulary (states, the label list, milestones), the index artifact locations,
and a worked example of each query shape, which is exactly the context an agent
needs to use A2 without an MCP server.

### Track A summary

| Option | Bytes per typical query | Granularity | Agent can drive it | Breaks at |
|---|---|---|---|---|
| A1 Pagefind from Node | 158 KB measured at 2k pages, ~300-400 KB projected | page | yes, measured | granularity, cold cost per process |
| A2 static postings buckets | 6-30 KB (3 terms, 4,096 buckets) | comment or hunk | yes, trivial client | no prefix/wildcard, client does scoring |
| A3 FTS5 over range | estimated 50-300 KB, unmeasured | arbitrary SQL | yes, with a wasm VFS | unmaintained VFS, RAM growth |
| A4 llms.txt + URLs | ~2.6 KB per page | item | yes | no recall at all |

Answer to the question: **yes, a genuinely useful agent search runs with zero
backend**, if "useful" means lexical and identifier retrieval at comment and
hunk granularity with faceted filtering, which is most of what this corpus is
asked. It is A2 plus A4, with A1 left in place for humans. What zero backend
does not give you: semantic or embedding recall, query-time analytics over the
whole corpus, and a single place to fix ranking without shipping a new client.

## 3. Track B: the smallest backend that is reliable

### Hard numbers, with sources

| Platform fact | Value | Source |
|---|---|---|
| Netlify Function sync execution limit | 60 s | [configuration](https://docs.netlify.com/build/functions/configuration/) |
| Scheduled / background limits | 30 s / 15 min | same |
| Default memory | 1024 MB, configurable 1024-4096 MB on credit-based Pro/Enterprise | same |
| Buffered response payload | 6 MB; streamed 20 MB | same |
| Function bundle size cap | not stated in Netlify's docs; AWS Lambda imposes 50 MB zipped, 250 MB unzipped including layers | [Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html); Netlify staff confirm the 50 MB figure in [community threads](https://answers.netlify.com/t/function-size-50-mb-but-cant-figure-out-why/75926) |
| Edge Function code size / memory / CPU | 20 MB compressed / 512 MB / **50 ms CPU per request** | [edge limits](https://docs.netlify.com/build/edge-functions/limits/) |
| Netlify Blobs object size | up to 5 GB; eventual consistency by default, propagation within 60 s, `strong` available at slower reads | [Netlify Blobs](https://docs.netlify.com/build/data-and-storage/netlify-blobs/) |
| ZIP deploy file cap | "a limit of 25,000 files per zip extraction for a site" | [API guide](https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/) |
| Deploy API rate | 3 deploys/min, 100/day; 500 req/min general | same |
| Range requests on the CDN | `accept-ranges: bytes`, `206`, `content-range` | measured 2026-10-23, section A3 |
| Cloudflare D1 | 10 GB paid, 500 MB free; 30 s query duration; 2 MB max row | [D1 limits](https://developers.cloudflare.com/d1/platform/limits/) |
| D1 FTS5 | "D1 supports a subset of SQLite extensions for added functionality, including" the FTS5 module and `fts5vocab` | [D1 SQL statements](https://developers.cloudflare.com/d1/sql-api/sql-statements/) |
| MCP transports | stdio and Streamable HTTP; HTTP+SSE deprecated | [MCP spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) |
| Claude Code MCP output budget | warns over 10,000 tokens, capped at 25,000 by default, >50,000 chars spilled to a file; idle abort 5 min HTTP / 30 min stdio | [Claude Code MCP](https://code.claude.com/docs/en/mcp) |

The deploy cap bites before any of the compute limits do. ~24,500 HTML pages
plus ~24,500 `.md` twins plus 4,273 attachments plus index artifacts is roughly
55,000 files, well past the 25,000-file ZIP limit, so the archive has to deploy
by the file-digest method with `async: true`, not by zip. One Netlify staff
thread reports a per-directory ceiling around 54,000 files
([thread](https://answers.netlify.com/t/is-there-a-storage-or-file-count-limit-for-business-plan-hosting/118172));
the flat `<url>/index.html` tree already avoids a single huge directory, and the
index artifacts should keep that property (bucket files under
`/_index/p/<nn>/<bucket>`, not 8,200 siblings).

### Candidates

| Option | Index it can hold | Verdict |
|---|---|---|
| Netlify Function, SQLite bundled via `included_files` | Must fit 250 MB unzipped, in practice ~40 MB of payload after runtime. Comments `detail=none` (14.8 MB) + hunk symbol index (23 MB) + metadata fits. Comment positions (36.4 MB) is borderline. Hunk text FTS (113 MB) plus any stored text does not | Workable only as a **pointer index**: the function returns ids and the client fetches text from the CDN `.md` twins. That is a real design, not a compromise, and it caps the response at ids plus scores |
| Netlify Function, SQLite pulled from Blobs to `/tmp` on cold start | Blobs hold up to 5 GB. Lambda `/tmp` defaults to 512 MB (Netlify does not document whether it is configurable, **unverified**). A ~600 MB full index does not fit; a 250 MB one does, at the price of a multi-hundred-MB cold download per instance inside the 60 s budget | Fragile. Cold start cost is unbounded in practice and every scale-out repeats it |
| Netlify Function, range-reading SQLite out of the CDN or Blobs | Range works on the CDN (measured). Blobs has no documented range read (**unverified**) | Means running a wasm VFS server-side to avoid running it client-side. Pointless |
| Netlify Edge Function | 50 ms CPU per request | Ruled out. An FTS query plus BM25 ordering plus snippet assembly will not fit 50 ms reliably, and the failure is silent truncation of the product, not an error you see in staging |
| Cloudflare Worker + D1 | 10 GB, FTS5 supported, 30 s query budget | The only listed managed option that holds the whole index, text included. Costs a second vendor and splits the deploy |
| Hosted search service (Typesense Cloud, Meilisearch Cloud, Algolia) | 255,344 + 365,000 documents. Per-record size caps and per-plan document caps apply and I did not verify any of them (**unverified**) | Buys ranking and typo tolerance, costs money forever, and makes the archive's availability depend on a vendor the archive exists because of unavailability |
| One small always-on box running SQLite behind a tiny HTTP service | Everything, no caps | Honest and cheap, and the thing the project explicitly does not want to operate |

### Recommendation for track B

If a backend is warranted, it is **one Netlify Function over a bundled,
pointer-only FTS5 index**, with document text served from the static `.md`
twins. It stays inside the Lambda cap by construction (`content=''` or
`detail=none`, no stored text), it has one artifact, it deploys with the site,
and when it is down the site and the A2 bucket index are still there. Promote
to **Worker + D1** only on a measured trigger: the pointer index crossing
~200 MB unzipped, or a need for query-time ranking features that need the text
in the same process as the query.

A backend is not warranted yet. Section 2 shows the retrieval an agent needs is
reachable statically, and the backend buys exactly one thing today: central
ranking changes without shipping a client.

## 4. MCP server design

Tools are shaped by the output budget: Claude Code warns above 10,000 tokens and
truncates at 25,000 by default. So no tool returns a page, a thread list, or a
diff without a cap, and every tool returns a link an agent can follow next. The
corpus-specific move is that each result carries its **code coordinates**, so a
discussion hit is already a code hit.

| Tool | Input | Returns | Code link |
|---|---|---|---|
| `search_discussions` | `q`, `kind`, `state`, `author`, `label`, `milestone`, `after`, `before`, `anchored_to_path`, `limit<=25` | per hit: `ref` (`#17418` / `!706`), title, state, date, author, comment id, 240-char marked snippet, `url`, `md_url`, score | `diff_anchor` when the comment is one of the 50,817 with a position: `{path, line, sha}` |
| `search_items` | same facets, `has_diff` | per hit: ref, title, state, labels, milestone, comment count, changed-file count, dates | `shas: {head, merge}` |
| `search_code` | `q` or `symbol`, `path_prefix`, `ext`, `after`, `before`, `limit<=20` | per hit: MR ref, file path, `@@` header, `new_start`, added/removed counts, hunk text truncated to a byte budget | `{path, new_start, sha, archive_url, git_url}` and the ids of review comments anchored to that hunk |
| `get_thread` | `ref`, `thread_id` or `comment_id`, `context` | one discussion as markdown: every note, author, date, resolved flag, system notes folded to one line | the thread's diff anchor, plus the hunk it refers to on request |
| `get_item` | `ref`, `include[]` of `body,threads,diff_summary,labels,links,timeline`, `max_bytes` | the `.md` twin, trimmed to the requested parts | per-file hunk table with SHAs |
| `file_history` | `path` or `path_prefix`, `limit` | MRs that touched it, newest first: ref, title, date, hunk count, whether review discussion is attached | SHA per MR |
| `symbol_history` | identifier | two lists: MRs whose hunks touch the identifier, and items that discuss it without touching it | hunk coordinates for the first list |
| `code_at` | `sha`, `path`, `line`, `context` | the file's lines at that commit if the SHA is resolvable, else a statement that it is not, plus the archive's hunk | see section 5 |
| `timeline` | `ref` | the item's life from system notes: state changes, label changes, milestone, assignee, linked issues, merge | merge SHA |

Notes on shape:

- Ten `search_discussions` hits at a 240-character snippet is on the order of
  1,500 tokens. That is the design target: an agent should be able to run three
  searches and still have room to read two threads.
- `resolved` on a thread is a first-class return value. A resolved review thread
  is where a decision was made, which is what "why does the interface look like
  this" is asking for.
- System notes (130,838 of them) are never returned as search hits. They are
  only the raw material for `timeline`.
- Every tool takes and returns the GitLab-native reference form (`#iid`, `!iid`)
  because that is what commit messages and comments cite, so an agent can go
  from a string in the source tree to a tool call with no translation.

### Local npm server or remote HTTP server

| | Local, npm-distributed, queries static assets | Remote Streamable HTTP |
|---|---|---|
| Install | `claude mcp add --transport stdio vtk-archive -- npx -y @pyvista/vtk-archive-mcp` | `claude mcp add --transport http vtk-archive https://.../mcp` |
| Who pays | the user's machine and bandwidth | the project, per query, forever |
| Cold cost | amortized: the dictionary and hot buckets cache in the process, and the process lives 30 min idle per the stdio timeout | none for the client |
| Offline | works from cache | no |
| Ranking fixes | ship a new package version | instant |
| Clients that can use it | anything that can spawn a process | also hosted clients that cannot |
| Failure mode | user sees an npm or network error | one endpoint down for everyone |
| Operational surface | none | TLS, abuse, auth, logging, a service to patch |

**Pick the local npm server.** It matches the archive's whole premise: no
origin to patch, no availability you have to promise. It is also the only option
where a stale answer is impossible to hide, since the client reads the same
deployed artifacts a human reads. Add the remote HTTP server later, backed by the
same index build, when a client that cannot run a local process is a real
request rather than a hypothetical. Same tools, same ranking, one artifact, two
transports.

## 5. Getting from a hit to the linked code

The archive holds hunks, paths, line numbers and SHAs. It does not hold VTK's
source. The gap is crossable, but not uniformly, and the measurement matters.

Measured 2026-10-23, 24 MR SHAs sampled from the corpus by state, resolved
against `raw.githubusercontent.com/Kitware/VTK/<sha>/<path>`:

| MR state | Head SHA resolves | Sample |
|---|---|---|
| merged | 8 of 8 | 200 OK |
| closed | 0 of 8 | 404 |
| opened | 0 of 8 | 404 |

A bogus SHA returns 404, so the 200s are real. `merge_commit_sha` is present on
7,513 of 13,870 MRs and resolves where present. GitLab's own raw route
(`gitlab.kitware.com/vtk/vtk/-/raw/<sha>/<path>`) and the REST files endpoint
both returned 200 anonymously for a merged head SHA, byte-identical in size to
the GitHub mirror. `Kitware/VTK` on GitHub is not a fork and was pushed to
within the hour of measurement.

Consequences:

1. For 11,812 merged MRs (85%), a hit resolves to real code at the exact commit
   through two independent hosts. Prefer the GitHub mirror for reads: it is not
   the host this archive exists because of, and it is not UA-gated.
2. For 2,058 closed and open MRs, the head commit is unreachable to an anonymous
   fetch. **The archive's stored diff is the only copy of that code state.**
   That is an argument for keeping full hunk text in the archive and for
   `search_code` returning hunk text rather than a link.
3. So `code_at` is a ladder, and it says which rung it used: merge SHA, then head
   SHA, then the target branch at the merge date, then the archive's own hunk,
   then a plain refusal. A tool that silently falls back from "the code at this
   commit" to "the code today" is worse than one that refuses, because the agent
   will reason about lines that were never there.
4. The hunk header already carries `@@ -a,b +c,d @@`, so a hunk maps to a line
   range in the post-image. Store `new_start` and the counts at build time (done
   in the probe: 177,821 hunks parsed in 2 s) so `search_code` can answer "what
   line range" without reparsing.
5. For an agent with a local VTK checkout, which is the common case for a coding
   agent, the most useful return is not file content at all. It is the pair
   (path, sha) plus the hunk, which the agent turns into `git log -L`,
   `git show <sha>`, or `git blame` locally. `search_code` should return a
   ready-to-run `git` invocation alongside the URLs.
6. Reverse direction, the one that pays off most: given a path and a line in the
   working tree, `git blame` gives a SHA, and a SHA-to-MR map built from
   `sha` and `merge_commit_sha` gives the MR, its review threads, and the issues
   it closed. That map is small (one row per MR) and turns the archive into a
   blame-to-rationale lookup. It is the single highest-value artifact in this
   whole proposal.

## 6. Ranking and recall

### What the queries actually are

| Query shape | Example | Needs |
|---|---|---|
| Identifier | `vtkOpenGLPolyDataMapper`, `VTK_SIZEHINT` | exact term match, high precision, code and discussion both |
| Symptom | `segfault on exit`, `black render on macOS` | phrase and proximity, recency weighting |
| Rationale | `why was SetUseGPU removed` | comment-level retrieval, resolved threads, state events |
| Provenance | `what changed in Rendering/OpenGL2 before 9.0` | path prefix plus milestone or date, not text at all |
| Prior art | `has anyone reported this` | recall over 20 years, dedup, closed items included |

Four of the five are served by fields and filters, not by better text scoring.
Filters are where the wins are.

### Fields and weights

Indexing unit: one document per comment, plus one per item (title + description),
plus one per hunk. Proposed weights for `bm25()`, which takes per-column weights
left to right ([FTS5](https://www.sqlite.org/fts5.html)):

| Field | Weight | Rationale |
|---|---|---|
| Item title | 8 | shortest, most deliberate text in the corpus |
| Identifier field (extracted `vtk*` and CamelCase tokens) | 6 | the precision lever for the most common query shape |
| File path tokens on a hunk | 5 | `Rendering/OpenGL2` as a query term should hit |
| Item description | 3 | |
| Human comment body | 2 | |
| Hunk added lines | 2 | what the change became |
| Hunk removed lines | 1 | what it replaced, still relevant, less so |
| System note body | 0 (excluded) | 130,838 documents of bookkeeping |

Multiplicative boosts applied after BM25:

- `resolved` review thread: x1.4. Resolution marks a decision.
- Comment is diff-anchored (50,817 of them): x1.3 for code queries.
- Item is merged or closed-with-merge: x1.2 for "how was this fixed".
- Comment length: dampen both tails. A 9-character note and a 7.2 M-character
  note are both noise; the p99 is 1,773 characters, so clamp the length
  normalization there.
- Recency: mild and explicit, never silent. VTK's old arguments are often the
  valuable ones (`#16824` era rendering decisions still govern today), so
  recency is a tiebreak, not a prior. Expose `after`/`before` instead and let the
  agent decide.
- Author: no boost. Tempting for core maintainers, and it would bury exactly the
  user reports that explain why an interface is awkward.

### Tokenization is the recall problem

Measured against the real comment index with the default `unicode61` tokenizer:

| Query | Hits |
|---|---|
| `vtkIdList` | 181 |
| `vtkidlist` | 181 (case folded, so case-insensitivity is free) |
| `idlist` | 7 |
| `IdList` | 7 |
| `vtkOpenGLPolyDataMapper` | 296 |
| `polydatamapper` | 47 |

An agent that knows a class by a fragment gets a fraction of the recall. The
fixes, in cost order:

1. **Camel-case split into a second indexed field.** `vtkOpenGLPolyDataMapper`
   also indexes as `vtk open gl poly data mapper`. Cheap, and it makes
   `polydatamapper`-style queries work via phrase match.
2. **A trigram index over the term dictionary only, not the text.** Measured,
   trigram costs 2.81 bytes per character of indexed text, so trigram over the
   97 MB of comment text is ~270 MB and over diffs is over 1 GB. Over the
   208,918-term dictionary (about 1.9 M characters) it is roughly 5 MB, derived
   not measured. Substring query hits the dictionary, expands to exact terms,
   then hits the real index. In the 20,000-document slice I measured, trigram
   found 60 documents for `idlist` where `unicode61` found 7 corpus-wide.
3. **`tokenchars '_'`** so `VTK_SIZEHINT` and `depth_peeling` survive as single
   tokens. Used in the hunk probe.
4. **A curated identifier vocabulary** from the 7,986 distinct `vtk*` terms in
   comments plus the identifiers extracted from hunks, exposed to agents in
   `llms.txt`. An agent that can see the vocabulary stops guessing spellings.

### Recall traps specific to this corpus

- **Stopwords carry 55% of postings.** 130 terms with docfreq over 10,000. Drop
  them from the index, keep them for phrase matching only if positions exist.
- **Code in prose.** `std::vector<int>` and `<vtkIdList>` appear across 1,172
  author comments as C++, not markup (see `SOURCES.md`). The tokenizer must not
  eat angle brackets into nothing, and the renderer already treats these as
  text; the indexer must agree with the renderer or snippets will not match the
  page.
- **One 7.2 M-character comment.** Any per-document buffer has to be bounded, and
  any "top snippet" has to be windowed. Build-time truncation with a recorded
  `truncated: true` is better than a build that OOMs.
- **Duplicate text across aliases.** Issues are emitted at both `issues/` and
  `work_items/`. The index must key on (kind, iid) and emit one canonical URL,
  or every result is doubled.
- **8 MRs have truncated diffs** (GitLab caps `/diffs` at 1,000 files) and 214
  files are flagged `too_large`. A code search that silently omits these is
  lying by omission; return the flag.
- **Diff coverage is partial until the crawl finishes** (6,755 of 13,870). The
  index must publish its own coverage so an agent can tell "no hits" from "not
  indexed yet". This is the same discipline as `check-access`: state what the
  artifact actually reaches.

### What good looks like

A measurable bar, not an adjective. Build a set of ~50 queries from real VTK
questions, each with a known-correct target item or thread, drawn from three
sources: references cited in VTK commit messages, questions on the PyVista
tracker that have a VTK upstream answer, and interface decisions whose rationale
is known. Then: recall@10 for the known target, and for code queries, whether
the returned hunk coordinates resolve. Ranking changes get accepted or rejected
against that set, and the set is committed. A gate that cannot go red is not a
gate.

## 7. Recommendation and staged work

Recommendation: **stay static, and make the static artifacts good enough that an
MCP server is a thin client over them.** Build the A2 bucket index and the
SHA-to-MR map; ship a local npm MCP server that owns ranking; keep Pagefind for
humans. Treat the Netlify Function as a later, measured decision with one
trigger (index crossing the Lambda cap, or a hosted-client requirement), not as
a phase.

Order is by dependency. Size is what each step contains, not how long it takes.

1. **Index-ready page emission.** Blocks everything else. Contains: the `.md`
   twin per page; a stable per-comment anchor; `_manifest.json` extended with
   crawl coverage per corpus; the digest-based deploy path (the 25,000-file ZIP
   cap is already exceeded). Exit: every item reachable at its `.md` URL, every
   comment addressable by id, and a deploy of the full tree succeeds.

2. **Build-time extraction into one SQLite file.** Contains: the three document
   tables (items, comments, hunks) with the fields in section 6; hunk header
   parsing into `(path, new_start, added, removed)`; identifier extraction with
   camel-case splitting; the SHA-to-MR map; the term dictionary with document
   frequencies. Measured inputs: 255,344 comment documents, ~365,000 hunks at
   full crawl. Exit: the file builds from `data/` alone, in one pass, and its
   row counts match the corpus counts in section 1.

3. **The static index artifacts.** Contains: 4,096 postings buckets for
   comments and 4,096 for hunks (16 MB and roughly 32 MB), the document metadata
   table, the term dictionary with its trigram sidecar, the SHA-to-MR map as a
   fetchable shard set, a version stamp and a coverage statement. Exit: a query
   for a known identifier resolves from the deployed site in three GETs, and the
   measured byte count per query is recorded in this doc.

4. **The evaluation set.** Must precede ranking work or ranking work has no
   verdict. Contains: ~50 queries with known targets across the five query
   shapes, committed with their expected hits. Exit: the set runs against the
   step-3 artifacts and reports recall@10, and at least one query is red (a set
   where everything passes on the first build is measuring the build, not the
   search).

5. **The local MCP server.** Contains: the nine tools in section 4; the client
   side of the bucket protocol with an in-process cache; BM25 plus the boosts;
   the `code_at` ladder with its rung reported; output budgeting against the
   10,000-token warning. Exit: every tool returns inside the budget on its worst
   measured input, `code_at` refuses an unmerged SHA rather than substituting
   today's file, and recall@10 on the step-4 set is recorded.

6. **llms.txt as the no-MCP path.** Contains: the URL grammar, facet
   vocabularies, the identifier vocabulary, the index artifact layout, and one
   worked query per shape. Exit: an agent with no MCP server and only
   `llms.txt` answers three of the five query shapes from the deployed site.

7. **Code-link verification as a standing check.** Contains: a sampled
   resolution run over MR SHAs against the mirror, by state, asserting the
   measured pattern in section 5 still holds, and failing on drift. Exit: the
   check is red when pointed at a deliberately wrong SHA.

8. **Decide the backend on the measurement, not the roadmap.** Contains: the
   recorded per-query bytes from step 3, the recall from step 5, and the
   artifact sizes against the 250 MB unzipped Lambda cap. Exit criterion for the
   whole programme: an agent with no special configuration, given a VTK symbol
   or a file path and line, reaches the merge request that changed it and the
   review thread that argued about it, in under four tool calls, with the code
   coordinates resolving. If that holds statically, no backend ships.

## 8. Not verified

- `sqlite-wasm-http`'s current maintenance state and behaviour on this corpus.
- Whether Netlify's function `/tmp` is 512 MB or configurable.
- Whether Netlify Blobs supports ranged reads.
- Maximum single static asset size on the Netlify CDN.
- Per-plan document and record limits for Typesense Cloud, Meilisearch Cloud and
  Algolia.
- Per-query request counts for an FTS5 index read through an HTTP range VFS.
  Estimated in section A3, not instrumented.
- `pf_meta` scaling to 24,500 pages is a linear extrapolation from 2,000.
- Trigram cost over the term dictionary is derived from the measured 2.81 bytes
  per character, not measured directly.
- Pagefind projections assume index chunk count scales with page count;
  vocabulary growth is sublinear, so they are upper bounds.
