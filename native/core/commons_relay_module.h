#pragma once
#include <QObject>
#include <QProcess>
#include <QJsonObject>
#include <QTimer>
#include <QSet>
#include <QHash>
#include <QJsonArray>
#include "interface.h"
#include "logos_api_client.h"

class CommonsRelayModule final : public QObject, public PluginInterface {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID PluginInterface_iid FILE "metadata.json")
    Q_INTERFACES(PluginInterface)
public:
    CommonsRelayModule();
    ~CommonsRelayModule() override;
    Q_INVOKABLE QString name() const override { return "commons_relay_module"; }
    Q_INVOKABLE QString version() const override { return "0.1.0"; }
    Q_INVOKABLE void initLogos(LogosAPI* api) { logosAPI = api; }
    Q_INVOKABLE QString configure(const QString& profile);
    Q_INVOKABLE QString request(const QString& commandJson);
    Q_INVOKABLE QString runtimeState() const;
    Q_INVOKABLE QString moduleProbe();
    Q_INVOKABLE void stop();
signals:
    void eventResponse(const QString& eventName,const QVariantList& data);
private:
    void readOutput();
    void handleBridge(const QJsonObject& request);
    void bridgeReply(const QString& id,bool ok,const QJsonValue& result,const QString& error={});
    bool attachModule(const QString& name);
    void moduleEvent(const QString& module,const QString& event,const QVariantList& args);
    QVariant invokeModule(const QString& module,const QString& method,const QVariantList& args);
    struct PendingBridge { QString id,action,session; };
    bool storageInitialized_=false, storageStarted_=false;
    QHash<QString,LogosAPIClient*> clients_;
    QHash<QString,PendingBridge> bridgePending_;
    QHash<QString,QVariantList> earlyEvents_;
    void publish(const QString& requestId,const QString& result);
    QProcess process_;
    QByteArray buffer_;
    QString error_,profile_;
    QSet<QString> pending_;
};
