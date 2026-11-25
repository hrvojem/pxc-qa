"""Property-based tests (Hypothesis) for writing options into config files
(pxc_startup.append_lines, used by StartCluster.add_conf). They need no server.

Run from the repository root (config.py reads ./config.ini):
    python3 test/test_conf_files.py
"""
import os
import tempfile
import unittest

import pbt_support  # noqa: F401  (sys.path, mysql stub, Hypothesis profile)
from hypothesis import given, strategies as st

from util import pxc_startup  # noqa: E402

lines = st.text(st.characters(blacklist_characters='\r\n', blacklist_categories=('Cs',)), max_size=20)


class TestAppendLines(unittest.TestCase):

    @given(st.lists(lines, max_size=5), st.booleans(), st.lists(lines, min_size=1, max_size=5))
    def test_appended_lines_start_on_a_new_line_after_the_existing_content(self, existing, trailing_newline,
                                                                           added):
        content = '\n'.join(existing) + ('\n' if trailing_newline and existing else '')
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'custom.cnf')
            with open(path, 'w', newline='\n') as handle:
                handle.write(content)
            pxc_startup.append_lines(path, added)
            with open(path, newline='\n') as handle:
                result = handle.read()
        separator = '' if content == '' or content.endswith('\n') else '\n'
        self.assertEqual(result, content + separator + ''.join(line + '\n' for line in added))

    def test_default_custom_cnf_ends_with_a_newline(self):
        with open(pxc_startup.default_custom_cnf) as handle:
            self.assertTrue(handle.read().endswith('\n'))


if __name__ == '__main__':
    unittest.main()
