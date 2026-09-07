import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Logos.Theme
import Logos.Controls
Item {
    id: root
    objectName: "commons_relay.root"
    readonly property var backend: logos.module("commons_relay_owner_ui")
    readonly property bool ready: backend && backend.ready
    readonly property bool connected: ready && backend.connected
    property bool showSettings: !connected
    property bool showTechnical: false
    property bool showSignedCommand: false
    readonly property var summary: parse(backend ? backend.summaryJson : "{}", {})
    readonly property var tasks: parse(backend ? backend.tasksJson : "[]", [])
    readonly property var skills: parse(backend ? backend.skillsJson : "[]", [])
    function parse(value, fallback) { try { return JSON.parse(value) } catch (_) { return fallback } }
    function call(reply) { if (logos && logos.watch) logos.watch(reply, function(){}, function(error){}) }
    component Card: Pane {
        padding: Theme.spacing.xlarge
        background: Rectangle { color: Theme.palette.surfaceRaised; radius: Theme.spacing.radiusXlarge }
    }
    component Caption: LogosText { font.pixelSize: Theme.typography.secondaryText; color: Theme.palette.textTertiary }
    component Field: TextField {
        font.family: Theme.typography.publicSans
        font.pixelSize: Theme.typography.primaryText
        color: Theme.palette.text
        placeholderTextColor: Theme.palette.textTertiary
        implicitHeight: 40
        selectByMouse: true
        background: Rectangle { color: Theme.palette.backgroundSecondary; radius: Theme.spacing.radiusSmall; border.color: Theme.palette.backgroundElevated }
    }
    Rectangle { anchors.fill: parent; color: Theme.palette.background }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: Theme.spacing.xxlarge; spacing: Theme.spacing.xlarge
        RowLayout {
            ColumnLayout {
                LogosText { text: "Commons Relay"; font.pixelSize: Theme.typography.pageTitleText; font.weight: Theme.typography.weightMedium }
                Caption { text: "Agent goals, permissions and durable tasks" }
            }
            Item { Layout.fillWidth: true }
            LogosText { text: "TESTNET"; color: Theme.palette.warning; font.pixelSize: Theme.typography.secondaryText }
            LogosButton { text: "Refresh"; Accessible.name: "Refresh Commons Relay runtime"; enabled: root.ready; onClicked: root.call(root.backend.refresh()) }
        }
        Card {
            Layout.fillWidth: true
            contentItem: ColumnLayout {
                RowLayout {
                    Rectangle { implicitWidth: 7; implicitHeight: 7; radius: 4; color: root.connected ? Theme.palette.success : Theme.palette.warning }
                    LogosText { text: root.connected ? root.backend.agentId : "Runtime not configured" }
                    Item { Layout.fillWidth: true }
                    Caption { text: "Inference off" }
                    LogosButton { text: "Settings"; implicitHeight: 30; onClicked: root.showSettings = !root.showSettings }
                }
                RowLayout {
                    visible: root.showSettings
                    Field { id: profile; objectName: "commons_relay.profile"; Accessible.name: "Commons Relay agent profile"; placeholderText: "Agent profile directory (public owner key only)"; Layout.fillWidth: true }
                    LogosButton { text: "Connect"; variant: LogosButton.Variant.Primary; Accessible.name: "Connect CommonsRelay profile"; enabled: root.ready; onClicked: root.call(root.backend.configure(profile.text)) }
                }
            }
        }
        LogosTabBar {
            id: tabs; Layout.fillWidth: true
            LogosTabButton { text: "Tasks"; Accessible.name: "CommonsRelay tasks tab"; width: 110 }
            LogosTabButton { text: "Skills"; Accessible.name: "CommonsRelay skills tab"; width: 110 }
            LogosTabButton { text: "Architecture"; Accessible.name: "CommonsRelay architecture tab"; width: 150 }
        }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width; spacing: Theme.spacing.large
                Card {
                    visible: tabs.currentIndex === 0; Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        LogosText { text: "Task ledger"; font.pixelSize: Theme.typography.panelTitleText }
                        Caption { text: "Spending and approvals are enforced outside the language-model loop."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        LogosText { text: root.tasks.length ? root.tasks.length + " recent tasks" : "No tasks yet"; color: Theme.palette.textSecondary }
                        Repeater {
                            model: root.tasks
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true; implicitHeight: 72; radius: Theme.spacing.radiusLarge; color: Theme.palette.backgroundSecondary
                                ColumnLayout { anchors.fill: parent; anchors.margins: Theme.spacing.medium
                                    LogosText { text: modelData.skill + "  /  " + modelData.state }
                                    Caption { text: "Maximum spend: " + modelData.maximum_spend + " base units. " + (modelData.error || "") }
                                }
                            }
                        }
                        LogosButton { text: root.showSignedCommand ? "Hide signed command" : "Submit signed command"; enabled: root.connected; onClicked: root.showSignedCommand = !root.showSignedCommand }
                        ColumnLayout {
                            visible: root.showSignedCommand; Layout.fillWidth: true
                            Caption { text: "Signed envelopes only. A prompt cannot approve spending or replace the owner."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            TextArea { id: signedCommand; objectName: "commons_relay.command"; Accessible.name: "Signed CommonsRelay command JSON"; Layout.fillWidth: true; Layout.preferredHeight: 130; color: Theme.palette.text; font.family: Theme.typography.mono; font.pixelSize: Theme.typography.secondaryText; placeholderText: "Paste the signed command produced by your owner client"; wrapMode: TextEdit.Wrap; background: Rectangle { color: Theme.palette.backgroundSecondary; radius: Theme.spacing.radiusSmall } }
                            LogosButton { text: "Verify and submit"; Accessible.name: "Verify signed CommonsRelay command"; onClicked: root.call(root.backend.sendSignedCommand(signedCommand.text)) }
                        }
                    }
                }
                Card {
                    visible: tabs.currentIndex === 1; Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        LogosText { text: "Skill registry"; font.pixelSize: Theme.typography.panelTitleText }
                        Caption { text: "Registered interfaces are separate from live adapters. Storage, messaging and wallet connections are still being integrated."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Repeater {
                            model: root.skills
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                LogosText { text: modelData.id; Layout.preferredWidth: 195; color: Theme.palette.primary }
                                Caption { text: modelData.description; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            }
                        }
                    }
                }
                Card {
                    visible: tabs.currentIndex === 2; Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: Theme.spacing.large
                        LogosText { text: "A swappable planner, a fixed permission boundary"; font.pixelSize: Theme.typography.panelTitleText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        LogosText { text: "1. Owner authorizes a goal, allowed skills, step limit and spending ceiling.\n2. A planner proposes tools. Pi or another model runtime can supply that layer.\n3. The task engine validates every proposal and reserves its budget atomically.\n4. Trusted adapters perform operations through Logos modules.\n5. Results, approvals and uncertain transactions survive restarts."; wrapMode: Text.WordWrap; Layout.fillWidth: true; lineHeight: 1.5 }
                        Caption { text: "The planner never receives the owner's signing key. Above-threshold spending waits for a signed approval. An ambiguous network result is reconciled before retrying. No general shell tool is exposed."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        LogosText { text: "Current build: durable core and native IPC. Live protocol adapters and multi-agent acceptance tests are still in progress."; color: Theme.palette.warning; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
            }
        }
        RowLayout {
            LogosText { text: root.backend ? root.backend.statusText : "Loading..."; objectName: "commons_relay.status"; Accessible.role: Accessible.StaticText; Accessible.name: text; font.pixelSize: Theme.typography.secondaryText; color: root.backend && root.backend.lastError ? Theme.palette.error : Theme.palette.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            LogosButton { text: root.showTechnical ? "Hide details" : "Technical details"; implicitHeight: 30; onClicked: root.showTechnical = !root.showTechnical }
        }
        ScrollView {
            visible: root.showTechnical; Layout.fillWidth: true; Layout.preferredHeight: 150
            TextArea { text: root.backend ? root.backend.lastResultJson : "{}"; readOnly: true; color: Theme.palette.textSecondary; font.family: Theme.typography.mono; font.pixelSize: Theme.typography.secondaryText; wrapMode: TextEdit.Wrap; background: Rectangle { color: Theme.palette.backgroundSecondary } }
        }
    }
}
