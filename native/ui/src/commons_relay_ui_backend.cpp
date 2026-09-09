#include "commons_relay_ui_backend.h"
#include "logos_sdk.h"
#include "logos_api.h"
#include <QDateTime>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QPointer>
#include <QProcessEnvironment>
#include <QRegularExpression>
#include <QStandardPaths>
#include <dlfcn.h>

namespace {
void ownerUiLocation() {}
QString compact(const QJsonObject& value) {
    return QString::fromUtf8(QJsonDocument(value).toJson(QJsonDocument::Compact));
}
QString compact(const QJsonArray& value) {
    return QString::fromUtf8(QJsonDocument(value).toJson(QJsonDocument::Compact));
}
QJsonObject object(const QString& value) { return QJsonDocument::fromJson(value.toUtf8()).object(); }
QJsonArray array(const QString& value) { return QJsonDocument::fromJson(value.toUtf8()).array(); }
QString friendlyError(const QString& raw) {
    const QHash<QString, QString> messages = {
        {"TESTNET_HISTORY_CHANGED", "The testnet restarted after this wallet was used. Its old balance is not current. Keep the archived wallet and deploy a fresh testnet profile."},
        {"PROFILE_IN_USE", "This messaging identity is already open in another Logos instance. Close that instance or choose a different local profile; do not run the same identity twice."},
        {"PROFILE_ALREADY_SELECTED", "This instance already owns a different messaging profile. Restart it before changing the local connection."},
        {"PLANNER_NOT_CONFIGURED", "Text chat is not configured for this agent yet. You can still use Tasks without a model or API charges."},
        {"PLANNER_BUSY", "This agent is already answering a message. Wait for it or stop the conversation before sending another."},
        {"PLANNER_RESTARTED", "The agent restarted during this message. It did not repeat the paid model request. Check Activity for any task already started."},
        {"TEST_BUDGET_EXHAUSTED", "The shared model-test budget is used up or reserved by an uncertain request. No new model request was sent."},
        {"INVALID_PLANNER_PROMPT", "Write a message of up to 4,000 characters. Large messages may need to be split into shorter questions."},
        {"INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN", "The inference configuration changed. Reload its settings and review the destination again."},
        {"INFERENCE_KEY_DESTINATION_CHANGED", "An existing API key cannot be sent to a different endpoint. Enter a new key or choose No API key."},
        {"INFERENCE_HTTPS_REQUIRED", "Use HTTPS for a remote inference server. HTTP is allowed only for a local server on the agent."},
        {"INFERENCE_CHANGE_WHILE_BUSY", "Wait for the current model conversation to finish before changing its settings."},
        {"INVALID_INFERENCE_CREDENTIAL", "Check the API key in Inference settings. Do not paste it into a conversation."},
        {"INFERENCE_BASE_URL_REQUIRED", "Enter the API base URL, such as https://api.openai.com/v1, without /responses or /chat/completions."},
        {"PERMISSION_ACTION_ALREADY_SUBMITTED", "This action was already submitted before the interruption. Inspect Activity before cancelling or retrying it."},
        {"PERMISSION_REVIEW_CHANGED", "The requested action changed. Open it again before deciding."},
        {"PERMISSION_TURN_STILL_RUNNING", "Wait for the agent to finish requesting permission before deciding."},
        {"PERMISSION_REQUEST_EXPIRED", "This permission request expired. Ask the agent for a fresh request."},
        {"PERMISSION_QUOTE_OR_POLICY_CHANGED", "The price or spending policy changed. Ask for a fresh action request."},
        {"INVALID_INFERENCE_SETTINGS", "Check the endpoint, model, API format and token limits in Inference settings."},
        {"OWNER_ROOT_NOT_CONFIGURED", "Set the local owner-profile directory before controlling agents."},
        {"OWNER_ROOT_PERMISSIONS", "The owner-profile directory must be private to your macOS user."},
        {"OWNER_KEY_BINDING_CHANGED", "This owner key does not match the selected agent. Nothing was signed."},
        {"OWNER_PROFILE_UNAVAILABLE", "The selected owner profile could not be read."},
        {"PRIVATE_KEY_PERMISSIONS", "The owner key must be a private, regular file. Nothing was signed."},
        {"OWNER_COMMAND_EXPIRED", "The command expired before the agent received it. Read current task state before retrying."},
        {"AUTHORIZATION_EXPIRED", "This task's authorization expired. It was not newly authorized by a retry."},
        {"OWNER_RESULT_TOO_LARGE", "This agent needs the bounded owner-channel update to display its status."},
        {"OWNER_PROFILE_OUTSIDE_ROOT", "Choose a profile from the configured owner directory."},
        {"APPROVAL_INTENT_MISMATCH", "The task changed. Reload its exact details before approving."},
        {"APPROVAL_REVIEW_REQUIRED", "Read the complete current task details before approving it."},
        {"INVALID_SKILL_ARGUMENTS", "Complete all required fields for this skill."},
        {"RUNTIME_NOT_CONFIGURED", "The local owner transport is not connected yet."},
        {"RUNTIME_STOPPED", "The local owner transport stopped. Existing agent tasks remain on the agent."},
        {"RUNTIME_NOT_READY", "Wait for an authenticated agent response before sending a task."},
        {"OWNER_HELPER_TIMEOUT", "Local signing timed out. No automatic resend was attempted."},
        {"OWNER_REPLY_PENDING", "The command is queued, but an authenticated reply has not arrived. Do not resubmit it blindly."},
        {"LOCAL_TRANSPORT_NO_REPLY", "The local messaging service did not reply. Your request was not automatically resent. Check Connection or reconnect the local service."},
        {"LOCAL_REPLY_TIMEOUT", "The local messaging service is taking too long. A sent task may still be working; check its status before sending it again."},
        {"TASK_NOT_FOUND", "The agent could not find that task. Refresh the task list."},
        {"INVALID_OWNER_PROFILE_NAME", "Choose one of the named owner profiles."},
    };
    if (messages.contains(raw)) return messages.value(raw);
    if (QRegularExpression("^[A-Z][A-Z0-9_]{0,99}$").match(raw).hasMatch())
        return QStringLiteral("The request was not accepted (%1). Check its fields and technical details.").arg(raw);
    return QStringLiteral("The request could not be completed. See the technical details.");
}
QJsonObject command(const QString& method, const QJsonObject& params = {}) {
    return {{"method", method}, {"params", params}};
}
}

