import importlib.util
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import patch


class TenDushiMarkdownLinks(unittest.TestCase):
    def test_generated_toc_uses_portable_heading_ids(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/extract_ten_dushi_from_pdf.py'
        spec = importlib.util.spec_from_file_location('ten_dushi_extractor_test', source)
        module = importlib.util.module_from_spec(spec)
        # PDF extraction is not involved in testing the Markdown formatter.
        with patch.dict(sys.modules, {'fitz': types.ModuleType('fitz')}):
            spec.loader.exec_module(module)
        cards = [{'title_en': 'The Gates of Shadows', 'title_ru': 'Врата Теней', 'section': 'Старшие арканы', 'body': 'Исходный текст карты.'}]
        text = module.build_markdown(cards, '', '', 'fixture.pdf')
        self.assertNotIn('{#', text)
        heading = next(s[5:] for s in text.splitlines() if s.startswith('#### '))
        anchor = module._slug(heading)
        self.assertEqual(heading, 'The Gates of Shadows — Врата Теней')
        self.assertIn(f'](#{anchor})', text)
        self.assertIn('Исходный текст карты.', text)
        self.assertEqual(len(re.findall(r'^#### ', text, re.M)), 1)


if __name__ == '__main__':unittest.main()
