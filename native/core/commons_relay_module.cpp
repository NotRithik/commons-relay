#include "commons_relay_module.h"
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QStandardPaths>
#include <QUuid>
#include <QFile>
#include <QUrl>
#include <QRegularExpression>
#include <QPointer>
#include "logos_types.h"
#include <dlfcn.h>
namespace { void locationAnchor() {} }
CommonsRelayModule::CommonsRelayModule() {
    process_.setProcessChannelMode(QProcess::SeparateChannels);
    connect(&process_,&QProcess::readyReadStandardOutput,this,&CommonsRelayModule::readOutput);
    // Private Python diagnostics are never copied into the module event stream.
    connect(&process_,&QProcess::readyReadStandardError,this,[this](){ process_.readAllStandardError(); });
    connect(&process_,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this](int code,QProcess::ExitStatus){
        const auto ids=pending_.values();pending_.clear();
        for(const auto& id:ids) publish(id,QString::fromUtf8(QJsonDocument(QJsonObject{{"id",id},{"success",false},{"error","RUNTIME_STOPPED"}}).toJson(QJsonDocument::Compact)));
        if(code!=0)error_="RUNTIME_STOPPED";
    });
}
CommonsRelayModule::~CommonsRelayModule(){ stop(); }
QString CommonsRelayModule::configure(const QString& rawProfile) {
    if(process_.state()!=QProcess::NotRunning)return "RUNTIME_ALREADY_CONFIGURED";
    QFileInfo info(rawProfile);
    const auto allowed=QFileInfo(qEnvironmentVariable("COMMONS_RELAY_ALLOWED_STATE_ROOT")).canonicalFilePath();
    const auto canonical=info.canonicalFilePath();
    if(allowed.isEmpty() || !info.isDir() || info.isSymLink() || canonical.isEmpty() || !canonical.startsWith(allowed+"/")) return "PROFILE_OUTSIDE_CONFIGURED_ROOT";
    QFileInfo config(canonical+"/settings.json");
    if(!config.isFile() || config.isSymLink())return "MISSING_AGENT_SETTINGS";
    QString python=qEnvironmentVariable("COMMONS_RELAY_PYTHON");
    if(python.isEmpty())python=QStandardPaths::findExecutable("python3");
    if(!QFileInfo(python).isExecutable())return "PYTHON_NOT_AVAILABLE";
    Dl_info location{};
    if(!dladdr(reinterpret_cast<void*>(&locationAnchor),&location) || !location.dli_fname)return "MODULE_LOCATION_UNKNOWN";
    const QString moduleDir=QFileInfo(QString::fromUtf8(location.dli_fname)).canonicalPath();
    const QString script=moduleDir+"/commons_relay_worker.py";
    if(!QFileInfo(script).isFile() || QFileInfo(script).isSymLink())return "BUNDLED_RUNTIME_MISSING";
    QProcessEnvironment env;
    env.insert("PATH","/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin");
    env.insert("HOME",canonical);env.insert("TMPDIR",canonical+"/tmp/");
    QDir().mkpath(canonical+"/tmp");
    const auto sodium=qEnvironmentVariable("COMMONS_RELAY_SODIUM_LIBRARY");
    if(!sodium.isEmpty())env.insert("COMMONS_RELAY_SODIUM_LIBRARY",sodium);
    env.insert("PYTHONNOUSERSITE","1");env.insert("RISC0_DEV_MODE","0");
    process_.setProcessEnvironment(env);process_.setWorkingDirectory(canonical);
    process_.setProgram(python);process_.setArguments({"-I",script,"--profile",canonical,"--native-bridge"});
    process_.start();profile_=canonical;error_.clear();
    return "STARTING_LOCAL_RUNTIME";
}
QString CommonsRelayModule::request(const QString& raw) {
    if(process_.state()==QProcess::NotRunning)return "RUNTIME_NOT_CONFIGURED";
    if(raw.toUtf8().size()>60000 || pending_.size()>=32)return "REQUEST_LIMIT";
    QJsonParseError parse;
    auto doc=QJsonDocument::fromJson(raw.toUtf8(),&parse);
    if(parse.error!=QJsonParseError::NoError || !doc.isObject())return "INVALID_REQUEST_JSON";
    auto object=doc.object();QString method=object.value("method").toString();
    const QSet<QString> methods={"status","skills","submit","approve","grant","revoke","recover","diagnostics","run","reconcile"};
    if(!methods.contains(method) || !object.value("params").isObject() || object.size()!=2)return "METHOD_NOT_ALLOWED";
    const auto id=QUuid::createUuid().toString(QUuid::WithoutBraces);
    object.insert("id",id);pending_.insert(id);
    process_.write(QJsonDocument(object).toJson(QJsonDocument::Compact)+"\n");
    return id;
}
QString CommonsRelayModule::runtimeState() const {
    return QString::fromUtf8(QJsonDocument(QJsonObject{{"running",process_.state()==QProcess::Running},{"pending",pending_.size()},
        {"configured",!profile_.isEmpty()},{"error",error_},{"inference_enabled",false}}).toJson(QJsonDocument::Compact));
}
void CommonsRelayModule::readOutput(){
    buffer_+=process_.readAllStandardOutput();
    if(buffer_.size()>131072){error_="RUNTIME_OUTPUT_LIMIT";stop();return;}
    while(buffer_.contains('\n')){
        const int at=buffer_.indexOf('\n');auto line=buffer_.left(at);buffer_.remove(0,at+1);
        QJsonParseError err;auto doc=QJsonDocument::fromJson(line,&err);
        if(err.error!=QJsonParseError::NoError || !doc.isObject()){error_="INVALID_RUNTIME_RESPONSE";stop();return;}
        auto object=doc.object();
        if(object.value("kind").toString()=="bridge_request") { handleBridge(object); continue; }
        auto id=object.value("id").toString();
        if(!pending_.remove(id)){error_="UNEXPECTED_RUNTIME_RESPONSE";continue;}
        publish(id,QString::fromUtf8(line));
    }
}
void CommonsRelayModule::publish(const QString& id,const QString& result){
    emit eventResponse("commons_relayResult",{id,result});
    // Logos Core's QtProviderObject forwards eventResponse. Emitting it twice
    // would deliver duplicate replies to every subscriber.
}
void CommonsRelayModule::stop(){
    if(process_.state()!=QProcess::NotRunning){process_.terminate();if(!process_.waitForFinished(2000)){process_.kill();process_.waitForFinished(1000);}}
    buffer_.clear();
}