CommonsRelayUiBackend::CommonsRelayUiBackend() {
    helper_.setProcessChannelMode(QProcess::SeparateChannels);
    helperTimeout_.setSingleShot(true);
    helperTimeout_.setInterval(15000);
    connect(&helper_, &QProcess::readyReadStandardOutput, this, [this] {
        helperOutput_ += helper_.readAllStandardOutput();
        if (helperOutput_.size() > 60000) helperFailure("OWNER_UI_RESPONSE_LIMIT");
    });
    connect(&helper_, &QProcess::readyReadStandardError, this, [this] {
        // Local signing diagnostics can contain paths. They never enter QML or the agent.
        helper_.readAllStandardError();
    });
    connect(&helper_, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this](int code, QProcess::ExitStatus) { finishHelper(code); });
    connect(&helper_, &QProcess::errorOccurred, this, [this](QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) helperFailure("OWNER_HELPER_UNAVAILABLE");
    });
    connect(&helperTimeout_, &QTimer::timeout, this, [this] { helperFailure("OWNER_HELPER_TIMEOUT"); });
    pollTimer_.setInterval(2500);
    connect(&pollTimer_, &QTimer::timeout, this, [this] {
        const auto now = QDateTime::currentMSecsSinceEpoch();
        for (const auto& id : rpcStarted_.keys()) {
            if (now - rpcStarted_.value(id) > 120000) {
                rpcStarted_.remove(id);
                rpcKinds_.remove(id);
                fail("LOCAL_REPLY_TIMEOUT");
            }
        }
        for (const auto& id : ownerPending_.keys()) {
            const auto pending = ownerPending_.value(id);
            if (now - pending.value("started").toInteger() > 600000) {
                ownerPending_.remove(id);
                fail("OWNER_REPLY_PENDING");
            }
        }
        updatePending();
        if (connected() && !selectedAgent().isEmpty()) pollOwnerChannel();
        if (connected() && !activeGoalId().isEmpty() && now - lastGoalPoll_ > 4000) {
            bool pending = false;
            for (const auto& item : ownerPending_) if (item.value("kind") == "planner_goal") pending = true;
            if (!pending) {
                lastGoalPoll_ = now;
                compose({{"kind", "planner_goal"}, {"goal_id", activeGoalId()}});
            }
        }
        if (connected() && !selectedAgent().isEmpty() && now - lastHealthCheck_ > 30000 && !hasPendingKind("health")) {
            lastHealthCheck_ = now;
            dispatch(command("transport.status"), "health");
        }
    });
}

CommonsRelayUiBackend::~CommonsRelayUiBackend() {
    helperTimeout_.stop();
    pollTimer_.stop();
    helperActive_ = false;
    if (helper_.state() != QProcess::NotRunning) {
        helper_.terminate();
        if (!helper_.waitForFinished(250)) {
            helper_.kill();
            helper_.waitForFinished(1000);
        }
    }
}

void CommonsRelayUiBackend::onContextReady() {
    client_ = modules().api->getClient("commons_relay_module");
    if (!client_) { fail("CORE_UNAVAILABLE"); return; }
    auto* remote = client_->requestObject("commons_relay_module");
    if (!remote) { fail("CORE_UNAVAILABLE"); return; }
    QPointer<CommonsRelayUiBackend> self(this);
    client_->onEvent(remote, "commons_relayResult",
                    [self](const QString& name, const QVariantList& args) {
                        if (self) self->receive(name, args);
                    });
    setReady(true);
    setStatusText("Local owner transport found. Connecting...");
    loadOwnerProfiles();
    const auto profile = qEnvironmentVariable("COMMONS_RELAY_DEFAULT_PROFILE");
    if (!profile.isEmpty()) QTimer::singleShot(0, this, [this, profile] { configure(profile); });
    else QTimer::singleShot(0, this, [this] { refresh(); });
    pollTimer_.start();
}

void CommonsRelayUiBackend::fail(const QString& code) {
    const QString safe = QRegularExpression("^[A-Z][A-Z0-9_]{0,99}$").match(code).hasMatch()
        ? code : QStringLiteral("LOCAL_TRANSPORT_NO_REPLY");
    setLastError(safe);
    setLastResultJson(compact(QJsonObject{{"success", false}, {"error", safe}}));
    setStatusText(friendlyError(safe));
}

void CommonsRelayUiBackend::updatePending() {
    setSigning(helperActive_);
    // Queued transport is not completion. A slow agent must not lock every other
    // read or independent task; task submission itself always requires a new review.
    setRequestPending(helperActive_ || !helperQueue_.isEmpty() || hasPendingKind("owner-send"));
}

