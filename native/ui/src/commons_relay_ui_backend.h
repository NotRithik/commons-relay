#pragma once
#include "logos_ui_plugin_context.h"
#include "rep_commons_relay_owner_ui_source.h"
#include "logos_api_client.h"
#include <QPointer>
class CommonsRelayUiBackend final : public CommonsRelayOwnerUiSimpleSource, public LogosUiPluginContext {
public:
    CommonsRelayUiBackend()=default;
    QString configure(QString profile) override;
    QString refresh() override;
    QString sendSignedCommand(QString json) override;
protected:
    void onContextReady() override;
private:
    void receive(const QString& name,const QVariantList& args);
    LogosAPIClient* client_=nullptr;
    QString dispatch(const QString& json);
};
