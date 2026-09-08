#include "commons_relay_ui_backend.h"
#include "logos_sdk.h"
#include "logos_api.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QTimer>

void CommonsRelayUiBackend::onContextReady(){
    client_=modules().api->getClient("commons_relay_module");
    if(!client_){setLastError("CommonsRelay core module is unavailable.");return;}
    auto* object=client_->requestObject("commons_relay_module");
    if(!object){setLastError("Load the CommonsRelay runtime module in this Basecamp instance.");return;}
    QPointer<CommonsRelayUiBackend> self(this);
    client_->onEvent(object,QString("commons_relayResult"),[self](const QString& name,const QVariantList& args){if(self)self->receive(name,args);});
    setReady(true);setStatusText("Core module connected. Select an agent profile.");
    const auto profile=qEnvironmentVariable("COMMONS_RELAY_DEFAULT_PROFILE");
    if(!profile.isEmpty())QTimer::singleShot(0,this,[this,profile](){configure(profile);});
}
QString CommonsRelayUiBackend::configure(QString profile){
    if(!client_)return "CORE_UNAVAILABLE";
    const auto reply=client_->invokeRemoteMethod(QString("commons_relay_module"),QString("configure"),QVariantList{profile}).toString();
    setStatusText(reply);
    if(reply=="STARTING_LOCAL_RUNTIME" || reply=="RUNTIME_ALREADY_CONFIGURED")QTimer::singleShot(700,this,[this](){refresh();});
    else setLastError(reply);
    return reply;
}
QString CommonsRelayUiBackend::dispatch(const QString& json){
    if(!client_)return "CORE_UNAVAILABLE";
    const auto reply=client_->invokeRemoteMethod(QString("commons_relay_module"),QString("request"),QVariantList{json}).toString();
    if(reply.size()!=36){setLastError(reply);setStatusText("The request was not accepted.");}
    return reply;
}
QString CommonsRelayUiBackend::refresh(){return dispatch("{\"method\":\"status\",\"params\":{}}");}
QString CommonsRelayUiBackend::pollOwnerChannel(){return dispatch("{\"method\":\"messaging.pump\",\"params\":{}}");}
QString CommonsRelayUiBackend::sendSignedCommand(QString json){
    if(json.toUtf8().size()>60000){setLastError("Request is too large.");return "REQUEST_LIMIT";}
    return dispatch(json);
}
void CommonsRelayUiBackend::receive(const QString&,const QVariantList& args){
    if(args.size()!=2)return;
    const auto doc=QJsonDocument::fromJson(args[1].toString().toUtf8());
    if(!doc.isObject()){setLastError("Invalid runtime reply.");return;}
    const auto object=doc.object();
    setLastResultJson(QString::fromUtf8(doc.toJson(QJsonDocument::Indented)));
    if(!object.value("success").toBool()){
        setLastError(object.value("error").toString());setStatusText("The runtime rejected this request.");return;
    }
    setLastError("");const auto data=object.value("result").toObject();
    if(data.contains("tasks") && data.contains("skills")){
        setConnected(true);setAgentId(data.value("agent_id").toString());
        setTasksJson(QString::fromUtf8(QJsonDocument(data.value("tasks").toArray()).toJson(QJsonDocument::Compact)));
        setSkillsJson(QString::fromUtf8(QJsonDocument(data.value("skills").toArray()).toJson(QJsonDocument::Compact)));
        auto summary=data;summary.remove("tasks");summary.remove("skills");
        setSummaryJson(QString::fromUtf8(QJsonDocument(summary).toJson(QJsonDocument::Compact)));
        setStatusText("Relay profile connected. Inference is off; no model charges.");
    }else if(data.contains("received")){
        setStatusText("Owner channel checked. Reading authenticated replies...");
        QTimer::singleShot(250,this,[this](){
            dispatch(QString("{\"method\":\"messaging.messages\",\"params\":{\"after\":%1}}").arg(ownerCursor_));
        });
    }else if(data.contains("messages")){
        const auto messages=data.value("messages").toArray();
        for(const auto& value:messages)if(value.isObject())ownerCursor_=qMax(ownerCursor_,value.toObject().value("cursor").toVariant().toULongLong());
        setOwnerMessagesJson(QString::fromUtf8(QJsonDocument(messages).toJson(QJsonDocument::Compact)));
        setStatusText(messages.isEmpty()?"No new owner-channel replies.":"Authenticated owner-channel replies received over Logos Messaging.");
    }else if(data.contains("message_id") && data.contains("recipient")){
        setStatusText("Encrypted owner command queued over Logos Messaging. Poll replies to read the remote result.");
    }else{
        setStatusText("Command processed. Refresh to inspect current tasks.");
    }
}