QString CommonsRelayUiBackend::configure(QString profile) {
    if (!client_) { fail("CORE_UNAVAILABLE"); return "CORE_UNAVAILABLE"; }
    if (dispatchActive_ || !dispatchQueue_.isEmpty()) return "CONNECTION_BUSY";
    setLastError("");
    setStatusText("Connecting the local owner transport...");
    QPointer<CommonsRelayUiBackend> self(this);
    client_->invokeRemoteMethodAsync(QString("commons_relay_module"), QString("configure"), QVariantList{profile},
        [self](QVariant result) {
            if (!self) return;
            const auto reply = result.toString();
            if (reply == "STARTING_LOCAL_RUNTIME" || reply == "RUNTIME_ALREADY_CONFIGURED") {
                QTimer::singleShot(700, self, [self] { if (self) self->refresh(); });
            } else self->fail(reply);
        }, Timeout(20000));
    return "CONNECTING_OWNER_TRANSPORT";
}

bool CommonsRelayUiBackend::hasPendingKind(const QString& kind) const {
    if (rpcKinds_.values().contains(kind) || (dispatchActive_ && activeDispatch_.kind == kind)) return true;
    for (const auto& item : dispatchQueue_) if (item.kind == kind) return true;
    return false;
}

QString CommonsRelayUiBackend::dispatch(const QJsonObject& request, const QString& kind) {
    if (!client_) { fail("CORE_UNAVAILABLE"); return "CORE_UNAVAILABLE"; }
    if (rpcKinds_.size() + dispatchQueue_.size() >= 24) { fail("REQUEST_LIMIT"); return "REQUEST_LIMIT"; }
    dispatchQueue_.enqueue({request, kind});
    updatePending();
    startNextDispatch();
    return "QUEUED_FOR_LOCAL_TRANSPORT";
}

void CommonsRelayUiBackend::startNextDispatch() {
    if (dispatchActive_ || dispatchQueue_.isEmpty() || !client_) return;
    activeDispatch_ = dispatchQueue_.dequeue();
    dispatchActive_ = true;
    updatePending();
    QPointer<CommonsRelayUiBackend> self(this);
    // Synchronous IPC runs a nested event loop. Poll timers could re-enter it,
    // and fast Python results could arrive before the returned request ID was
    // registered. Serialize acceptance asynchronously and retain early replies.
    client_->invokeRemoteMethodAsync(QString("commons_relay_module"), QString("request"),
        QVariantList{compact(activeDispatch_.request)}, [self](QVariant result) {
            if (!self) return;
            const auto kind = self->activeDispatch_.kind;
            self->dispatchActive_ = false;
            const auto id = result.toString();
            if (!QRegularExpression("^[a-f0-9-]{36}$").match(id).hasMatch()) {
                self->fail(id);
            } else {
                self->rpcKinds_.insert(id, kind);
                self->rpcStarted_.insert(id, QDateTime::currentMSecsSinceEpoch());
                if (self->earlyReplies_.contains(id)) self->receive(QString(), self->earlyReplies_.take(id));
            }
            self->earlyReplies_.clear();
            self->updatePending();
            QTimer::singleShot(0, self, [self] { if (self) self->startNextDispatch(); });
        }, Timeout(20000));
}

QString CommonsRelayUiBackend::refresh() {
    return dispatch(command("status"), "local-status");
}

QString CommonsRelayUiBackend::pollOwnerChannel() {
    if (!connected()) return "RUNTIME_NOT_READY";
    if (hasPendingKind("pump") || hasPendingKind("messages"))
        return "POLL_ALREADY_PENDING";
    return dispatch(command("messaging.pump"), "pump");
}

QString CommonsRelayUiBackend::loadOwnerProfiles() {
    runHelper({{"action", "catalog"}});
    return "READING_OWNER_PROFILES";
}

QString CommonsRelayUiBackend::selectOwnerProfile(QString name) {
    if (chatBusy()) { fail("CONVERSATION_ALREADY_RUNNING"); return "CONVERSATION_ALREADY_RUNNING"; }
    QJsonObject selected;
    for (const auto& value : array(profilesJson())) {
        const auto candidate = value.toObject();
        if (candidate.value("name").toString() == name) { selected = candidate; break; }
    }
    if (selected.isEmpty()) { fail("INVALID_OWNER_PROFILE_NAME"); return "INVALID_OWNER_PROFILE_NAME"; }
    ++generation_;
    setPermissionReviewJson("{}");
    helperQueue_.clear();
    ownerPending_.clear();
    setSelectedProfile(name);
    setSelectedAgent(selected.value("agent_id").toString());
    setRemoteReady(false);
    setPlannerInfoJson("{}"); setConversationJson("[]"); setActiveGoalId(""); setChatBusy(false);
    setSummaryJson("{}");
    setTasksJson("[]");
    setSkillsJson("[]");
    setTaskDetailsJson("{}");
    setSkillDetailsJson("{}");
    setHasMoreTasks(false);
    setHasMoreConversation(false); conversationOffset_ = 0;
    requestedSkill_.clear();
    requestedTask_.clear();
    nextTaskOffset_ = 0;
    setLastError("");
    setStatusText("Requesting authenticated agent status over Logos Messaging...");
    refreshAgent();
    requestSkillsPage(0);
    refreshPlanner();
    loadConversation();
    return "AGENT_SELECTED";
}

