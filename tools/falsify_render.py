"""Show the input that makes the code-block protection tests red.

Neutralise `_PROTECTED` so reference rewriting runs over the whole document —
the naive implementation these tests exist to reject — then run the protected
region tests in THIS process, where the patch applies. Spawning pytest as a
subprocess would not inherit it and would pass vacuously.
"""

from __future__ import annotations

import re
import sys

from builder import render
from tests import test_render as suite

PROTECTION_TESTS = [
    suite.test_fenced_code_block_is_untouched,
    suite.test_inline_code_is_untouched,
    suite.test_indented_code_block_is_untouched,
    suite.test_existing_link_is_not_rewritten_inside,
    suite.test_href_attribute_of_a_bare_tag_is_not_rewritten,
]


def run(label: str) -> list[str]:
    md = render.build_parser()
    failed = []
    for test in PROTECTION_TESTS:
        try:
            test(md) if test.__code__.co_argcount else test()
        except AssertionError:
            failed.append(test.__name__)
    print(f"{label}: {len(failed)} of {len(PROTECTION_TESTS)} failed {failed}")
    return failed


original = render._PROTECTED
if run("protection intact") != []:
    print("\nFAILED: the suite does not pass against the real implementation.")
    sys.exit(1)

render._PROTECTED = re.compile(r"(?!x)x")  # matches nothing
broken = run("protection disabled")
render._PROTECTED = original

if not broken:
    print("\nFALSIFICATION FAILED: these tests pass with protection removed. They are vacuous.")
    sys.exit(1)

print(f"\nOK: removing protection reds {len(broken)} tests. They test the thing they claim.")
