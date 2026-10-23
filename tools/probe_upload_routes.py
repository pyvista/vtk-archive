"""Find a route for project uploads that is not Anubis-gated.

/-/project/13/uploads/... is an HTML route, so it takes the User-Agent
allowlist and 709 of 1867 fetches came back 403. GitLab also exposes uploads
under /api/v4, which is not UA-gated and accepts PRIVATE-TOKEN.
"""

from __future__ import annotations

import json

import httpx

from crawler.rest import USER_AGENT, load_token

SAMPLE = "00091928749c76e5132b5c6456c698e3/OBJReader.zip"
TOKEN = load_token()

ROUTES = {
    "html /-/project/13/uploads": f"https://gitlab.kitware.com/-/project/13/uploads/{SAMPLE}",
    "api /projects/13/uploads": f"https://gitlab.kitware.com/api/v4/projects/13/uploads/{SAMPLE}",
    "html /vtk/vtk/-/uploads": f"https://gitlab.kitware.com/vtk/vtk/-/uploads/{SAMPLE}",
}

VARIANTS = {
    "honest UA, no token": {"User-Agent": USER_AGENT},
    "honest UA + token": {"User-Agent": USER_AGENT, "PRIVATE-TOKEN": TOKEN or ""},
}

for route, url in ROUTES.items():
    for label, headers in VARIANTS.items():
        if "token" in label and not TOKEN:
            continue
        try:
            with httpx.Client(timeout=60, headers=headers, follow_redirects=True) as c:
                r = c.get(url)
            note = ""
            if r.status_code == 403 and "Verifying" in r.text[:300]:
                note = " (anubis)"
            elif r.status_code == 200:
                note = f" ({len(r.content)} bytes, {r.headers.get('content-type')})"
            print(f"  {route:34} {label:22} {r.status_code}{note}")
        except Exception as exc:  # noqa: BLE001 — a probe reports
            print(f"  {route:34} {label:22} {type(exc).__name__}")

print()
print("uploads API endpoint docs shape:")
print(json.dumps({"GET": "/projects/:id/uploads/:secret/:filename"}, indent=1))