QString CommonsRelayUiBackend::compose(const QJsonObject& value) {
    if (!connected() || selectedProfile().isEmpty()) { fail("RUNTIME_NOT_READY"); return "RUNTIME_NOT_READY"; }
    if (ownerPending_.size() >= 64) { fail("REQUEST_LIMIT"); return "REQUEST_LIMIT"; }
    runHelper({{"action", "compose"}, {"profile", selectedProfile()}, {"command", value}});
    return "SIGNING_OWNER_COMMAND";
}

QString CommonsRelayUiBackend::refreshAgent() {
    return compose({{"kind", "snapshot"}, {"offset", 0}});
}
QString CommonsRelayUiBackend::refreshPlanner() {
    if (selectedAgent().isEmpty()) return "CHOOSE_AN_AGENT";
    return compose({{"kind", "planner_status"}});
}

QString CommonsRelayUiBackend::configureInference(QString settingsJson, QString apiKey, QString expectedHash) {
    if (!remoteReady() || selectedAgent().isEmpty()) { fail("CHOOSE_AN_AGENT"); return "CHOOSE_AN_AGENT"; }
    if (chatBusy() || requestPending()) { fail("PLANNER_BUSY"); return "PLANNER_BUSY"; }
    const auto info = object(plannerInfoJson());
    if (expectedHash != info.value("configuration_hash").toString()
        || !QRegularExpression("^[a-f0-9]{64}$").match(expectedHash).hasMatch()) {
        fail("INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN"); return "INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN";
    }
    QJsonParseError error;
    const auto document = QJsonDocument::fromJson(settingsJson.toUtf8(), &error);
    if (settingsJson.toUtf8().size() > 4096 || apiKey.size() > 4096 || error.error != QJsonParseError::NoError || !document.isObject()) {
        fail("INVALID_INFERENCE_SETTINGS"); return "INVALID_INFERENCE_SETTINGS";
    }
    setStatusText("Saving your inference settings to this agent. No model request is being made.");
    // Only the one-shot local signer sees the entered key. Its output contains
    // a recipient-sealed credential, never plaintext in the messaging journal.
    return compose({{"kind", "planner_configure"}, {"settings", document.object()},
        {"api_key", apiKey}, {"expected_hash", expectedHash}, {"box_key", info.value("configuration_box_key")},
        {"credential_digest", info.value("credential_digest")}});
}

QString CommonsRelayUiBackend::loadConversation() {
    if (selectedAgent().isEmpty()) return "CHOOSE_AN_AGENT";
    return compose({{"kind", "planner_history"}, {"offset", 0}});
}

QString CommonsRelayUiBackend::loadEarlierConversation() {
    if (selectedAgent().isEmpty() || !hasMoreConversation()) return "NO_EARLIER_MESSAGES";
    return compose({{"kind", "planner_history"}, {"offset", conversationOffset_}});
}
QString CommonsRelayUiBackend::loadConversationGoal(QString goalId) {
    if (selectedAgent().isEmpty()) return "CHOOSE_AN_AGENT";
    if (!QRegularExpression("^[A-Za-z0-9_.:-]{1,120}$").match(goalId).hasMatch()) return "INVALID_GOAL_ID";
    return compose({{"kind", "planner_goal"}, {"goal_id", goalId}});
}

QString CommonsRelayUiBackend::reviewConversationPermission(QString goalId) {
    setPermissionReviewJson("{}");
    if (!remoteReady() || selectedAgent().isEmpty()) return "CHOOSE_AN_AGENT";
    if (!QRegularExpression("^[A-Za-z0-9_.:-]{1,120}$").match(goalId).hasMatch()) return "INVALID_GOAL_ID";
    return compose({{"kind", "planner_permission_review"}, {"goal_id", goalId}});
}

QString CommonsRelayUiBackend::decideConversationPermission(QString goalId, QString permissionHash, bool approve) {
    if (!remoteReady() || chatBusy() || requestPending()) { fail("PERMISSION_TURN_STILL_RUNNING"); return "PERMISSION_TURN_STILL_RUNNING"; }
    const auto review = object(permissionReviewJson());
    const auto permission = review.value("permission").toObject();
    const auto request = permission.value("request").toObject();
    if (review.value("agent_id").toString() != selectedAgent() || review.value("id").toString() != goalId
        || permission.value("decision") != "pending" || request.value("intent_hash").toString() != permissionHash
        || !QRegularExpression("^[a-f0-9]{64}$").match(permissionHash).hasMatch()) {
        fail("PERMISSION_REVIEW_CHANGED"); return "PERMISSION_REVIEW_CHANGED";
    }
    setPermissionReviewJson("{}");
    return compose({{"kind", "planner_permission"}, {"goal_id", goalId}, {"permission", request},
                    {"decision", approve ? "approve" : "decline"}});
}

