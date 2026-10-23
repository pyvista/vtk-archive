"""Confirm diff-anchored review notes keep their file/line position via /discussions."""

from crawler.rest import Rest

with Rest(delay=0.1) as api:
    found = 0
    for iid in (13000, 13500, 13729, 12000, 11000):
        disc = api.all_rows(f"merge_requests/{iid}/discussions")
        for d in disc:
            for n in d["notes"]:
                pos = n.get("position")
                if pos:
                    found += 1
                    if found <= 3:
                        print(f"MR {iid} discussion {d['id'][:8]} resolvable={d['resolvable']}")
                        print(f"  new_path={pos.get('new_path')} new_line={pos.get('new_line')}")
                        print(f"  old_line={pos.get('old_line')} type={pos.get('position_type')}")
                        print(f"  body={n['body'][:70]!r}")
        if found:
            break
    print(f"\ndiff-anchored notes found: {found}")
    if not found:
        print("WARNING: no position on any note - diff anchoring would be lost")
