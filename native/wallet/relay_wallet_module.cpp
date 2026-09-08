#include "relay_wallet_module.h"
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonArray>
#include <QSaveFile>
#include <QRegularExpression>
#include <QUuid>
#include <QTimer>
RelayWalletModule::RelayWalletModule(){
    process_.setProcessChannelMode(QProcess::SeparateChannels);
    connect(&process_,&QProcess::readyReadStandardOutput,this,&RelayWalletModule::consume);
    connect(&process_,&QProcess::readyReadStandardError,this,[this](){
        const auto data=QString::fromUtf8(process_.readAllStandardError());
        for(const auto& line:data.split('\n'))if(line.startsWith("Relay wallet: ")){
            const auto code=line.mid(14).trimmed();if(QRegularExpression("^[A-Z_0-9]{1,100}$").match(code).hasMatch())lastCode_=code;
        }
    });
    connect(&process_,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,&RelayWalletModule::finish);
    connect(&process_,&QProcess::errorOccurred,this,[this](QProcess::ProcessError error){if(error==QProcess::FailedToStart && !active_.isEmpty())publish({{"success",false},{"error","WALLET_EXECUTABLE_FAILED"}});});
}
RelayWalletModule::~RelayWalletModule(){
    if(process_.state()!=QProcess::NotRunning){process_.terminate();if(!process_.waitForFinished(3000)){process_.kill();process_.waitForFinished(1000);}}
}
QString RelayWalletModule::configure(const QString& raw,const QString& rawProgramRoot){
    if(process_.state()!=QProcess::NotRunning)return "WALLET_BUSY";
    const auto allowed=QFileInfo(qEnvironmentVariable("COMMONS_RELAY_WALLET_ROOT")).canonicalFilePath();
    const QFileInfo path(raw);const auto canonical=path.canonicalFilePath();
    if(allowed.isEmpty() || !path.isDir() || path.isSymLink() || !canonical.startsWith(allowed+"/"))return "WALLET_PROFILE_DENIED";
    const QFileInfo programRoot(rawProgramRoot);const auto canonicalProgramRoot=programRoot.canonicalFilePath();
    if(!programRoot.isDir() || programRoot.isSymLink() || canonicalProgramRoot.isEmpty())return "PROGRAM_ROOT_DENIED";
    const QFileInfo binary(qEnvironmentVariable("COMMONS_RELAY_WALLET_EXECUTABLE"));
    if(!binary.isFile() || !binary.isExecutable() || binary.isSymLink())return "WALLET_EXECUTABLE_UNAVAILABLE";
    if(!QFileInfo(canonical+"/relay-wallet.json").isFile())return "WALLET_MARKER_MISSING";
    if(!profile_.isEmpty() && (profile_!=canonical || programRoot_!=canonicalProgramRoot))return "WALLET_ALREADY_BOUND";
    profile_=canonical;programRoot_=canonicalProgramRoot;binary_=binary.canonicalFilePath();return "WALLET_CONFIGURED";
}
QString RelayWalletModule::state() const {
    return QString::fromUtf8(QJsonDocument(QJsonObject{{"configured",!profile_.isEmpty()},{"busy",process_.state()!=QProcess::NotRunning},{"operation",operation_},{"network","testnet-only"}}).toJson(QJsonDocument::Compact));
}
QString RelayWalletModule::request(const QString& raw){
    if(profile_.isEmpty())return "WALLET_NOT_CONFIGURED";
    if(process_.state()!=QProcess::NotRunning)return "WALLET_BUSY";
    if(raw.toUtf8().size()>60000)return "WALLET_REQUEST_LIMIT";
    QJsonParseError parse;const auto doc=QJsonDocument::fromJson(raw.toUtf8(),&parse);
    if(parse.error!=QJsonParseError::NoError || !doc.isObject())return "INVALID_WALLET_REQUEST";
    const auto obj=doc.object();if(obj.size()!=2 || !obj.value("mode").isString() || !obj.value("params").isObject())return "INVALID_WALLET_REQUEST";
    QString mode=obj.value("mode").toString();const auto params=obj.value("params").toObject();QStringList args{mode,profile_};
    const auto validId=[](const QString& id){return QRegularExpression("^[A-Za-z0-9_-]{1,80}$").match(id).hasMatch();};
    if((mode=="balance" || mode=="identity" || mode=="program-account" || mode=="receive-address" || mode=="history") && params.isEmpty()){}
    else if(mode=="query" && params.size()==1 && params.value("account").isString()){
        const auto id=params.value("account").toString();if(!QRegularExpression("^[A-Za-z0-9]{32,64}$").match(id).hasMatch())return "INVALID_QUERY_ACCOUNT";args.append(id);
    }else if(mode=="check-payment" && params.size()==3 && params.value("account").isString() && params.value("tx_hash").isString() && params.value("amount").isString()){
        const auto account=params.value("account").toString();const auto hash=params.value("tx_hash").toString();const auto amount=params.value("amount").toString();
        if(!QRegularExpression("^[a-f0-9]{64}$").match(account).hasMatch() || !QRegularExpression("^[a-f0-9]{64}$").match(hash).hasMatch() || !QRegularExpression("^[1-9][0-9]{0,38}$").match(amount).hasMatch())return "INVALID_PAYMENT_QUERY";
        args.append(account);args.append(hash);args.append(amount);
    }else if(mode=="prepare-program" && params.size()==2 && params.value("operation_id").isString() && params.value("intent").isObject()){
        const auto id=params.value("operation_id").toString();if(!validId(id))return "INVALID_OPERATION_ID";
        auto intent=params.value("intent").toObject();const auto kind=intent.value("kind").toString();
        if(kind!="program-call-public" && kind!="program-deploy")return "PROGRAM_INTENT_DENIED";
        if(kind=="program-deploy"){
            const auto arguments=intent.value("arguments").toObject();const QFileInfo file(arguments.value("binary_path").toString());
            const auto canonicalFile=file.canonicalFilePath();
            if(file.isSymLink() || !file.isFile() || canonicalFile.isEmpty() || !canonicalFile.startsWith(programRoot_+"/") || file.size()<=0 || file.size()>64*1024*1024)return "PROGRAM_BINARY_DENIED";
            auto safe=arguments;safe.insert("binary_path",canonicalFile);intent.insert("arguments",safe);
        }
        const auto requestDir=profile_+"/requests";QDir().mkpath(requestDir);QFile::setPermissions(requestDir,QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner);
        const auto requestPath=requestDir+"/"+id+".json";const auto encoded=QJsonDocument(intent).toJson(QJsonDocument::Compact);QFileInfo existing(requestPath);
        if(existing.isSymLink())return "WALLET_REQUEST_SYMLINK";
        if(existing.exists()){
            QFile f(requestPath);if(!f.open(QIODevice::ReadOnly)||f.readAll()!=encoded)return "WALLET_INTENT_REUSED";
        }else{
            QSaveFile f(requestPath);if(!f.open(QIODevice::WriteOnly))return "WALLET_REQUEST_WRITE_FAILED";f.setPermissions(QFile::ReadOwner|QFile::WriteOwner);f.write(encoded);if(!f.commit())return "WALLET_REQUEST_WRITE_FAILED";
        }
        args.append(id);args.append(requestPath);mode="prepare";args[0]=mode;
    }else if((mode=="broadcast" || mode=="reconcile") && params.size()==1 && params.value("operation_id").isString()){
        const auto id=params.value("operation_id").toString();if(!validId(id))return "INVALID_OPERATION_ID";args.append(id);
    }else if(mode=="prepare" && params.size()==2 && params.value("operation_id").isString() && params.value("intent").isObject()){
        const auto id=params.value("operation_id").toString();if(!validId(id))return "INVALID_OPERATION_ID";
        const auto intent=params.value("intent").toObject();const auto kind=intent.value("kind").toString();
        const QSet<QString> kinds={"transfer-private","initialize-private"};
        if(!kinds.contains(kind))return "WALLET_INTENT_DENIED";
        const auto requestDir=profile_+"/requests";QDir().mkpath(requestDir);QFile::setPermissions(requestDir,QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner);
        const auto path=requestDir+"/"+id+".json";QFileInfo existing(path);const auto encoded=QJsonDocument(intent).toJson(QJsonDocument::Compact);
        if(existing.isSymLink())return "WALLET_REQUEST_SYMLINK";
        if(existing.exists()){
            QFile f(path);if(!f.open(QIODevice::ReadOnly)||f.readAll()!=encoded)return "WALLET_INTENT_REUSED";
        }else{
            QSaveFile f(path);if(!f.open(QIODevice::WriteOnly))return "WALLET_REQUEST_WRITE_FAILED";f.setPermissions(QFile::ReadOwner|QFile::WriteOwner);f.write(encoded);if(!f.commit())return "WALLET_REQUEST_WRITE_FAILED";
        }
        args.append(id);args.append(path);
    }else return "WALLET_METHOD_DENIED";
    QProcessEnvironment env;env.insert("PATH","/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin");env.insert("HOME",profile_);
    env.insert("TMPDIR",profile_+"/tmp/");QDir().mkpath(profile_+"/tmp");
    for(const auto& key:{QString("LBC_ROOT_DIR"),QString("RISC0_SERVER_PATH")})if(!qEnvironmentVariable(key.toUtf8()).isEmpty())env.insert(key,qEnvironmentVariable(key.toUtf8()));
    env.insert("COMMONS_RELAY_PROGRAM_ROOT",programRoot_);
    env.insert("RISC0_DEV_MODE","0");env.insert("RISC0_PROVER","ipc");env.insert("RISC0_EXECUTOR","ipc");
    auto proofThreads=qEnvironmentVariable("COMMONS_RELAY_PROOF_THREADS");
    if(!QRegularExpression("^[1-8]$").match(proofThreads).hasMatch())proofThreads="2";
    env.insert("RAYON_NUM_THREADS",proofThreads);env.insert("SUPPRESS_VERBOSE_PRINTS","1");
    if(qEnvironmentVariable("COMMONS_ALLOW_PUBLIC_TESTNET")=="1")env.insert("COMMONS_ALLOW_PUBLIC_TESTNET","1");
    active_=QUuid::createUuid().toString(QUuid::WithoutBraces);operation_=mode;buffer_.clear();typedResult_={};lastCode_.clear();
    process_.setProcessEnvironment(env);process_.setWorkingDirectory(profile_);process_.setProgram(binary_);process_.setArguments(args);process_.start();
    const auto id=active_;QTimer::singleShot(7200000,this,[this,id](){if(active_==id && process_.state()!=QProcess::NotRunning){lastCode_="WALLET_PROOF_TIME_LIMIT";process_.terminate();}});
    return id;
}
void RelayWalletModule::consume(){
    buffer_+=process_.readAllStandardOutput();
    if(buffer_.size()>262144){lastCode_="WALLET_OUTPUT_LIMIT";process_.terminate();buffer_.clear();return;}
    while(buffer_.contains('\n')){
        const auto at=buffer_.indexOf('\n');const auto line=buffer_.left(at);buffer_.remove(0,at+1);
        if(!line.startsWith("{\"relay_wallet_result\":"))continue;
        const auto doc=QJsonDocument::fromJson(line);if(doc.isObject() && doc.object().value("relay_wallet_result").isObject()){
            typedResult_=doc.object().value("relay_wallet_result").toObject();
            // The worker IPC intentionally rejects floating-point JSON. Proof
            // duration is diagnostic only, so keep it out of the deterministic
            // financial bridge while preserving an integer millisecond value.
            const auto proof=typedResult_.take("proof_seconds");
            if(proof.isDouble())typedResult_.insert("proof_millis",QString::number(qRound64(proof.toDouble()*1000.0)));
        }
    }
}
void RelayWalletModule::finish(int code,QProcess::ExitStatus status){
    consume();if(active_.isEmpty())return;
    if(code==0 && status==QProcess::NormalExit && !typedResult_.isEmpty())publish({{"success",true},{"result",typedResult_}});
    else publish({{"success",false},{"error",lastCode_.isEmpty()?"WALLET_OPERATION_FAILED":lastCode_}});
}
void RelayWalletModule::publish(QJsonObject result){
    result.insert("id",active_);const auto id=active_;const auto raw=QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Compact));
    replies_.insert(id,raw);order_.enqueue(id);while(order_.size()>64)replies_.remove(order_.dequeue());
    active_.clear();operation_.clear();emit eventResponse("walletResult",{id,raw});
}
QString RelayWalletModule::reply(const QString& id) const{
    if(replies_.contains(id))return replies_.value(id);
    return QString::fromUtf8(QJsonDocument(QJsonObject{{"id",id},{"pending",id==active_},{"expired",id!=active_}}).toJson(QJsonDocument::Compact));
}