QString CommonsRelayUiBackend::startConversation(QString prompt, bool allowActions, QString maximumSpend, QString inferenceHash) {
    if (!remoteReady() || selectedAgent().isEmpty()) { fail("CHOOSE_AN_AGENT"); return "CHOOSE_AN_AGENT"; }
    const auto info = object(plannerInfoJson());
    if (!info.value("enabled").toBool()) { fail("PLANNER_NOT_CONFIGURED"); return "PLANNER_NOT_CONFIGURED"; }
    if (inferenceHash != info.value("configuration_hash").toString() || !QRegularExpression("^[a-f0-9]{64}$").match(inferenceHash).hasMatch()) { fail("INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN"); return "INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN"; }
    if (chatBusy() || requestPending()) { fail("PLANNER_BUSY"); return "PLANNER_BUSY"; }
    prompt = prompt.trimmed();
    if (prompt.isEmpty() || prompt.size() > 4000) { fail("INVALID_PLANNER_PROMPT"); return "INVALID_PLANNER_PROMPT"; }
    const auto policy = object(summaryJson()).value("policy").toObject();
    const auto scope = info.value(allowActions ? "action_skills" : "read_skills").toArray();
    if (!allowActions) maximumSpend = "0";
    if (!QRegularExpression("^(0|[1-9][0-9]{0,38})$").match(maximumSpend).hasMatch()) {
        fail("INVALID_PLANNER_LIMITS"); return "INVALID_PLANNER_LIMITS";
    }
    const int ttl = qMin(allowActions ? 7200 : 600, policy.value("approval_ttl").toInt(600));
    setChatBusy(true); setLastError("");
    setStatusText("Authorizing this conversation. No model request is repeated automatically.");
    return compose({{"kind", "planner_start"}, {"goal", prompt},
        {"delegate_key_id", info.value("delegate_key_id")}, {"mode", allowActions ? "actions" : "read"},
        {"allowed_skills", scope}, {"maximum_spend", maximumSpend}, {"max_steps", 8},
        {"expires_in", ttl}, {"policy_version", policy.value("version").toInt(1)},
        {"inference_hash", info.value("configuration_hash")}});
}

QString CommonsRelayUiBackend::cancelConversation() {
    if (activeGoalId().isEmpty()) return "NO_ACTIVE_CONVERSATION";
    return compose({{"kind", "planner_cancel"}, {"goal_id", activeGoalId()}});
}

void CommonsRelayUiBackend::mergeGoal(const QJsonObject& goal) {
    auto goals = array(conversationJson());
    bool found = false;
    for (int index = 0; index < goals.size(); ++index) {
        if (goals[index].toObject().value("id") == goal.value("id")) {
            goals[index] = goal; found = true; break;
        }
    }
    if (!found) goals.append(goal);
    while (goals.size() > 30) goals.removeFirst();
    setConversationJson(compact(goals));
    const auto state = goal.value("state").toString();
    if (state == "queued" || state == "thinking" || state == "working") {
        setActiveGoalId(goal.value("id").toString()); setChatBusy(true);
    } else if (activeGoalId().isEmpty() || activeGoalId() == goal.value("id").toString()) {
        setActiveGoalId(""); setChatBusy(false);
    }
}

QString CommonsRelayUiBackend::loadMoreTasks() {
    if (!hasMoreTasks()) return "NO_MORE_TASKS";
    return compose({{"kind", "snapshot"}, {"offset", nextTaskOffset_}});
}
void CommonsRelayUiBackend::requestSkillsPage(int offset) {
    compose({{"kind", "skills"}, {"offset", offset}});
}
QString CommonsRelayUiBackend::requestSkill(QString name) {
    requestedSkill_ = name;
    setSkillDetailsJson("{}");
    return compose({{"kind", "skill"}, {"name", name}});
}
QString CommonsRelayUiBackend::requestTask(QString taskId) {
    requestedTask_ = taskId;
    setTaskDetailsJson("{}");
    return compose({{"kind", "task"}, {"task_id", taskId}});
}

QString CommonsRelayUiBackend::submitTask(QString skill, QString argumentsJson, int expiresIn) {
    if (!remoteReady()) { fail("RUNTIME_NOT_READY"); return "RUNTIME_NOT_READY"; }
    QJsonParseError error;
    const auto arguments = QJsonDocument::fromJson(argumentsJson.toUtf8(), &error);
    if (error.error != QJsonParseError::NoError || !arguments.isObject() || argumentsJson.toUtf8().size() > 18000) {
        fail("INVALID_SKILL_ARGUMENTS"); return "INVALID_SKILL_ARGUMENTS";
    }
    setLastError("");
    setStatusText("Signing this exact task on the owner device...");
    return compose({{"kind", "submit"}, {"skill", skill}, {"arguments", arguments.object()}, {"expires_in", expiresIn}});
}

QString CommonsRelayUiBackend::approveTask(QString taskId, QString intentHash, int policyVersion) {
    const auto task = object(taskDetailsJson());
    if (!remoteReady() || task.value("id").toString() != taskId
        || task.value("state").toString() != "input-required"
        || !task.value("arguments_complete").toBool()
        || task.value("intent_hash").toString() != intentHash
        || task.value("policy_version").toInt() != policyVersion) {
        fail("APPROVAL_REVIEW_REQUIRED"); return "APPROVAL_REVIEW_REQUIRED";
    }
    const int ttl = qBound(30, object(summaryJson()).value("policy").toObject().value("approval_ttl").toInt(300), 7200);
    return compose({{"kind", "approve"}, {"task_id", taskId}, {"intent_hash", intentHash},
                    {"policy_version", policyVersion}, {"expires_in", ttl}});
}

QString CommonsRelayUiBackend::cancelTask(QString taskId) {
    if (!remoteReady() || object(taskDetailsJson()).value("id").toString() != taskId) {
        fail("APPROVAL_REVIEW_REQUIRED"); return "APPROVAL_REVIEW_REQUIRED";
    }
    return compose({{"kind", "cancel"}, {"task_id", taskId}, {"expires_in", 120}});
}

