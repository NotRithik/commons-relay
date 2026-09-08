"""Cross-layer regression checks. No network, wallet, or model calls."""
from pathlib import Path
from collections import Counter
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / 'native/ui/src/qml/Main.qml'
REP = ROOT / 'native/ui/src/commons_relay_owner_ui.rep'

class RecoveryUiContractTests(unittest.TestCase):
    def test_no_duplicate_root_functions(self):
        names = re.findall(r'^    function\s+(\w+)\s*\(', QML.read_text(), re.M)
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        self.assertEqual(duplicates, [], 'Duplicate QML functions can mask a broken screen')

    def test_all_explicit_root_references_are_declared(self):
        source = QML.read_text()
        declared = set(re.findall(r'(?:readonly\s+)?property\s+\w+\s+(\w+)\s*:', source))
        declared.update(re.findall(r'function\s+(\w+)\s*\(', source))
        declared.update({'width', 'height', 'x', 'y', 'parent', 'visible', 'enabled',
                         'opacity', 'children', 'focus', 'activeFocus', 'forceActiveFocus',
                         'grabToImage', 'mapToItem', 'mapFromItem', 'anchors', 'objectName'})
        referenced = set(re.findall(r'\broot\.(\w+)', source))
        self.assertEqual(sorted(referenced - declared), [])

    def test_qml_uses_the_published_native_contract(self):
        source, contract = QML.read_text(), REP.read_text()
        slots = set(re.findall(r'SLOT\(\w+\s+(\w+)\s*\(', contract))
        properties = set(re.findall(r'PROP\(\w+\s+(\w+)\s*=', contract))
        used = set(re.findall(r'\bbackend\.(\w+)', source))
        self.assertEqual(sorted(used - slots - properties), [])

    def test_private_material_is_not_a_user_interface_property(self):
        contract = REP.read_text()
        names = re.findall(r'PROP\(\w+\s+(\w+)\s*=', contract)
        forbidden = ('privatekey', 'mnemonic', 'apikey', 'seedphrase', 'ownerpem')
        self.assertEqual([name for name in names if any(word in name.lower() for word in forbidden)], [])

    def test_shared_theme_and_plain_text_remain_in_use(self):
        source = QML.read_text()
        self.assertIn('import Logos.Theme', source)
        self.assertIn('import Logos.Controls', source)
        self.assertIn('Text.PlainText', source)
        self.assertIn('TextEdit.PlainText', source)
        self.assertNotRegex(source, r'#[0-9a-fA-F]{6}\b')

if __name__ == '__main__':
    unittest.main()