namespace {
QVariant unwrapModuleResult(const QVariant& value,bool* ok) {
    if(value.canConvert<LogosResult>()) { const auto r=value.value<LogosResult>();*ok=r.success;return r.value; }
    *ok=value.isValid();return value;
}
}
bool CommonsRelayModule::attachModule(const QString& name) {
    if(clients_.contains(name))return true;
    if(!logosAPI || (name!="storage_module" && name!="chat_module"))return false;
    auto* client=logosAPI->getClient(name);if(!client)return false;
    auto* object=client->requestObject(name);if(!object)return false;
    QPointer<CommonsRelayModule> self(this);
    client->onEvent(object,QString("*"),[self,name](const QString& event,const QVariantList& args){if(self)self->moduleEvent(name,event,args);});
    clients_.insert(name,client);return true;
}
QVariant CommonsRelayModule::invokeModule(const QString& module,const QString& method,const QVariantList& args){
    if(!attachModule(module))return {};
    return clients_.value(module)->invokeRemoteMethod(module,method,args,Timeout(15000));
}
QString CommonsRelayModule::moduleProbe() {
    QJsonObject result;
    for(const auto& name:{QString("storage_module"),QString("chat_module")}) {
        const bool attached=attachModule(name);
        result.insert(name,QJsonObject{{"ipc_connected",attached},{"network_started",name=="storage_module" ? storageStarted_ : false}});
    }
    return QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Compact));
}
void CommonsRelayModule::bridgeReply(const QString& id,bool ok,const QJsonValue& result,const QString& error) {
    QJsonObject reply{{"kind","bridge_response"},{"id",id},{"success",ok},{"result",result}};
    if(!ok)reply.insert("error",error);
    process_.write(QJsonDocument(reply).toJson(QJsonDocument::Compact)+"\n");
}
void CommonsRelayModule::handleBridge(const QJsonObject& request) {
    const auto id=request.value("id").toString();const auto action=request.value("action").toString();
    if(!QRegularExpression("^bridge-[a-f0-9]{32}$").match(id).hasMatch() || !request.value("params").isObject())return;
    const auto params=request.value("params").toObject();
    if(profile_.isEmpty()){bridgeReply(id,false,{},"PROFILE_NOT_CONFIGURED");return;}
    if(action=="modules.probe") { bridgeReply(id,true,QJsonDocument::fromJson(moduleProbe().toUtf8()).object());return; }
    if(!action.startsWith("storage.")){bridgeReply(id,false,{},"BRIDGE_OPERATION_NOT_ALLOWED");return;}
    const QString module="storage_module";
    if(bridgePending_.contains(module)){bridgeReply(id,false,{},"MODULE_BUSY");return;}
    QString method;QVariantList args;QString waitEvent;
    if(action=="storage.version" && params.isEmpty())method="version";
    else if(action=="storage.init" && params.isEmpty()) {
        if(storageInitialized_){bridgeReply(id,true,QJsonObject{{"initialized",true}});return;}
        const auto data=profile_+"/storage-node";QDir().mkpath(data);
        QFile::setPermissions(data,QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner);
        QJsonObject cfg{{"data-dir",data},{"listen-ip","127.0.0.1"},{"listen-port",0},{"num-threads",1},
            {"api-bindaddr","127.0.0.1"},{"api-port",0},{"disc-port",0},{"nat","none"},
            {"log-level","ERROR"},{"metrics",false},{"max-peers",8},{"storage-quota",268435456}};
        method="init";args={QString::fromUtf8(QJsonDocument(cfg).toJson(QJsonDocument::Compact))};
    } else if(action=="storage.start" && params.isEmpty()) {
        if(storageStarted_){bridgeReply(id,true,QJsonObject{{"started",true}});return;}
        method="start";waitEvent="storageStart";
    }
    else if(action=="storage.upload" && params.size()==1 && params.value("path").isString()) {
        QFileInfo file(params.value("path").toString());
        const auto allowed=QFileInfo(profile_+"/vault/blobs").canonicalFilePath();const auto path=file.canonicalFilePath();
        if(allowed.isEmpty() || file.isSymLink() || !file.isFile() || !path.startsWith(allowed+"/") || file.size()>269484032) {bridgeReply(id,false,{},"UPLOAD_PATH_REJECTED");return;}
        QFile input(path);if(!input.open(QIODevice::ReadOnly) || input.read(8)!=QByteArray("CSTVLT1\0",8)){bridgeReply(id,false,{},"UPLOAD_MUST_BE_ENCRYPTED");return;}
        method="uploadUrl";args={QUrl::fromLocalFile(path),65536};waitEvent="storageUploadDone";
    } else if(action=="storage.download" && params.size()==2 && params.value("address").isString() && params.value("path").isString()) {
        const auto cid=params.value("address").toString();QFileInfo file(params.value("path").toString());
        const auto allowed=QFileInfo(profile_+"/vault/downloads").canonicalFilePath();
        if(!QRegularExpression("^[A-Za-z0-9]{20,180}$").match(cid).hasMatch() || allowed.isEmpty() || file.exists() || file.isSymLink() || file.canonicalPath()!=allowed){bridgeReply(id,false,{},"DOWNLOAD_PATH_REJECTED");return;}
        method="downloadToUrl";args={cid,QUrl::fromLocalFile(file.absoluteFilePath()),false,65536};waitEvent="storageDownloadDone";
    } else if(action=="storage.manifests" && params.isEmpty())method="manifests";
    else {bridgeReply(id,false,{},"BRIDGE_OPERATION_NOT_ALLOWED");return;}
    if(!waitEvent.isEmpty())bridgePending_.insert(module,{id,waitEvent,{}});
    bool ok=false;const auto value=unwrapModuleResult(invokeModule(module,method,args),&ok);
    if(!ok || (value.metaType().id()==QMetaType::Bool && !value.toBool())) {
        bridgePending_.remove(module);bridgeReply(id,false,{},"MODULE_CALL_REJECTED");return;
    }
    if(waitEvent.isEmpty()){
        if(action=="storage.init")storageInitialized_=true;
        bridgeReply(id,true,QJsonValue::fromVariant(value));return;
    }
    if(!bridgePending_.contains(module))return; // Early completion already replied.
    auto pending=bridgePending_.value(module);pending.session=value.toString();bridgePending_.insert(module,pending);
    const auto earlyKey=module+":"+waitEvent;
    if(earlyEvents_.contains(earlyKey)){const auto event=earlyEvents_.take(earlyKey);moduleEvent(module,waitEvent,event);}
    QTimer::singleShot(90000,this,[this,module,id](){
        if(bridgePending_.contains(module) && bridgePending_.value(module).id==id){bridgePending_.remove(module);bridgeReply(id,false,{},"MODULE_EVENT_TIMEOUT");}
    });
}
void CommonsRelayModule::moduleEvent(const QString& module,const QString& event,const QVariantList& args) {
    if(!bridgePending_.contains(module))return;
    const auto pending=bridgePending_.value(module);
    if(event!=pending.action)return;
    if(event=="storageUploadDone" || event=="storageDownloadDone") {
        if(pending.session.isEmpty()){earlyEvents_.insert(module+":"+event,args);return;}
        if(args.size()<3 || args[1].toString()!=pending.session)return;
    }
    bridgePending_.remove(module);
    const bool ok=!args.isEmpty() && args[0].toBool();
    if(!ok){bridgeReply(pending.id,false,{},"MODULE_OPERATION_FAILED");return;}
    if(event=="storageStart")storageStarted_=true;
    QJsonObject result{{"event",event},{"success",true}};
    if(event=="storageUploadDone")result.insert("address",args[2].toString());
    bridgeReply(pending.id,true,result);
}
