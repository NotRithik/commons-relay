import QtQuick

// Preserve the identity and keyboard focus of rows while live data changes.
ListModel {
    id: rows
    property var items: []
    property var keyFields: ["id"]
    onItemsChanged: synchronize()
    onKeyFieldsChanged: synchronize()
    Component.onCompleted: synchronize()
    function synchronize() {
        const values = items || []
        const seen = ({})
        let destination = 0
        for (const value of values) {
            const key = keyFields.map(function(field) { return String(value[field] || "") }).join("|")
            if (!key || seen[key]) continue
            seen[key] = true
            let found = -1
            for (let index = destination; index < count; index++) {
                if (get(index).rowKey === key) { found = index; break }
            }
            const encoded = JSON.stringify(value)
            if (found < 0) insert(destination, {rowKey: key, payloadJson: encoded})
            else {
                if (found !== destination) move(found, destination, 1)
                if (get(destination).payloadJson !== encoded) setProperty(destination, "payloadJson", encoded)
            }
            destination++
        }
        if (count > destination) remove(destination, count - destination)
    }
}
