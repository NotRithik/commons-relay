//! Testnet-only wallet companion. Never prints seeds, private keys or witnesses.
use std::{fs,io::Write,path::{Path,PathBuf},str::FromStr,os::unix::{fs::{PermissionsExt,OpenOptionsExt},io::AsRawFd},time::Duration};
use anyhow::{Result,Context,ensure,bail};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use lee::{AccountId,ProgramId};
use wallet::config::{WalletConfig,SequencerConnectionData};
use sequencer_service_rpc::{RpcClient as _,SequencerClientBuilder};
pub use wallet::{WalletCore,ExecutionFailureKind,helperfunctions};
mod account_manager;
mod transactions;
const PROTOCOL:[u32;8]=[1334328888,3910590567,1244219104,3671232111,3138827701,405554639,4064616947,1864368340];
const PIN:&str="47eba256479f6f785acbd138834340703cd03401";

struct Lock(fs::File);
impl Lock {
 fn take(root:&Path)->Result<Self>{
  let p=root.join(".wallet.lock");if p.exists(){ensure!(!fs::symlink_metadata(&p)?.file_type().is_symlink(),"SYMLINK_LOCK");}
  let f=fs::OpenOptions::new().read(true).write(true).create(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(p)?;
  ensure!(unsafe{libc::flock(f.as_raw_fd(),libc::LOCK_EX|libc::LOCK_NB)}==0,"WALLET_BUSY");Ok(Self(f))
 }
}
impl Drop for Lock { fn drop(&mut self){unsafe{libc::flock(self.0.as_raw_fd(),libc::LOCK_UN);}} }
fn endpoint(raw:&str)->Result<url::Url>{
 let u:url::Url=raw.parse()?;
 ensure!(u.username().is_empty()&&u.password().is_none()&&u.query().is_none()&&u.fragment().is_none(),"INVALID_ENDPOINT");
 let allowed=(u.scheme()=="https"&&u.host_str()==Some("testnet.lez.logos.co")&&u.port().is_none()&&u.path()=="/")
  ||(u.scheme()=="http"&&u.host_str()==Some("127.0.0.1")&&u.port()==Some(34341)&&u.path()=="/");
 ensure!(allowed,"UNSUPPORTED_NETWORK");
 if u.scheme()=="https" {ensure!(std::env::var("COMMONS_ALLOW_PUBLIC_TESTNET").as_deref()==Ok("1"),"PUBLIC_TESTNET_OPT_IN_REQUIRED");}
 Ok(u)
}
fn write_private(path:&Path,bytes:&[u8])->Result<()>{
 if path.exists(){ensure!(!fs::symlink_metadata(path)?.file_type().is_symlink(),"SYMLINK_DESTINATION");}
 let temp=path.with_extension(format!("tmp-{}",rand::random::<u64>()));
 let mut f=fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temp)?;
 f.write_all(bytes)?;f.sync_all()?;fs::rename(temp,path)?;Ok(())
}
fn save(path:&Path,value:&Value)->Result<()>{write_private(path,&serde_json::to_vec_pretty(value)?)}
fn read_json(path:&Path,max:u64)->Result<Value>{let meta=fs::symlink_metadata(path)?;ensure!(meta.is_file()&&!meta.file_type().is_symlink()&&meta.len()<=max,"INVALID_INPUT_FILE");Ok(serde_json::from_slice(&fs::read(path)?)?)}
fn account(raw:&str)->Result<AccountId>{
 if raw.len()==64 {return Ok(AccountId::new(hex::decode(raw)?.try_into().map_err(|_|anyhow::anyhow!("INVALID_ACCOUNT"))?));}
 Ok(AccountId::from_str(raw)?)
}
fn output(value:Value){println!("{}",json!({"relay_wallet_result":value}));}
fn storage_private_identity(storage:&wallet::storage::Storage,id:AccountId)->Result<Value>{
 let entry=storage.key_chain().private_account(id).context("PRIVATE_ACCOUNT_NOT_FOUND")?;
 let keys=&entry.key_chain;
 Ok(json!({"account_id":hex::encode(id.as_ref()),"account_base58":id.to_string(),
  "npk":hex::encode(keys.nullifier_public_key.0),
  "vpk_borsh":hex::encode(borsh::to_vec(&keys.viewing_public_key)?),
  "identifier":entry.kind.identifier().to_string()}))
}
fn public_identity(w:&WalletCore,id:AccountId)->Result<Value>{storage_private_identity(w.storage(),id)}
async fn open(root:&Path)->Result<WalletCore>{
 let marker=read_json(&root.join("relay-wallet.json"),8192)?;
 let config:WalletConfig=serde_json::from_value(read_json(&root.join("config.json"),65536)?)?;
 ensure!(marker["schema"]==1&&config.sequencers.len()==1,"INVALID_WALLET_PROFILE");
 let u=endpoint(config.sequencers[0].sequencer_addr.as_str())?;
 ensure!(marker["endpoint"].as_str()==Some(u.as_str()),"WALLET_ENDPOINT_CHANGED");
 ensure!(config.sequencers[0].basic_auth.is_none(),"UNSUPPORTED_AUTH");
 let storage_meta=fs::symlink_metadata(root.join("storage.json"))?;
 ensure!(storage_meta.is_file()&&!storage_meta.file_type().is_symlink()&&(storage_meta.permissions().mode()&0o077)==0,"INSECURE_WALLET_STORAGE");
 let w=WalletCore::new_update_chain(root.join("config.json"),root.join("storage.json"),root.join("statistics.json"),None).await?;
 let programs=w.get_program_ids().await?;ensure!(programs.get("privacy_preserving_circuit")==Some(&PROTOCOL),"PROTOCOL_FINGERPRINT_CHANGED");
 Ok(w)
}
async fn run()->Result<()> {
 let mut args=std::env::args().skip(1);let mode=args.next().context("MODE_REQUIRED")?;
 if mode=="--help" {println!("Relay testnet wallet: init|init-local-offline|identity|program-account|receive-address|balance|query|history|abandon-expired PROFILE [ARGS]. No mainnet endpoints.");return Ok(());}
 let root=PathBuf::from(args.next().context("PROFILE_REQUIRED")?);
 ensure!(root.is_absolute(),"ABSOLUTE_PROFILE_REQUIRED");
 if !root.exists(){fs::create_dir_all(&root)?;fs::set_permissions(&root,fs::Permissions::from_mode(0o700))?;}
 ensure!(!fs::symlink_metadata(&root)?.file_type().is_symlink(),"SYMLINK_PROFILE");
 let _lock=Lock::take(&root)?;
 if mode=="init-local-offline" {
  ensure!(!root.join("storage.json").exists(),"EXISTING_WALLET_PRESERVED");
  let u=endpoint("http://127.0.0.1:34341/")?;
  let(mut storage,_unused_mnemonic)=wallet::storage::Storage::new("local-proof-fixture-only-never-use-for-money")?;
  let(payer,_)=storage.key_chain_mut().generate_new_public_transaction_private_key(None);
  let(private,_)=storage.key_chain_mut().generate_new_privacy_preserving_transaction_key_chain(None);
  let mut config=WalletConfig::default();config.sequencers=vec![SequencerConnectionData{sequencer_addr:u.clone(),basic_auth:None}];
  config.multi_sequencer_client_config.calibration_limit=2;config.seq_tx_poll_max_blocks=240;config.seq_poll_timeout=Duration::from_millis(500);
  save(&root.join("config.json"),&serde_json::to_value(config)?)?;
  storage.set_last_synced_block(0);write_private(&root.join("storage.json"),b"{}")?;storage.save_to_path(&root.join("storage.json"))?;
  fs::set_permissions(root.join("storage.json"),fs::Permissions::from_mode(0o600))?;
  save(&root.join("statistics.json"),&json!({}))?;
  save(&root.join("relay-wallet.json"),&json!({"schema":1,"endpoint":u.as_str(),"root_account":hex::encode(private.as_ref()),"payer":hex::encode(payer.as_ref()),"birthday_block":0,"upstream_revision":PIN,"local_offline_fixture":true}))?;
  let info=storage_private_identity(&storage,private)?;save(&root.join("identity-public.json"),&info)?;
  output(json!({"created":true,"private_account":info["account_id"],"payer":hex::encode(payer.as_ref()),"payer_base58":payer.to_string(),"birthday_block":0,"endpoint":u.as_str(),"network_transactions":0,"offline":true}));return Ok(());
 }
 if mode=="init" {
  ensure!(!root.join("storage.json").exists(),"EXISTING_WALLET_PRESERVED");
  let u=endpoint(&args.next().context("ENDPOINT_REQUIRED")?)?;
  let client=SequencerClientBuilder::default().request_timeout(Duration::from_secs(20)).build(u.clone())?;
  let ids=client.get_program_ids().await?;ensure!(ids.get("privacy_preserving_circuit")==Some(&PROTOCOL),"PROTOCOL_FINGERPRINT_CHANGED");
  let birthday=client.get_last_block_id().await?;
  let(mut storage,_unused_mnemonic)=wallet::storage::Storage::new("testnet-only-use-owner-only-files-not-production-encryption")?;
  let(payer,_)=storage.key_chain_mut().generate_new_public_transaction_private_key(None);
  let(private,_)=storage.key_chain_mut().generate_new_privacy_preserving_transaction_key_chain(None);
  let mut config=WalletConfig::default();config.sequencers=vec![SequencerConnectionData{sequencer_addr:u.clone(),basic_auth:None}];
  config.multi_sequencer_client_config.calibration_limit=2;config.seq_tx_poll_max_blocks=240;config.seq_poll_timeout=Duration::from_millis(500);
  save(&root.join("config.json"),&serde_json::to_value(config)?)?;
  // All keys above are fresh. Nothing predating this checkpoint can belong to them.
  storage.set_last_synced_block(birthday);
  write_private(&root.join("storage.json"),b"{}")?;storage.save_to_path(&root.join("storage.json"))?;
  fs::set_permissions(root.join("storage.json"),fs::Permissions::from_mode(0o600))?;
  save(&root.join("statistics.json"),&json!({}))?;
  save(&root.join("relay-wallet.json"),&json!({"schema":1,"endpoint":u.as_str(),"root_account":hex::encode(private.as_ref()),"payer":hex::encode(payer.as_ref()),"birthday_block":birthday,"upstream_revision":PIN}))?;
  let w=open(&root).await?;let info=public_identity(&w,private)?;
  save(&root.join("identity-public.json"),&info)?;
  output(json!({"created":true,"private_account":info["account_id"],"payer":hex::encode(payer.as_ref()),"birthday_block":birthday,"endpoint":u.as_str(),"network_transactions":0}));return Ok(());
 }
 if mode=="abandon-expired" {
  let id=args.next().context("OPERATION_ID_REQUIRED")?;output(transactions::abandon_expired(&root,&id)?);return Ok(());
 }
 let mut w=open(&root).await?;
 match mode.as_str(){
  "prepare"=>{let id=args.next().context("OPERATION_ID_REQUIRED")?;let file=PathBuf::from(args.next().context("INTENT_FILE_REQUIRED")?);ensure!(file.canonicalize()?.starts_with(root.canonicalize()?),"INTENT_OUTSIDE_WALLET");output(transactions::prepare(&mut w,&root,&id,read_json(&file,32768)?).await?);},
  "broadcast"=>{let id=args.next().context("OPERATION_ID_REQUIRED")?;output(transactions::broadcast(&mut w,&root,&id).await?);},
  "reconcile"=>{let id=args.next().context("OPERATION_ID_REQUIRED")?;output(transactions::reconcile(&mut w,&root,&id).await?);},
  "faucet-solution"=>output(transactions::faucet_solution(&w).await?),
  "export-messaging-identity"=>{
   let target=root.join("messaging-identity.seeds");ensure!(!target.exists(),"EXISTING_IDENTITY_EXPORT_PRESERVED");
   let marker=read_json(&root.join("relay-wallet.json"),8192)?;
   let id=account(marker["root_account"].as_str().context("ROOT_ACCOUNT_MISSING")?)?;
   let entry=w.storage().key_chain().private_account(id).context("ROOT_ACCOUNT_MISSING")?;
   let nsk=entry.key_chain.private_key_holder.nullifier_secret_key;
   let hk=hkdf::Hkdf::<Sha256>::new(Some(b"commons-relay/messaging-identity/v1"),nsk.as_ref());
   let mut signing=[0_u8;32];let mut encryption=[0_u8;32];
   let mut info=b"ed25519/".to_vec();info.extend_from_slice(id.as_ref());hk.expand(&info,&mut signing).map_err(|_|anyhow::anyhow!("IDENTITY_DERIVATION_FAILED"))?;
   let mut info=b"x25519/".to_vec();info.extend_from_slice(id.as_ref());hk.expand(&info,&mut encryption).map_err(|_|anyhow::anyhow!("IDENTITY_DERIVATION_FAILED"))?;
   let npk=hex::encode(entry.key_chain.nullifier_public_key.as_ref());
   // This deployment-only file contains only domain-separated child seeds,
   // never the wallet spending/nullifier key. It is not an agent-facing mode.
   save(&target,&json!({"schema":1,"root_account":hex::encode(id.as_ref()),"root_npk":npk,"address":format!("lez-{npk}"),
      "signing_seed":hex::encode(signing),"encryption_seed":hex::encode(encryption)}))?;
   signing.fill(0);encryption.fill(0);
   output(json!({"exported":true,"address":format!("lez-{npk}"),"root_npk":npk,"seed_material_printed":false}));
  },
  "check-payment"=>{
   let id=account(&args.next().context("RECIPIENT_ACCOUNT_REQUIRED")?)?;
   let hash=args.next().context("TRANSACTION_HASH_REQUIRED")?;
   let expected: u128=args.next().context("EXPECTED_AMOUNT_REQUIRED")?.parse()?;
   ensure!(expected>0,"EXPECTED_PAYMENT_MUST_BE_POSITIVE");
   ensure!(w.storage().key_chain().private_account(id).is_some(),"PAYMENT_ACCOUNT_NOT_OWNED");
   let hash=common::HashType(hex::decode(hash)?.try_into().map_err(|_|anyhow::anyhow!("INVALID_TRANSACTION_HASH"))?);
   match w.helm_owned().get_transaction(hash).await? {
    None=>output(json!({"confirmed":false,"reason":"TRANSACTION_NOT_CONFIRMED"})),
    Some((tx,block))=>{
     ensure!(tx.hash()==hash,"TRANSACTION_HASH_MISMATCH");
     let common::transaction::LeeTransaction::PrivacyPreserving(ref private)=tx else{bail!("PAYMENT_MUST_BE_PRIVATE");};
     w.sync_to_latest_block().await?;w.store_persistent_data()?;
     let note=w.get_account_private(id).context("PAYMENT_NOTE_NOT_FOUND")?;
     let commitment=w.get_private_account_commitment(id).context("PAYMENT_COMMITMENT_NOT_FOUND")?;
     ensure!(private.message.commitments().contains(&commitment),"TRANSACTION_DOES_NOT_CREATE_RECEIVER_NOTE");
     ensure!(note.program_owner==programs::authenticated_transfer().id()&&note.balance==expected,"PAYMENT_AMOUNT_OR_PROGRAM_MISMATCH");
     output(json!({"confirmed":true,"transaction_hash":hash.to_string(),"block_id":block,"amount":expected.to_string(),"receiver_account":hex::encode(id.as_ref()),"private":true}));
    }
   }
  },
  "program-account"=>{
   let marker=read_json(&root.join("relay-wallet.json"),8192)?;
   let id=account(marker["payer"].as_str().context("PAYER_ACCOUNT_MISSING")?)?;
   ensure!(w.get_account_public_signing_key(id).is_some(),"PAYER_SIGNING_KEY_MISSING");
   output(json!({"account_id":hex::encode(id.as_ref()),"wallet_owned":true,"public":true}));
  },
  "identity"=>output(read_json(&root.join("identity-public.json"),8192)?),
  "receive-address"=>{let(id,_)=w.create_new_account_private(None);w.store_persistent_data()?;output(public_identity(&w,id)?);},
  "balance"=>{
   w.sync_to_latest_block().await?;w.store_persistent_data()?;
   let marker=read_json(&root.join("relay-wallet.json"),8192)?;let id=account(marker["root_account"].as_str().context("ROOT_MISSING")?)?;
   let value=w.get_account_private(id).context("ROOT_ACCOUNT_MISSING")?;
   output(json!({"root_account":hex::encode(id.as_ref()),"balance":w.storage().key_chain().private_accounts().map(|entry|entry.account.balance).try_fold(0_u128,|sum,n|sum.checked_add(n).context("BALANCE_OVERFLOW"))?.to_string(),"root_balance":value.balance.to_string(),"nonce":format!("{:?}",value.nonce),"shielded":true,"block":w.storage().last_synced_block()}));
  },
  "query"=>{let id=account(&args.next().context("ACCOUNT_REQUIRED")?)?;let acc=w.get_account_public(id).await?;
   output(json!({"account_id":hex::encode(id.as_ref()),"program_owner":acc.program_owner,"balance":acc.balance.to_string(),"data_borsh_hex":hex::encode(acc.data.as_ref()),"block":w.get_last_block_id().await?}));},
  "history"=>{
   let dir=root.join("operations");let mut values=vec![];
   if dir.is_dir(){for entry in fs::read_dir(dir)?.take(1000){let p=entry?.path();if p.extension().and_then(|x|x.to_str())==Some("json"){let op:transactions::Operation=serde_json::from_value(read_json(&p,65536)?)?;values.push(transactions::view(&op));}}}
   output(json!({"operations":values}));
  },
  _=>bail!("UNSUPPORTED_WALLET_OPERATION")
 }
 Ok(())
}
#[tokio::main]
async fn main(){
 if let Err(error)=run().await {
  let message=error.to_string();let code=if message.len()<=100 && message.bytes().all(|b|b.is_ascii_uppercase()||b.is_ascii_digit()||b==b'_'){message}else{"WALLET_OPERATION_FAILED".into()};
  eprintln!("Relay wallet: {code}");std::process::exit(1);
 }
}
