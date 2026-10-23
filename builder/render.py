"""Markdown to HTML, with GitLab's reference syntax resolved to archive pages.

GitLab renders its own markdown and would hand us the HTML via GraphQL, but
that route is User-Agent gated (docs/SOURCES.md), so we render the markdown the
REST API gives us. Resolving `#123` and `!456` to local pages is better for an
archive anyway: the result does not depend on the host staying up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markupsafe import Markup
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

CANONICAL = "https://gitlab.kitware.com/vtk/vtk"
UPLOADS = "/-/project/13/uploads"
# Kitware's own GitHub mirror of the same git history, so a commit or file link
# resolves to the same SHAs without sending anyone to the host this archive
# exists to take load off. See docs/SOURCES.md "Code links go to the mirror".
MIRROR = "https://github.com/Kitware/VTK"

# Regions whose contents are verbatim and must never be rewritten. An anchor's
# whole tag is skipped so a reference inside an href or title is left alone.
_PROTECTED = re.compile(
    r"(<pre\b.*?</pre>|<code\b.*?</code>|<a\b.*?</a>|<[^>]+>)",
    re.DOTALL | re.IGNORECASE,
)

# A fenced block or an inline span, where the text is what the author typed.
_VERBATIM = re.compile(r"(<pre\b.*?</pre>|<code\b.*?</code>)", re.DOTALL | re.IGNORECASE)

# A pasted log is sometimes megabytes. The `.md` twin keeps every byte; the
# HTML page is what a browser has to open. docs/HOSTING.md "A pasted log".
BODY_CAP = 200_000
_CAPPED = Markup(
    '<p class="note-cap">Cut off at {:,} characters, which is as much as this'
    " page renders. Append <code>.md</code> to this page's URL for the whole"
    " thing.</p>"
).format(BODY_CAP)


def cap_body(markdown: str) -> tuple[str, bool]:
    """Trim an overlong body on a line boundary.

    An open fence needs no closing: markdown-it ends an unterminated one at the
    end of the input, so the cut lands inside a `<pre>` that still closes.
    """
    if len(markdown) <= BODY_CAP:
        return markdown, False
    kept = markdown[:BODY_CAP]
    cut = kept.rfind("\n")
    return (kept[:cut] if cut > 0 else kept), True


def outside_code(html: str, rewrite) -> str:
    """Apply `rewrite` to everything except the inside of a code region.

    `resolve_references` protected itself; the upload and code-link rewrites
    did not, so a URL in a code sample came back altered on 19 pages.
    """
    parts = _VERBATIM.split(html)
    # split() with one capture group alternates text, protected, text, ...
    for i in range(0, len(parts), 2):
        parts[i] = rewrite(parts[i])
    return "".join(parts)


# Five digits, not six: VTK's highest iid is ~20k, and `#123456` is far more
# likely a CSS color than an issue. Full 40-char SHAs only, because `deadbeef`
# and any hex dump match a short-SHA pattern.
_ISSUE_REF = re.compile(r"(?<![\w&#/])#(\d{1,5})\b")
_MR_REF = re.compile(r"(?<![\w!/])!(\d{1,5})\b")
_USER_REF = re.compile(r"(?<![\w@/])@([a-zA-Z0-9][\w.\-]{1,48})\b")
_SHA_REF = re.compile(r"(?<![\w/])([0-9a-f]{40})(?![\w/])")


def build_parser() -> MarkdownIt:
    md = MarkdownIt("gfm-like", {"linkify": True, "html": False, "typographer": False})
    md.use(footnote_plugin).use(tasklists_plugin)
    md.enable("table")
    md.enable("strikethrough")
    return md


@dataclass
class Corpus:
    """What the archive holds, so a link never dangles.

    `mirror_commits` is the set of SHAs Kitware's GitHub mirror actually has.
    VTK force-pushes merge-request branches, so two thirds of the SHAs this
    corpus names are not reachable there and must go to GitLab instead.
    """

    issues: frozenset[str]
    merge_requests: frozenset[str]
    mirror_commits: frozenset[str] = frozenset()
    # Attachment keys whose bytes are really on disk. Empty means "claim none".
    uploads: frozenset[str] = frozenset()
    # Handles the corpus itself shows. Empty means "link no @mention", which is
    # what an unpopulated Corpus in a test should do.
    users: frozenset[str] = frozenset()

    @classmethod
    def empty(cls) -> Corpus:
        return cls(frozenset(), frozenset())


def _link(href: str, text: str, *, external: bool = False) -> str:
    rel = ' rel="nofollow noopener"' if external else ""
    cls = ' class="ref ref-ext"' if external else ' class="ref"'
    return f'<a href="{href}"{cls}{rel}>{text}</a>'


def _resolve_segment(text: str, corpus: Corpus) -> str:
    # An iid the corpus lacks is not an issue on the host either: 40 sampled
    # absences all answer 404 (docs/SOURCES.md "The iid sequence is sparse"),
    # and most are `#1` or `#0` meaning "point one". Leave those as text.
    def issue(m: re.Match[str]) -> str:
        n = m.group(1)
        if n in corpus.issues:
            return _link(f"/vtk/vtk/-/issues/{n}", m.group(0))
        return m.group(0)

    def mr(m: re.Match[str]) -> str:
        n = m.group(1)
        if n in corpus.merge_requests:
            return _link(f"/vtk/vtk/-/merge_requests/{n}", m.group(0))
        return m.group(0)

    # Only a handle the corpus has seen a person use. Doxygen's `@param`,
    # `@brief` and `@return` are all over pasted code comments, and each one
    # became a link to a gitlab.kitware.com profile page that does not exist.
    def user(m: re.Match[str]) -> str:
        if m.group(1) not in corpus.users:
            return m.group(0)
        return _link(f"https://gitlab.kitware.com/{m.group(1)}", m.group(0), external=True)

    def sha(m: re.Match[str]) -> str:
        return _link(commit_url(m.group(1), corpus), m.group(1)[:12], external=True)

    text = _ISSUE_REF.sub(issue, text)
    text = _MR_REF.sub(mr, text)
    text = _USER_REF.sub(user, text)
    return _SHA_REF.sub(sha, text)


def resolve_references(html: str, corpus: Corpus) -> str:
    """Rewrite GitLab references in `html`, leaving code and existing links alone."""
    parts = _PROTECTED.split(html)
    # split() with one capture group alternates text, protected, text, ...
    for i in range(0, len(parts), 2):
        parts[i] = _resolve_segment(parts[i], corpus)
    return "".join(parts)


# GitLab writes system notes itself and puts real HTML in them; everyone
# else's body keeps every tag escaped, because `std::vector<int>` is not
# markup. See docs/SOURCES.md "System notes carry GitLab's own HTML".
_SYSTEM_TAG = re.compile(
    r"&lt;(/?)(ul|ol|li|p|code|span|br|strong|em)((?: class=&quot;[a-zA-Z0-9 _-]+&quot;)?)/?&gt;"
)


def unescape_system_html(html: str) -> str:
    """Restore the closed set of tags GitLab emits in its own system notes."""

    def repl(m: re.Match[str]) -> str:
        close, name, attr = m.groups()
        return f"<{close}{name}{attr.replace('&quot;', chr(34))}>"

    return _SYSTEM_TAG.sub(repl, html)


# A link someone pasted as a full URL should land in the archive, not send the
# reader back to the host this exists because of. Only vtk/vtk issue, work-item
# and MR URLs are local; a commit, a pipeline or another project stays external.
_ANCHOR = re.compile(r"<a\s([^>]*?)href=\"([^\"]+)\"([^>]*)>(.*?)</a>", re.DOTALL | re.IGNORECASE)
_CANONICAL_ITEM = re.compile(
    r"^(?:https?://gitlab\.kitware\.com)?/vtk/vtk/(?:-/)?"
    r"(issues|work_items|merge_requests)/(\d{1,6})"
    r"(?:/[\w./-]*)?(\?[^#]*)?(#[\w-]*)?$"
)
_LOCAL_KIND = {"issues": "issues", "work_items": "issues", "merge_requests": "merge_requests"}


def localize_urls(html: str, corpus: Corpus) -> str:
    """Point a pasted gitlab.kitware.com item URL at the archive's own page."""

    def repl(m: re.Match[str]) -> str:
        before, href, after, text = m.groups()
        hit = _CANONICAL_ITEM.match(href.strip())
        if not hit:
            return m.group(0)
        kind, iid, _query, fragment = hit.groups()
        local_kind = _LOCAL_KIND[kind]
        held = corpus.issues if local_kind == "issues" else corpus.merge_requests
        if iid not in held:
            return m.group(0)
        attrs = f"{before}{after}".replace('rel="nofollow noopener"', "").strip()
        attrs = f" {attrs}" if attrs else ""
        # A bare URL as its own link text is unreadable; GitLab shows the sigil.
        sigil = "#" if local_kind == "issues" else "!"
        shown = f"{sigil}{iid}" if text.strip().rstrip("/") == href.strip().rstrip("/") else text
        return f'<a href="/vtk/vtk/-/{kind}/{iid}{fragment or ""}" class="ref"{attrs}>{shown}</a>'

    return _ANCHOR.sub(repl, html)


