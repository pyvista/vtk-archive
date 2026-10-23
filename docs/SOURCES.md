# What gitlab.kitware.com serves to an anonymous caller

Measured 2026-10-22 against `gitlab.kitware.com`. Re-measure before trusting
any row: these are the host's live access controls, not documented contracts.

## Anubis gates `/api/graphql` by User-Agent allowlist

The proof-of-work challenge (`403`, `Verifying connection...`) is applied to
`/api/graphql` based on User-Agent prefix. Measured:

| User-Agent | GraphQL |
|---|---|
| *(empty)* | `200` |
| `curl/8.7.1`, `curl/0` | `200` |
| `Wget/1.21` | `200` |
| `curl`, `mycurl/1.0`, `vtk-archive/0.1 curl/8.7.1` | `403` |
| `git/2.56.0`, `Go-http-client/1.1`, `python-requests/2.32` | `403` |
| `Mozilla/5.0`, `Googlebot/2.1`, anything else | `403` |

Prefix match on Anubis's default allowlist. A bare `curl` with no version
fails, so the rule is `curl/*` and `Wget/*`, not a substring test.

`/api/v4` (REST) is **not** UA-gated: a custom agent gets `200`.

**We do not spoof an allowlisted agent.** Sending `curl/*` to get past this
is circumventing the host's bot defense, and a `curl` agent pulling 19,000
items is both dishonest and trivially visible in their logs. The crawler sends
its real name and takes the `403`. Completing the backlog needs one allowlist
line from Kitware.

## Where the comments live

| Route | Anonymous | Carries comment bodies |
|---|---|---|
| GraphQL `notes`/`discussions` | UA-gated | Yes, with `bodyHtml`, diff `position`, `resolved` |
| REST `/issues/:iid/notes`, `/merge_requests/:iid/notes` | `401` | — |
| REST `/resource_label_events`, `/resource_state_events` | `401` | — |
| REST `/events?action=commented` | `200`, any UA | Yes, but see retention |

### `/events` retention stops around 2023

| `before=` | Result |
|---|---|
| `2024-01-01` | records back to `2023-12-31` |
| `2023-01-01` and earlier | empty |

So events reach roughly 2023 onward. VTK's oldest issue is `iid` 11, created
2003-06-26. Events cannot supply the other twenty years, and they carry no
discussion threading, no diff anchoring and no resolved state.

Events also expose `author.public_email` (a real address), which GraphQL's
`publicEmail` returns empty. Strip it; see docs/PRIVACY.md.

## Open without any UA condition

- Issues: list and detail. `iid`s are sparse — 5,000 is a `404`, imported from
  Mantis with original numbering.
- Merge requests: list, detail, `/diffs`, `/changes`, `/commits`
- `/award_emoji`
- Repository: `/repository/tree`, `/repository/files`, `/repository/commits`
- Attachments at `/-/project/13/uploads/<hash>/<file>` — note the `/-/project/13/`
  form; the `/vtk/vtk/uploads/...` spelling in comment markdown is a `404`
- Plain `git clone`

## Closed

`/export` (`403`, needs Maintainer), `/wikis` (`403`), `/snippets` (`401`),
`/merge_requests/:iid/versions` (`401`, so the final diff only, not each
review round).

Only 829 of 13,868 merge requests advertise a `refs/merge-requests/*/head` to
an anonymous clone. Diffs come from the API, not from git refs.

## Corpus size

5,226 issues, 13,868 merge requests, ~304,000 comments, 4,273 attachments at
2.06 GB. Issue JSON ~4.8 KB each, MR JSON ~8.8 KB each: ~150 MB total.

## System notes carry GitLab's own HTML

Note bodies arrive as markdown and render with `html=False`, so a tag an
author typed stays visible text. That is right for human bodies — `<vtkIdList>`
and `std::vector<int>` appear across 1,172 author comments and are C++, not
markup — and wrong for system notes, which GitLab writes itself. 10,588 of
57,457 system notes carry real HTML, in a closed vocabulary: `<li>` 27,389,
`<ul>` 10,503, `<code>` 2,044, `<code class="idiff">` 152, `<p>` 73, and
`<span class="idiff …">` 151 across eight class values.

`builder.render.unescape_system_html` restores exactly that set, for notes
flagged `system` only. The tag name must be one of `ul ol li p code span br
strong em`, and a `class` of bare words is the only attribute that survives,
so an `href` or an `on*` handler stays escaped. `tests/test_system_notes.py`
holds the refusals; widening the pattern to any tag turns three of them red.
