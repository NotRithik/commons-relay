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
#include <QRandomGenerator>
#include <QSaveFile>
#include <QPointer>
#include "logos_types.h"
#include <dlfcn.h>
namespace {
void locationAnchor() {}
bool privateLanIp(const QString& value) {
    const auto parts=value.split('.');
    if(parts.size()!=4)return false;
    int octets[4];
    for(int n=0;n<4;++n) {
        bool ok=false;octets[n]=parts[n].toInt(&ok);
        if(!ok || octets[n]<0 || octets[n]>255 || QString::number(octets[n])!=parts[n])return false;
    }
    return octets[0]==10 || (octets[0]==172 && octets[1]>=16 && octets[1]<=31)
        || (octets[0]==192 && octets[1]==168);
}
bool privateLanPeer(const QString& value) {
    const auto match=QRegularExpression("^/ip4/([0-9.]+)/tcp/([0-9]{1,5})/p2p/([A-Za-z0-9]{20,100})$").match(value);
    return match.hasMatch() && privateLanIp(match.captured(1))
        && match.captured(2).toInt()>=1024 && match.captured(2).toInt()<=65535;
}
}
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
    const auto extensions=qEnvironmentVariable("COMMONS_RELAY_EXTENSION_ROOT");
    if(!extensions.isEmpty()) {
        const QFileInfo root(extensions);
        if(!root.isAbsolute() || !root.isDir() || root.isSymLink() || root.canonicalFilePath().isEmpty())
            return "EXTENSION_ROOT_INVALID";
        env.insert("COMMONS_RELAY_EXTENSION_ROOT",root.canonicalFilePath());
    }
    const auto sodium=qEnvironmentVariable("COMMONS_RELAY_SODIUM_LIBRARY");
    if(!sodium.isEmpty())env.insert("COMMONS_RELAY_SODIUM_LIBRARY",sodium);
    env.insert("PYTHONNOUSERSITE","1");env.insert("RISC0_DEV_MODE","0");
    // The native module owns the transport identity even while its Python
    // worker is stopped. A second Core/Basecamp instance must not reuse it.
    if (profileLease_ && profile_ != canonical) return "PROFILE_ALREADY_SELECTED";
    if (!profileLease_) {
        auto lease = std::make_unique<QLockFile>(canonical + "/.core-owner.lock");
        lease->setStaleLockTime(0);
        if (!lease->tryLock(0)) return "PROFILE_IN_USE";
        profileLease_ = std::move(lease);
    }
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
    const QSet<QString> methods={"status","skills","submit","approve","grant","revoke","recover","diagnostics","run","reconcile","task","messaging.contact","messaging.start","messaging.messages","messaging.pump","messaging.reload_contacts","controller.start","controller.status","schedule","owner.send","cancel","agent.start","agent.cards","storage.connect_local","transport.status","owner.inbox","owner.inbox_cursor","planner.status","planner.history","planner.goal","planner.start","planner.cancel","provider.status","provider.directory","provider.configure","a2a.client.card","a2a.client.verified_card","a2a.client.discover","a2a.client.request","a2a.client.response","a2a.client.events"};
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
QString CommonsRelayModule::reply(const QString& id) const {
    if(!QRegularExpression("^[a-f0-9-]{36}$").match(id).hasMatch())
        return QString::fromUtf8(QJsonDocument(QJsonObject{{"id",id.left(40)},{"success",false},{"error","INVALID_REQUEST_ID"}}).toJson(QJsonDocument::Compact));
    if(replies_.contains(id))return replies_.value(id);
    return QString::fromUtf8(QJsonDocument(QJsonObject{{"id",id},{"pending",pending_.contains(id)},
        {"expired",!pending_.contains(id)}}).toJson(QJsonDocument::Compact));
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
    if(!replies_.contains(id))replyOrder_.enqueue(id);
    replies_.insert(id,result);
    while(replyOrder_.size()>64)replies_.remove(replyOrder_.dequeue());
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
    if(!logosAPI || (name!="storage_module" && name!="delivery_module" && name!="commons_relay_wallet"))return false;
    auto* client=logosAPI->getClient(name);if(!client)return false;
    auto* object=client->requestObject(name);if(!object)return false;
    QPointer<CommonsRelayModule> self(this);
    client->onEvent(object,QString(),[self,name](const QString& event,const QVariantList& args){if(self)self->moduleEvent(name,event,args);});
    clients_.insert(name,client);return true;
}
QVariant CommonsRelayModule::invokeModule(const QString& module,const QString& method,const QVariantList& args){
    if(!attachModule(module))return {};
    return clients_.value(module)->invokeRemoteMethod(module,method,args,Timeout(15000));
}
QString CommonsRelayModule::moduleProbe() {
    QJsonObject result;
    for(const auto& name:{QString("storage_module"),QString("delivery_module")}) {
        const bool attached=attachModule(name);
        result.insert(name,QJsonObject{{"ipc_connected",attached},{"network_started",name=="storage_module" ? storageStarted_ : deliveryStarted_}});
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
    if(action.startsWith("wallet.")){handleWalletBridge(id,action,params);return;}
    if(action.startsWith("delivery.")){handleDeliveryBridge(id,action,params);return;}
    if(!action.startsWith("storage.")){bridgeReply(id,false,{},"BRIDGE_OPERATION_NOT_ALLOWED");return;}
    const QString module="storage_module";
    if(bridgePending_.contains(module)){bridgeReply(id,false,{},"MODULE_BUSY");return;}
    QString method;QVariantList args;QString waitEvent;
    if(action=="storage.version" && params.isEmpty())method="version";
    else if(action=="storage.init" && params.isEmpty()) {
        if(storageInitialized_){bridgeReply(id,true,QJsonObject{{"initialized",true}});return;}
        const auto data=profile_+"/storage-node";QDir().mkpath(data);
        QFile::setPermissions(data,QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner);
        int listenPort=0;QString storageMode="local";QJsonArray bootstraps;
        QFileInfo storageConfig(profile_+"/storage.json");
        if(storageConfig.exists()){
            if(!storageConfig.isFile() || storageConfig.isSymLink() || storageConfig.size()>32000){bridgeReply(id,false,{},"STORAGE_CONFIGURATION_INVALID");return;}
            QFile input(storageConfig.filePath());if(!input.open(QIODevice::ReadOnly)){bridgeReply(id,false,{},"STORAGE_CONFIGURATION_UNAVAILABLE");return;}
            const auto doc=QJsonDocument::fromJson(input.readAll());if(!doc.isObject()){bridgeReply(id,false,{},"STORAGE_CONFIGURATION_INVALID");return;}
            const auto config=doc.object();const QSet<QString> allowed={"mode","listenPort","bootstrapNodes"};
            for(const auto& key:config.keys())if(!allowed.contains(key)){bridgeReply(id,false,{},"STORAGE_CONFIGURATION_KEY_DENIED");return;}
            if(!config.value("mode").isString() || !config.value("listenPort").isDouble() || !config.value("bootstrapNodes").isArray()){bridgeReply(id,false,{},"STORAGE_CONFIGURATION_INVALID");return;}
            storageMode=config.value("mode").toString();listenPort=config.value("listenPort").toInt();bootstraps=config.value("bootstrapNodes").toArray();
            if((storageMode!="local" && storageMode!="official-testnet") || (listenPort!=0 && (listenPort<1024 || listenPort>65535)) || bootstraps.size()>16){bridgeReply(id,false,{},"STORAGE_CONFIGURATION_INVALID");return;}
            if(storageMode=="local" && !bootstraps.isEmpty()){bridgeReply(id,false,{},"LOCAL_STORAGE_BOOTSTRAP_DENIED");return;}
            if(storageMode=="official-testnet" && bootstraps.isEmpty()){bridgeReply(id,false,{},"STORAGE_BOOTSTRAP_REQUIRED");return;}
            for(const auto& value:bootstraps)if(!value.isString() || !QRegularExpression("^spr:[A-Za-z0-9_-]{40,2000}$").match(value.toString()).hasMatch()){bridgeReply(id,false,{},"STORAGE_BOOTSTRAP_INVALID");return;}
        }
        QJsonObject cfg{{"data-dir",data},{"listen-ip","127.0.0.1"},{"listen-port",listenPort},{"num-threads",1},
            {"api-bindaddr","127.0.0.1"},{"api-port",0},{"disc-port",0},{"nat","none"},
            {"log-level","ERROR"},{"metrics",false},{"max-peers",storageMode=="official-testnet"?24:8},{"storage-quota",268435456}};
        if(storageMode=="official-testnet")cfg.insert("bootstrap-node",bootstraps);
        method="init";args={QString::fromUtf8(QJsonDocument(cfg).toJson(QJsonDocument::Compact))};
    } else if(action=="storage.start" && params.isEmpty()) {
        if(storageStarted_){bridgeReply(id,true,QJsonObject{{"started",true}});return;}
        method="start";waitEvent="storageStart";
    }
    else if(action=="storage.connect-local" && params.size()==2 && params.value("peer_id").isString() && params.value("addresses").isArray()) {
        const auto peer=params.value("peer_id").toString();const auto array=params.value("addresses").toArray();
        if(!QRegularExpression("^[A-Za-z0-9]{20,100}$").match(peer).hasMatch() || array.isEmpty() || array.size()>4){bridgeReply(id,false,{},"LOCAL_STORAGE_PEER_INVALID");return;}
        QStringList addresses;
        for(const auto& value:array){
            if(!value.isString() || !QRegularExpression("^/ip4/127[.]0[.]0[.]1/tcp/[0-9]{4,5}$").match(value.toString()).hasMatch()){bridgeReply(id,false,{},"LOCAL_STORAGE_ADDRESS_REQUIRED");return;}
            const auto port=value.toString().section('/',-1).toInt();if(port<1024 || port>65535){bridgeReply(id,false,{},"LOCAL_STORAGE_ADDRESS_REQUIRED");return;}
            addresses.append(value.toString());
        }
        method="connect";args={peer,QVariant::fromValue(addresses)};waitEvent="storageConnect";
    }
    else if(action=="storage.publish-card" && params.size()==1 && params.value("path").isString()) {
        const QFileInfo file(params.value("path").toString());const auto allowed=QFileInfo(profile_+"/public").canonicalFilePath();
        if(allowed.isEmpty() || file.isSymLink() || !file.isFile() || file.canonicalPath()!=allowed || (file.fileName()!="agent-card.json" && file.fileName()!="public-agent-card.json") || file.size()>60000){bridgeReply(id,false,{},"PUBLIC_CARD_PATH_REJECTED");return;}
        QFile input(file.canonicalFilePath());if(!input.open(QIODevice::ReadOnly)){bridgeReply(id,false,{},"PUBLIC_CARD_UNAVAILABLE");return;}
        const auto doc=QJsonDocument::fromJson(input.readAll());
        if(!doc.isObject() || !doc.object().value("signatures").isArray() || !doc.object().value("supportedInterfaces").isArray()){bridgeReply(id,false,{},"SIGNED_AGENT_CARD_REQUIRED");return;}
        method="uploadUrl";args={QUrl::fromLocalFile(file.canonicalFilePath()),65536};waitEvent="storageUploadDone";
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
        if(!QRegularExpression("^[A-Za-z0-9]{20,180}$").match(cid).hasMatch() || allowed.isEmpty() || file.exists() || file.isSymLink() || QFileInfo(file.absolutePath()).canonicalFilePath()!=allowed){bridgeReply(id,false,{},"DOWNLOAD_PATH_REJECTED");return;}
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
    if(module=="commons_relay_wallet" && event=="walletResult" && args.size()==2) {
        if(!bridgePending_.contains(module))return;
        const auto pending=bridgePending_.value(module);
        if(pending.session.isEmpty()){earlyEvents_.insert(module+":walletResult",args);return;}
        if(pending.session!=args[0].toString())return;
        const auto reply=QJsonDocument::fromJson(args[1].toString().toUtf8()).object();
        bridgePending_.remove(module);
        const auto code=reply.value("error").toString();
        bridgeReply(pending.id,reply.value("success").toBool(),reply.value("result"),QRegularExpression("^[A-Z_0-9]{1,100}$").match(code).hasMatch()?code:"WALLET_OPERATION_FAILED");return;
    }
    if(module=="delivery_module") {
        if(event=="messageReceived" && args.size()>=4) {
            const auto topic=args[1].toString();const auto encoded=args[2].toString();
            if(!deliveryTopics_.contains(topic) || encoded.size()>65536)return;
            const auto decoded=QByteArray::fromBase64(encoded.toUtf8(),QByteArray::AbortOnBase64DecodingErrors);
            if(decoded.isEmpty() || decoded.size()>48000)return;
            deliveryEvents_.enqueue(QJsonObject{{"sequence",QString::number(++deliverySequence_)},{"event",event},
                {"hash",args[0].toString().left(160)},{"topic",topic},{"payload",QString::fromUtf8(decoded)}});
        }else if((event=="messageSent" || event=="messageError" || event=="messagePropagated") && args.size()>=2) {
            deliveryEvents_.enqueue(QJsonObject{{"sequence",QString::number(++deliverySequence_)},{"event",event},
                {"request_id",args[0].toString().left(160)},{"hash",args[1].toString().left(160)}});
        }
        while(deliveryEvents_.size()>256)deliveryEvents_.dequeue();
        return;
    }
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

void CommonsRelayModule::handleDeliveryBridge(const QString& id,const QString& action,const QJsonObject& params) {
    if (action == "delivery.health" && params.isEmpty()) {
        bool ok = false;
        const auto value = unwrapModuleResult(invokeModule("delivery_module", "getNodeInfo", {QString("Metrics")}), &ok);
        if (!ok) { bridgeReply(id, false, {}, "DELIVERY_HEALTH_UNAVAILABLE"); return; }
        const auto text = value.toString();
        if (text.size() > 500000) { bridgeReply(id, false, {}, "DELIVERY_METRICS_LIMIT"); return; }
        const QHash<QString, QString> accepted = {
            {"libp2p_peers", "connected_peers"}, {"libp2p_pubsub_peers", "relay_peers"},
            {"libp2p_pubsub_topics", "subscribed_shards"}, {"libp2p_gossipsub_no_peers_topics", "unreachable_shards"}
        };
        QJsonObject health;
        const QRegularExpression metric("^([a-z0-9_]+) ([0-9]+)(?:[.]0+)?$");
        for (const auto& line : text.split('\n')) {
            const auto match = metric.match(line.trimmed());
            if (match.hasMatch() && accepted.contains(match.captured(1)))
                health.insert(accepted.value(match.captured(1)), match.captured(2).toInt());
        }
        health.insert("checked", true);
        bridgeReply(id, true, health); return;
    }
    const auto topicOk=[](const QString& topic){return QRegularExpression("^/commons-relay/1/[A-Za-z0-9_-]{1,128}/json$").match(topic).hasMatch();};
    if(action=="delivery.events" && params.size()==1 && params.value("after").isString()) {
        bool valid=false;const quint64 after=params.value("after").toString().toULongLong(&valid);
        if(!valid){bridgeReply(id,false,{},"INVALID_EVENT_CURSOR");return;}
        QJsonArray events;int size=0;
        for(const auto& event:deliveryEvents_){
            if(event.value("sequence").toString().toULongLong()<=after)continue;
            const int bytes=QJsonDocument(event).toJson(QJsonDocument::Compact).size();
            if(size+bytes>52000)break;size+=bytes;events.append(event);
        }
        bridgeReply(id,true,QJsonObject{{"events",events},{"latest",QString::number(deliverySequence_)},
            {"oldest",deliveryEvents_.isEmpty()?QString::number(deliverySequence_):deliveryEvents_.head().value("sequence").toString()}});return;
    }
    QString method;QVariantList args;
    if(action=="delivery.init" && params.isEmpty()) {
        if(deliveryInitialized_){bridgeReply(id,true,QJsonObject{{"initialized",true}});return;}
        QFileInfo file(profile_+"/delivery.json");
        if(!file.isFile() || file.isSymLink() || file.size()>10000){bridgeReply(id,false,{},"DELIVERY_CONFIGURATION_REQUIRED");return;}
        QFile input(file.filePath());if(!input.open(QIODevice::ReadOnly)){bridgeReply(id,false,{},"DELIVERY_CONFIGURATION_UNAVAILABLE");return;}
        const auto doc=QJsonDocument::fromJson(input.readAll());
        if(!doc.isObject()){bridgeReply(id,false,{},"DELIVERY_CONFIGURATION_INVALID");return;}
        const auto config=doc.object();
        const QSet<QString> allowed={"mode","clusterId","tcpPort","entryNodes","advertiseAddress"};
        for(const auto& key:config.keys())if(!allowed.contains(key)){bridgeReply(id,false,{},"DELIVERY_CONFIGURATION_KEY_DENIED");return;}
        const auto port=config.value("tcpPort").toInt();const auto mode=config.value("mode").toString("local");
        if(port<1024 || port>65535 || (mode!="local" && mode!="lan" && mode!="logos.dev")){bridgeReply(id,false,{},"DELIVERY_CONFIGURATION_INVALID");return;}
        if(mode!="lan" && config.contains("advertiseAddress")){bridgeReply(id,false,{},"LAN_ADDRESS_REQUIRES_EXPLICIT_LAN_MODE");return;}
        if(mode=="lan" && !privateLanIp(config.value("advertiseAddress").toString())){bridgeReply(id,false,{},"PRIVATE_LAN_ADDRESS_REQUIRED");return;}
        if(mode=="local" || mode=="lan"){
            const auto cluster=config.value("clusterId").toInt();const auto nodes=config.value("entryNodes").toArray();
            if(cluster<2 || cluster>65535 || !config.value("entryNodes").isArray() || nodes.size()>8){bridgeReply(id,false,{},"DELIVERY_CONFIGURATION_INVALID");return;}
            for(const auto& node:nodes) {
                const bool valid=node.isString() && (mode=="lan" ? privateLanPeer(node.toString())
                    : QRegularExpression("^/ip4/127[.]0[.]0[.]1/tcp/[0-9]{4,5}/p2p/[A-Za-z0-9]{20,100}$").match(node.toString()).hasMatch());
                if(!valid){bridgeReply(id,false,{},mode=="lan" ? "PRIVATE_LAN_PEER_REQUIRED" : "LOCAL_TEST_PEER_REQUIRED");return;}
            }
        }else if(config.contains("clusterId") || config.contains("entryNodes")){
            // Public mode is deliberately a named preset rather than an arbitrary
            // internet peer list. The reviewed Delivery module owns the current
            // Logos Dev Network bootstrap records.
            bridgeReply(id,false,{},"DELIVERY_PRESET_OVERRIDES_DENIED");return;
        }
        // Stable local peer identity survives a process restart. The key is
        // operator-private and never returned through the model/GUI APIs.
        const auto nodeKeyPath=profile_+"/delivery-node.key";QFileInfo keyInfo(nodeKeyPath);QByteArray nodeKey;
        if(keyInfo.exists()){
            if(keyInfo.isSymLink() || keyInfo.size()!=64){bridgeReply(id,false,{},"DELIVERY_NODE_KEY_INVALID");return;}
            QFile keyFile(nodeKeyPath);if(!keyFile.open(QIODevice::ReadOnly)){bridgeReply(id,false,{},"DELIVERY_NODE_KEY_UNAVAILABLE");return;}
            nodeKey=keyFile.readAll();
            if(!QRegularExpression("^[0-9a-f]{64}$").match(QString::fromLatin1(nodeKey)).hasMatch()){bridgeReply(id,false,{},"DELIVERY_NODE_KEY_INVALID");return;}
        }else{
            QByteArray random(32,'\0');
            do {for(int n=0;n<32;n+=4){quint32 v=QRandomGenerator::system()->generate();memcpy(random.data()+n,&v,4);}nodeKey=random.toHex();}
            while(nodeKey==QByteArray(64,'0') || nodeKey>=QByteArray("fffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141"));
            random.fill(0);QSaveFile keyFile(nodeKeyPath);if(!keyFile.open(QIODevice::WriteOnly)){bridgeReply(id,false,{},"DELIVERY_NODE_KEY_WRITE_FAILED");return;}
            keyFile.setPermissions(QFile::ReadOwner|QFile::WriteOwner);keyFile.write(nodeKey);if(!keyFile.commit()){bridgeReply(id,false,{},"DELIVERY_NODE_KEY_WRITE_FAILED");return;}
        }
        QJsonObject cfg{{"nodekey",QString::fromLatin1(nodeKey)},{"tcpPort",port},{"listenAddress",mode=="lan" ? "0.0.0.0" : "127.0.0.1"},
            {"relay",true},{"rest",false},{"restAdmin",false},{"metricsServer",false},{"metricsLogging",false},{"websocketSupport",false},
            {"logLevel","ERROR"},{"store",false},{"storeMessageDbUrl","sqlite://"+profile_+"/delivery-store.sqlite3"}};
        if(mode=="logos.dev"){
            // Let the reviewed preset choose its supported NAT/discovery defaults;
            // overriding `nat` here broke the current Delivery 1.1.0 builder.
            cfg.insert("mode","Core");cfg.insert("preset","logos.dev");cfg.insert("discv5UdpPort",port);
        }else{
            cfg.insert("mode","noMode");cfg.insert("clusterId",config.value("clusterId"));cfg.insert("entryNodes",config.value("entryNodes"));
            cfg.insert("rlnRelay",false);cfg.insert("numShardsInNetwork",1);cfg.insert("shards",QJsonArray{0});
            cfg.insert("relayPeerExchange",false);cfg.insert("peerExchange",false);cfg.insert("dnsDiscovery",false);cfg.insert("discv5Discovery",false);cfg.insert("nat",mode=="lan" ? "extip:"+config.value("advertiseAddress").toString() : "extip:127.0.0.1");
        }
        method="createNode";args={QString::fromUtf8(QJsonDocument(cfg).toJson(QJsonDocument::Compact))};
    }else if(action=="delivery.start" && params.isEmpty()) {
        if(deliveryStarted_){bridgeReply(id,true,QJsonObject{{"started",true}});return;}
        if(!deliveryInitialized_){bridgeReply(id,false,{},"DELIVERY_NOT_INITIALIZED");return;}method="start";
    }else if(action=="delivery.info" && params.isEmpty()) {method="getNodeInfo";args={QString("MyMultiaddresses")};}
    else if((action=="delivery.subscribe" || action=="delivery.unsubscribe") && params.size()==1 && params.value("topic").isString()) {
        const auto topic=params.value("topic").toString();
        if(!topicOk(topic) || (action=="delivery.subscribe" && !deliveryTopics_.contains(topic) && deliveryTopics_.size()>=64)){bridgeReply(id,false,{},"DELIVERY_TOPIC_DENIED");return;}
        if(action=="delivery.subscribe" && deliveryTopics_.contains(topic)){bridgeReply(id,true,true);return;}
        method=action=="delivery.subscribe"?"subscribe":"unsubscribe";args={topic};
    }else if(action=="delivery.publish-card" && params.size()==2 && params.value("topic").isString() && params.value("payload").isString()) {
        const auto topic=params.value("topic").toString();const auto payload=params.value("payload").toString();
        if(!QRegularExpression("^/commons-relay/1/discovery-[a-f0-9]{32}/json$").match(topic).hasMatch() || payload.toUtf8().size()>48000){bridgeReply(id,false,{},"DISCOVERY_TOPIC_DENIED");return;}
        const auto doc=QJsonDocument::fromJson(payload.toUtf8());
        const auto frame=doc.object(); const auto body=frame.value("body").toObject();
        const auto kind=body.value("kind").toString();
        const bool legacy=frame.value("kind").toString()=="agent-card"
            && frame.value("card").toObject().value("signatures").isArray();
        const bool publicFrame=frame.value("key_id").isString() && frame.value("signature").isString()
            && body.value("domain").toString()=="commons/relay/public-discovery/v1"
            && body.value("sender").isObject() && body.value("topic").isString()
            && (kind=="query" || (kind=="agent-card" && body.value("card").toObject().value("signatures").isArray()));
        if(!doc.isObject() || (!legacy && !publicFrame)){bridgeReply(id,false,{},"SIGNED_AGENT_CARD_REQUIRED");return;}
        method="send";args={topic,payload};
    }else if(action=="delivery.send" && params.size()==2 && params.value("topic").isString() && params.value("payload").isString()) {
        const auto topic=params.value("topic").toString();const auto payload=params.value("payload").toString();
        if(!topicOk(topic) || payload.toUtf8().size()>48000){bridgeReply(id,false,{},"DELIVERY_PAYLOAD_DENIED");return;}
        const auto parsed=QJsonDocument::fromJson(payload.toUtf8());
        if(!parsed.isObject() || !parsed.object().contains("ciphertext")){bridgeReply(id,false,{},"DELIVERY_REQUIRES_ENCRYPTED_ENVELOPE");return;}
        method="send";args={topic,payload};
    }else{bridgeReply(id,false,{},"BRIDGE_OPERATION_NOT_ALLOWED");return;}
    bool ok=false;auto result=unwrapModuleResult(invokeModule("delivery_module",method,args),&ok);
    if(!ok){bridgeReply(id,false,{},"DELIVERY_MODULE_REJECTED");return;}
    if(action=="delivery.init")deliveryInitialized_=true;
    if(action=="delivery.start")deliveryStarted_=true;
    if(action=="delivery.subscribe")deliveryTopics_.insert(params.value("topic").toString());
    if(action=="delivery.unsubscribe")deliveryTopics_.remove(params.value("topic").toString());
    bridgeReply(id,true,QJsonValue::fromVariant(result));
}

void CommonsRelayModule::handleWalletBridge(const QString& id,const QString& action,const QJsonObject& params){
    const QString module="commons_relay_wallet";
    if(action=="wallet.init" && params.isEmpty()){
        if(walletInitialized_){bridgeReply(id,true,true);return;}
        QFileInfo config(profile_+"/wallet.json");
        if(!config.isFile() || config.isSymLink() || config.size()>4096){bridgeReply(id,false,{},"WALLET_BINDING_REQUIRED");return;}
        QFile file(config.filePath());if(!file.open(QIODevice::ReadOnly)){bridgeReply(id,false,{},"WALLET_BINDING_UNAVAILABLE");return;}
        const auto object=QJsonDocument::fromJson(file.readAll()).object();
        if(object.size()!=1 || !object.value("profile").isString()){bridgeReply(id,false,{},"WALLET_BINDING_INVALID");return;}
        const auto result=invokeModule(module,"configure",{object.value("profile").toString(),profile_+"/inputs"}).toString();
        if(result!="WALLET_CONFIGURED"){bridgeReply(id,false,{},"WALLET_CONFIGURATION_REJECTED");return;}
        walletInitialized_=true;bridgeReply(id,true,true);return;
    }
    if(action!="wallet.invoke" || params.size()!=2 || !params.value("mode").isString() || !params.value("params").isObject()){bridgeReply(id,false,{},"BRIDGE_OPERATION_NOT_ALLOWED");return;}
    if(!walletInitialized_){bridgeReply(id,false,{},"WALLET_NOT_INITIALIZED");return;}
    if(bridgePending_.contains(module)){bridgeReply(id,false,{},"WALLET_BUSY");return;}
    bridgePending_.insert(module,{id,"walletResult",{}});
    const auto request=QString::fromUtf8(QJsonDocument(params).toJson(QJsonDocument::Compact));
    const auto result=invokeModule(module,"request",{request}).toString();
    if(!QRegularExpression("^[a-f0-9-]{36}$").match(result).hasMatch()){
        bridgePending_.remove(module);bridgeReply(id,false,{},result=="WALLET_BUSY"?result:"WALLET_REQUEST_REJECTED");return;
    }
    if(!bridgePending_.contains(module))return;
    auto pending=bridgePending_.value(module);pending.session=result;bridgePending_.insert(module,pending);
    const auto early=module+":walletResult";
    if(earlyEvents_.contains(early)){const auto args=earlyEvents_.take(early);moduleEvent(module,"walletResult",args);}
    QTimer::singleShot(7190000,this,[this,module,id](){if(bridgePending_.contains(module)&&bridgePending_.value(module).id==id){bridgePending_.remove(module);bridgeReply(id,false,{},"WALLET_PROOF_TIME_LIMIT");}});
}