# GitLab and GitHub spell these the same way after the host, except `raw`.
_CODE_PATH = re.compile(r"(?:https?://gitlab\.kitware\.com)?/vtk/vtk/-/(blob|tree|blame)/")
_RAW_PATH = re.compile(r"(?:https?://gitlab\.kitware\.com)?/vtk/vtk/-/raw/")


def commit_url(sha: str, corpus: Corpus) -> str:
    """The mirror when it has the commit, GitLab when it does not."""
    if sha in corpus.mirror_commits:
        return f"{MIRROR}/commit/{sha}"
    return f"{CANONICAL}/-/commit/{sha}"


_COMMIT_LINK = re.compile(r"(?:https?://gitlab\.kitware\.com)?/vtk/vtk/-/commit/([0-9a-f]{7,40})")


def rewrite_code_links(text: str, corpus: Corpus) -> str:
    """Send file and tree links to the mirror; commits to whoever has them."""
    text = _RAW_PATH.sub("https://raw.githubusercontent.com/Kitware/VTK/", text)
    # A commit link only moves when the mirror really holds that SHA. A short
    # SHA cannot be checked against the table, so it stays where it was.
    text = _COMMIT_LINK.sub(lambda m: commit_url(m.group(1), corpus), text)
    return _CODE_PATH.sub(rf"{MIRROR}/\1/", text)


