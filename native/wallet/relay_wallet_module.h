#pragma once
#include <QObject>
#include <QProcess>
#include <QJsonObject>
#include <QHash>
#include <QQueue>
#include "interface.h"
class RelayWalletModule final : public QObject, public PluginInterface {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID PluginInterface_iid FILE "metadata.json")
    Q_INTERFACES(PluginInterface)
public:
    RelayWalletModule();
    ~RelayWalletModule() override;
    Q_INVOKABLE QString name() const override {return "commons_relay_wallet";}
    Q_INVOKABLE QString version() const override {return "0.1.0";}
    Q_INVOKABLE void initLogos(LogosAPI* api){logosAPI=api;}
    Q_INVOKABLE QString configure(const QString& profile,const QString& programRoot);
    Q_INVOKABLE QString request(const QString& json);
    Q_INVOKABLE QString reply(const QString& requestId) const;
    Q_INVOKABLE QString state() const;
signals:
    void eventResponse(const QString& event,const QVariantList& data);
private:
    void finish(int code,QProcess::ExitStatus status);
    void consume();
    void publish(QJsonObject result);
    QProcess process_;
    QString profile_,programRoot_,binary_,active_,operation_,lastCode_;
    QByteArray buffer_;
    QJsonObject typedResult_;
    QHash<QString,QString> replies_;
    QQueue<QString> order_;
};
