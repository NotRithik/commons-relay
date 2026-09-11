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
    readonly property var activeProfiles: {
        const current = profiles.filter(function(item) { return !item.archived })
        return current.length ? current : profiles
    }
    readonly property var summary: parse(backend ? backend.summaryJson : "{}", {})
    readonly property var tasks: parse(backend ? backend.tasksJson : "[]", [])
    readonly property var peerMessages: summary.peer_messages || []
    readonly property var remoteHealth: parse(backend ? backend.remoteHealthJson : "{}", {})
    readonly property var chatTasks: {
        const merged = ({})
        for (const task of root.tasks) merged[task.id] = task
        for (const task of (root.remoteHealth.recent_task_updates || [])) merged[task.id] = Object.assign({}, merged[task.id] || {}, task)
        for (const task of (root.remoteHealth.tasks || [])) merged[task.id] = Object.assign({}, merged[task.id] || {}, task)
        return Object.keys(merged).map(function(id) { return merged[id] })
    }
    readonly property var activityTasks: root.chatTasks.slice().sort(function(a, b) {
    const rank = function(task) {
        const state = (task && task.state) || ""
        if (state === "input-required") return 0
        if (state === "submitted" || state === "working" || state === "unknown") return 1
        return 2
    }
    const diff = rank(a) - rank(b)
    if (diff !== 0) return diff
    return Number(b.created || b.updated || 0) - Number(a.created || a.updated || 0)
})
    StableRows { id: activityRows; items: root.activityTasks }
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
    readonly property bool dockSettings: width >= 1080
    property bool settingsOpen: false
    property bool showTechnical: false
    property bool manualTools: false
    property string selectedSkillId: ""
    property bool applyingSkill: false
    property var fields: []
    property var formValues: ({})
    property var reviewed: ({})
    property var reviewedChat: ({})
    property string reviewKind: ""
    property string requestedTaskId: ""
    property string viewError: ""
    property string pendingDraft: ""
    property string pendingDraftAgent: ""
    property var draftByProfile: ({})
    property string draftProfile: ""
    property string permissionGoalRequested: ""
    property string permissionProfile: ""
    property string permissionAgent: ""
    property var permissionReview: ({})
    property int nowSeconds: Math.floor(Date.now() / 1000)

    Timer {
        interval: 1000
        running: !!(root.backend && root.backend.chatBusy)
        repeat: true
        onTriggered: root.nowSeconds = Math.floor(Date.now() / 1000)
    }

    function parse(value, fallback) {
        try { return JSON.parse(value) } catch (_) { return fallback }
    }
    function call(reply) {
        if (logos && logos.watch)
            logos.watch(reply, function() {}, function(error) { root.viewError = String(error) })
    }
    function label(name) {
        const names = { path: "File path on the agent", binary_path: "Program file on the agent",
            payment_mode: "Payment privacy", recipient: "Recipient", amount: "Amount in testnet base units", label: "File label",
            address: "Stored file reference", message: "Message", members: "Group members",
            group_id: "Group", program_id: "Program ID", instruction: "Encoded instruction",
            params: "Inputs", agent_address: "Other agent", skill: "Service or task", task_id: "Task to follow",
            topic: "Discovery topic", key: "Setting", value: "Value" }
        return names[name] || name.replace(/_/g, " ")
    }
    function elapsedLabel(seconds) {
        const value = Math.max(0, Number(seconds) || 0)
        return value < 60 ? Math.floor(value) + "s" : value < 3600 ? Math.floor(value / 60) + "m " + Math.floor(value % 60) + "s" : Math.floor(value / 3600) + "h " + Math.floor(value % 3600 / 60) + "m"
    }
    function liveTask(value) {
        if (!value || !value.id) return value || ({})
        const recent = (root.remoteHealth.recent_task_updates || []).find(function(item) { return item.id === value.id })
        const active = (root.remoteHealth.tasks || []).find(function(item) { return item.id === value.id })
        const merged = Object.assign({}, value, recent || {}, active || {})
        if (!root.taskIsLive(merged) && merged.progress)
            merged.progress = null
        return merged
    }
    function taskIsLive(task) {
        return ["submitted", "working", "unknown", "input-required"].indexOf((task && task.state) || "") >= 0
    }
    function stateLabel(state) {
        const names = { submitted: "Waiting to start", working: "Working", "input-required": "Needs your approval",
            unknown: "Checking what happened", completed: "Completed", failed: "Failed",
            rejected: "Not started", canceled: "Canceled" }
        return names[state] || state
    }
    function stepLabel(stage) {
        const names = {
            "peer-preflight": "Checking the other agent",
            "peer-ready": "Other agent replied",
            "peer-unresponsive": "Other agent did not answer",
            "remote-handshake": "Sending the task",
            "remote-handshake-timeout": "Other agent did not accept in time",
            "remote-execution": "Waiting for the other agent",
            "remote-result-pending": "Waiting for the latest result",
            "remote-failed": "Other agent could not finish",
            "payment-proving": "Preparing the private payment",
            "payment-prepared": "Payment ready to send",
            "payment-broadcasting": "Sending the payment",
            "payment-confirmation": "Waiting for payment confirmation",
            completed: "Complete"
        }
        return names[stage] || String(stage || "").replace(/-/g, " ")
    }
    function progressText(task) {
        if (!task || !task.progress || !root.taskIsLive(task)) return ""
        const stage = task.progress.stage || ""
        const names = {
            "peer-preflight": "Checking that the other agent is online before doing any paid work.",
            "peer-ready": task.skill === "agent.ping" ? "The other agent replied." : "The other agent replied. Sending the task now.",
            "peer-unresponsive": "The other agent did not answer within 8 seconds. Nothing was paid.",
            "remote-handshake": "The other agent is online. Asking it to accept this task.",
            "remote-handshake-timeout": "The other agent replied but did not accept the task in time. Nothing was paid.",
            "remote-execution": "The other agent accepted the task. Waiting for its result.",
            "remote-result-pending": "Waiting for the other agent’s latest result.",
            "remote-failed": "The other agent could not finish the task.",
            "payment-proving": "Building a private payment proof on this device. This is local math and often takes 15 to 45 minutes. Nothing has been sent yet.",
            "payment-prepared": "The approved payment is ready to send.",
            "payment-broadcasting": "Sending the approved payment now.",
            "payment-confirmation": "Waiting for the network to confirm the payment.",
            completed: task.skill === "agent.ping" ? "The other agent replied." : "The other agent finished the task."
        }
        if (names[stage]) {
            const age = task.progress_age_seconds
            if (stage === "payment-proving" && age !== undefined)
                return names[stage] + " Been working for " + root.elapsedLabel(age) + "."
            return names[stage]
        }
        return String(task.progress.detail || "")
            .replace(/downstream agent/gi, "other agent")
            .replace(/preflight/gi, "online check")
            .replace(/remote task handshake/gi, "task request")
            .replace(/authenticated result/gi, "result")
    }
    function chatReply(text) {
        return String(text || "")
            .replace("Live agent status (no model call or new task):", "Here’s what your agent was doing at that moment:")
            .replace("No pending tasks are recorded.", "Nothing was waiting or running.")
            .replace("A heartbeat means the agent responds; it does not mean a payment or task completed.", "The agent was responding. A task could still have been waiting on another agent or the network.")
            .replace(/\*\*([^*]+)\*\*/g, "$1")
            .replace(/__([^_]+)__/g, "$1")
            .replace(/`([^`]+)`/g, "$1")
            .replace(/^#{1,6}\s+/gm, "")
    }
    function showLatestMessage() {
        chatTranscript.goToLatest()
    }
    function chatState(state) {
        const names = { queued: "Sending your message", thinking: "Waiting on the model", working: "Using tools", completed: "Complete",
            waiting: "Waiting on a task or approval", failed: "Could not complete", cancelled: "Stopped",
            interrupted: "Stopped unexpectedly — not retried" }
        return names[state] || state
    }
    function liveGoal() {
        const rows = Array.isArray(root.conversation) ? root.conversation : []
        const id = root.backend ? root.backend.activeGoalId : ""
        if (id) {
            for (let i = 0; i < rows.length; ++i)
                if (rows[i] && rows[i].id === id) return rows[i]
        }
        for (let i = 0; i < rows.length; ++i) {
            const state = rows[i] && rows[i].state
            if (["queued", "thinking", "working", "waiting"].indexOf(state) >= 0) return rows[i]
        }
        return null
    }
    function liveGoalAgeSeconds() {
        const goal = root.liveGoal()
        if (!goal) return 0
        return Math.max(0, root.nowSeconds - Number(goal.updated || goal.created || 0))
    }
    function chatProgressLine() {
        if (!root.backend || !root.backend.chatBusy) return ""
        const goal = root.liveGoal()
        const model = (root.planner && root.planner.model) || "the model"
        const wait = root.liveGoalAgeSeconds() > 0 ? " " + root.elapsedLabel(root.liveGoalAgeSeconds()) + " so far." : ""
        const state = goal ? goal.state : ""
        if (state === "working")
            return "The model is using tools." + wait + " Press Stop to cancel."
        if (state === "waiting")
            return "This message needs your approval or a finishing task. Do not send it again."
        return "Waiting on " + model + "." + wait + " This can take a minute. Press Stop to cancel. No extra model request is sent."
    }
    function composerPlaceholder() {
        if (root.backend && root.backend.chatBusy)
            return "This reply is still running. Press Stop to cancel it, then send a new message."
        if (root.backend && root.backend.remoteReady)
            return "Ask a question or describe what you want to do..."
        return "Write a draft here. Connect an agent before sending."
    }
    function sendButtonLabel() {
        if (!root.backend || !root.backend.chatBusy) return "Send"
        const state = root.liveGoal() ? root.liveGoal().state : ""
        if (state === "working") return "Using tools"
        if (state === "waiting") return "Needs you"
        return "Waiting on model"
    }
    function agentHealthTitle() {
        if (!root.backend || !root.backend.selectedAgent) return "Choose an agent"
        if (root.backend.chatBusy) {
            const state = root.liveGoal() ? root.liveGoal().state : ""
            if (state === "working") return "Busy — using tools"
            if (state === "waiting") return "Busy — needs your approval"
            return "Busy — waiting on the model"
        }
        const status = root.remoteHealth.connection_state
        if (status === "unresponsive") return "Agent not responding"
        if (status === "online") return "Agent online"
        if (status === "delayed") return "Reply delayed"
        return "Checking connection..."
    }
    function agentHealthDetail() {
        if (root.backend && root.backend.chatBusy && root.remoteHealth.connection_state === "unresponsive")
            return "Health ping is stale because this message still has the agent. That is not a dropped chat by itself."
        if (root.remoteHealth.last_reply_age_ms >= 0)
            return "Last health ping " + Math.floor(root.remoteHealth.last_reply_age_ms / 1000) + "s ago · response time " + (root.remoteHealth.round_trip_ms / 1000).toFixed(1) + "s"
        return "Waiting for a fresh encrypted reply."
    }
    function healthDotColor() {
        if (root.backend && root.backend.chatBusy) return Theme.palette.warning
        if (root.remoteHealth.connection_state === "unresponsive") return Theme.palette.error
        if (root.backend && root.backend.remoteReady) return Theme.palette.success
        return Theme.palette.warning
    }
    function footerStatus() {
        if (root.viewError) return root.viewError
        if (Number(root.summary.approval_count || 0) > 0) return "An action is waiting for your approval. Open Activity to review it."
        if (root.backend && root.backend.chatBusy) return root.chatProgressLine()
        return root.backend ? root.backend.statusText : "Loading Relay..."
    }
    function chatWaitBody(turn) {
        if (!turn) return ""
        const model = (root.planner && root.planner.model) || "the model"
        const age = Math.max(0, root.nowSeconds - Number(turn.updated || turn.created || 0))
        const wait = age > 0 ? " " + root.elapsedLabel(age) + " so far." : ""
        if (turn.state === "queued") return "Your message is signed and waiting to start." + wait
        if (turn.state === "thinking") return "Waiting on " + model + ". No tools have been used yet." + wait + " Press Stop to cancel."
        if (turn.state === "working") return "The model asked for a tool. Live progress is attached below." + wait
        return ""
    }
    function selectedCurrentSkill() {
        const id = root.selectedSkillId || (skillPicker.currentIndex >= 0 ? skillPicker.currentValue : "")
        const selected = root.skills.find(function(item) { return item.id === id })
        if (!selected || root.skillDetails.id !== selected.id || !root.skillDetails.input_schema)
            throw new Error("Wait for this tool’s options to load.")
        return root.skillDetails
    }
    function openManualSkill(skillId) {
        root.applyingSkill = true
        root.selectedSkillId = skillId
        root.manualTools = true
        root.fields = []
        root.formValues = ({})
        root.viewError = ""
        if (root.backend)
            root.call(root.backend.requestSkill(skillId))
        Qt.callLater(function() { root.applyingSkill = false })
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
        const builtIn = {
            "storage.upload":"Encrypt and store a file", "storage.download":"Download and decrypt a stored file",
            "storage.list":"List files this agent has stored", "storage.share":"Share a stored file with someone",
            "messaging.send":"Send an encrypted Logos message", "messaging.inbox":"View recent messages from other agents",
            "messaging.join":"Join a Logos group", "messaging.create_group":"Create a Logos group",
            "wallet.balance":"Check this agent’s balance", "wallet.send":"Send testnet tokens within your limits",
            "wallet.history":"View recent wallet activity", "program.query":"Check a program’s current state",
            "program.call":"Run an approved action on a LEZ program", "program.deploy":"Publish a compiled program to LEZ testnet",
            "agent.card":"View this agent’s public capabilities", "agent.discover":"Find other agents and what they offer",
            "agent.ping":"Check whether another agent is online", "agent.task":"Ask another agent to do a task at its listed price",
            "agent.subscribe":"Follow another agent’s task", "agent.cancel":"Ask another agent to stop a task",
            "meta.skills":"View this agent’s tools", "meta.status":"View this agent’s current status",
            "meta.configure":"Change an agent setting with owner approval"
        }
        return builtIn[id] || String(id || "Task").replace(/[._]/g, " ")
    }
    function peerMessageText(message) {
        if (!message) return ""
        if (message.kind === "text") return message.text || "Message received"
        if (message.kind === "group-message") return (message.text || "Group message") + (message.group_id ? "  ·  " + message.group_id : "")
        if (message.kind === "group-invite") return "Invited you to join " + (message.group_id || "a group")
        if (message.kind === "file-share") return "Shared a stored file with you" + (message.address ? "  ·  " + message.address : "")
        return "Message received"
    }
    function taskColor(state) {
        if (state === "completed") return Theme.palette.success
        if (state === "failed" || state === "rejected") return Theme.palette.error
        if (state === "input-required" || state === "unknown") return Theme.palette.warning
        return Theme.palette.textSecondary
    }
    function openTask(id) {
        root.viewError = ""
        root.requestedTaskId = id
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
            AUTHORIZATION_EXPIRED: "Approval expired before sending. Nothing was paid.",
            PREPARATION_FAILED: "The task could not be prepared. Nothing was sent or paid.",
            WALLET_OPERATION_FAILED: "The payment was stopped before anything was sent.",
            OWNER_OR_REQUESTER_CANCELED: "This action was canceled. Nothing was sent.",
            TASK_NOT_CANCELABLE: "This action cannot be canceled now. If a payment was already sent, cancellation will not undo it.",
            SKILL_STRING_PATTERN: "This identifier is not valid. Use the program and account references from the network.",
            REMOTE_INPUT_SCHEMA_REQUIRED: "The other agent has not published the information this task needs.",
            OPENAI_HTTP_400: "The model provider rejected the request format. Check the model and tool configuration before sending a new message; no automatic retry was made.",
            OPENAI_HTTP_401: "The model credential was not accepted. Ask the operator to check the private model configuration; never paste an API key into chat.",
            OPENAI_HTTP_429: "The model provider reported a usage or rate limit. This message was not repeated automatically.",
            GOAL_GRANT_NOT_ACTIVE: "Permission for this conversation was stopped or expired. No new action is authorized.",
            INSUFFICIENT_PUBLIC_BALANCE: "There are not enough testnet units in the sending account.",
            WALLET_BUSY: "Another wallet operation is in progress. Check its result before starting another.",
            WALLET_HAS_UNRECONCILED_OPERATION: "An earlier wallet action still needs to be checked. This request was not paid. Resolve the earlier action before trying again.",
            EFFECT_STATUS_UNKNOWN: "The agent is checking whether the network accepted this action. Do not send a duplicate.",
            DOWNSTREAM_PEER_UNRESPONSIVE: "The other agent did not answer within 8 seconds. Nothing was paid.",
            DOWNSTREAM_TASK_HANDSHAKE_TIMEOUT: "The other agent replied but did not accept the task in time. Nothing was paid.",
            A2A_REMOTE_ERROR_32004: "That task already finished, so there are no more live updates.",
            A2A_REMOTE_ERROR_32002: "That task cannot be canceled now.",
            REMOTE_TASK_NOT_READY_TO_FOLLOW: "The other agent has not accepted that task yet. Try again in a moment.",
            FOLLOW_TARGET_PEER_MISMATCH: "That task belongs to a different agent."
        }
        return messages[code] || String(code || "")
    }
    function resultSummary(value) {
        if (value && typeof value.result_summary === "string" && value.result_summary.length)
            return value.result_summary
        // A submitted request or model sentence is never a completed receipt.
        if (!value || value.state !== "completed"
            || !value.result_preview || value.result_preview === "null") return ""
        const result = parse(value.result_preview, null)
        if (!result || typeof result !== "object" || Array.isArray(result)) return ""
        const decimal = function(x) { return typeof x === "string" && /^(0|[1-9][0-9]{0,38})$/.test(x) }
        const count = function(x) { return typeof x === "number" && isFinite(x) && x >= 0 && Math.floor(x) === x && x <= 9007199254740991 }
        const block = count(result.block) ? " Recorded at block " + result.block + "." : ""
        if (value.skill === "wallet.balance" && decimal(result.balance))
            return "Recorded wallet balance: " + result.balance + " testnet units." + block + ""
        if (value.skill === "storage.list" && Array.isArray(result.files))
            return result.files.length === 0 ? "No saved files were found in this agent’s file vault." : result.files.length + " saved files were found. Open details to see their file references."
        if (value.skill === "storage.upload" && count(result.bytes) && typeof result.address === "string")
            return "Encrypted file stored: " + result.bytes + " bytes.\nContent reference: " + result.address
        if (value.skill === "storage.download" && result.authenticated === true && count(result.bytes) && typeof result.path === "string")
            return "Downloaded and verified: " + result.bytes + " bytes.\nSaved as: " + result.path
        if (value.skill === "messaging.inbox" && Array.isArray(result.messages))
            return result.messages.length === 0 ? "No recent messages from other agents." : result.messages.length + " recent message" + (result.messages.length === 1 ? "." : "s.")
        if (value.skill === "messaging.send" && result.state === "acknowledged")
            return "Message delivered to the recipient."
        if (value.skill === "messaging.create_group" && typeof result.group_id === "string")
            return "Group created and invitations sent.\nGroup: " + result.group_id
        if (value.skill === "messaging.join" && result.joined === true)
            return "Joined the group."
        if (value.skill === "agent.task" && decimal(result.paid_amount) && Array.isArray(result.artifacts)
            && typeof result.provider === "string") {
            if (result.paid_amount !== "0" && !/^[a-f0-9]{64}$/.test(result.payment_transaction || ""))
                return "The task finished, but the payment reference needs a closer look. Open details to inspect it."
            return "Service completed with " + result.artifacts.length + " returned result" + (result.artifacts.length === 1 ? "." : "s.")
                + (result.paid_amount === "0" ? " No payment was needed." : " Paid " + result.paid_amount + " testnet units.")
                + "\nProvider: " + result.provider
                + (result.paid_amount !== "0" ? "\nPayment reference: " + result.payment_transaction : "")
        }
        if (value.skill === "wallet.history" && Array.isArray(result.operations))
            return result.operations.length === 0 ? "No wallet activity is recorded for this agent yet." : result.operations.length + " wallet actions recorded."
        if (value.skill === "agent.discover" && Array.isArray(result.agents))
            return result.agents.length === 0 ? "No other agents were found on that topic." : "Found " + result.agents.length + " other agents."
        if (value.skill === "program.query") return "Program state was read." + block + " This is not the agent's wallet balance."
        if (value.skill === "agent.card") {
            const card = result.card && typeof result.card === "object" ? result.card : result
            const listed = Array.isArray(card.skills) ? card.skills.length : null
            if (listed !== null)
                return "This agent’s public card lists " + listed + " advertised tool" + (listed === 1 ? "." : "s.")
            if (card && (card.name || card.skills))
                return "This agent’s public card was loaded."
        }
        if (value.skill === "meta.status") {
            const wallet = result.wallet && typeof result.wallet === "object" ? result.wallet : result
            const extra = decimal(wallet.balance) ? " Balance " + wallet.balance + " testnet units." : ""
            const storage = result.storage_usage && typeof result.storage_usage === "object" ? result.storage_usage : {}
            const files = count(storage.file_count) ? " " + storage.file_count + " stored files." : ""
            return "Current agent status loaded." + extra + files
        }
        if (value.skill === "agent.subscribe") {
            if (result.already_finished === true)
                return "That task already finished. There are no more live updates."
            if (result.subscribed === true)
                return "Now following that task for updates."
        }
        if (value.skill === "example.text_statistics" && count(result.words) && count(result.characters))
            return "The tool counted " + result.words + " words and " + result.characters + " characters."
        return value.result_complete === false ? "" : "Open full details to read the recorded result."
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
        id: mainLayout
        anchors.fill: parent
        anchors.margins: 16
        anchors.rightMargin: root.dockSettings ? 348 : 16
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                Copy { text: "Kite"; font.pixelSize: root.width < 650 ? 22 : 26; font.weight: Theme.typography.weightMedium }
            }
            Caption { text: "TESTNET"; color: Theme.palette.warning }
            LogosButton { visible: !root.dockSettings; text: "Agent settings"; Accessible.name: "Open agent side panel"; onClicked: root.settingsOpen = !root.settingsOpen }
        }
        LogosTabBar {
            id: tabs
            Layout.fillWidth: true
            LogosTabButton { text: "Chat"; Accessible.name: "Relay chat tab"; width: implicitWidth + Theme.spacing.xlarge }
            LogosTabButton { text: "Activity"; Accessible.name: "Relay activity tab"; width: implicitWidth + Theme.spacing.xlarge }
            LogosTabButton { text: "Skills & tools"; Accessible.name: "Relay skills tab"; width: implicitWidth + Theme.spacing.xlarge }
            LogosTabButton { text: "Services"; Accessible.name: "Relay services tab"; width: implicitWidth + Theme.spacing.xlarge }
        }
        ChatTranscript {
            id: chatTranscript
            visible: tabs.currentIndex === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            entries: root.conversation
            tasks: root.chatTasks
            agentLabel: root.selectedLabel
            hasEarlier: root.backend && root.backend.hasMoreConversation
            loading: root.pending
            describeState: root.chatState
            describeTaskState: root.stateLabel
            describeProgress: root.progressText
            formatReply: root.chatReply
            describeError: root.taskError
            describeWait: root.chatWaitBody
            nowSeconds: root.nowSeconds
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
                Services {
                    visible: tabs.currentIndex === 3
                    Layout.fillWidth: true
                    backend: root.backend
                    dialogHost: root
                    onDispatched: function(reply) { root.call(reply) }
                    onTaskSent: tabs.currentIndex = 1
                }
                Card {
                    visible: tabs.currentIndex === 1
                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: Theme.spacing.medium
                        Heading { text: "Activity and approvals" }
                        Copy { text: "Track current work, review approvals and open completed results."; Layout.fillWidth: true }
                        Caption {
                            text: root.backend && root.backend.remoteReady
                                ? (root.summary.approval_count || 0) + " awaiting approval  /  " + (root.summary.task_count || 0) + " recorded tasks"
                                : "Choose an agent to load its recent activity."
                            Layout.fillWidth: true
                        }
                        Caption {
                            visible: root.backend && root.backend.remoteReady && !!root.summary.policy
                            text: "Automatic payments: up to " + (root.summary.policy ? root.summary.policy.per_transaction : "-")
                                + " testnet units each. " + (root.summary.reserved_and_recent_spend || "0") + " units used or set aside in this period."
                            Layout.fillWidth: true
                        }
                        Caption { visible: root.backend && root.backend.remoteReady && root.tasks.length === 0; text: "No tasks yet. Start in Chat, or use a tool from Skills & tools."; Layout.fillWidth: true }
                    }
                }
                Card {
                    visible: tabs.currentIndex === 1 && root.backend && root.backend.remoteReady && root.peerMessages.length > 0
                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: Theme.spacing.medium
                        Heading { text: "Recent messages" }
                        Copy { text: "Messages and invitations other agents sent to this agent."; Layout.fillWidth: true }
                        Repeater {
                            model: root.peerMessages
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                RowLayout {
                                    Layout.fillWidth: true
                                    Copy { text: modelData.sender_label || modelData.sender || "Another agent"; Layout.fillWidth: true; font.bold: true }
                                    Caption { text: modelData.received ? Qt.formatDateTime(new Date(Number(modelData.received) * 1000), "d MMM, hh:mm") : "" }
                                }
                                Copy { text: root.peerMessageText(modelData); Layout.fillWidth: true }
                            }
                        }
                    }
                }
                Repeater {
                    model: tabs.currentIndex === 1 ? activityRows : null
                    delegate: Card {
                        required property string payloadJson
                        readonly property var modelData: root.parse(payloadJson, {})
                        Layout.fillWidth: true
                        contentItem: RowLayout {
                            spacing: Theme.spacing.large
                            ColumnLayout {
                                Layout.fillWidth: true
                                Copy { text: root.skillTitle(modelData.skill); Layout.fillWidth: true }
                                Caption { text: root.stateLabel(modelData.state); color: root.taskColor(modelData.state) }
                                Copy { visible: !!root.progressText(root.liveTask(modelData)); text: root.progressText(root.liveTask(modelData)); Layout.fillWidth: true }
                                Caption { visible: !!root.progressText(root.liveTask(modelData)); text: root.liveTask(modelData).progress ? "Now: " + root.stepLabel(root.liveTask(modelData).progress.stage) + " · updated " + root.elapsedLabel(root.liveTask(modelData).progress_age_seconds !== undefined ? root.liveTask(modelData).progress_age_seconds : Math.max(0, Math.floor(Date.now()/1000) - Number(root.liveTask(modelData).progress.updated || 0))) + " ago" : ""; Layout.fillWidth: true }
                                Copy { visible: !root.taskIsLive(modelData) && !modelData.error && !!(modelData.result_summary || root.liveTask(modelData).result_summary); text: modelData.result_summary || root.liveTask(modelData).result_summary || ""; Layout.fillWidth: true }
                                Caption { text: modelData.maximum_spend === "0" ? "No testnet tokens used" : "Up to " + modelData.maximum_spend + " testnet units allowed for this task" }
                                Caption { visible: !!modelData.error; text: root.taskError(modelData.error); Layout.fillWidth: true }
                            }
                            LogosButton {
                                text: modelData.state === "input-required" ? "Review approval" : modelData.state === "completed" ? "View result" : (["failed", "rejected", "canceled"].indexOf(modelData.state) >= 0 ? "View details" : "View progress")
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
                        Copy { text: "Browse available tools or run one directly without a model call."; Layout.fillWidth: true }
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
                                LogosButton {
                                    text: "Run this tool"
                                    Accessible.name: "Run " + (modelData.description || modelData.id)
                                    enabled: root.backend && root.backend.remoteReady && !root.pending
                                    onClicked: root.openManualSkill(modelData.id)
                                }
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
                            onTextChanged: {
                                if (root.applyingSkill)
                                    return
                                root.selectedSkillId = ""
                                skillPicker.currentIndex = -1
                                root.fields = []
                                root.formValues = ({})
                            }
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
                            palette.text: Theme.palette.text
                            palette.buttonText: Theme.palette.text
                            palette.base: Theme.palette.backgroundSecondary
                            palette.button: Theme.palette.backgroundSecondary
                            enabled: root.backend && root.backend.remoteReady && !root.pending
                            onActivated: { root.selectedSkillId = currentValue; root.fields = []; root.formValues = ({}); root.viewError = ""; root.call(root.backend.requestSkill(currentValue)) }
                        }
                        Caption { visible: !!root.selectedSkillId && root.skillDetails.id !== root.selectedSkillId; text: "Loading this tool’s options..." }
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
                        LogosButton { text: "Review task"; Accessible.name: "Review agent task"; enabled: root.backend && root.backend.remoteReady && !root.pending && !!root.skillDetails.input_schema && (root.skillDetails.id === root.selectedSkillId || root.skillDetails.id === skillPicker.currentValue); onClicked: root.reviewTask() }
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
                    placeholderText: {
                        const _busy = root.backend && root.backend.chatBusy
                        const _ready = root.backend && root.backend.remoteReady
                        return root.composerPlaceholder()
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Caption { text: allowChatActions.checked ? "Actions allowed for this message" : "Actions need your approval"; Layout.fillWidth: true }
                    LogosButton { visible: root.backend && root.backend.chatBusy; text: "Stop"; Accessible.name: "Stop agent conversation"; onClicked: root.call(root.backend.cancelConversation()) }
                    LogosButton {
                        text: {
                            const _busy = root.backend && root.backend.chatBusy
                            const _conversation = root.conversation
                            const _goalId = root.backend ? root.backend.activeGoalId : ""
                            return root.sendButtonLabel()
                        }
                        Accessible.name: "Send message to agent"
                        variant: LogosButton.Variant.Primary
                        enabled: root.backend && root.backend.remoteReady && root.planner.enabled && !root.backend.chatBusy && !root.pending && chatInput.text.trim().length > 0
                        onClicked: root.sendChat()
                    }
                }

            }
        }
        RowLayout {
            Layout.fillWidth: true
            Caption {
                objectName: "commons_relay.status"
                text: {
                    const _error = root.viewError
                    const _busy = root.backend && root.backend.chatBusy
                    const _tick = root.nowSeconds
                    const _status = root.backend ? root.backend.statusText : ""
                    const _conversation = root.conversation
                    const _model = root.planner.model
                    return root.footerStatus()
                }
                color: root.viewError || (root.backend && root.backend.lastError) ? Theme.palette.error : Theme.palette.textSecondary
                Layout.fillWidth: true
                maximumLineCount: 3
                elide: Text.ElideRight
            }
            LogosButton { text: root.showTechnical ? "Hide details" : "Details"; onClicked: root.showTechnical = !root.showTechnical }
        }
        ScrollView { visible: root.showTechnical; Layout.fillWidth: true; Layout.preferredHeight: 130; CodeText { readOnly: true; text: root.backend ? root.backend.lastResultJson : "{}" } }
    }

    Rectangle {
        anchors.fill: parent; z: 5
        visible: !root.dockSettings && root.settingsOpen
        color: "#88000000"
        MouseArea { anchors.fill: parent; onClicked: root.settingsOpen = false }
    }
    Rectangle {
        id: settingsPanel
        objectName: "commons_relay.settingsPanel"
        z: 6
        visible: root.dockSettings || root.settingsOpen
        anchors.top: parent.top; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 16
        width: Math.min(316, root.width - 32)
        color: Theme.palette.backgroundSecondary
        radius: 14; border.color: Theme.palette.border
        ScrollView {
            id: settingsScroll
            anchors.fill: parent; anchors.margins: 16
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: settingsScroll.availableWidth
                spacing: 14
                RowLayout {
                    Layout.fillWidth: true
                    Heading { text: "Agent settings"; Layout.fillWidth: true }
                    LogosButton { visible: !root.dockSettings; text: "Close"; Accessible.name: "Close agent side panel"; onClicked: root.settingsOpen = false }
                }
                Caption { text: "AGENTS YOU CONTROL" }
                LogosButton {
                    text: "Create an agent"; Accessible.name: "Create a new local agent"
                    enabled: root.backend && !root.backend.chatBusy
                    onClicked: agentSetupDialog.open()
                }
                ThemedPicker {
                    id: agentPicker
                    objectName: "commons_relay.ownerProfile"
                    Accessible.name: "Agent owner profile"
                    Layout.fillWidth: true; implicitHeight: 44
                    model: root.activeProfiles; textRole: "label"; currentIndex: -1
                    displayText: root.selectedLabel
                    enabled: root.ready && root.backend && root.backend.connected && !root.backend.chatBusy && !root.pending
                    font.family: Theme.typography.publicSans
                    palette.text: Theme.palette.text; palette.buttonText: Theme.palette.text
                    palette.base: Theme.palette.backgroundSecondary; palette.button: Theme.palette.backgroundSecondary
                    palette.highlight: Theme.palette.overlayOrange
                    onActivated: root.call(root.backend.selectOwnerProfile(root.activeProfiles[currentIndex].name))
                }
                Caption {
                    text: "Your saved owner connections. Finding a public service does not give you control of its agent."
                    Layout.fillWidth: true
                }
                LogosButton {
                    text: "Find a service"; Accessible.name: "Find services from other agents"
                    onClicked: tabs.currentIndex = 3
                }
                RowLayout {
                    Layout.fillWidth: true
                    Rectangle {
                        implicitWidth: 7; implicitHeight: 7; radius: 4
                        color: {
                            const _busy = root.backend && root.backend.chatBusy
                            const _ready = root.backend && root.backend.remoteReady
                            const _state = root.remoteHealth.connection_state
                            return root.healthDotColor()
                        }
                    }
                    Copy {
                        Layout.fillWidth: true
                        text: {
                            const _busy = root.backend && root.backend.chatBusy
                            const _agent = root.backend ? root.backend.selectedAgent : ""
                            const _state = root.remoteHealth.connection_state
                            const _conversation = root.conversation
                            const _goalId = root.backend ? root.backend.activeGoalId : ""
                            return root.agentHealthTitle()
                        }
                    }
                }
                Caption {
                    Layout.fillWidth: true
                    text: {
                        const _busy = root.backend && root.backend.chatBusy
                        const _state = root.remoteHealth.connection_state
                        const _age = root.remoteHealth.last_reply_age_ms
                        return root.agentHealthDetail()
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    LogosButton { text: "Refresh"; Accessible.name: "Refresh selected agent"; enabled: root.backend && root.backend.selectedProfile.length > 0 && !root.pending; onClicked: { root.call(root.backend.refreshAgent()); root.call(root.backend.refreshPlanner()); root.call(root.backend.loadConversation()) } }
                    LogosButton { text: "Connection"; Accessible.name: "Relay connection settings"; onClicked: connectionDialog.open() }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.palette.border }
                Caption { text: "MODEL" }
                Copy { text: root.planner.enabled ? root.planner.model : "Not configured"; Layout.fillWidth: true }
                Caption { text: root.planner.enabled ? root.providerLabel : "Choose an endpoint and model to chat."; Layout.fillWidth: true }
                LogosButton { text: "Model settings"; Accessible.name: "Open inference settings"; enabled: !root.backend || !root.backend.chatBusy; onClicked: root.openInferenceSettings() }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.palette.border }
                Caption { text: "NEXT MESSAGE" }
                ConsentCheck { id: allowChatActions; text: "Allow actions for this message"; Layout.fillWidth: true; palette.text: Theme.palette.text; enabled: root.backend && !root.backend.chatBusy }
                Caption { text: "Off by default. The agent can ask you to approve a specific action instead."; Layout.fillWidth: true }
                Caption { visible: allowChatActions.checked; text: "Maximum testnet units"; Layout.fillWidth: true }
                Field { id: chatSpend; visible: allowChatActions.checked; Accessible.name: "Conversation testnet spending limit"; Layout.fillWidth: true; text: "0"; enabled: root.backend && !root.backend.chatBusy }
                Caption { text: root.summary.policy ? "Automatic payments: up to " + root.summary.policy.per_transaction + " testnet units each." : "Limits load after the agent connects."; Layout.fillWidth: true }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.palette.border }
                Caption { text: "CURRENT WORK"; visible: (root.remoteHealth.tasks || []).length > 0 }
                Repeater {
                    model: (root.remoteHealth.tasks || []).slice(0, 3)
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Copy { text: root.skillTitle(modelData.skill); Layout.fillWidth: true }
                        Caption { text: modelData.detail; Layout.fillWidth: true }
                        Caption { text: "Elapsed " + root.elapsedLabel(modelData.age_seconds); Layout.fillWidth: true }
                        LogosButton { text: "View task"; onClicked: root.openTask(modelData.id) }
                    }
                }
                LogosButton { text: "How to use Relay"; Accessible.name: "Open Relay tutorial"; onClicked: helpDialog.open() }
                Item { Layout.preferredHeight: 4 }
            }
        }
    }

    ThemedDialog {
        id: agentSetupDialog
        title: "Create an agent"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width - 32, 640)
        height: Math.min(root.height - 32, 620)
        standardButtons: Dialog.Close
        property var job: root.parse(root.backend ? root.backend.agentSetupJson : "{}", {})
        property bool running: job.state === "queued" || job.state === "creating"
        onOpened: if (root.backend) root.call(root.backend.refreshAgentSetup(""))
        contentItem: ScrollView {
            id: setupScroll
            contentWidth: availableWidth
            ColumnLayout {
                width: setupScroll.availableWidth
                spacing: 12
                Copy { text: "Run your own agent on this computer. It gets its own wallet and identity; your owner keys stay here."; Layout.fillWidth: true }
                Caption { text: "Agent name" }
                Field { id: newAgentName; placeholderText: "For example, Research assistant"; maximumLength: 80; enabled: !agentSetupDialog.running; Layout.fillWidth: true; Accessible.name: "New agent name" }
                Copy { text: "Starts with no funds, no model, and no public listing. Creating it does not call an AI or send tokens. Configure its model and offer selected services afterward."; Layout.fillWidth: true }
                LogosButton { text: "Review setup"; enabled: !!root.backend && !root.pending && !agentSetupDialog.running && agentSetupDialog.job.state !== "needs_attention" && newAgentName.text.trim().length > 0; onClicked: root.call(root.backend.previewAgentSetup(newAgentName.text)) }
                Copy { visible: !!agentSetupDialog.job.message; text: agentSetupDialog.job.message || ""; Layout.fillWidth: true }
                Copy { visible: agentSetupDialog.job.state === "review"; text: agentSetupDialog.job.resume_existing ? "Resume " + agentSetupDialog.job.name + " with its existing wallet and identity? No replacement will be created." : "Create " + (agentSetupDialog.job.name || "") + " on this computer? No funds or API credentials will be copied from existing agents."; Layout.fillWidth: true }
                LogosButton { text: agentSetupDialog.job.resume_existing ? "Resume this agent" : "Create this agent"; Accessible.name: "Start reviewed local agent setup"; visible: agentSetupDialog.job.state === "review"; enabled: !root.pending; onClicked: root.call(root.backend.startAgentSetup(agentSetupDialog.job.id, agentSetupDialog.job.review_hash)) }
                LogosButton { text: "Review recovery"; Accessible.name: "Review recovery of existing agent"; visible: agentSetupDialog.job.state === "needs_attention"; enabled: !root.pending; onClicked: root.call(root.backend.reviewAgentRecovery(agentSetupDialog.job.id)) }
                LogosButton { text: "Check setup"; visible: !!agentSetupDialog.job.id && agentSetupDialog.job.state !== "review"; enabled: !root.pending; onClicked: root.call(root.backend.refreshAgentSetup(agentSetupDialog.job.id)) }
                LogosButton {
                    text: "Connect to " + (agentSetupDialog.job.name || "your new agent")
                    Accessible.name: "Connect to the created agent"
                    visible: agentSetupDialog.job.state === "ready"
                    enabled: !root.pending && root.activeProfiles.some(function(p) { return p.name === agentSetupDialog.job.profile_name })
                    onClicked: { root.call(root.backend.selectOwnerProfile(agentSetupDialog.job.profile_name)); tabs.currentIndex = 0; agentSetupDialog.close() }
                }
                Copy { text: "To use someone else's agent, choose Find a service instead. Remote deployment uses the documented headless CLI; this dialog creates a local agent only."; Layout.fillWidth: true }
                Caption { visible: agentSetupDialog.job.state === "needs_attention"; text: "The existing setup was preserved. Do not create another agent just to retry it."; Layout.fillWidth: true }
                Caption { visible: !!root.viewError; text: root.viewError || ""; Layout.fillWidth: true }
            }
        }
        Timer { interval: 4000; repeat: true; running: agentSetupDialog.visible && agentSetupDialog.running; onTriggered: if (root.backend && !root.pending) root.call(root.backend.refreshAgentSetup(agentSetupDialog.job.id)) }
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
                Copy { visible: root.permissionReview.skill === "agent.task"; text: "Relay first checks that the other agent is online. If it does not answer within 8 seconds, Relay stops before preparing any payment."; Layout.fillWidth: true }
                Copy { visible: !!root.permissionReview.execution_expires_at; text: "If a private payment is needed, preparing it can take several minutes. You can keep using Chat, and the current step stays visible here and in Activity."; Layout.fillWidth: true }
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
            Caption { text: "Permission lasts up to two hours, within your agent's policy. Private payments run in the background and can take several minutes. An uncertain model request is not repeated automatically."; Layout.fillWidth: true }
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
                Heading { text: root.task.id ? root.skillTitle(root.task.skill) : (root.requestedTaskId ? "Loading this result…" : "Choose a result to inspect"); Layout.fillWidth: true }
                Copy { text: root.stateLabel(root.task.state || ""); color: root.taskColor(root.task.state); Layout.fillWidth: true }
                Copy { visible: !!root.progressText(root.liveTask(root.task)); text: root.progressText(root.liveTask(root.task)); Layout.fillWidth: true }
                Caption { visible: !!root.progressText(root.liveTask(root.task)); text: root.liveTask(root.task).progress ? "Now: " + root.stepLabel(root.liveTask(root.task).progress.stage) + " · updated " + root.elapsedLabel(root.liveTask(root.task).progress_age_seconds !== undefined ? root.liveTask(root.task).progress_age_seconds : Math.max(0, Math.floor(Date.now()/1000) - Number(root.liveTask(root.task).progress.updated || 0))) + " ago" : ""; Layout.fillWidth: true }
                Copy { visible: root.resultSummary(root.task).length > 0; text: root.resultSummary(root.task); Layout.fillWidth: true }
                Copy { visible: !!root.task.id; text: (root.task.maximum_spend || "0") === "0" ? "No testnet tokens used" : "Up to " + root.task.maximum_spend + " testnet units were allowed for this task"; Layout.fillWidth: true }
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
                    text: detailsDialog.showDetails ? "Hide full details" : "Show full details"
                    Accessible.name: "Toggle task full details"
                    visible: root.task.state === "completed" || !!root.task.result_preview
                    onClicked: detailsDialog.showDetails = !detailsDialog.showDetails
                }
                Caption { visible: root.task.state === "completed" && root.task.result_complete === false && !root.resultSummary(root.task); text: "The raw record is long. The summary above is the useful part; full details are optional."; Layout.fillWidth: true }
                Caption { visible: !!root.task.result_text; text: "Search response returned by the service"; Layout.fillWidth: true }
                TextArea {
                    visible: !!root.task.result_text; text: root.task.result_text || ""
                    readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap
                    color: Theme.palette.text; background: null
                    Layout.fillWidth: true; Layout.preferredHeight: Math.min(420, implicitHeight)
                    Accessible.name: "Returned search response"
                }
                Caption {
                    visible: detailsDialog.showDetails && !!root.task.result_preview && root.task.result_preview !== "null"
                    text: root.task.result_length !== undefined
                        ? "Saved result: characters " + (Number(root.task.result_offset || 0) + 1) + " to " + root.task.result_next_offset + " of " + root.task.result_length
                        : root.task.result_complete ? "Recorded result" : "Result preview (not complete)"
                }
                RowLayout {
                    visible: detailsDialog.showDetails && root.task.result_length !== undefined
                    LogosButton {
                        text: "First page"; Accessible.name: "Show first page of saved result"
                        enabled: !root.pending && Number(root.task.result_offset || 0) > 0
                        onClicked: root.call(root.backend.requestTaskPage(root.task.id, 0, root.task.result_sha256))
                    }
                    LogosButton {
                        text: "Next page"; Accessible.name: "Show next page of saved result"
                        enabled: !root.pending && root.task.result_has_more === true
                        onClicked: root.call(root.backend.requestTaskPage(root.task.id, root.task.result_next_offset, root.task.result_sha256))
                    }
                    Caption { text: "Reads the saved response. Does not run or pay again."; Layout.fillWidth: true }
                }
                ScrollView {
                    id: savedResultScroll
                    visible: detailsDialog.showDetails && !!root.task.result_preview && root.task.result_preview !== "null"
                    Layout.fillWidth: true; Layout.preferredHeight: 260
                    clip: true; contentWidth: availableWidth
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                    TextArea {
                        readOnly: true; selectByMouse: true
                        textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap
                        font.family: "monospace"; font.pixelSize: 12
                        color: Theme.palette.text; padding: 12
                        background: Rectangle { color: Theme.palette.backgroundSecondary; radius: 6 }
                        text: {
                            const raw = root.task.result_preview || ""
                            if (root.task.result_complete) {
                                try { return JSON.stringify(JSON.parse(raw), null, 2) } catch (_) { return raw }
                            }
                            return raw
                        }
                        Accessible.name: "Scrollable saved task result JSON"
                    }
                }
                RowLayout {
                    LogosButton { text: "Refresh details"; enabled: !!(root.task.id || root.requestedTaskId) && !root.pending; onClicked: root.call(root.backend.requestTask(root.task.id || root.requestedTaskId)) }
                    LogosButton { text: "Request cancellation"; visible: ["submitted", "input-required", "working", "unknown"].indexOf(root.task.state) >= 0; enabled: !root.pending; onClicked: root.reviewAction("cancel") }
                    LogosButton { text: "Review approval"; visible: root.task.state === "input-required"; enabled: root.task.arguments_complete === true && !root.pending; onClicked: root.reviewAction("approve") }
                }
                Caption { visible: detailsDialog.showDetails && !!root.task.id; text: "Task reference: " + root.task.id; Layout.fillWidth: true }
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
                Caption { text: root.reviewKind === "approve" ? "Your signature applies only to these exact arguments and this policy version." : root.reviewKind === "cancel" ? "If the private payment is still being prepared, cancel stops it and nothing is sent. A payment the network already accepted cannot be undone." : "Only this exact request will be approved. Your spending and safety limits still apply."; Layout.fillWidth: true }
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
            Copy { text: "Basecamp connects to your agents over Logos Messaging. Your owner keys are kept separate from each agent’s wallet keys."; Layout.fillWidth: true }
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
                Copy { text: "Choose an agent and wait until it says Agent online. The tools shown belong to that agent, including skills you installed. Each agent keeps its own activity and approvals."; Layout.fillWidth: true }
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