def blob_url(sha: str | None, path: str, line: int | None = None) -> str:
    """Where one file of a diff lives in the mirror.

    `sha` must be a commit the mirror actually holds, which means a merged
    merge request's head. An open or closed one points at a branch that was
    never pushed, so its head 404s there and the caller passes None to get the
    file as it stands on master instead. docs/SOURCES.md "Code links".
    """
    ref = sha or "master"
    anchor = f"#L{line}" if line else ""
    return f"{MIRROR}/blob/{ref}/{path}{anchor}"


# The project-scoped spelling is matched too. Anchoring on `/uploads/` alone
# rewrote the tail of `.../vtk/vtk/uploads/<hex>/x.png` and left the host and
# project prefix in front of it, producing a URL that exists nowhere.
_UPLOAD_LINK = re.compile(
    r"(?:https?://gitlab\.kitware\.com)?(?:/vtk/vtk)?(?:/-)?/uploads/([0-9a-f]{32})/"
)


def rewrite_uploads(html: str, corpus: Corpus | None = None) -> str:
    """Point attachment links at the archived copy, when there is one.

    A link to a file the archive never fetched is left on the canonical host,
    where it still resolves. Rewriting it unconditionally turned every missed
    attachment into an archive 404 that nothing reported.
    """

    def swap(m: re.Match[str]) -> str:
        digest = m.group(1)
        if corpus is not None and digest not in corpus.uploads:
            return m.group(0)
        return f"{UPLOADS}/{digest}/"

    return _UPLOAD_LINK.sub(swap, html)


def to_html(
    markdown: str | None,
    corpus: Corpus,
    md: MarkdownIt | None = None,
    *,
    system: bool = False,
) -> Markup:
    """Render one markdown body to HTML a template may emit verbatim.

    The return is `Markup`, not `str`, because the templates autoescape and a
    plain string puts the tags on the page as literal text. Marking it here
    keeps the decision in the one place that knows the HTML is already safe:
    `build_parser` sets `html=False`, so raw HTML an author typed into an issue
    arrives escaped and never becomes live markup.
    """
    if not markdown:
        return Markup()
    markdown, capped = cap_body(markdown)
    parser = md or build_parser()
    html = parser.render(markdown)
    if system:
        html = unescape_system_html(html)
    # These two match a bare URL in text, so they are held outside code.
    html = outside_code(html, lambda s: rewrite_code_links(rewrite_uploads(s, corpus), corpus))
    # This one only touches an `<a href>` the renderer generated, and
    # markdown-it does not linkify inside a code region, so it sees no code.
    html = localize_urls(html, corpus)
    out = Markup(resolve_references(html, corpus))
    return out + _CAPPED if capped else out
