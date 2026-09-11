"""Ensure UI local calls can pass the real native bridge allowlist."""
import re
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class NativeUiDispatchTests(unittest.TestCase):
    def test_every_literal_ui_dispatch_is_allowlisted_by_native_core(self):
        ui=(ROOT/'native/ui/src/commons_relay_ui_backend.cpp').read_text()
        core=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        match=re.search(r'const QSet<QString> methods=\{([^}]+)\}',core)
        self.assertIsNotNone(match)
        allowed=set(re.findall(r'"([a-z_.]+)"',match[1]))
        methods=set(re.findall(r'dispatch\(command\("([a-z_.]+)"',ui))
        self.assertIn('owner.inbox_cursor',methods)
        self.assertEqual(methods-allowed,set())
    def test_background_reads_do_not_freeze_the_composer(self):
        ui=(ROOT/'native/ui/src/commons_relay_ui_backend.cpp').read_text()
        match=re.search(r'static const QSet<QString> foreground = \{([^}]+)\}',ui,re.S)
        self.assertIsNotNone(match)
        foreground=set(re.findall(r'"([a-z_]+)"',match[1]))
        self.assertTrue({'planner_start','planner_permission','planner_configure','submit','approve','cancel'} <= foreground)
        self.assertTrue({'heartbeat','snapshot','skills','skill','task','planner_status','planner_history','planner_goal'}.isdisjoint(foreground))
        self.assertIn('"owner-send-bg"',ui)
        self.assertIn('Background snapshots, skill reads and heartbeats must never freeze chat.',ui)
        self.assertNotIn('Authenticated agent status received. Tasks and spending policy are live.',ui)

    def test_sidepanel_uses_single_authorization_controls(self):
        qml=(ROOT/'native/ui/src/qml/Main.qml').read_text()
        self.assertEqual(qml.count('id: allowChatActions;'),1)
        self.assertEqual(qml.count('id: chatSpend;'),1)
        self.assertIn('readonly property bool dockSettings: width >= 1080',qml)
        self.assertIn('visible: root.dockSettings || root.settingsOpen',qml)
        self.assertIn('objectName: "commons_relay.settingsPanel"',qml)
