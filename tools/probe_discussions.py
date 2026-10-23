"""Does /discussions already carry full note bodies? If so the crawl halves."""

from crawler.rest import Rest

with Rest(delay=0.1) as api:
    for target in ("merge_requests/13000", "issues/20218"):
        disc = api.all_rows(f"{target}/discussions")
        notes = api.all_rows(f"{target}/notes")
        inside = [n for d in disc for n in d["notes"]]
        ids_d = {n["id"] for n in inside}
        ids_n = {n["id"] for n in notes}
        print(f"== {target}")
        print(f"   discussions={len(disc)} notes_inside={len(inside)} notes_endpoint={len(notes)}")
        print(f"   same ids: {ids_d == ids_n}  only_in_notes={len(ids_n - ids_d)}")
        if inside:
            sample = inside[0]
            print(f"   body={bool(sample.get('body'))} position={'position' in sample}")
            print(f"   keys={sorted(sample.keys())}")
        print(f"   discussion keys={sorted(disc[0].keys()) if disc else None}")
