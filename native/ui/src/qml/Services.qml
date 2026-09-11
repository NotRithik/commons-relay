import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Logos.Theme
import Logos.Controls

ColumnLayout {
    id: panel
    property var backend: null
    property Item dialogHost: null
    signal taskSent()
    signal dispatched(var reply)
    readonly property bool ready: !!(backend && backend.remoteReady)
    readonly property bool busy: !!(backend && (backend.requestPending || backend.signing))
    readonly property var directory: decode(backend ? backend.serviceDirectoryJson : "{}", {})
    readonly property var provider: decode(backend ? backend.providerSettingsJson : "{}", {})
    readonly property var entries: {
        const output = []
        for (const provider of (directory.services || []))
            for (const skill of (provider.skills || []))
                output.push({address: provider.address, name: provider.name, description: skill.description,
                    skill: skill.id, price: skill.price, input_schema: skill.input_schema, payment_modes: skill.payment_modes || ["private"],
                    trust: provider.trust, expires: provider.expires})
        return output
    }
    StableRows { id: serviceRows; items: panel.entries; keyFields: ["address", "skill"] }
    property bool offering: false
    property bool searched: false
    property int refreshes: 0
    property string error: ""
    property var selected: null
    property var inputs: ({})
    property string paymentMode: "private"
    property var draft: ({name: "", description: "", discovery_topic: "commons", "public": false, exports: {}})
    property string settingsHash: ""
    property bool dirty: false
    property bool saving: false
    property string removedPrivate: ""
    property var reviewed: null
    spacing: 20

    function decode(text, fallback) { try { return JSON.parse(text) } catch (_) { return fallback } }
    function clone(value) { return JSON.parse(JSON.stringify(value)) }
    function price(value) { return value === "0" ? "Free" : value + (value === "1" ? " testnet unit" : " testnet units") }
    function shortAddress(value) { return value ? value.slice(0, 16) + "..." + value.slice(-8) : "" }
    function load() {
        if (!ready) return
        backend.loadProviderSettings()
        findServices()
    }
    function findServices() {
        if (!ready || !topic.text.trim()) return
        error = ""; selected = null; searched = true; refreshes = 6
        backend.loadServiceDirectory(topic.text.trim(), 0, true)
    }
    function choose(entry) {
        selected = clone(entry); inputs = ({}); paymentMode = "private"; error = ""
        const fields = (entry.input_schema || {}).properties || {}
        const values = {}
        Object.keys(fields).forEach(function(key) {
            const spec = fields[key]
            values[key] = spec.type === "boolean" ? false : spec.enum ? spec.enum[0] : ""
        })
        inputs = values
    }
    function setInput(key, value) { const next = clone(inputs); next[key] = value; inputs = next }
    function argumentsForService() {
        const args = {}, schema = selected.input_schema || {}, fields = schema.properties || {}
        Object.keys(fields).forEach(function(key) {
            const spec = fields[key], raw = inputs[key]
            if (spec.type === "boolean") args[key] = !!raw
            else if (spec.type === "integer") {
                if (!/^-?(0|[1-9][0-9]*)$/.test(String(raw))) throw new Error("Enter a whole number for " + key + ".")
                const value = Number(raw)
                if (!Number.isSafeInteger(value) || (spec.minimum !== undefined && value < spec.minimum)
                    || (spec.maximum !== undefined && value > spec.maximum)) throw new Error("Check the allowed number for " + key + ".")
                args[key] = value
            } else if (spec.type === "object" || spec.type === "array" || !spec.type) {
                try { args[key] = JSON.parse(String(raw)) } catch (_) { throw new Error("Check the structured input for " + key + ".") }
            } else {
                args[key] = String(raw === undefined ? "" : raw)
                if ((spec.minLength !== undefined && args[key].length < spec.minLength)
                    || (spec.maxLength !== undefined && args[key].length > spec.maxLength)) throw new Error("Check the length of " + key + ".")
                if (spec.enum && spec.enum.indexOf(args[key]) < 0) throw new Error("Choose an allowed value for " + key + ".")
            }
        })
        if (JSON.stringify(args).length > 10000) throw new Error("This request is too large. Shorten the input before sending it.")
        return args
    }
    function reviewTask() {
        try {
            const args = argumentsForService()
            reviewed = {kind: "task", profile: backend.selectedProfile, service: clone(selected), arguments: args, paymentMode: paymentMode}
            error = ""; review.open()
        } catch (failure) { error = failure.message }
    }
    function edit(key, value) { const next = clone(draft); next[key] = value; draft = next; dirty = true }
    function exportSkill(id, enabled) {
        const next = clone(draft)
        if (enabled) {
            const item = (provider.available_services || []).filter(function(s) { return s.id === id })[0]
            next.exports[id] = {price: "0", description: item ? item.description : id}
        } else delete next.exports[id]
        draft = next; dirty = true
    }
    function setPrice(id, value) {
        if (!draft.exports[id]) return
        const next = clone(draft); next.exports[id].price = value; draft = next; dirty = true
    }
    function setPublic(value) {
        const next = clone(draft); next["public"] = value
        if (value) {
            const allowed = (provider.available_services || []).map(function(s) { return s.id })
            const removed = Object.keys(next.exports).filter(function(id) { return allowed.indexOf(id) < 0 })
            removed.forEach(function(id) { delete next.exports[id] })
            removedPrivate = removed.length ? "Private tools were left out of this public listing: " + removed.join(", ") + "." : ""
        }
        draft = next; dirty = true
    }
    function reviewSettings() {
        error = ""
        if (!draft.name.trim() || !draft.description.trim() || !draft.discovery_topic.trim()) {
            error = "Give your service a name, a description and a topic."; return
        }
        if (draft["public"] && Object.keys(draft.exports).length === 0) {
            error = "Choose at least one service to offer."; return
        }
        const invalid = Object.keys(draft.exports).some(function(id) { return !/^(0|[1-9][0-9]{0,38})$/.test(draft.exports[id].price) })
        if (invalid) { error = "Prices must be whole testnet base units. Use 0 for a free service."; return }
        reviewed = {kind: "settings", profile: backend.selectedProfile, settings: clone(draft), hash: settingsHash}
        review.open()
    }
    function confirm() {
        const value = reviewed
        if (!value || !ready || busy || value.profile !== backend.selectedProfile) { review.close(); error = "The selected agent changed. Review this again."; return }
        reviewed = null; review.close()
        if (value.kind === "task") {
            const reply = backend.submitServiceTaskWithMode(value.service.address, value.service.skill, JSON.stringify(value.arguments), value.service.price, value.paymentMode)
            dispatched(reply); selected = null; taskSent()
        } else {
            saving = true
            const reply = backend.saveProviderSettings(JSON.stringify(value.settings), value.hash)
            dispatched(reply)
            refreshes = 6
        }
    }
    onProviderChanged: {
        const value = provider
        if (value.settings && !(dirty && !saving)) {
            draft = clone(value.settings); settingsHash = value.settings_hash
            dirty = false; saving = false
        }
    }
    onVisibleChanged: if (visible && ready) load()
    Connections {
        target: panel.backend
        function onRemoteReadyChanged() { if (panel.visible && panel.ready) panel.load() }
        function onSelectedProfileChanged() {
            panel.selected = null; panel.reviewed = null; panel.dirty = false; panel.saving = false
            panel.settingsHash = ""; panel.refreshes = 0; panel.searched = false; panel.error = ""; review.close()
        }
        function onLastErrorChanged() {
            if (panel.backend.lastError && panel.saving) { panel.saving = false; panel.error = "Settings were not saved. Refresh the current settings and review your changes again." }
        }
    }
    Timer {
        interval: 2500; repeat: true
        running: panel.visible && panel.ready && panel.refreshes > 0
        onTriggered: {
            if (panel.busy) return
            panel.refreshes -= 1
            if (panel.offering) panel.backend.loadProviderSettings()
            else panel.backend.loadServiceDirectory(topic.text.trim(), 0, false)
        }
    }
    component Body: LogosText {
        color: Theme.palette.text; textFormat: Text.PlainText; wrapMode: Text.Wrap
        font.pixelSize: 14; Layout.fillWidth: true
    }
    component Note: Body { color: Theme.palette.textSecondary; font.pixelSize: 12 }
    component Title: Body { font.pixelSize: 23; font.weight: Font.DemiBold }
    component Input: TextField {
        Layout.fillWidth: true; implicitHeight: 42; padding: 10
        color: Theme.palette.text; placeholderTextColor: Theme.palette.textTertiary
        selectByMouse: true; font.pixelSize: 14
        background: Rectangle { radius: 7; color: Theme.palette.backgroundSecondary; border.color: parent.activeFocus ? Theme.palette.primary : Theme.palette.border }
    }
    component Sheet: Pane {
        Layout.fillWidth: true; padding: 18
        background: Rectangle { radius: 12; color: Theme.palette.surface; border.color: Theme.palette.border }
    }
    component Check: CheckBox {
        id: check
        implicitHeight: Math.max(36, contentItem.implicitHeight + 8)
        padding: 4
        contentItem: Body { text: check.text; leftPadding: 30; verticalAlignment: Text.AlignVCenter }
        indicator: Rectangle {
            x: 4; y: (check.height - height) / 2; width: 20; height: 20; radius: 4
            color: check.checked ? Theme.palette.primary : Theme.palette.backgroundSecondary
            border.color: check.checked || check.activeFocus ? Theme.palette.primary : Theme.palette.textTertiary
            border.width: check.activeFocus ? 2 : 1
            LogosText { anchors.centerIn: parent; text: check.checked ? "\u2713" : ""; color: Theme.palette.background; font.pixelSize: 14 }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        LogosButton { text: "Find a service"; enabled: panel.offering; onClicked: { panel.offering = false; panel.findServices() } }
        LogosButton { text: "Offer a service"; enabled: !panel.offering; onClicked: { panel.offering = true; if (panel.ready) panel.backend.loadProviderSettings() } }
        Item { Layout.fillWidth: true }
    }
    Body { visible: !panel.ready; text: "Connect your agent to browse or offer services." }
    Body { visible: !!panel.error; text: panel.error; color: Theme.palette.warning }

    ColumnLayout {
        visible: !panel.offering && !panel.selected; Layout.fillWidth: true; spacing: 16
        Title { text: "Find the right help." }
        Note { text: "Your agent can ask another agent to do a job. Pick a service, check its price, then send only the input it needs." }
        RowLayout {
            Layout.fillWidth: true
            Input { id: topic; text: "commons"; placeholderText: "Topic"; Accessible.name: "Service discovery topic"; onAccepted: panel.findServices() }
            LogosButton { text: "Find services"; enabled: panel.ready && !panel.busy; onClicked: panel.findServices() }
        }
        Note { visible: panel.searched; text: panel.refreshes > 0 ? "Listening for services on this topic..." : (panel.entries).length + " services replied. Listings expire when a provider stops responding." }
        Note { visible: panel.searched && panel.refreshes === 0 && !(panel.entries).length; text: "No services have replied yet. The provider must be online, connected to the same Logos Messaging network, and publish to this topic. Try again after it connects." }
        Repeater {
            model: serviceRows
            delegate: Sheet {
                required property string payloadJson
                readonly property var modelData: panel.decode(payloadJson, {})
                contentItem: ColumnLayout {
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Body { text: modelData.name; font.weight: Font.DemiBold; font.pixelSize: 17 }
                        LogosText { text: panel.price(modelData.price); textFormat: Text.PlainText; color: Theme.palette.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    }
                    Body { text: modelData.description }
                    Note { text: modelData.skill + "  /  " + panel.shortAddress(modelData.address) }
                    RowLayout {
                        Note { text: modelData.trust === "owner-contact" ? "An agent you have introduced." : "Public provider. Signature checked; this is not a reputation rating." }
                        LogosButton { text: "Use service"; Accessible.name: "Use " + modelData.skill + " from " + modelData.name; enabled: panel.ready && !panel.busy; onClicked: panel.choose(modelData) }
                    }
                }
            }
        }
        LogosButton { visible: !!panel.directory.has_more; text: "Show more services"; enabled: !panel.busy; onClicked: panel.backend.loadServiceDirectory(topic.text.trim(), panel.directory.next_offset, false) }
    }
    ColumnLayout {
        visible: !panel.offering && !!panel.selected; Layout.fillWidth: true; spacing: 14
        LogosButton { text: "Back to services"; onClicked: { panel.selected = null; panel.error = "" } }
        Title { text: panel.selected ? panel.selected.name : "" }
        Body { text: panel.selected ? panel.selected.description : "" }
        Note { text: panel.selected ? panel.price(panel.selected.price) + ". The provider receives the input below, not access to your agent or wallet." : "" }
        ColumnLayout {
            visible: panel.selected && panel.selected.price !== "0"; Layout.fillWidth: true
            Body { text: "How should this request be paid?"; font.weight: Font.DemiBold }
            ComboBox {
                Accessible.name: "Service payment privacy"; Layout.fillWidth: true
                model: panel.selected && (panel.selected.payment_modes || []).indexOf("public") >= 0
                    ? ["Private - hides the payment details", "Public - faster, visible on-chain"] : ["Private - hides the payment details"]
                currentIndex: panel.paymentMode === "public" ? 1 : 0
                palette.text: Theme.palette.text; palette.button: Theme.palette.backgroundSecondary
                onActivated: panel.paymentMode = currentIndex === 1 ? "public" : "private"
            }
            Note { text: panel.paymentMode === "public"
                ? "Public reveals the sender, recipient and amount. It uses public funds and does not create a private proof; network confirmation is still required. You will review this exact choice."
                : "Private is the default. It requires a real proof and can take tens of minutes on this computer. There is no automatic switch to public." }
        }
        Repeater {
            model: panel.selected ? Object.keys((panel.selected.input_schema || {}).properties || {}) : []
            delegate: ColumnLayout {
                required property string modelData
                readonly property var spec: panel.selected ? panel.selected.input_schema.properties[modelData] : ({})
                Layout.fillWidth: true; spacing: 6
                Body { text: spec.title || modelData.replace(/_/g, " "); font.weight: Font.DemiBold }
                Note { visible: !!spec.description; text: spec.description || "" }
                Check { visible: spec.type === "boolean"; text: "Yes"; checked: !!panel.inputs[modelData]; onClicked: panel.setInput(modelData, checked) }
                ComboBox {
                    visible: !!spec.enum; Layout.fillWidth: true; model: spec.enum || []
                    palette.text: Theme.palette.text; palette.button: Theme.palette.backgroundSecondary
                    onActivated: panel.setInput(modelData, currentText)
                }
                Input {
                    visible: !spec.enum && spec.type !== "boolean" && spec.type !== "object" && spec.type !== "array" && !!spec.type
                    text: String(panel.inputs[modelData] === undefined ? "" : panel.inputs[modelData])
                    Accessible.name: "Service input " + modelData
                    onTextEdited: panel.setInput(modelData, text)
                }
                TextArea {
                    visible: spec.type === "object" || spec.type === "array" || !spec.type
                    Layout.fillWidth: true; Layout.preferredHeight: 110; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
                    color: Theme.palette.text; selectByMouse: true; placeholderText: spec.type === "array" ? "Enter a JSON list: [...]" : "Enter a JSON object: {...}"
                    background: Rectangle { radius: 7; color: Theme.palette.backgroundSecondary; border.color: Theme.palette.border }
                    Accessible.name: "Structured service input " + modelData
                    text: String(panel.inputs[modelData] === undefined ? "" : panel.inputs[modelData])
                    onTextChanged: if (String(panel.inputs[modelData] || "") !== text) panel.setInput(modelData, text)
                }
            }
        }
        LogosButton { text: "Review request"; enabled: panel.ready && !panel.busy; onClicked: panel.reviewTask() }
    }
    ColumnLayout {
        visible: panel.offering; Layout.fillWidth: true; spacing: 14
        Title { text: "Put your agent to work." }
        Note { text: "Choose what you offer and set a price. Anyone connected to the same Logos Messaging network and topic can find a public listing. Your private tools and spending controls stay separate." }
        Sheet {
            contentItem: ColumnLayout {
                spacing: 8
                Body { text: !(panel.provider.settings || {})["public"] ? "Not publicly listed" : panel.provider.publication_state === "published" ? "Your listing was published" : "Publishing your listing..."; font.weight: Font.DemiBold }
                Note { visible: !!(panel.provider.settings || {})["public"]; text: "Other agents need to be online on the same Logos Messaging network and topic to receive it. A published listing is not a guarantee of discovery." }
                Note { visible: !!panel.provider.publication_error; text: "The network has not accepted the listing yet. Check the agent's Storage and Messaging connection, then refresh."; color: Theme.palette.warning }
                Note { visible: !!(panel.provider.settings || {})["public"]; text: panel.shortAddress(panel.provider.public_address) }
                LogosButton { text: "Refresh status"; enabled: panel.ready && !panel.busy; onClicked: { panel.backend.loadProviderSettings(); panel.refreshes = 6 } }
            }
        }
        Body { text: "Listing name" }
        Input { text: panel.draft.name || ""; Accessible.name: "Public service name"; placeholderText: "A name other agents will recognize"; onTextEdited: panel.edit("name", text) }
        Body { text: "What does it do?" }
        Input { text: panel.draft.description || ""; Accessible.name: "Public service description"; placeholderText: "Describe the help you offer"; onTextEdited: panel.edit("description", text) }
        Body { text: "Topic" }
        Input { text: panel.draft.discovery_topic || "commons"; Accessible.name: "Provider discovery topic"; onTextEdited: panel.edit("discovery_topic", text) }
        Check { text: "Make this listing public"; checked: !!panel.draft["public"]; Accessible.name: "Enable public service listing"; onCheckedChanged: if (!!panel.draft["public"] !== checked) panel.setPublic(checked) }
        Check { text: "Also accept public payments"; Accessible.name: "Accept public service payments"; checked: !!panel.draft.allow_public_payments; onCheckedChanged: if (!!panel.draft.allow_public_payments !== checked) panel.edit("allow_public_payments", checked) }
        Note { text: "Off by default. Public receiving must be enabled first in Skills & tools. Public payments reveal the parties and amount; private payments remain available." }
        Note { visible: !!panel.removedPrivate; text: panel.removedPrivate }
        Body { text: "Services to offer"; font.weight: Font.DemiBold }
        Note { text: "Select only what strangers should be allowed to request. A service may earn tokens without gaining permission to spend your wallet." }
        Repeater {
            model: panel.provider.available_services || []
            delegate: Sheet {
                required property var modelData
                contentItem: ColumnLayout {
                    spacing: 8
                    Check { text: modelData.description; checked: !!panel.draft.exports[modelData.id]; Accessible.name: "Offer " + modelData.id; onCheckedChanged: if (!!panel.draft.exports[modelData.id] !== checked) panel.exportSkill(modelData.id, checked) }
                    Note { text: modelData.id + (modelData.extension ? " / installed extension" : "") }
                    RowLayout {
                        visible: !!panel.draft.exports[modelData.id]; Layout.fillWidth: true
                        Body { text: "Price"; Layout.fillWidth: false }
                        Input { text: panel.draft.exports[modelData.id] ? panel.draft.exports[modelData.id].price : "0"; Accessible.name: "Price for " + modelData.id; onTextEdited: panel.setPrice(modelData.id, text) }
                        Note { text: "testnet base units"; Layout.fillWidth: false }
                    }
                }
            }
        }
        Note { text: "Add your own service with the extension installer on the agent's computer. No changes to the agent's core code are needed." }
        LogosButton { text: "Review listing"; enabled: panel.ready && !panel.busy && !!panel.settingsHash && panel.dirty; onClicked: panel.reviewSettings() }
    }
    Dialog {
        id: review
        parent: panel.dialogHost || panel
        x: Math.max(16, (parent.width - width) / 2)
        y: Math.max(16, (parent.height - height) / 2)
        width: Math.min(560, Math.max(280, parent.width - 32))
        height: Math.min(parent.height - 32, reviewBody.implicitHeight + 100)
        modal: true; padding: 22; closePolicy: Popup.CloseOnEscape
        background: Rectangle { radius: 14; color: Theme.palette.surfaceRaised; border.color: Theme.palette.border }
        contentItem: ScrollView {
            clip: true; contentWidth: availableWidth
            ScrollBar.vertical.policy: ScrollBar.AsNeeded
            ColumnLayout {
            id: reviewBody
            width: review.availableWidth
            spacing: 14
            Title { text: panel.reviewed && panel.reviewed.kind === "task" ? "Send this request?" : "Save this listing?" }
            Body { text: panel.reviewed && panel.reviewed.kind === "task" ? panel.reviewed.service.name + " / " + panel.reviewed.service.skill : panel.reviewed ? panel.reviewed.settings.name : ""; font.weight: Font.DemiBold }
            Body { text: panel.reviewed && panel.reviewed.kind === "task" ? "Price: " + panel.price(panel.reviewed.service.price) + ". A changed price will be rejected. Your normal spending limit still applies." : panel.reviewed ? panel.reviewed.settings["public"] ? "This name, description and selected services will be published to the " + panel.reviewed.settings.discovery_topic + " topic. Anyone can request the selected services." : "This agent will stop publicly offering new service tasks. Already accepted tasks and payment records are preserved." : "" }
            Body { visible: !!panel.reviewed && panel.reviewed.kind === "task" && panel.reviewed.service.price !== "0"; text: panel.reviewed && panel.reviewed.paymentMode === "public" ? "PUBLIC PAYMENT: sender, recipient and amount will be visible. Only public funds are used. No private-proof delay; still waits for confirmation." : "PRIVATE PAYMENT: transaction details stay shielded. Proof generation can take tens of minutes."; color: panel.reviewed && panel.reviewed.paymentMode === "public" ? Theme.palette.warning : Theme.palette.text }
            Note { visible: !!panel.reviewed && panel.reviewed.kind === "settings"; text: panel.reviewed && panel.reviewed.settings.allow_public_payments ? "Payment choices offered: private and public. Public receiving was explicitly enabled." : "Payment offered: private only." }
            Note { text: panel.reviewed && panel.reviewed.kind === "task" ? "Sent to: " + panel.reviewed.service.address : "Wallet control, personal files and private messages are not published." }
            TextArea {
                visible: !!panel.reviewed && panel.reviewed.kind === "task"
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(150, implicitHeight)
                readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap
                color: Theme.palette.text; background: Rectangle { color: Theme.palette.backgroundSecondary; radius: 6 }
                text: panel.reviewed && panel.reviewed.kind === "task" ? JSON.stringify(panel.reviewed.arguments, null, 2) : ""
            }
            Note { visible: !!panel.reviewed && panel.reviewed.kind === "settings"; text: panel.reviewed && panel.reviewed.kind === "settings" ? Object.keys(panel.reviewed.settings.exports).map(function(id) { return id + ": " + panel.price(panel.reviewed.settings.exports[id].price) }).join("\n") : "" }
            }
        }
        footer: Pane {
            padding: 16
            background: Item {}
            contentItem: RowLayout {
                LogosButton { text: "Back"; Accessible.name: "Back from service review"; onClicked: review.close() }
                Item { Layout.fillWidth: true }
                LogosButton {
                    text: panel.reviewed && panel.reviewed.kind === "task" ? "Send request" : "Save listing"
                    Accessible.name: text
                    enabled: panel.ready && !panel.busy
                    onClicked: panel.confirm()
                }
            }
        }
    }
}