QString CommonsRelayUiBackend::sendSignedCommand(QString json) {
    const auto request = object(json);
    if (json.toUtf8().size() > 60000 || request.size() != 2
        || request.value("method").toString() != "owner.send"
        || !request.value("params").isObject()) {
        fail("OWNER_UI_ACTION_NOT_ALLOWED"); return "OWNER_UI_ACTION_NOT_ALLOWED";
    }
    // Advanced import is a signed owner-channel wrapper, never an unsigned local
    // service method or a general-purpose module/shell proxy.
    return dispatch(request, "owner-send");
}

void CommonsRelayUiBackend::runHelper(const QJsonObject& request) {
    if (helperQueue_.size() >= 12) { fail("REQUEST_LIMIT"); return; }
    helperQueue_.enqueue({request, generation_});
    updatePending();
    startNextHelper();
}

void CommonsRelayUiBackend::startNextHelper() {
    if (helperActive_ || helper_.state() != QProcess::NotRunning || helperQueue_.isEmpty()) return;
    activeHelper_ = helperQueue_.dequeue();
    const auto ownerRoot = qEnvironmentVariable("COMMONS_RELAY_OWNER_ROOT");
    if (ownerRoot.isEmpty()) { fail("OWNER_ROOT_NOT_CONFIGURED"); helperQueue_.clear(); updatePending(); return; }
    Dl_info location{};
    if (!dladdr(reinterpret_cast<void*>(&ownerUiLocation), &location) || !location.dli_fname) {
        fail("OWNER_HELPER_UNAVAILABLE"); helperQueue_.clear(); updatePending(); return;
    }
    const auto directory = QFileInfo(QString::fromUtf8(location.dli_fname)).canonicalPath();
    const auto script = directory + "/commons_relay_owner_ui.py";
    if (!QFileInfo(script).isFile() || QFileInfo(script).isSymLink()) {
        fail("OWNER_HELPER_UNAVAILABLE"); helperQueue_.clear(); updatePending(); return;
    }
    QString python = qEnvironmentVariable("COMMONS_RELAY_PYTHON");
    if (python.isEmpty()) python = QStandardPaths::findExecutable("python3");
    QProcessEnvironment environment;
    environment.insert("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin");
    environment.insert("HOME", ownerRoot);
    environment.insert("COMMONS_RELAY_OWNER_ROOT", ownerRoot);
    environment.insert("PYTHONNOUSERSITE", "1");
    const auto sodium = qEnvironmentVariable("COMMONS_RELAY_SODIUM_LIBRARY");
    if (!sodium.isEmpty()) environment.insert("COMMONS_RELAY_SODIUM_LIBRARY", sodium);
    helper_.setProcessEnvironment(environment);
    helper_.setWorkingDirectory(directory);
    helper_.setProgram(python);
    helper_.setArguments({"-I", script});
    helperOutput_.clear();
    helperActive_ = true;
    updatePending();
    helper_.start();
    helper_.write(QJsonDocument(activeHelper_.request).toJson(QJsonDocument::Compact) + "\n");
    helper_.closeWriteChannel();
    helperTimeout_.start();
}

void CommonsRelayUiBackend::helperFailure(const QString& code) {
    if (activeHelper_.request.value("command").toObject().value("kind") == "planner_start") setChatBusy(false);
    if (!helperActive_) return;
    helperActive_ = false;
    helperTimeout_.stop();
    fail(code);
    if (helper_.state() != QProcess::NotRunning) helper_.kill();
    updatePending();
    QTimer::singleShot(0, this, [this] { startNextHelper(); });
}

void CommonsRelayUiBackend::finishHelper(int code) {
    helperTimeout_.stop();
    if (!helperActive_) { startNextHelper(); return; }
    helperOutput_ += helper_.readAllStandardOutput();
    helperActive_ = false;
    const auto work = activeHelper_;
    activeHelper_.request = {}; // release any local credential input promptly
    if (helperOutput_.size() > 60000) {
        helperOutput_.clear(); fail("OWNER_UI_RESPONSE_LIMIT"); updatePending(); startNextHelper(); return;
    }
    QJsonParseError error;
    const auto parsed = QJsonDocument::fromJson(helperOutput_, &error);
    helperOutput_.clear();
    if (code != 0 || error.error != QJsonParseError::NoError || !parsed.isObject()) {
        if (work.generation == generation_ && work.request.value("command").toObject().value("kind") == "planner_start") setChatBusy(false);
        fail("OWNER_HELPER_INVALID_REPLY");
    } else if (work.request.value("action") == "catalog" || work.generation == generation_) {
        const auto reply = parsed.object();
        if (reply.value("success").toBool()) applyHelperResult(reply.value("result").toObject(), work);
        else {
            if (work.request.value("command").toObject().value("kind") == "planner_start") setChatBusy(false);
            fail(reply.value("error").toString());
        }
    }
    updatePending();
    startNextHelper();
}

void CommonsRelayUiBackend::applyHelperResult(const QJsonObject& result, const HelperWork& work) {
    if (work.request.value("action") == "catalog") {
        setProfilesJson(compact(result.value("profiles").toArray()));
        if (result.value("profiles").toArray().isEmpty()) fail("OWNER_PROFILE_UNAVAILABLE");
        return;
    }
    if (result.value("agent_id").toString() != selectedAgent()) return;
    const auto id = result.value("command_id").toString();
    const auto request = result.value("request").toObject();
    if (id.isEmpty() || request.value("method") != "owner.send"
        || request.value("params").toObject().value("recipient").toString() != selectedAgent()) {
        fail("OWNER_HELPER_INVALID_REPLY"); return;
    }
    if (result.value("command_kind").toString() == "planner_start") {
        const auto goalId = result.value("conversation_id").toString();
        if (!QRegularExpression("^chat-[a-f0-9]{24}$").match(goalId).hasMatch()) {
            setChatBusy(false); fail("OWNER_HELPER_INVALID_REPLY"); return;
        }
        setActiveGoalId(goalId);
        setChatBusy(true);
    }
    ownerPending_.insert(id, {{"kind", result.value("command_kind")},
                             {"agent", selectedAgent()}, {"generation", generation_},
                             {"started", QDateTime::currentMSecsSinceEpoch()}});
    dispatch(request, "owner-send");
}

