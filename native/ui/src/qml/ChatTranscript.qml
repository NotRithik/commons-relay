import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Logos.Theme
import Logos.Controls

Item {
    id: transcript
    property var entries: []
    property var tasks: []
    property string agentLabel: "Agent"
    property bool hasEarlier: false
    property bool loading: false
    property bool followTail: true
    property string restoreId: ""
    property var describeState: function(value) { return value }
    property var describeTaskState: function(value) { return value }
    property var describeProgress: function(value) { return value && value.progress ? value.progress.detail : "" }
    property var formatReply: function(value) { return value }
    property var describeError: function(value) { return value }
    property var describeWait: function(value) { return "" }
    property int nowSeconds: 0
    signal earlierRequested()
    signal fullReplyRequested(string goalId)
    signal taskRequested(string taskId)
    signal permissionRequested(string goalId)
    signal exampleRequested(string text)

    function timeLabel(value) {
        const date = new Date(Number(value || 0) * 1000)
        return value ? Qt.formatDateTime(date, "ddd d MMM, hh:mm") : ""
    }
    function linkedTasks(ids) {
        const wanted = ids || []
        return transcript.tasks.filter(function(task) { return wanted.indexOf(task.id) >= 0 })
    }
    function goToLatest() {
        followTail = true
        restoreId = ""
        Qt.callLater(function() { messages.positionViewAtEnd() })
    }
    function earlier() {
        const index = messages.indexAt(1, messages.contentY + 2)
        restoreId = index >= 0 && entries[index] ? entries[index].id : ""
        followTail = false
        earlierRequested()
    }
    onEntriesChanged: Qt.callLater(function() {
        if (transcript.restoreId) {
            const index = transcript.entries.findIndex(function(row) { return row.id === transcript.restoreId })
            if (index >= 0) messages.positionViewAtIndex(index, ListView.Beginning)
            transcript.restoreId = ""
        } else if (transcript.followTail) messages.positionViewAtEnd()
    })

    ListView {
        id: messages
        anchors.fill: parent
        clip: true
        spacing: 22
        boundsBehavior: Flickable.StopAtBounds
        model: transcript.entries
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        onMovementEnded: transcript.followTail = atYEnd
        header: Item {
            width: messages.width
            height: transcript.hasEarlier ? 54 : 12
            LogosButton {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: transcript.hasEarlier
                text: transcript.loading ? "Loading earlier messages..." : "Load earlier messages"
                Accessible.name: "Load earlier chat messages"
                enabled: !transcript.loading
                onClicked: transcript.earlier()
            }
        }
        footer: Item { width: messages.width; height: 18 }
        delegate: Item {
            id: turn
            required property var modelData
            width: messages.width
            implicitHeight: bubbles.implicitHeight
            Column {
                id: bubbles
                width: Math.min(parent.width - 28, 920)
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 12
                Rectangle {
                    width: Math.min(bubbles.width * 0.85, 760)
                    anchors.right: parent.right
                    implicitHeight: userMessage.implicitHeight + 24
                    color: Theme.palette.backgroundElevated
                    radius: 14
                    Column {
                        id: userMessage
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 5
                        Label { text: "You  ·  " + transcript.timeLabel(turn.modelData.created); color: Theme.palette.textSecondary; font.pixelSize: 11; textFormat: Text.PlainText }
                        TextEdit {
                            width: parent.width
                            text: turn.modelData.prompt || ""
                            textFormat: TextEdit.PlainText
                            readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                            color: Theme.palette.text; font.pixelSize: 15
                            font.family: Theme.typography.publicSans
                            Accessible.name: "Your message"
                            Accessible.description: text
                        }
                    }
                }
                Rectangle {
                    width: bubbles.width
                    implicitHeight: answer.implicitHeight + 28
                    color: Theme.palette.surfaceRaised
                    radius: 14
                    Column {
                        id: answer
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10
                        Label {
                            width: parent.width
                            text: transcript.agentLabel + "  ·  " + transcript.describeState(turn.modelData.state)
                            color: turn.modelData.state === "failed" ? Theme.palette.error : Theme.palette.textSecondary
                            font.pixelSize: 12; font.bold: true; textFormat: Text.PlainText
                        }
                        TextEdit {
                            width: parent.width
                            text: {
                                const _tick = transcript.nowSeconds
                                return transcript.formatReply(turn.modelData.reply)
                                    || transcript.describeWait(turn.modelData)
                                    || (turn.modelData.state === "queued" ? "Your message is signed and waiting to start." : turn.modelData.state === "thinking" ? "Waiting on the model. No tools have been used yet. Press Stop to cancel." : turn.modelData.state === "working" ? "The model asked for a tool. Live progress is attached below." : turn.modelData.permission ? "I need your permission before continuing." : "No reply text was received.")
                            }
                            textFormat: TextEdit.PlainText
                            readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                            color: Theme.palette.text; font.pixelSize: 15
                            font.family: Theme.typography.publicSans
                            Accessible.name: "Agent reply"
                            Accessible.description: text
                        }
                        Label {
                            width: parent.width; visible: !!turn.modelData.error
                            text: transcript.describeError(turn.modelData.error || "")
                            color: Theme.palette.error; wrapMode: Text.WordWrap; textFormat: Text.PlainText
                        }
                        Repeater {
                            model: transcript.linkedTasks(turn.modelData.task_ids)
                            delegate: Rectangle {
                                required property var modelData
                                width: answer.width
                                implicitHeight: taskProgressText.implicitHeight + 20
                                radius: 9
                                color: Theme.palette.backgroundSecondary
                                border.color: Theme.palette.border
                                Column {
                                    id: taskProgressText
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                                    spacing: 4
                                    Label { width: parent.width; text: "Tool · " + transcript.describeTaskState(modelData.state); color: modelData.state === "failed" || modelData.state === "rejected" ? Theme.palette.error : Theme.palette.textSecondary; font.pixelSize: 12; font.bold: true; textFormat: Text.PlainText }
                                    Label { width: parent.width; visible: !!modelData.progress && ["submitted","working","unknown","input-required"].indexOf(modelData.state) >= 0; text: transcript.describeProgress(modelData); color: Theme.palette.text; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
                                    Label { width: parent.width; visible: !(modelData.progress && ["submitted","working","unknown","input-required"].indexOf(modelData.state) >= 0) && modelData.state !== "completed"; text: modelData.state === "input-required" ? "Waiting for your approval." : modelData.state === "unknown" ? "Checking what happened on the network. Do not send it again." : modelData.state === "submitted" ? "Waiting to start." : modelData.state === "failed" ? (modelData.result_summary || "This tool failed. Open its details for the recorded reason.") : modelData.state === "rejected" ? "This action did not start." : modelData.state === "canceled" ? "This action was canceled." : "Work is in progress."; color: (modelData.state === "failed" || modelData.state === "rejected") ? Theme.palette.error : Theme.palette.textSecondary; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
                                }
                            }
                        }
                        Rectangle {
                            width: parent.width
                            visible: !!turn.modelData.permission
                            implicitHeight: permissionText.implicitHeight + 24
                            radius: 9; color: Theme.palette.backgroundSecondary; border.color: Theme.palette.border
                            Column {
                                id: permissionText
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                                spacing: 8
                                Label { text: (turn.modelData.permission || {}).decision === "approve" ? "Action approved" : (turn.modelData.permission || {}).decision === "decline" ? "Action declined" : "Permission needed"; color: Theme.palette.warning; font.bold: true }
                                Label { width: parent.width; text: ((turn.modelData.permission || {}).request || {}).reason || "Review the requested action before it runs."; wrapMode: Text.WordWrap; color: Theme.palette.text; textFormat: Text.PlainText }
                                LogosButton { visible: (turn.modelData.permission || {}).decision === "pending"; text: "Review requested action"; Accessible.name: "Review action requested by agent"; enabled: !transcript.loading; onClicked: transcript.permissionRequested(turn.modelData.id) }
                            }
                        }
                        Flow {
                            width: parent.width; spacing: 8
                            LogosButton { visible: turn.modelData.preview === true; text: "Show full reply"; onClicked: transcript.fullReplyRequested(turn.modelData.id) }
                            Repeater {
                                model: turn.modelData.task_ids || []
                                delegate: LogosButton {
                                    required property string modelData
                                    text: "View details"
                                    Accessible.name: "View details"
                                    onClicked: transcript.taskRequested(modelData)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Column {
        visible: transcript.entries.length === 0
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 560)
        spacing: 14
        Label { width: parent.width; text: "How can I help?"; horizontalAlignment: Text.AlignHCenter; color: Theme.palette.text; font.pixelSize: 27; font.bold: true }
        Label { width: parent.width; text: "Ask a question or choose a starting point."; horizontalAlignment: Text.AlignHCenter; color: Theme.palette.textSecondary; wrapMode: Text.WordWrap }
        Flow {
            width: parent.width; spacing: 8
            LogosButton { text: "What can you do?"; onClicked: transcript.exampleRequested("What can you help me with? Explain it simply.") }
            LogosButton { text: "List stored files"; onClicked: transcript.exampleRequested("List the files this agent has stored. Do not change or share anything.") }
            LogosButton { text: "Explain approvals"; onClicked: transcript.exampleRequested("Explain which actions need my approval and what my current limits are.") }
        }
    }
    LogosButton {
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 12
        visible: transcript.entries.length > 1 && !messages.atYEnd
        text: "Latest message ↓"
        Accessible.name: "Scroll to latest agent reply"
        onClicked: transcript.goToLatest()
    }
}
