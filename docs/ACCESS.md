# The access this archive needs

Two tiers. Tier 1 needs nothing from Kitware and unblocks the comment threads,
which is the whole point. Tier 2 is a fidelity upgrade worth asking for.

Verify any change with `uv run vtk-archive check-access`, which probes each
endpoint and prints what the current credential actually reaches. The
anonymous baseline, measured 2026-10-22:

```
identity: anonymous
  issues list              OK          MR diffs      OK
  merge_requests list      OK          MR commits    OK
  issue notes              401         award emoji   OK
  MR notes                 401
  MR discussions           401
  labels                   401
  milestones               401
  issue links              401
  resource label events    401
7 of 12 probes blocked
```

## Tier 1 — a `read_api` token on your own account

**No Kitware coordination.** Self-registration at
`https://gitlab.kitware.com/users/sign_up` is open (`200`), and you likely
already have an account from when you worked there.

The seven blocked probes above are `401 Unauthorized`, not `403 Forbidden`:
they fail for lack of *authentication*, not lack of permission. VTK is a
public project; any signed-in user can read its notes. Kitware gates these
endpoints deliberately — Ben Boeckel said so in the Discourse thread, and
Matthew Woehlke wrote "I don't see why a login requirement is an issue for a
legitimate archivist." Authenticating is the sanctioned path, not a
workaround.

### Steps

1. Sign in at `https://gitlab.kitware.com`.
2. **Preferences → Access tokens → Add new token.**
3. Name it `vtk-archive`, set an expiry you'll actually renew.
4. Scope: **`read_api` only.** Not `api`. Nothing that can write.
5. Put it in `~/.config/vtk-archive/env` (`chmod 600`):

   ```
   GITLAB_TOKEN=glpat-...
   ```

6. Confirm: `uv run vtk-archive check-access` should print your username and
   turn those seven `401`s into `OK`.

### What it unlocks

| Endpoint | Gives |
|---|---|
| `/issues/:iid/notes`, `/merge_requests/:iid/notes` | ~304,000 comments, full history to 2003 |
| `/discussions` | Thread structure, `resolved`, diff `position` |
| `/labels`, `/milestones` | The label and milestone catalog |
| `/issues/:iid/links` | Related-issue graph |
| `/resource_label_events`, `/resource_state_events` | The label and state timeline |

Comments arrive as **raw markdown**. The archive renders it, resolving `#123`,
`!456` and `@user` against its own pages — which makes the archive
self-contained rather than linking back to a host that may be down.

### Rate limits under a token

Authenticated callers get a higher ceiling than anonymous ones, but the
crawler stays sequential with a delay regardless. Nothing about having a token
licenses going faster: parallel scraping is the thing that broke this host.

## Tier 2 — an Anubis allowlist entry (needs Kitware)

GraphQL returns GitLab's **own rendered HTML** (`descriptionHtml`, `bodyHtml`)
— GitLab-flavored markdown, reference resolution and syntax highlighting,
already done. That is strictly better fidelity than rendering markdown
ourselves.

But Anubis gates `/api/graphql` to a User-Agent allowlist of `curl/*` and
`Wget/*`. An honestly-named crawler gets `403`. See docs/SOURCES.md for the
measured table.

**We do not spoof an allowlisted agent.** A `curl` User-Agent pulling 19,000
items is dishonest and trivially visible in their logs, and the archive's
legitimacy is the only thing it really has.

### The ask

One entry in Kitware's Anubis policy allowlisting User-Agent prefix
`vtk-archive/`, applied to `/api/graphql`.

That is the entire request. It is not an export, not a backup, not a
credential, not a firewall change.

### Draft message

> I'm building a read-only archive of VTK's GitLab issue and merge-request
> history at github.com/pyvista/vtk-archive — the discussions, not the
> code, which is already mirrored everywhere.
>
> Almost all of it is reachable with an ordinary `read_api` token on my own
> account, and that is what the crawler uses. One thing is not: `/api/graphql`
> is the only route that returns your rendered HTML for comment bodies, and
> Anubis allowlists `curl/*` and `Wget/*` by User-Agent. My crawler sends
> `vtk-archive/0.1 (+<repo url>)` and gets a 403. I'm not going to send a curl
> User-Agent to get around that.
>
> Could you allowlist the prefix `vtk-archive/` on `/api/graphql`? If that's a
> no, the archive still works — it renders the markdown from the REST API
> instead — so this is a fidelity request, not a blocker.
>
> On load: the crawler is sequential with a delay between requests, never
> concurrent, and the whole backlog is roughly 20,000 items once. Happy to run
> it in a window you pick, cap the rate wherever you want, or stop on request.
> Contact is in the repo.

### If the answer is no

Nothing breaks. The archive renders markdown from REST. It loses GitLab's
exact reference resolution and some code-block highlighting nuance, and gains
self-contained internal links. Ship it and move on.

## Tier 3 — page snapshots (optional, later)

Pixel-faithful WARC/WACZ capture with browsertrix needs a UA or IP exemption
on the HTML routes, which are Anubis-gated for every browser agent. Purely
additive; the content archive does not depend on it. Don't ask for this in the
same conversation as Tier 2.
