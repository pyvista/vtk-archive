#!/usr/bin/env bash
# Ask the deployed site which Cache-Control it really serves.
#
# `dist/_headers` is read by Netlify, whose rule precedence is documented
# nowhere: the LAST match wins and specificity counts for nothing. The first
# version of that file put the catch-all last, so the immutable rule on 1.18 GB
# of attachments was dead on a green suite. A test over the file's text cannot
# see this; only a response can. docs/HOSTING.md "Which header rule wins".
set -euo pipefail

site=${1:-${VTK_ARCHIVE_SITE:-}}
dist=${2:-dist}
if [[ -z $site ]]; then
    echo "usage: $0 <site-url> [dist-dir]" >&2
    exit 2
fi
site=${site%/}

# A real attachment, taken from the build rather than written down here, so a
# renamed upload prefix fails loudly instead of skipping the one rule that matters.
upload=$(find "$dist"/-/project/*/uploads -type f -print -quit 2>/dev/null || true)
if [[ -z $upload ]]; then
    echo "FAIL: no attachment found under $dist; this check would pass vacuously"
    exit 1
fi

fail=0

served() {
    curl -fsSI "$site$1" | tr -d '\r' | grep -i '^cache-control:' |
        cut -d: -f2- | tr -d ' ' || true
}

# `want` is a substring the header must carry, `reject` one it must not. The
# catch-all says max-age=3600 too, so only the absence of stale-while-revalidate
# tells an /assets/* rule that works from one that never ran.
expect() {
    local path=$1 want=$2 reject=${3:-} got
    got=$(served "$path")
    if [[ $got == *"$want"* ]] && { [[ -z $reject ]] || [[ $got != *"$reject"* ]]; }; then
        printf 'ok      %-46s %s\n' "$path" "$got"
    else
        printf 'FAIL    %-46s %s (wanted %s%s)\n' \
            "$path" "${got:-none}" "$want" "${reject:+, without $reject}"
        fail=1
    fi
}

expect "${upload#"$dist"}" immutable
expect /assets/style.css max-age=3600 stale-while-revalidate
expect /pagefind/pagefind.js max-age=86400
expect / stale-while-revalidate

if [[ $fail -ne 0 ]]; then
    echo
    echo "A rule in dist/_headers is not reaching the wire. Last match wins:"
    echo "the catch-all goes FIRST and every narrower rule after it."
    exit 1
fi
echo
echo "every header rule reaches the wire"
