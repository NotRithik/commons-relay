#pragma once
#include "logos_ui_plugin_context.h"
#include "rep_commons_relay_owner_ui_source.h"
#include "logos_api_client.h"
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QProcess>
#include <QQueue>
#include <QTimer>

class CommonsRelayUiBackend final : public CommonsRelayOwnerUiSimpleSource,
                                    public LogosUiPluginContext {
public:
    CommonsRelayUiBackend();
    ~CommonsRelayUiBackend() override;
    QString configure(QString profile) override;
    QString refresh() override;
    QString refreshPlanner() override;
    QString startConversation(QString prompt, bool allowActions, QString maximumSpend) override;
    QString cancelConversation() override;
    QString loadConversation() override;
    QString sendSignedCommand(QString json) override;
    QString pollOwnerChannel() override;
    QString loadOwnerProfiles() override;
    QString selectOwnerProfile(QString name) override;
    QString refreshAgent() override;
    QString loadMoreTasks() override;
    QString requestSkill(QString name) override;
    QString requestTask(QString taskId) override;
    QString submitTask(QString skill, QString argumentsJson, int expiresIn) override;
    QString approveTask(QString taskId, QString intentHash, int policyVersion) override;
    QString cancelTask(QString taskId) override;
protected:
    void onContextReady() override;
private:
    struct HelperWork { QJsonObject request; int generation; };
    struct DispatchWork { QJsonObject request; QString kind; };
    void receive(const QString& name, const QVariantList& args);
    QString dispatch(const QJsonObject& request, const QString& kind);
    void startNextDispatch();
    bool hasPendingKind(const QString& kind) const;
    QString compose(const QJsonObject& command);
    void runHelper(const QJsonObject& request);
    void startNextHelper();
    void finishHelper(int code);
    void helperFailure(const QString& code);
    void applyHelperResult(const QJsonObject& result, const HelperWork& work);
    void consumeOwnerMessage(const QJsonObject& message);
    void applyOwnerResult(const QJsonObject& result, const QString& kind);
    void updatePending();
    void fail(const QString& code);
    void mergeTask(const QJsonObject& task);
    void mergeGoal(const QJsonObject& goal);
    void requestSkillsPage(int offset);

    LogosAPIClient* client_ = nullptr;
    QProcess helper_;
    QTimer helperTimeout_;
    QTimer pollTimer_;
    QQueue<HelperWork> helperQueue_;
    HelperWork activeHelper_;
    bool helperActive_ = false;
    QByteArray helperOutput_;
    QQueue<DispatchWork> dispatchQueue_;
    DispatchWork activeDispatch_;
    bool dispatchActive_ = false;
    QHash<QString, QVariantList> earlyReplies_;
    QHash<QString, QString> rpcKinds_;
    QHash<QString, qint64> rpcStarted_;
    QHash<QString, QJsonObject> ownerPending_;
    QJsonArray ownerMessages_;
    quint64 ownerCursor_ = 0;
    int generation_ = 0;
    int nextTaskOffset_ = 0;
    qint64 lastHealthCheck_ = 0;
    qint64 lastGoalPoll_ = 0;
    QString requestedSkill_;
    QString requestedTask_;
};