void CommonsRelayUiBackend::receive(const QString&, const QVariantList& args) {
    if (args.size() != 2) return;
    const auto id = args[0].toString();
    if (!rpcKinds_.contains(id)) {
        // Only a result subsequently matched to this UI's accepted UUID is used.
        if (dispatchActive_ && earlyReplies_.size() < 32 && args[1].toString().size() <= 60000)
            earlyReplies_.insert(id, args);
        return;
    }
    const auto kind = rpcKinds_.take(id);
    rpcStarted_.remove(id);
    const auto document = QJsonDocument::fromJson(args[1].toString().toUtf8());
    updatePending();
    if (!document.isObject()) { fail("INVALID_RUNTIME_REPLY"); return; }
    const auto reply = document.object();
    if (reply.contains("id") && reply.value("id").toString() != id) { fail("RUNTIME_REPLY_ID_MISMATCH"); return; }
    if (!reply.value("success").toBool()) {
        if (kind == "health") {
            setTransportHealthJson(compact(QJsonObject{{"checked", false}, {"error", "HEALTH_UNAVAILABLE"}}));
            return; // Optional diagnostics must not replace a task's real result.
        }
        setLastResultJson(QString::fromUtf8(document.toJson(QJsonDocument::Indented)));
        fail(reply.value("error").toString()); return;
    }
    const auto data = reply.value("result").toObject();
    if (kind == "health") {
        setTransportHealthJson(compact(data.value("transport_health").toObject()));
        return;
    }
    if (kind == "local-status") {
        setConnected(true);
        setAgentId(data.value("agent_id").toString());
        if (selectedAgent().isEmpty()) setStatusText("Owner transport connected. Choose an agent to begin.");
    } else if (kind == "pump") {
        dispatch(command("owner.inbox", {{"after", static_cast<qint64>(ownerCursor_)}}), "messages");
    } else if (kind == "messages") {
        for (const auto& value : data.value("messages").toArray()) {
            const auto message = value.toObject();
            const auto cursor = static_cast<quint64>(qMax<qint64>(0, message.value("cursor").toInteger()));
            if (cursor <= ownerCursor_) continue;
            ownerCursor_ = cursor;
            ownerMessages_.append(message);
            while (ownerMessages_.size() > 20) ownerMessages_.removeFirst();
            consumeOwnerMessage(message);
        }
        setOwnerMessagesJson(compact(ownerMessages_));
    } else if (kind == "owner-send") {
        if (!remoteReady()) setStatusText("Encrypted command queued. Waiting for an authenticated agent reply...");
        QTimer::singleShot(0, this, [this] { pollOwnerChannel(); });
    }
}

void CommonsRelayUiBackend::consumeOwnerMessage(const QJsonObject& message) {
    if (message.value("kind") != "owner-result" || message.value("sender").toString() != selectedAgent()) return;
    if (message.value("payload_omitted").toBool()) return;
    const auto encoded = message.value("payload_json").toString();
    if (encoded.toUtf8().size() > 30000) return;
    const auto payload = encoded.isEmpty() ? message.value("payload").toObject() : object(encoded);
    const auto id = payload.value("command_id").toString();
    if (!id.isEmpty()) {
        if (!ownerPending_.contains(id)) return;
        const auto pending = ownerPending_.take(id);
        if (pending.value("generation").toInt() != generation_ || pending.value("agent").toString() != selectedAgent()) return;
        setLastResultJson(QString::fromUtf8(QJsonDocument(payload).toJson(QJsonDocument::Indented)));
        if (!payload.value("success").toBool()) {
            if (pending.value("kind").toString().startsWith("planner_")) setChatBusy(false);
            fail(payload.value("error").toString()); return;
        }
        setLastError("");
        applyOwnerResult(payload.value("result").toObject(), pending.value("kind").toString());
    } else if (remoteReady() && payload.contains("task_update")) {
        const auto update = payload.value("task_update").toObject();
        const auto taskId = update.value("task_id").toString();
        auto tasks = array(tasksJson());
        for (int index = 0; index < tasks.size(); ++index) {
            auto task = tasks.at(index).toObject();
            if (task.value("id").toString() != taskId) continue;
            task["state"] = update.value("state");
            tasks[index] = task;
        }
        setTasksJson(compact(tasks));
        if (taskId == requestedTask_) requestTask(taskId);
        const auto taskState = update.value("state").toString();
        if (taskState == "completed" || taskState == "failed" || taskState == "rejected" || taskState == "canceled") {
            int refreshed = 0;
            for (const auto& item : array(conversationJson())) {
                const auto goal = item.toObject();
                if (goal.value("task_ids").toArray().contains(QJsonValue(taskId)) && refreshed++ < 4)
                    loadConversationGoal(goal.value("id").toString());
            }
        }
    } else if (remoteReady() && payload.contains("notice")) {
        refreshAgent();
        if (payload.value("notice").toObject().value("task_id").toString() == requestedTask_)
            requestTask(requestedTask_);
    }
}

