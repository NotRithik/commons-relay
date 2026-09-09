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
    readonly property bool pending: backend && (backend.requestPending || backend.signing)
    readonly property var profiles: parse(backend ? backend.profilesJson : "[]", [])
    readonly property var summary: parse(backend ? backend.summaryJson : "{}", {})
    readonly property var tasks: parse(backend ? backend.tasksJson : "[]", [])
    readonly property var remoteHealth: parse(backend ? backend.remoteHealthJson : "{}", {})
    readonly property var skills: parse(backend ? backend.skillsJson : "[]", [])
    readonly property var skillDetails: parse(backend ? backend.skillDetailsJson : "{}", {})
    readonly property var task: parse(backend ? backend.taskDetailsJson : "{}", {})
    readonly property var planner: parse(backend ? backend.plannerInfoJson : "{}", {})
    readonly property var conversation: parse(backend ? backend.conversationJson : "[]", [])
    readonly property string selectedLabel: {
        for (let i = 0; i < profiles.length; ++i)
            if (backend && profiles[i].name === backend.selectedProfile) return profiles[i].label
        return "Choose an agent"
    }
    readonly property string providerLabel: planner.provider || "the configured model provider"
    readonly property string inferenceFingerprint: root.planner.configuration_hash || ""
    onInferenceFingerprintChanged: { root.reviewedChat = ({}); chatPermissionDialog.close() }
    property bool showTechnical: false
    property bool manualTools: false
    property var fields: []
    property var formValues: ({})
    property var reviewed: ({})
    property var reviewedChat: ({})
    property string reviewKind: ""
    property string viewError: ""
    property string pendingDraft: ""
    property string pendingDraftAgent: ""
    property var draftByProfile: ({})
    property string draftProfile: ""
    property string permissionGoalRequested: ""
    property string permissionProfile: ""
    property string permissionAgent: ""
    property var permissionReview: ({})

    function parse(value, fallback) {
        try { return JSON.parse(value) } catch (_) { return fallback }
    }
    function call(reply) {
        if (logos && logos.watch)
            logos.watch(reply, function() {}, function(error) { root.viewError = String(error) })
    }
    function label(name) {
        const names = { path: "File path on the agent", binary_path: "Program file on the agent",
            recipient: "Recipient", amount: "Amount in testnet base units", label: "File label",
            address: "Stored file reference", message: "Message", members: "Group members",
            group_id: "Group", program_id: "Program account", instruction: "Encoded instruction",
            params: "Additional options", agent_address: "Other agent", skill: "Task", task_id: "Task reference",
            topic: "Discovery topic", key: "Setting", value: "Value" }
        return names[name] || name.replace(/_/g, " ")
    }
    function stateLabel(state) {
        const names = { submitted: "Queued", working: "Working", "input-required": "Needs your approval",
            unknown: "Checking network outcome", completed: "Completed", failed: "Failed",
            rejected: "Not authorized", canceled: "Canceled" }
        return names[state] || state
    }
    function showLatestMessage() {
        chatTranscript.goToLatest()
    }
    function chatState(state) {
        const names = { queued: "Queued", thinking: "Thinking", working: "Using tools", completed: "Complete",
            waiting: "Waiting on a task or approval", failed: "Could not complete", cancelled: "Stopped",
            interrupted: "Interrupted - not automatically retried" }
        return names[state] || state
    }
    function selectedCurrentSkill() {
        const id = skillPicker.currentIndex >= 0 ? skillPicker.currentValue : ""
        const selected = root.skills.find(function(item) { return item.id === id })
        if (!selected || root.skillDetails.id !== selected.id || !root.skillDetails.input_schema)
            throw new Error("Wait for the selected tool's verified fields.")
        return root.skillDetails
    }
    function renderFieldValues() {
        root.fields = []
        root.formValues = ({})
        try {
            const detail = root.selectedCurrentSkill()
            const props = detail.input_schema.properties
            const rows = [], values = {}
            for (const name of Object.keys(props)) {
                const spec = props[name]
                rows.push({ name: name, type: spec.type || "any", label: label(name), description: spec.description || "" })
                values[name] = spec.type === "object" ? "{}" : spec.type === "array" ? "[]" : spec.type === "boolean" ? "false" : ""
            }
            root.fields = rows
            root.formValues = values
        } catch (_) { /* a new selected skill is still loading */ }
    }
    function checkStructured(value, depth) {
        if (depth > 16) throw new Error("The options are nested too deeply.")
        if (typeof value === "number" && !Number.isSafeInteger(value))
            throw new Error("Use whole safe numbers; keep large token amounts as text.")
        if (value && typeof value === "object") {
            for (const key of Object.keys(value)) {
                if (["__proto__", "constructor", "prototype"].indexOf(key) >= 0)
                    throw new Error("The options contain an unsupported field name.")
                checkStructured(value[key], depth + 1)
            }
        }
    }
    function collectForm() {
        const detail = root.selectedCurrentSkill()
        const props = detail.input_schema.properties
        const required = detail.input_schema.required || []
        const values = {}
        for (const name of Object.keys(props)) {
            if (["__proto__", "constructor", "prototype"].indexOf(name) >= 0)
                throw new Error("This capability contains an unsupported field name.")
            const spec = props[name]
            const raw = root.formValues[name]
            if (raw === undefined || raw === "") {
                if (required.indexOf(name) >= 0) throw new Error("Complete " + label(name) + ".")
                continue
            }
            let value
            if (spec.type === "string") {
                value = String(raw)
                if (spec.minLength !== undefined && value.length < spec.minLength) throw new Error(label(name) + " is too short.")
                if (spec.maxLength !== undefined && value.length > spec.maxLength) throw new Error(label(name) + " is too long.")
                if (spec.pattern && !(new RegExp(spec.pattern)).test(value)) throw new Error(label(name) + " has an invalid format.")
            } else if (spec.type === "integer" || spec.type === "number") {
                if (!/^-?(0|[1-9][0-9]*)$/.test(String(raw))) throw new Error(label(name) + " must be a whole number.")
                value = Number(raw)
                if (!Number.isSafeInteger(value)) throw new Error(label(name) + " is outside the supported number range.")
                if (spec.minimum !== undefined && value < spec.minimum) throw new Error(label(name) + " is below its minimum.")
                if (spec.maximum !== undefined && value > spec.maximum) throw new Error(label(name) + " exceeds its maximum.")
            } else {
                if (spec.type === "array" && (spec.items || {}).type === "string" && !String(raw).trim().startsWith("["))
                    value = String(raw).split(/\n|,/).map(function(item) { return item.trim() }).filter(Boolean)
                else {
                    try { value = JSON.parse(raw) } catch (_) { throw new Error(label(name) + " needs valid structured input.") }
                }
                if (spec.type === "object" && (!value || Array.isArray(value) || typeof value !== "object")) throw new Error(label(name) + " must be an object.")
                if (spec.type === "array" && !Array.isArray(value)) throw new Error(label(name) + " must be a list.")
                if (spec.type === "boolean" && typeof value !== "boolean") throw new Error(label(name) + " must be true or false.")
                if (spec.type === "array") {
                    if (spec.minItems !== undefined && value.length < spec.minItems) throw new Error(label(name) + " needs more items.")
                    if (spec.maxItems !== undefined && value.length > spec.maxItems) throw new Error(label(name) + " has too many items.")
                    if (spec.uniqueItems && new Set(value.map(function(v) { return JSON.stringify(v) })).size !== value.length)
                        throw new Error("Remove duplicate " + label(name).toLowerCase() + ".")
                    if ((spec.items || {}).type === "string" && value.some(function(v) { return typeof v !== "string" }))
                        throw new Error(label(name) + " must contain text items.")
                }
                checkStructured(value, 0)
            }
            if (spec.enum && spec.enum.indexOf(value) < 0) throw new Error("Choose a supported " + label(name) + ".")
            values[name] = value
        }
        return values
    }
    function reviewTask() {
        root.viewError = ""
        try {
            const detail = root.selectedCurrentSkill()
            root.reviewed = { skill: detail.id, arguments: JSON.parse(JSON.stringify(root.collectForm())), _profile: root.backend.selectedProfile, expires: Math.min(7200, (root.summary.policy || {}).approval_ttl || 600) }
            root.reviewKind = "submit"
            reviewDialog.open()
        } catch (error) { root.viewError = String(error.message || error) }
    }
    function reviewAction(kind) {
        root.viewError = ""
        if (!root.task.id || !root.task.arguments_complete) {
            root.viewError = "Load the complete current task details first."
            return
        }
        root.reviewed = Object.assign({}, JSON.parse(JSON.stringify(root.task)), { _profile: root.backend.selectedProfile })
        root.reviewKind = kind
        reviewDialog.open()
    }
    function confirmReview() {
        if (!root.backend || root.reviewed._profile !== root.backend.selectedProfile) {
            root.viewError = "The selected agent changed. Review the action again."
            return
        }
        if (root.reviewKind === "submit")
            root.call(root.backend.submitTask(root.reviewed.skill, JSON.stringify(root.reviewed.arguments), root.reviewed.expires))
        else if (root.reviewKind === "approve") {
            if (!root.reviewed.arguments_complete) {
                root.viewError = "The complete task arguments are required for approval."
                return
            }
            root.call(root.backend.approveTask(root.reviewed.id, root.reviewed.intent_hash, root.reviewed.policy_version))
        } else if (root.reviewKind === "cancel") root.call(root.backend.cancelTask(root.reviewed.id))
        root.reviewKind = ""
        root.reviewed = ({})
    }
    function sendChat() {
        root.viewError = ""
        if (!root.backend || !root.backend.remoteReady || !root.planner.enabled) {
            root.viewError = "Choose an available agent with its model connected first."
            return
        }
        if (!/^[a-f0-9]{64}$/.test(root.planner.configuration_hash || "")) {
            root.viewError = "Wait for the selected model settings to load."
            return
        }
        const message = chatInput.text.trim()
        const amount = allowChatActions.checked ? chatSpend.text.trim() : "0"
        if (!message || message.length > 4000 || !/^(0|[1-9][0-9]{0,38})$/.test(amount)) {
            root.viewError = "Write a message and use a whole-number testnet spending limit."
            return
        }
        root.reviewedChat = { profile: root.backend.selectedProfile, agent: root.backend.selectedAgent,
            label: root.selectedLabel, message: message, actions: allowChatActions.checked, amount: amount, inference_hash: root.planner.configuration_hash || "" }
        if (root.reviewedChat.actions) chatPermissionDialog.open()
        else root.submitReviewedChat()
    }
    function submitReviewedChat() {
        const reviewed = root.reviewedChat
        if (!reviewed.message || !root.backend
                || reviewed.inference_hash !== root.planner.configuration_hash
                || reviewed.profile !== root.backend.selectedProfile
                || reviewed.agent !== root.backend.selectedAgent) {
            root.viewError = "The agent or model settings changed. Review your message again."
            return
        }
        root.viewError = ""
        root.pendingDraft = reviewed.message
        root.pendingDraftAgent = reviewed.agent
        root.call(root.backend.startConversation(reviewed.message, reviewed.actions, reviewed.amount, reviewed.inference_hash))
        root.reviewedChat = ({})
    }
    Connections {
        target: root.backend
        function onSkillDetailsJsonChanged() { root.renderFieldValues() }
        function onPermissionReviewJsonChanged() { root.receivePermissionReview() }
        function onSelectedProfileChanged() {
            root.fields = []
            root.formValues = ({})
            root.reviewKind = ""
            root.reviewed = ({})
            root.reviewedChat = ({})
            root.viewError = ""
            reviewDialog.close()
            chatPermissionDialog.close()
            modelActionDialog.close()
            root.permissionGoalRequested = ""
            root.permissionReview = ({})
        }
    }

    function reviewModelAction(goalId) {
        if (!root.backend || !root.backend.remoteReady) return
        root.viewError = "Loading the current action request..."
        root.permissionGoalRequested = goalId
        root.permissionProfile = root.backend.selectedProfile
        root.permissionAgent = root.backend.selectedAgent
        root.permissionReview = ({})
        root.call(root.backend.reviewConversationPermission(goalId))
    }
    function receivePermissionReview() {
        if (!root.backend) return
        const goal = root.parse(root.backend.permissionReviewJson, {})
        if (!goal.id || goal.id !== root.permissionGoalRequested || goal.agent_id !== root.permissionAgent
            || root.backend.selectedAgent !== root.permissionAgent || root.backend.selectedProfile !== root.permissionProfile) return
        const permission = goal.permission
        if (!permission || permission.decision !== "pending" || !permission.request) {
            root.viewError = "This action request no longer needs a decision."
            return
        }
        root.permissionReview = JSON.parse(JSON.stringify(permission.request))
        actionRequestReviewed.checked = false
        root.viewError = ""
        modelActionDialog.open()
    }
    function confirmRequestedAction(approve) {
        const request = root.permissionReview
        if (!root.backend || !root.backend.remoteReady || root.backend.chatBusy || root.pending
            || root.backend.selectedProfile !== root.permissionProfile || root.backend.selectedAgent !== root.permissionAgent
            || request.goal_id !== root.permissionGoalRequested || !/^[a-f0-9]{64}$/.test(request.intent_hash || "")
            || (approve && !actionRequestReviewed.checked)) {
            root.viewError = "Reload this action request and read its details before deciding."
            return
        }
        root.call(root.backend.decideConversationPermission(request.goal_id, request.intent_hash, approve))
        modelActionDialog.close()
        root.permissionReview = ({})
        root.permissionGoalRequested = ""
    }
    function actionImplications(skill) {
        if (skill === "wallet.send") return "Transfers testnet tokens to the recipient below. A confirmed transfer cannot be undone here."
        if (skill === "messaging.send" || skill === "storage.share") return "Sends information to the recipient below. They may keep what they receive."
        if (skill === "storage.upload") return "Reads the named file on the agent and stores an encrypted copy in Logos Storage."
        if (skill === "storage.download") return "Downloads and decrypts the named stored file to the agent's output directory."
        if (skill === "program.call" || skill === "program.deploy") return "Submits a testnet program operation. Check the program and all inputs before approving."
        if (skill === "agent.task") return "Sends the inputs below to another agent. Its quoted token price is shown separately."
        if (skill === "messaging.create_group" || skill === "messaging.join") return "Creates or joins the specified messaging group and may send invitations."
        return "Runs this registered tool once with exactly the inputs below."
    }
    function priceMicro(value) {
        const raw = String(value).trim()
        if (!/^(0|[1-9][0-9]{0,3})([.][0-9]{1,6})?$/.test(raw)) throw new Error("Use a non-negative price with up to six decimal places.")
        const parts = raw.split(".")
        const result = Number(parts[0]) * 1000000 + Number((parts[1] || "").padEnd(6, "0"))
        if (result > 1000000000) throw new Error("The price estimate is too large.")
        return result
    }
    function openInferenceSettings() {
        inferenceDialog.profileAtOpen = root.backend ? root.backend.selectedProfile : ""
        inferenceDialog.hashAtOpen = root.planner.configuration_hash || ""
        inferenceDialog.errorText = ""
        inferenceDialog.saveAttempted = false
        inferenceEndpoint.text = root.planner.endpoint || "https://api.openai.com/v1"
        inferenceModel.text = root.planner.model || ""
        inferenceApi.currentIndex = root.planner.api === "chat-completions" ? 1 : 0
        inferenceCredential.currentIndex = root.planner.credential_configured ? 0 : 2
        inferenceKey.text = ""
        inferenceOutput.text = String(root.planner.max_output_tokens || 1536)
        inferenceInputPrice.text = String(Number(root.planner.input_micro_per_million || 0) / 1000000)
        inferenceOutputPrice.text = String(Number(root.planner.output_micro_per_million || 0) / 1000000)
        inferenceReview.checked = false
        inferenceDialog.open()
    }
    function saveInferenceSettings() {
        try {
            if (!root.backend || !root.backend.remoteReady
                || inferenceDialog.profileAtOpen !== root.backend.selectedProfile
                || !inferenceDialog.hashAtOpen || inferenceDialog.hashAtOpen !== (root.planner.configuration_hash || ""))
                throw new Error("Connect to the selected agent and reload its current inference settings.")
            if (!inferenceReview.checked) throw new Error("Review the destination before saving.")
            if (!/^[0-9]{3,4}$/.test(inferenceOutput.text)) throw new Error("Output tokens must be a whole number between 128 and 8192.")
            const maximum = Number(inferenceOutput.text)
            if (maximum < 128 || maximum > 8192) throw new Error("Output tokens must be between 128 and 8192.")
            const settings = { api: inferenceApi.currentIndex === 0 ? "responses" : "chat-completions",
                endpoint: inferenceEndpoint.text.trim(), model: inferenceModel.text.trim(),
                credential_mode: ["keep", "replace", "none"][inferenceCredential.currentIndex],
                max_output_tokens: maximum, input_micro_per_million: root.priceMicro(inferenceInputPrice.text),
                output_micro_per_million: root.priceMicro(inferenceOutputPrice.text) }
            const key = settings.credential_mode === "replace" ? inferenceKey.text : ""
            if (settings.credential_mode === "replace" && !key) throw new Error("Enter the new endpoint's API key, or choose No API key.")
            inferenceDialog.saveAttempted = true
            root.call(root.backend.configureInference(JSON.stringify(settings), key, inferenceDialog.hashAtOpen))
            inferenceKey.text = ""
            inferenceReview.checked = false
        } catch (error) { inferenceDialog.errorText = String(error.message || error) }
    }
    function skillTitle(id) {
        for (let i = 0; i < root.skills.length; ++i)
            if (root.skills[i].id === id) return root.skills[i].description
        return String(id || "Task").replace(/[._]/g, " ")
    }
    function taskColor(state) {
        if (state === "completed") return Theme.palette.success
        if (state === "failed" || state === "rejected") return Theme.palette.error
        if (state === "input-required" || state === "unknown") return Theme.palette.warning
        return Theme.palette.textSecondary
    }
    function openTask(id) {
        root.viewError = ""
        root.call(root.backend.requestTask(id))
        detailsDialog.open()
    }
    function setField(name, value) {
        const next = Object.assign({}, root.formValues)
        next[name] = value
        root.formValues = next
    }
    function taskError(code) {
        const messages = {
            TESTNET_HISTORY_CHANGED: "This wallet belongs to an earlier testnet. Its old balance is not usable on the restarted network.",
            AUTHORIZATION_EXPIRED: "The permission expired before this task could finish. It was not automatically approved again.",
            OPENAI_HTTP_400: "The model provider rejected the request format. Check the model and tool configuration before sending a new message; no automatic retry was made.",
            OPENAI_HTTP_401: "The model credential was not accepted. Ask the operator to check the private model configuration; never paste an API key into chat.",
            OPENAI_HTTP_429: "The model provider reported a usage or rate limit. This message was not repeated automatically.",
            GOAL_GRANT_NOT_ACTIVE: "Permission for this conversation was stopped or expired. No new action is authorized.",
            INSUFFICIENT_PUBLIC_BALANCE: "There are not enough testnet units in the sending account.",
            WALLET_BUSY: "Another wallet operation is in progress. Check its result before starting another.",
            EFFECT_STATUS_UNKNOWN: "The agent is checking whether the network accepted this action. Do not send a duplicate."
        }
        return messages[code] || String(code || "")
    }
    function resultSummary(value) {
        // A submitted request or model sentence is never a completed receipt.
        if (!value || value.state !== "completed" || !value.result_complete
            || !value.result_preview || value.result_preview === "null") return ""
        const result = parse(value.result_preview, null)
        if (!result || typeof result !== "object" || Array.isArray(result)) return ""
        const decimal = function(x) { return typeof x === "string" && /^(0|[1-9][0-9]{0,38})$/.test(x) }
        const count = function(x) { return typeof x === "number" && isFinite(x) && x >= 0 && Math.floor(x) === x && x <= 9007199254740991 }
        const block = count(result.block) ? " Recorded at block " + result.block + "." : ""
        if (value.skill === "wallet.balance" && decimal(result.balance))
            return "Recorded wallet balance: " + result.balance + " testnet units." + block + ""
        if (value.skill === "storage.list" && Array.isArray(result.files))
            return result.files.length === 0 ? "No saved files were found in this agent's file vault." : result.files.length + " saved files were found. Open technical details for their recorded references."
        if (value.skill === "storage.upload" && count(result.bytes) && typeof result.address === "string")
            return "Encrypted file stored: " + result.bytes + " bytes.\nContent reference: " + result.address
        if (value.skill === "storage.download" && result.authenticated === true && count(result.bytes) && typeof result.path === "string")
            return "Retrieved and authenticated: " + result.bytes + " bytes.\nSaved as: " + result.path
        if (value.skill === "agent.task" && decimal(result.paid_amount) && Array.isArray(result.artifacts)
            && typeof result.provider === "string") {
            if (result.paid_amount !== "0" && !/^[a-f0-9]{64}$/.test(result.payment_transaction || ""))
                return "The completed task returned a result. The payment reference needs inspection in technical details."
            return "Service completed with " + result.artifacts.length + " returned result" + (result.artifacts.length === 1 ? "." : "s.")
                + (result.paid_amount === "0" ? " No testnet-token payment was required." : " Paid " + result.paid_amount + " testnet units.")
                + "\nProvider: " + result.provider
                + (result.paid_amount !== "0" ? "\nPayment reference: " + result.payment_transaction : "")
        }
        if (value.skill === "program.query") return "Program state was read." + block + " This is not the agent's wallet balance."
        if (value.skill === "example.text_statistics" && count(result.words) && count(result.characters))
            return "The tool counted " + result.words + " words and " + result.characters + " characters."
        return "The completed task returned a result. Open technical details to inspect it."
    }
    function example(text) {
        if (!root.backend || root.backend.chatBusy) return
        chatInput.text = text
        chatInput.forceActiveFocus()
    }
    Connections {
        target: root.backend
        function onSelectedProfileChanged() {
            const drafts = Object.assign({}, root.draftByProfile)
            drafts[root.draftProfile || "__unselected__"] = chatInput.text
            root.draftByProfile = drafts
            root.draftProfile = root.backend ? root.backend.selectedProfile : ""
            allowChatActions.checked = false
            chatInput.text = drafts[root.draftProfile || "__unselected__"] || ""
            root.pendingDraft = ""
            root.pendingDraftAgent = ""
            detailsDialog.close()
        }
        function onChatBusyChanged() {
            if (root.backend && !root.backend.chatBusy) {
                allowChatActions.checked = false
                chatSpend.text = "0"
                root.showLatestMessage()
            }
        }
        function onConversationJsonChanged() {
            if (!root.pendingDraft || !root.backend || root.pendingDraftAgent !== root.backend.selectedAgent) return
            for (let i = root.conversation.length - 1; i >= 0; --i) {
                const goal = root.conversation[i]
                if (goal.id === root.backend.activeGoalId && goal.prompt === root.pendingDraft) {
                    if (chatInput.text.trim() === root.pendingDraft) chatInput.text = ""
                    root.showLatestMessage()
                    root.pendingDraft = ""
                    break
                }
            }
        }
    }

    component Copy: LogosText {
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        Accessible.role: Accessible.StaticText
        Accessible.name: text
        color: Theme.palette.text
    }
    component Caption: Copy {
        font.pixelSize: Theme.typography.secondaryText
        color: Theme.palette.textSecondary
    }
    component Heading: Copy {
        font.pixelSize: Theme.typography.panelTitleText
        font.weight: Theme.typography.weightMedium
    }
    component ConsentCheck: CheckBox {
        id: control
        implicitHeight: Math.max(32, contentItem.implicitHeight + 8)
        padding: 4
        contentItem: LogosText {
            text: control.text
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: control.enabled ? Theme.palette.text : Theme.palette.textTertiary
            leftPadding: 28
            verticalAlignment: Text.AlignVCenter
        }
        indicator: Rectangle {
            x: 4; y: (control.height - height) / 2
            width: 18; height: 18; radius: 4
            color: control.checked ? Theme.palette.primary : Theme.palette.backgroundSecondary
            border.width: control.activeFocus ? 2 : 1
            border.color: control.checked || control.activeFocus ? Theme.palette.primary : Theme.palette.textTertiary
            LogosText {
                anchors.centerIn: parent
                text: control.checked ? "\u2713" : ""
                color: Theme.palette.background
                font.pixelSize: 14
                Accessible.ignored: true
            }
        }
    }
    component ThemedDialog: Dialog {
        id: dialogControl
        property string confirmLabel: "Confirm"
        property bool confirmationEnabled: !root.pending
        padding: Theme.spacing.large
        background: Rectangle { color: Theme.palette.surfaceRaised; radius: Theme.spacing.radiusLarge; border.color: Theme.palette.border }
        header: LogosText {
            text: dialogControl.title
            textFormat: Text.PlainText
            color: Theme.palette.text
            font.pixelSize: Theme.typography.panelTitleText
            font.weight: Theme.typography.weightMedium
            wrapMode: Text.WordWrap
            padding: Theme.spacing.large
        }
        footer: Pane {
            padding: Theme.spacing.large
            background: Item {}
            contentItem: RowLayout {
                LogosButton {
                    text: (dialogControl.standardButtons & Dialog.Ok) !== 0 ? "Back" : "Close"
                    onClicked: dialogControl.reject()
                }
                Item { Layout.fillWidth: true }
                LogosButton {
                    visible: (dialogControl.standardButtons & Dialog.Ok) !== 0
                    text: dialogControl.confirmLabel
                    Accessible.name: dialogControl.confirmLabel
                    variant: LogosButton.Variant.Primary
                    enabled: dialogControl.confirmationEnabled
                    onClicked: dialogControl.accept()
                }
            }
        }
    }
    component ThemedPicker: ComboBox {
        id: choiceControl
        implicitHeight: 42
        leftPadding: 12; rightPadding: 30
        contentItem: LogosText {
            text: choiceControl.displayText
            textFormat: Text.PlainText
            color: choiceControl.enabled ? Theme.palette.text : Theme.palette.textTertiary
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        indicator: LogosText {
            x: choiceControl.width - width - 12
            y: (choiceControl.height - height) / 2
            text: "\u2304"
            color: Theme.palette.textSecondary
        }
        background: Rectangle {
            color: Theme.palette.backgroundSecondary
            radius: Theme.spacing.radiusSmall
            border.color: choiceControl.activeFocus ? Theme.palette.primary : Theme.palette.border
        }
        delegate: ItemDelegate {
            required property int index
            width: choiceControl.width - 8
            height: 40
            text: choiceControl.textAt(index)
            Accessible.name: text
            highlighted: choiceControl.highlightedIndex === index
            contentItem: LogosText {
                text: choiceControl.textAt(index)
                textFormat: Text.PlainText
                color: Theme.palette.text
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: Theme.spacing.radiusSmall
                color: parent.highlighted ? Theme.palette.backgroundElevated : Theme.palette.surfaceRaised
            }
        }
        popup: Popup {
            implicitHeight: Math.min(pickerList.contentHeight + 8, 328)
            width: choiceControl.width
            y: choiceControl.mapToItem(root, 0, choiceControl.height).y + implicitHeight > root.height - 16
                ? -implicitHeight - 4 : choiceControl.height + 4
            padding: 4
            contentItem: ListView {
                id: pickerList
                clip: true
                model: choiceControl.popup.visible ? choiceControl.delegateModel : null
                currentIndex: choiceControl.highlightedIndex
                ScrollBar.vertical: ScrollBar {}
            }
            background: Rectangle {
                color: Theme.palette.surfaceRaised
                radius: Theme.spacing.radiusSmall
                border.color: Theme.palette.border
            }
        }
    }
    component Card: Pane {
        padding: Theme.spacing.xlarge
        background: Rectangle { color: Theme.palette.surfaceRaised; radius: Theme.spacing.radiusXlarge }
    }
    component Field: TextField {
        implicitHeight: 42
        font.family: Theme.typography.publicSans
        font.pixelSize: Theme.typography.primaryText
        color: Theme.palette.text
        placeholderTextColor: Theme.palette.textTertiary
        selectByMouse: true
        leftPadding: Theme.spacing.medium
        rightPadding: Theme.spacing.medium
        background: Rectangle {
            color: Theme.palette.backgroundSecondary
            radius: Theme.spacing.radiusSmall
            border.color: parent.activeFocus ? Theme.palette.overlayOrange : Theme.palette.backgroundElevated
        }
    }
    component InputArea: TextArea {
        textFormat: TextEdit.PlainText
        color: Theme.palette.text
        placeholderTextColor: Theme.palette.textTertiary
        font.family: Theme.typography.publicSans
        font.pixelSize: Theme.typography.primaryText
        wrapMode: TextEdit.Wrap
        selectByMouse: true
        padding: Theme.spacing.medium
        background: Rectangle { color: Theme.palette.backgroundSecondary; radius: Theme.spacing.radiusSmall }
    }
    component CodeText: InputArea { font.family: Theme.typography.mono; font.pixelSize: Theme.typography.secondaryText }

    Rectangle { anchors.fill: parent; color: Theme.palette.background }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                Copy { text: "Commons Relay"; font.pixelSize: 26; font.weight: Theme.typography.weightMedium }
            }
            Caption { text: "TESTNET"; color: Theme.palette.warning }
            LogosButton { text: "How to use Relay"; Accessible.name: "Open Relay tutorial"; onClicked: helpDialog.open() }
        }
        Card {
            padding: 10
            Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Copy { text: "Agent" }
                    ThemedPicker {
                        id: agentPicker
                        objectName: "commons_relay.ownerProfile"
                        Accessible.name: "Agent owner profile"
                        Layout.fillWidth: true
                        implicitHeight: 42
                        model: root.profiles
                        textRole: "label"
                        currentIndex: -1
                        displayText: root.selectedLabel
                        enabled: root.ready && root.backend && !root.backend.chatBusy && !root.pending
                        font.family: Theme.typography.publicSans
                        palette.text: Theme.palette.text
                        palette.buttonText: Theme.palette.text
                        palette.base: Theme.palette.backgroundSecondary
                        palette.button: Theme.palette.backgroundSecondary
                        palette.highlight: Theme.palette.overlayOrange
                        onActivated: root.call(root.backend.selectOwnerProfile(root.profiles[currentIndex].name))
                    }
                    LogosButton {
                        text: "Refresh"
                        Accessible.name: "Refresh selected agent"
                        enabled: root.backend && root.backend.selectedProfile.length > 0 && !root.pending
                        onClicked: { root.call(root.backend.refreshAgent()); root.call(root.backend.refreshPlanner()); root.call(root.backend.loadConversation()) }
                    }
                    LogosButton { text: "Connection"; Accessible.name: "Relay connection settings"; onClicked: connectionDialog.open() }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Rectangle { implicitWidth: 7; implicitHeight: 7; radius: 4; color: root.backend && root.backend.remoteReady ? Theme.palette.success : Theme.palette.warning }
                    Caption {
                        Layout.fillWidth: true
                        text: !root.backend || !root.backend.selectedAgent ? "Choose an agent to check its connection."
                            : root.remoteHealth.last_reply_age_ms === undefined || root.remoteHealth.last_reply_age_ms < 0 ? "Checking agent connection; no fresh heartbeat yet."
                            : root.remoteHealth.connection_state === "unresponsive" ? "Agent has not responded for " + Math.floor(root.remoteHealth.last_reply_age_ms / 1000) + "s. Task outcomes are unknown; do not resend."
                            : (root.remoteHealth.connection_state === "delayed" ? "Agent replies delayed" : "Agent responding") + " · heartbeat " + Math.floor(root.remoteHealth.last_reply_age_ms / 1000) + "s ago · round trip " + (root.remoteHealth.round_trip_ms / 1000).toFixed(1) + "s"
                    }
                    Caption { visible: root.backend && root.backend.remoteReady; text: root.planner.enabled ? (root.backend.chatBusy ? "Conversation in progress" : "Model configured") : "Model not connected"; color: root.planner.enabled ? Theme.palette.success : Theme.palette.warning }
                }
                Repeater {
                    model: (root.remoteHealth.tasks || []).slice(0, 3)
                    delegate: Caption {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.skill + " · " + modelData.detail + " · elapsed " + modelData.age_seconds + "s; phase unchanged " + modelData.phase_age_seconds + "s"
                        color: Theme.palette.textSecondary
                        wrapMode: Text.WordWrap
                        textFormat: Text.PlainText
                        Accessible.name: "Live task progress"
                    }
                }
            }
        }
        LogosTabBar {
            id: tabs
            Layout.fillWidth: true
            LogosTabButton { text: "Chat"; Accessible.name: "Relay chat tab"; width: implicitWidth + Theme.spacing.xlarge }
            LogosTabButton { text: "Activity"; Accessible.name: "Relay activity tab"; width: implicitWidth + Theme.spacing.xlarge }
            LogosTabButton { text: "Skills & tools"; Accessible.name: "Relay skills tab"; width: implicitWidth + Theme.spacing.xlarge }
        }
        ChatTranscript {
            id: chatTranscript
            visible: tabs.currentIndex === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            entries: root.conversation
            agentLabel: root.selectedLabel
            hasEarlier: root.backend && root.backend.hasMoreConversation
            loading: root.pending
            describeState: root.chatState
            describeError: root.taskError
            onEarlierRequested: root.call(root.backend.loadEarlierConversation())
            onFullReplyRequested: function(goalId) { root.call(root.backend.loadConversationGoal(goalId)) }
            onTaskRequested: function(taskId) { root.openTask(taskId) }
            onPermissionRequested: function(goalId) { root.reviewModelAction(goalId) }
            onExampleRequested: function(text) { root.example(text) }
        }
        ScrollView {
            id: mainScroll
            visible: tabs.currentIndex !== 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: mainScroll.availableWidth
                spacing: Theme.spacing.large
                Card {
                    visible: tabs.currentIndex === 1
                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: Theme.spacing.medium
                        Heading { text: "Activity and approvals" }
                        Copy { text: "This is the agent's recorded task history, not the model's description of what happened."; Layout.fillWidth: true }
                        Caption {
                            text: root.backend && root.backend.remoteReady
                                ? (root.summary.approval_count || 0) + " awaiting approval  /  " + (root.summary.task_count || 0) + " recorded tasks"
                                : "Choose an agent to read its authenticated history."
                            Layout.fillWidth: true
                        }
                        Caption {
                            visible: root.backend && root.backend.remoteReady && !!root.summary.policy
                            text: "Automatic payment limit: " + (root.summary.policy ? root.summary.policy.per_transaction : "-")
                                + " testnet units per transaction. Used or reserved in the current period: " + (root.summary.reserved_and_recent_spend || "0") + "."
                            Layout.fillWidth: true
                        }
                        Caption { visible: root.backend && root.backend.remoteReady && root.tasks.length === 0; text: "No tasks yet. Start in Chat, or use a tool from Skills & tools."; Layout.fillWidth: true }
                    }
                }
                Repeater {
                    model: tabs.currentIndex === 1 ? root.tasks : []
                    delegate: Card {
                        required property var modelData
                        Layout.fillWidth: true
                        contentItem: RowLayout {
                            spacing: Theme.spacing.large
                            ColumnLayout {
                                Layout.fillWidth: true
                                Copy { text: root.skillTitle(modelData.skill); Layout.fillWidth: true }
                                Caption { text: root.stateLabel(modelData.state); color: root.taskColor(modelData.state) }
                                Caption { text: modelData.maximum_spend === "0" ? "No testnet-token spending" : "Authorized maximum: " + modelData.maximum_spend + " testnet units" }
                                Caption { visible: !!modelData.error; text: root.taskError(modelData.error); Layout.fillWidth: true }
                            }
                            LogosButton {
                                text: modelData.state === "input-required" ? "Review approval" : modelData.state === "completed" ? "View result" : "View progress"
                                Accessible.name: text + " " + modelData.skill
                                enabled: !root.pending
                                onClicked: root.openTask(modelData.id)
                            }
                        }
                    }
                }
                LogosButton { visible: tabs.currentIndex === 1 && root.backend && root.backend.hasMoreTasks; text: "Load more activity"; enabled: !root.pending; onClicked: root.call(root.backend.loadMoreTasks()) }
                Card {
                    visible: tabs.currentIndex === 2
                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: Theme.spacing.medium
                        Heading { text: "What this agent can do" }
                        Copy { text: "These capabilities come from the agent's registered skill catalog. Chat chooses from this same catalog; the permissions engine checks every action."; Layout.fillWidth: true }
                        LogosButton {
                            text: root.manualTools ? "Show capabilities" : "Use a tool manually"
                            onClicked: root.manualTools = !root.manualTools
                        }
                        Field {
                            id: capabilitySearch
                            visible: !root.manualTools
                            Accessible.name: "Search agent capabilities"
                            placeholderText: "Search capabilities..."
                            Layout.fillWidth: true
                        }
                        Repeater {
                            model: root.manualTools ? [] : root.skills.filter(function(item) {
                                return (item.id + " " + item.description).toLowerCase().indexOf(capabilitySearch.text.toLowerCase()) >= 0
                            })
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Copy { text: modelData.description || modelData.id; Layout.fillWidth: true }
                                Caption { text: modelData.id; font.family: Theme.typography.mono }
                            }
                        }
                        Caption { visible: root.skills.length === 0; text: "Choose an agent to load its capabilities." }
                    }
                }
                Card {
                    visible: tabs.currentIndex === 2 && root.manualTools
                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: Theme.spacing.medium
                        Heading { text: "Advanced: run a specific tool" }
                        Caption { text: "For precise inputs. You can normally ask for the same action in Chat."; Layout.fillWidth: true }
                        Field {
                            id: manualToolSearch
                            Accessible.name: "Search tools for manual use"
                            placeholderText: "Find a tool by name or description..."
                            Layout.fillWidth: true
                            onTextChanged: { skillPicker.currentIndex = -1; root.fields = []; root.formValues = ({}) }
                        }
                        ThemedPicker {
                            id: skillPicker
                            objectName: "commons_relay.skillPicker"
                            Accessible.name: "Agent task skill"
                            Layout.fillWidth: true
                            implicitHeight: 42
                            model: root.skills.filter(function(item) { return (item.id + " " + item.description).toLowerCase().indexOf(manualToolSearch.text.toLowerCase()) >= 0 })
                            textRole: "description"
                            valueRole: "id"
                            currentIndex: -1
                            displayText: currentIndex >= 0 ? currentText : "Choose a tool"
                            onModelChanged: Qt.callLater(function() { skillPicker.currentIndex = -1 })
                            palette.text: Theme.palette.text
                            palette.buttonText: Theme.palette.text
                            palette.base: Theme.palette.backgroundSecondary
                            palette.button: Theme.palette.backgroundSecondary
                            enabled: root.backend && root.backend.remoteReady && !root.pending
                            onActivated: { root.fields = []; root.formValues = ({}); root.viewError = ""; root.call(root.backend.requestSkill(currentValue)) }
                        }
                        Caption { visible: skillPicker.currentIndex >= 0 && root.skillDetails.id !== skillPicker.currentValue; text: "Loading the verified input form..." }
                        Repeater {
                            model: root.fields
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Caption { text: modelData.label }
                                Field {
                                    visible: ["object", "array", "any"].indexOf(modelData.type) < 0
                                    Layout.fillWidth: true
                                    Accessible.name: modelData.label
                                    text: String(root.formValues[modelData.name] === undefined ? "" : root.formValues[modelData.name])
                                    onTextChanged: if (String(root.formValues[modelData.name] === undefined ? "" : root.formValues[modelData.name]) !== text) root.setField(modelData.name, text)
                                }
                                CodeText {
                                    visible: ["object", "array", "any"].indexOf(modelData.type) >= 0
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 90
                                    Accessible.name: modelData.label
                                    text: String(root.formValues[modelData.name] === undefined ? "" : root.formValues[modelData.name])
                                    onTextChanged: if (String(root.formValues[modelData.name] === undefined ? "" : root.formValues[modelData.name]) !== text) root.setField(modelData.name, text)
                                }
                                Caption { visible: !!modelData.description; text: modelData.description; Layout.fillWidth: true }
                            }
                        }
                        LogosButton { text: "Review task"; Accessible.name: "Review agent task"; enabled: root.backend && root.backend.remoteReady && !root.pending && skillPicker.currentIndex >= 0; onClicked: root.reviewTask() }
                    }
                }
            }
        }
        Card {
            visible: tabs.currentIndex === 0
            padding: 10
            Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: Theme.spacing.small
                InputArea {
                    id: chatInput
                    objectName: "commons_relay.chatInput"
                    enabled: true
                    Accessible.name: "Message your agent"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(128, Math.max(62, contentHeight + 20))
                    Keys.onPressed: function(event) { if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) { root.sendChat(); event.accepted = true } }
                    placeholderText: root.backend && root.backend.remoteReady ? "Ask a question or describe what you want to do..." : "Write a draft here. Connect an agent before sending."
                }
                RowLayout {
                    Layout.fillWidth: true
                    ConsentCheck { id: allowChatActions; text: "Allow actions for this message"; palette.text: Theme.palette.text; enabled: root.backend && !root.backend.chatBusy }
                    Item { Layout.fillWidth: true }
                    Caption { visible: allowChatActions.checked; text: "Max testnet units" }
                    Field { id: chatSpend; visible: allowChatActions.checked; Accessible.name: "Conversation testnet spending limit"; Layout.preferredWidth: 110; text: "0"; enabled: root.backend && !root.backend.chatBusy }
                    LogosButton { visible: root.backend && root.backend.chatBusy; text: "Stop"; Accessible.name: "Stop agent conversation"; onClicked: root.call(root.backend.cancelConversation()) }
                    LogosButton {
                        text: root.backend && root.backend.chatBusy ? "Working..." : "Send"
                        Accessible.name: "Send message to agent"
                        variant: LogosButton.Variant.Primary
                        enabled: root.backend && root.backend.remoteReady && root.planner.enabled && !root.backend.chatBusy && !root.pending && chatInput.text.trim().length > 0
                        onClicked: root.sendChat()
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Caption { text: allowChatActions.checked ? "Actions enabled · spending capped separately" : "Read-only · actions need permission"; Layout.fillWidth: true }
                    Caption { text: root.planner.enabled ? root.planner.model + " · " + root.providerLabel : "Model not connected"; color: Theme.palette.textSecondary }
                    LogosButton { text: "Model settings"; Accessible.name: "Open inference settings"; enabled: !root.backend || !root.backend.chatBusy; onClicked: root.openInferenceSettings() }
                }

            }
        }
        RowLayout {
            Layout.fillWidth: true
            Caption {
                objectName: "commons_relay.status"
                text: root.viewError || (root.backend ? root.backend.statusText : "Loading Relay...")
                color: root.viewError || (root.backend && root.backend.lastError) ? Theme.palette.error : Theme.palette.textSecondary
                Layout.fillWidth: true
                maximumLineCount: 3
                elide: Text.ElideRight
            }
            LogosButton { text: root.showTechnical ? "Hide details" : "Technical details"; onClicked: root.showTechnical = !root.showTechnical }
        }
        ScrollView { visible: root.showTechnical; Layout.fillWidth: true; Layout.preferredHeight: 130; CodeText { readOnly: true; text: root.backend ? root.backend.lastResultJson : "{}" } }
    }

    ThemedDialog {
        id: modelActionDialog
        title: "Allow this action?"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 32, 700)
        height: Math.min(root.height - 32, 650)
        standardButtons: Dialog.Close
        contentItem: ScrollView {
            id: actionReviewScroll
            contentWidth: availableWidth
            ColumnLayout {
                width: actionReviewScroll.availableWidth
                spacing: 12
                Heading { text: root.permissionReview.description || "Requested action"; Layout.fillWidth: true }
                Copy { text: root.permissionReview.reason || ""; Layout.fillWidth: true }
                Copy { text: root.actionImplications(root.permissionReview.skill || ""); Layout.fillWidth: true }
                Caption { text: "Tool: " + (root.permissionReview.skill || ""); Layout.fillWidth: true }
                Repeater {
                    model: Object.keys(root.permissionReview.arguments || {})
                    delegate: ColumnLayout {
                        required property string modelData
                        Layout.fillWidth: true
                        Caption { text: root.label(modelData) }
                        Copy { text: typeof root.permissionReview.arguments[modelData] === "string" ? root.permissionReview.arguments[modelData] : JSON.stringify(root.permissionReview.arguments[modelData], null, 2); Layout.fillWidth: true }
                    }
                }
                Copy { text: "Maximum token spend: " + (root.permissionReview.maximum_spend || "0") + " " + (root.permissionReview.asset || ""); Layout.fillWidth: true }
                Caption { text: "Approving allows this one action, not every action in the conversation. Existing spending limits still apply and may require a separate transaction approval. Declining runs nothing."; Layout.fillWidth: true }
                ConsentCheck { id: actionRequestReviewed; text: "I reviewed the recipient, inputs and spending limit."; Layout.fillWidth: true }
                RowLayout {
                    LogosButton { text: "Decline"; Accessible.name: "Decline requested action"; enabled: !root.pending && root.backend && !root.backend.chatBusy; onClicked: root.confirmRequestedAction(false) }
                    LogosButton { text: "Approve this action"; Accessible.name: "Approve exact requested action"; enabled: actionRequestReviewed.checked && !root.pending && root.backend && !root.backend.chatBusy; onClicked: root.confirmRequestedAction(true) }
                }
            }
        }
    }
    ThemedDialog {
        id: chatPermissionDialog
        confirmLabel: "Authorize and send"
        objectName: "commons_relay.chatReview"
        title: "Review agent permissions"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 48, 620)
        standardButtons: Dialog.Ok | Dialog.Cancel
        palette.window: Theme.palette.surfaceRaised
        palette.windowText: Theme.palette.text
        palette.button: Theme.palette.backgroundSecondary
        palette.buttonText: Theme.palette.text
        contentItem: ColumnLayout {
            spacing: Theme.spacing.medium
            Heading { text: root.reviewedChat.label || "Selected agent" }
            Copy { text: root.reviewedChat.message || ""; Layout.fillWidth: true; maximumLineCount: 5; elide: Text.ElideRight }
            Copy { text: "Authorize this message for up to 8 tool steps and " + (root.reviewedChat.amount || "0") + " testnet units. Existing transaction limits still apply. The agent cannot approve its own spending."; Layout.fillWidth: true }
            Caption { text: "The message, recent conversation and requested tool results go to " + root.providerLabel + ". No private signing keys are sent. Model charges use a separate, estimated budget."; Layout.fillWidth: true }
            Caption { text: "An uncertain model request is not automatically retried."; Layout.fillWidth: true }
        }
        onAccepted: root.submitReviewedChat()
        onRejected: root.reviewedChat = ({})
    }
    ThemedDialog {
        id: detailsDialog
        property bool showDetails: false
        onOpened: showDetails = false
        objectName: "commons_relay.taskDetails"
        title: "Task details"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 48, 690)
        height: Math.min(root.height - 48, 620)
        standardButtons: Dialog.Close
        palette.window: Theme.palette.surfaceRaised
        palette.windowText: Theme.palette.text
        contentItem: ScrollView {
            id: detailScroll
            contentWidth: availableWidth
            ColumnLayout {
                width: detailScroll.availableWidth
                spacing: Theme.spacing.medium
                Heading { text: root.task.id ? root.skillTitle(root.task.skill) : "Reading the current task..."; Layout.fillWidth: true }
                Copy { text: root.stateLabel(root.task.state || ""); color: root.taskColor(root.task.state); Layout.fillWidth: true }
                Copy { visible: root.resultSummary(root.task).length > 0; text: root.resultSummary(root.task); Layout.fillWidth: true }
                Copy { visible: !!root.task.id; text: "Maximum authorized: " + (root.task.maximum_spend || "0") + " testnet units"; Layout.fillWidth: true }
                Repeater {
                    model: (root.task.state !== "completed" || detailsDialog.showDetails) && root.task.arguments_complete ? Object.keys(root.task.arguments || {}) : []
                    delegate: ColumnLayout {
                        required property string modelData
                        Layout.fillWidth: true
                        Caption { text: root.label(modelData) }
                        Copy { text: typeof root.task.arguments[modelData] === "string" ? root.task.arguments[modelData] : JSON.stringify(root.task.arguments[modelData], null, 2); Layout.fillWidth: true }
                    }
                }
                Caption { visible: root.task.arguments_complete === false; text: "The full arguments cannot be displayed safely here. Approval is disabled; inspect the complete local record first."; color: Theme.palette.warning; Layout.fillWidth: true }
                Caption { visible: !!root.task.error; text: root.taskError(root.task.error); color: Theme.palette.error; Layout.fillWidth: true }
                LogosButton {
                    text: detailsDialog.showDetails ? "Hide technical details" : "Show technical details"
                    Accessible.name: "Toggle task technical details"
                    visible: root.task.state === "completed" || !!root.task.result_preview
                    onClicked: detailsDialog.showDetails = !detailsDialog.showDetails
                }
                Caption { visible: root.task.state === "completed" && root.task.result_complete === false; text: "The result is too large for this view. Technical details contain a labelled preview, not the full result."; Layout.fillWidth: true }
                Caption { visible: detailsDialog.showDetails && !!root.task.result_preview && root.task.result_preview !== "null"; text: root.task.result_complete ? "Recorded result" : "Result preview (not complete)" }
                CodeText { visible: detailsDialog.showDetails && !!root.task.result_preview && root.task.result_preview !== "null"; readOnly: true; text: root.task.result_preview || ""; Layout.fillWidth: true; Layout.preferredHeight: 170 }
                RowLayout {
                    LogosButton { text: "Refresh details"; enabled: !!root.task.id && !root.pending; onClicked: root.call(root.backend.requestTask(root.task.id)) }
                    LogosButton { text: "Request cancellation"; visible: ["submitted", "input-required", "working", "unknown"].indexOf(root.task.state) >= 0; enabled: !root.pending; onClicked: root.reviewAction("cancel") }
                    LogosButton { text: "Review approval"; visible: root.task.state === "input-required"; enabled: root.task.arguments_complete === true && !root.pending; onClicked: root.reviewAction("approve") }
                }
                Caption { visible: !!root.task.id; text: "Task reference: " + root.task.id; Layout.fillWidth: true }
            }
        }
    }
    ThemedDialog {
        id: reviewDialog
        confirmLabel: root.reviewKind === "approve" ? "Approve exact action" : root.reviewKind === "cancel" ? "Request cancellation" : "Send task"
        objectName: "commons_relay.actionReview"
        title: root.reviewKind === "approve" ? "Approve this exact action?" : root.reviewKind === "cancel" ? "Request cancellation?" : "Review your task"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 48, 630)
        height: Math.min(root.height - 48, 500)
        standardButtons: Dialog.Ok | Dialog.Cancel
        palette.window: Theme.palette.surfaceRaised
        palette.windowText: Theme.palette.text
        contentItem: ScrollView {
            id: reviewScroll
            contentWidth: availableWidth
            ColumnLayout {
                width: reviewScroll.availableWidth
                spacing: Theme.spacing.medium
                Heading { text: root.selectedLabel }
                Copy { text: root.skillTitle(root.reviewed.skill); Layout.fillWidth: true }
                Caption { text: root.reviewKind === "approve" ? "Your signature applies only to these exact arguments and this policy version." : root.reviewKind === "cancel" ? "A transaction already accepted by the network cannot be undone by cancellation." : "This signs the exact request locally. The agent still checks policy before taking action."; Layout.fillWidth: true }
                Repeater {
                    model: Object.keys(root.reviewed.arguments || {})
                    delegate: ColumnLayout {
                        required property string modelData
                        Layout.fillWidth: true
                        Caption { text: root.label(modelData) }
                        Copy { text: typeof root.reviewed.arguments[modelData] === "string" ? root.reviewed.arguments[modelData] : JSON.stringify(root.reviewed.arguments[modelData], null, 2); Layout.fillWidth: true }
                    }
                }
                Copy { visible: root.reviewKind === "approve"; text: "Maximum: " + (root.reviewed.maximum_spend || "0") + " testnet units"; Layout.fillWidth: true }
            }
        }
        onAccepted: root.confirmReview()
        onRejected: { root.reviewKind = ""; root.reviewed = ({}) }
    }
    ThemedDialog {
        id: inferenceDialog
        objectName: "commons_relay.inferenceSettings"
        property string profileAtOpen: ""
        property string hashAtOpen: ""
        property string errorText: ""
        property bool saveAttempted: false
        title: "Inference settings"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 40, 690)
        height: Math.min(root.height - 40, 710)
        standardButtons: Dialog.Close
        onClosed: inferenceKey.text = ""
        contentItem: ScrollView {
            id: inferenceScroll; contentWidth: availableWidth
            ColumnLayout {
                width: inferenceScroll.availableWidth; spacing: 10
                Copy { Layout.fillWidth: true; text: "Choose where this agent sends model requests. Saving changes settings only; it does not send a conversation to the model." }
                Caption { Layout.fillWidth: true; visible: !root.backend || !root.backend.remoteReady; text: "Connect to an agent to load and save its settings. You can still edit a draft here."; color: Theme.palette.warning }
                Caption { text: "API format" }
                ThemedPicker { id: inferenceApi; onActivated: inferenceReview.checked = false; Layout.fillWidth: true; model: ["Responses API", "OpenAI-compatible Chat Completions"]; Accessible.name: "Inference API format" }
                Caption { text: "API base URL" }
                Field { id: inferenceEndpoint; onTextChanged: inferenceReview.checked = false; Layout.fillWidth: true; placeholderText: "https://your-provider.example/v1"; Accessible.name: "Inference endpoint" }
                Caption { Layout.fillWidth: true; text: "Use HTTPS for remote servers. HTTP localhost addresses refer to the machine running the agent, not necessarily this laptop." }
                Caption { text: "Model ID" }
                Field { id: inferenceModel; onTextChanged: inferenceReview.checked = false; Layout.fillWidth: true; placeholderText: "Model name supplied by your provider"; Accessible.name: "Inference model" }
                Caption { text: "API credential" }
                ThemedPicker { id: inferenceCredential; Layout.fillWidth: true; model: ["Keep key for this same endpoint", "Set a new API key", "No API key (for example, a local server)"]; Accessible.name: "Inference credential mode"; onActivated: { inferenceKey.text = ""; inferenceReview.checked = false } }
                Field { id: inferenceKey; onTextChanged: inferenceReview.checked = false; visible: inferenceCredential.currentIndex === 1; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: "New endpoint API key"; Accessible.name: "New inference API key" }
                Caption { Layout.fillWidth: true; text: "A new key is sealed to this agent before it enters the message channel, then stored privately on the agent. Existing keys are never silently sent to another endpoint." }
                Caption { text: "Maximum output tokens per request" }
                Field { id: inferenceOutput; onTextChanged: inferenceReview.checked = false; Layout.fillWidth: true; Accessible.name: "Inference output token limit" }
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout { Layout.fillWidth: true; Caption { text: "Input USD per million tokens" } Field { id: inferenceInputPrice; onTextChanged: inferenceReview.checked = false; Layout.fillWidth: true; Accessible.name: "Inference input price estimate" } }
                    ColumnLayout { Layout.fillWidth: true; Caption { text: "Output USD per million tokens" } Field { id: inferenceOutputPrice; onTextChanged: inferenceReview.checked = false; Layout.fillWidth: true; Accessible.name: "Inference output price estimate" } }
                }
                Caption { Layout.fillWidth: true; text: "These price estimates protect the existing model budget; they are not provider billing guarantees. Use the provider's own spending controls as well. Use zero only for genuinely free inference." }
                ConsentCheck { id: inferenceReview; Layout.fillWidth: true; text: "I reviewed the endpoint, model and price estimates above."; onCheckedChanged: inferenceDialog.errorText = "" }
                Copy { Layout.fillWidth: true; visible: !!inferenceDialog.errorText; color: Theme.palette.error; text: inferenceDialog.errorText }
                Caption { Layout.fillWidth: true; visible: inferenceDialog.saveAttempted; text: root.backend ? root.backend.statusText : ""; Accessible.name: text }
                LogosButton { text: "Save inference settings"; Accessible.name: "Save inference settings"; enabled: root.backend && root.backend.remoteReady && !root.pending && !root.backend.chatBusy && inferenceReview.checked; onClicked: root.saveInferenceSettings() }
            }
        }
    }
    ThemedDialog {
        id: connectionDialog
        title: "Connection settings"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 48, 620)
        standardButtons: Dialog.Close
        palette.window: Theme.palette.surfaceRaised
        palette.windowText: Theme.palette.text
        contentItem: ColumnLayout {
            spacing: Theme.spacing.medium
            Copy { text: "The local owner transport connects Basecamp to your agents over Logos Messaging. Owner keys and agent wallet keys are kept separately."; Layout.fillWidth: true }
            Caption { text: "Advanced: local owner-transport profile" }
            Field { id: transportProfile; Accessible.name: "Local Relay transport profile"; placeholderText: "Configured profile directory"; Layout.fillWidth: true }
            RowLayout {
                LogosButton { text: "Connect profile"; enabled: root.ready && transportProfile.text.length > 0 && !root.pending; onClicked: root.call(root.backend.configure(transportProfile.text)) }
                LogosButton { text: "Reload agent list"; enabled: root.ready && !root.pending; onClicked: root.call(root.backend.loadOwnerProfiles()) }
            }
            Caption { text: "Use Inference settings to choose the model and its endpoint. Never paste API keys, seed phrases or signing keys into a conversation."; Layout.fillWidth: true }
        }
    }
    ThemedDialog {
        id: helpDialog
        objectName: "commons_relay.tutorial"
        title: "Start here"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 48, 710)
        height: Math.min(root.height - 48, 650)
        standardButtons: Dialog.Close
        palette.window: Theme.palette.surfaceRaised
        palette.windowText: Theme.palette.text
        contentItem: ScrollView {
            id: helpScroll
            contentWidth: availableWidth
            ColumnLayout {
                width: helpScroll.availableWidth
                spacing: Theme.spacing.large
                Heading { text: "Relay is your agent control panel"; Layout.fillWidth: true }
                Copy { text: "The three names identify independent demo deployments required by LP-0008, not three hardcoded kinds of intelligence. Any instance can use its permitted Storage, Messaging, Blockchain and custom tools. Choose one for ordinary work; use another only when you need a separate identity or a collaborating peer. The model plans, while the permission engine checks each action."; Layout.fillWidth: true }
                Heading { text: "1. Choose an agent" }
                Copy { text: "Choose an instance in the Agent selector. Wait for the authenticated connection and Chat ready indicator. Its available tools come from its live registry, including installed custom skills. Each instance keeps its own history and permissions."; Layout.fillWidth: true }
                Heading { text: "2. Ask in ordinary language" }
                Copy { text: "Start with 'What can you help me with?' or 'List my stored files.' Read and accept the selected provider's data-sharing notice, then send. Chat is read-only by default."; Layout.fillWidth: true }
                Heading { text: "3. Review actions and results" }
                Copy { text: "For an upload, a message or a payment, enable Allow actions for this message and review the exact spending limit. A larger or restricted action appears in Activity for your separate approval. Activity shows the agent's recorded result, not just a model claim."; Layout.fillWidth: true }
                Heading { text: "Model costs are not testnet tokens" }
                Copy { text: "The model provider receives your prompt, recent conversation and requested tool results. Wallet and owner signing keys are not sent. The displayed model budget uses configured price estimates; request and token limits also apply. Testnet tokens are only demonstration funds."; Layout.fillWidth: true }
                Heading { text: "What happens after an interruption?" }
                Copy { text: "A stopped or interrupted conversation is not automatically billed again. Existing task records remain. An uncertain payment is checked before any retry; it is not sent again just because the screen refreshed."; Layout.fillWidth: true }
                Heading { text: "Where does Commons fit?" }
                Copy { text: "Commons for Logos is the other app in this Basecamp window. It handles private membership registration and group approvals. It is not the agent chat. Use its own guide for membership passes and shared decisions."; Layout.fillWidth: true }
            }
        }
    }
}
