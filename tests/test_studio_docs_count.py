"""The tool counts quoted in docs/STUDIO*.md are the real size of the cold catalogue."""
import re
from pathlib import Path

from vulcan.studio.catalog import tool_catalog

DOCS = Path(__file__).resolve().parents[1] / 'docs'


def test_studio_docs_quote_the_real_cold_catalogue_size():
    real = len(tool_catalog())
    for name in ('STUDIO.md', 'STUDIO-STATUS.md'):
        text = (DOCS / name).read_text(encoding='utf-8')
        quoted = re.findall(r'(?:exposes|contains) (\d+) tools', text)
        assert quoted, name
        assert all(int(n) == real for n in quoted), (name, quoted, real)