void CommonsRelayUiBackend::mergeTask(const QJsonObject& task) {
    auto tasks = array(tasksJson());
    for (int index = 0; index < tasks.size(); ++index) {
        if (tasks.at(index).toObject().value("id") == task.value("id")) {
            tasks[index] = task; setTasksJson(compact(tasks)); return;
        }
    }
    tasks.prepend(task);
    while (tasks.size() > 100) tasks.removeLast();
    setTasksJson(compact(tasks));
}

void CommonsRelayUiBackend::applyOwnerResult(const QJsonObject& result, const QString& kind) {
    if (result.contains("agent_id") && result.value("agent_id").toString() != selectedAgent()) {
        fail("OWNER_REPLY_AGENT_MISMATCH"); return;
    }
    if (kind == "planner_configure" && result.value("inference_updated").toBool()) {
        const auto current = result.value("planner").toObject();
        if (current.value("agent_id").toString() != selectedAgent()) { fail("OWNER_REPLY_AGENT_MISMATCH"); return; }
        setPlannerInfoJson(compact(current));
        setStatusText("Inference settings saved. Review the data-sharing notice before sending a message.");
        return;
    }
    if (result.value("planner_status").toBool()) {
        setPlannerInfoJson(compact(result));
        if (result.value("active_goal").isString() && !result.value("active_goal").toString().isEmpty()) {
            setActiveGoalId(result.value("active_goal").toString()); setChatBusy(true);
        }
        return;
    }
    if (result.value("planner_history").toBool()) {
        const auto goals = result.value("goals").toArray();
        const auto existing = array(conversationJson());
        QJsonArray page, chronological;
        QSet<QString> pageIds;
        for (int index = goals.size() - 1; index >= 0; --index) {
            auto goal = goals[index].toObject();
            const auto id = goal.value("id").toString();
            if (id.isEmpty() || pageIds.contains(id)) continue;
            pageIds.insert(id);
            for (const auto& previous : existing) {
                const auto old = previous.toObject();
                if (old.value("id") == goal.value("id") && !old.value("preview").toBool()
                    && old.value("updated").toInteger() >= goal.value("updated").toInteger()) goal = old;
            }
            page.append(goal);
        }
        if (result.value("offset").toInt() == 0) {
            for (const auto& old : existing) if (!pageIds.contains(old.toObject().value("id").toString())) chronological.append(old);
            for (const auto& goal : page) chronological.append(goal);
        } else {
            for (const auto& goal : page) chronological.append(goal);
            for (const auto& old : existing) if (!pageIds.contains(old.toObject().value("id").toString())) chronological.append(old);
        }
        while (chronological.size() > 1000) chronological.removeFirst();
        conversationOffset_ = qMax(conversationOffset_, result.value("next_offset").toInt());
        setHasMoreConversation(result.value("has_more").toBool());
        setConversationJson(compact(chronological));
        return;
    }
    if (kind == "planner_permission_review" && result.value("permission_review").toBool()) {
        setPermissionReviewJson(compact(result)); return;
    }
    if (result.value("planner_goal").toBool()) {
        const auto goal = result.value("goal").toObject();
        mergeGoal(goal);
        if (kind == "planner_permission_review") {
            auto review = goal; review["agent_id"] = selectedAgent();
            setPermissionReviewJson(compact(review));
        }
        if (!chatBusy()) { refreshPlanner(); refreshAgent(); }
        return;
    }
    if (result.value("owner_snapshot").toBool()) {
        setRemoteReady(true);
        auto summary = result; summary.remove("tasks");
        setSummaryJson(compact(summary));
        auto tasks = result.value("offset").toInt() == 0 ? QJsonArray() : array(tasksJson());
        for (const auto& value : result.value("tasks").toArray()) {
            bool exists = false;
            for (const auto& old : tasks) if (old.toObject().value("id") == value.toObject().value("id")) { exists = true; break; }
            if (!exists) tasks.append(value);
        }
        setTasksJson(compact(tasks));
        nextTaskOffset_ = result.value("next_offset").toInt();
        setHasMoreTasks(result.value("has_more").toBool());
        setStatusText("Authenticated agent status received. Tasks and spending policy are live.");
    } else if (result.value("owner_skills").toBool()) {
        auto skills = result.value("offset").toInt() == 0 ? QJsonArray() : array(skillsJson());
        for (const auto& value : result.value("skills").toArray()) skills.append(value);
        setSkillsJson(compact(skills));
        if (result.value("has_more").toBool() && skills.size() < 128)
            requestSkillsPage(result.value("next_offset").toInt());
    } else if (result.value("owner_skill").toBool()) {
        const auto skill = result.value("skill").toObject();
        if (skill.value("id").toString() == requestedSkill_) setSkillDetailsJson(compact(skill));
    } else if (result.value("owner_task").toBool()) {
        const auto task = result.value("task").toObject();
        mergeTask(task);
        if (task.value("id").toString() == requestedTask_) setTaskDetailsJson(compact(task));
    } else if (result.contains("id") && result.contains("state")) {
        mergeTask(result);
        setStatusText(result.value("state") == "input-required"
            ? "Task received. It is waiting for your approval; no spending has been authorized."
            : kind == "approve" ? "Exact intent approved. The agent will execute it under the recorded policy."
            : kind == "cancel" ? "The agent processed the cancellation request. Read the final task state."
            : "Task accepted by the agent. Completion will be reported over the owner channel.");
        refreshAgent();
        if (result.value("id").toString() == requestedTask_) requestTask(requestedTask_);
    }
}
