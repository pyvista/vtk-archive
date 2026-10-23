#!/usr/bin/env bash
# Resolve every `uses:` ref in .github/workflows against the GitHub API.
#
# actionlint validates syntax and does not ask whether the action exists, so
# `astral-sh/setup-uv@v10` passed every local check and failed every real run
# with "unable to find version". See docs/MAINTENANCE.md "Pinning the actions".
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
fail=0
seen=""

refs=$(grep -rhoE 'uses:[[:space:]]*[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[A-Za-z0-9._-]+' \
    "$here/.github/workflows" | sed -E 's/uses:[[:space:]]*//' | sort -u)

if [[ -z $refs ]]; then
    echo "FAIL: no action references found; this check would pass vacuously"
    exit 1
fi

for ref in $refs; do
    repo=${ref%@*}
    tag=${ref##*@}
    # A subdirectory action (owner/repo/path@ref) is versioned by its repo.
    owner=$(cut -d/ -f1 <<<"$repo")
    name=$(cut -d/ -f2 <<<"$repo")
    key="$owner/$name@$tag"
    case " $seen " in *" $key "*) continue ;; esac
    seen="$seen $key"

    if gh api "repos/$owner/$name/git/ref/tags/$tag" >/dev/null 2>&1 ||
        gh api "repos/$owner/$name/git/ref/heads/$tag" >/dev/null 2>&1 ||
        gh api "repos/$owner/$name/commits/$tag" >/dev/null 2>&1; then
        echo "ok      $key"
    else
        echo "MISSING $key"
        fail=1
    fi
done

if [[ $fail -ne 0 ]]; then
    echo
    echo "An action above does not resolve. Every run using it fails before its first step."
    exit 1
fi
echo
echo "all $(wc -w <<<"$seen") action references resolve"
