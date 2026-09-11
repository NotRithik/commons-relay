import QtQuick
import QtQuick.Controls
import Logos.Theme

// Read-only recorded data, sized by its actual wrapped text. The explicit
// content height prevents nested ScrollViews from scrolling beyond the text.
Rectangle {
    id: panel
    property alias text: output.text
    property string accessibleName: "Saved response"
    property bool monospace: false
    radius: 6
    color: Theme.palette.backgroundSecondary
    clip: true
    Flickable {
        id: viewport
        anchors.fill: parent
        anchors.margins: 10
        contentWidth: width
        contentHeight: output.height
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        clip: true
        TextEdit {
            id: output
            width: Math.max(1, viewport.width - 16)
            height: Math.max(1, contentHeight)
            textFormat: TextEdit.PlainText
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            color: Theme.palette.text
            font.family: panel.monospace ? "monospace" : Theme.typography.publicSans
            font.pixelSize: panel.monospace ? 12 : 14
            Accessible.name: panel.accessibleName
            Accessible.description: text.slice(0, 500)
            onTextChanged: viewport.contentY = 0
        }
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AlwaysOn
            Accessible.name: panel.accessibleName + " scrollbar"
        }
    }
}
