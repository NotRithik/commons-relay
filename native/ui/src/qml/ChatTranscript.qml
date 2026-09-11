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
    property var describeTaskTitle: function(value) { return value || "Saved tool result" }
    property var describeTaskResult: function(value) { return value.result_summary || "" }
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
        return wanted.map(function(id) {
            return transcript.tasks.find(function(task) { return task.id === id }) || {id: id, state: "recorded", skill: ""}
        })
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

    StableRows { id: conversationRows; items: transcript.entries }
    ListView {
        id: messages
        anchors.fill: parent
        clip: true
        spacing: 22
        boundsBehavior: Flickable.StopAtBounds
        model: conversationRows
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        onMovementStarted: transcript.followTail = false
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
            required property string payloadJson
            readonly property var modelData: JSON.parse(payloadJson)
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
                        Label {
                            width: parent.width; visible: !!turn.modelData.error
                            text: transcript.describeError(turn.modelData.error || "")
                            color: Theme.palette.error; wrapMode: Text.WordWrap; textFormat: Text.PlainText
                        }
                        Repeater {
                            model: turn.modelData.updates || []
                            delegate: Label {
                                required property var modelData
                                width: answer.width
                                text: modelData.text || ""
                                color: Theme.palette.textSecondary; font.pixelSize: 13
                                wrapMode: Text.WordWrap; textFormat: Text.PlainText
                                Accessible.name: "Agent update"
                            }
                        }
                        StableRows { id: linkedToolRows; items: transcript.linkedTasks(turn.modelData.task_ids) }
                        Repeater {
                            model: linkedToolRows
                            delegate: Button {
                                id: taskCard
                                required property string payloadJson
                                readonly property var modelData: JSON.parse(payloadJson)
                                width: answer.width
                                padding: 12
                                hoverEnabled: true
                                focusPolicy: Qt.StrongFocus
                                Accessible.name: transcript.describeTaskTitle(modelData.skill) + ": " + transcript.describeTaskState(modelData.state)
                                Accessible.description: modelData.state === "input-required" ? "Review this exact action before it runs" : "Open the saved result; does not run or pay again"
                                onClicked: transcript.taskRequested(modelData.id)
                                background: Rectangle {
                                    radius: 9
                                    color: taskCard.hovered ? Theme.palette.surfaceRaised : Theme.palette.backgroundSecondary
                                    border.width: taskCard.activeFocus ? 2 : 1
                                    border.color: taskCard.activeFocus ? Theme.palette.primary : Theme.palette.border
                                }
                                contentItem: Column {
                                    spacing: 5
                                    Label {
                                        width: parent.width
                                        text: transcript.describeTaskTitle(taskCard.modelData.skill) + "  ·  " + transcript.describeTaskState(taskCard.modelData.state)
                                        color: ["failed","rejected"].indexOf(taskCard.modelData.state) >= 0 ? Theme.palette.error : Theme.palette.text
                                        font.pixelSize: 13; font.bold: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText
                                    }
                                    Label {
                                        width: parent.width
                                        text: {
                                            const task = taskCard.modelData
                                            if (task.state === "input-required") return "Review and approve this exact action" + (task.maximum_spend ? " · up to " + task.maximum_spend + " testnet units" : "") + "."
                                            const summary = transcript.describeTaskResult(task)
                                            if (summary) return summary
                                            if (task.progress) return transcript.describeProgress(task)
                                            if (task.state === "completed") return "Finished. Click to read the saved result."
                                            if (task.state === "recorded") return "Click to load this saved task. No new request or payment."
                                            if (task.state === "unknown") return "Checking the original task. Do not submit it again."
                                            if (task.state === "submitted") return "Queued and waiting to start."
                                            if (["failed","rejected","canceled"].indexOf(task.state) >= 0) return "Open the recorded outcome and reason."
                                            return "Work is in progress."
                                        }
                                        color: taskCard.modelData.state === "input-required" ? Theme.palette.warning : Theme.palette.textSecondary
                                        font.pixelSize: 13; wrapMode: Text.WordWrap; textFormat: Text.PlainText
                                    }
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
                        Flow {
                            width: parent.width; spacing: 8
                            LogosButton { visible: turn.modelData.preview === true; text: "Show full reply"; onClicked: transcript.fullReplyRequested(turn.modelData.id) }
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
