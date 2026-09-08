//! Durable prepare/broadcast/reconcile operations for the pinned testnet wallet.
use super::*;
use account_manager::{AccountIdentity as Identity,AccountManager};
use common::transaction::LeeTransaction;
use lee::program::Program;
use lee::privacy_preserving_transaction::circuit::ProgramWithDependencies;
use lee_core::{NullifierPublicKey,encryption::ViewingPublicKey};
use serde::{Deserialize,Serialize};

#[derive(Serialize,Deserialize,Clone)]
pub struct Operation {
 pub version:u8,pub id:String,pub intent:Value,pub intent_sha256:String,
 pub network:String,pub state:String,pub private:bool,pub maximum_spend:String,
 pub transaction_hash:Option<String>,pub transaction_file:Option<String>,
 #[serde(default)] pub program_id:Option<String>,
 pub confirmed_block:Option<u64>,pub proof_seconds:Option<f64>,pub error:Option<String>,
}
fn now()->u64 {std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs()}
fn op_id(value:&str)->Result<()>{ensure!(!value.is_empty()&&value.len()<=80&&value.bytes().all(|c|c.is_ascii_alphanumeric()||c==b'-'||c==b'_'),"INVALID_OPERATION_ID");Ok(())}
fn dir(root:&Path)->Result<PathBuf>{let p=root.join("operations");if !p.exists(){fs::create_dir(&p)?;fs::set_permissions(&p,fs::Permissions::from_mode(0o700))?;}ensure!(!fs::symlink_metadata(&p)?.file_type().is_symlink(),"SYMLINK_OPERATIONS");Ok(p)}
fn record_path(root:&Path,id:&str)->Result<PathBuf>{op_id(id)?;Ok(dir(root)?.join(format!("{id}.json")))}
fn load(root:&Path,id:&str)->Result<Operation>{let op:Operation=serde_json::from_value(read_json(&record_path(root,id)?,65536)?)?;ensure!(op.version==1&&op.id==id,"INVALID_OPERATION_RECORD");Ok(op)}
fn store(root:&Path,op:&Operation)->Result<()>{save(&record_path(root,&op.id)?,&serde_json::to_value(op)?)}
pub fn view(op:&Operation)->Value {
 let proof_millis=op.proof_seconds.map(|seconds| (seconds*1000.0).round() as u64);
 json!({"operation_id":op.id,"state":op.state,"private":op.private,"maximum_spend":op.maximum_spend,"tx_hash":op.transaction_hash,"program_id":op.program_id,"block_id":op.confirmed_block,"proof_millis":proof_millis,"error":op.error})
}
fn bounded_amount(value:&Value)->Result<u128>{let s=value.as_str().context("AMOUNT_STRING_REQUIRED")?;ensure!(!s.is_empty()&&s.len()<=39&&s.bytes().all(|c|c.is_ascii_digit())&&(s=="0"||!s.starts_with('0')),"INVALID_AMOUNT");Ok(s.parse()?) }
fn field<'a>(value:&'a Value,key:&str)->Result<&'a str>{value[key].as_str().with_context(||format!("MISSING_FIELD_{}",key.to_uppercase()))}
fn exact(value:&Value,keys:&[&str])->Result<()>{let obj=value.as_object().context("OBJECT_REQUIRED")?;ensure!(obj.len()==keys.len()&&keys.iter().all(|k|obj.contains_key(*k)),"INVALID_OPERATION_FIELDS");Ok(())}
fn hash(value:&Value)->Result<String>{Ok(hex::encode(Sha256::digest(serde_json::to_vec(value)?)))}
fn program_id_hex(value:&str)->Result<lee::ProgramId>{
 ensure!(value.len()==64&&value.bytes().all(|b|b.is_ascii_hexdigit()&&!b.is_ascii_uppercase()),"INVALID_PROGRAM_ID");
 let bytes=hex::decode(value)?;let mut words=[0_u32;8];
 for(i,chunk)in bytes.chunks_exact(4).enumerate(){words[i]=u32::from_le_bytes(chunk.try_into().unwrap());}
 Ok(words)
}
fn public_call_arguments(value:&Value)->Result<(lee::ProgramId,Vec<u32>,Vec<Identity>)>{
 exact(value,&["program_id","instruction","params"])?;
 let pid=program_id_hex(field(value,"program_id")?)?;
 let raw=hex::decode(field(value,"instruction")?)?;ensure!(raw.len()<=65536&&raw.len()%4==0,"INVALID_PROGRAM_INSTRUCTION");
 let words=raw.chunks_exact(4).map(|x|u32::from_le_bytes(x.try_into().unwrap())).collect::<Vec<_>>();
 let params=value["params"].as_object().context("INVALID_PROGRAM_PARAMS")?;ensure!(params.len()==1&&params.contains_key("accounts"),"INVALID_PROGRAM_PARAMS");
 let list=params["accounts"].as_array().context("INVALID_PROGRAM_ACCOUNTS")?;ensure!(!list.is_empty()&&list.len()<=16,"INVALID_PROGRAM_ACCOUNTS");
 let mut signers=0_usize;let mut accounts=Vec::with_capacity(list.len());
 for item in list{exact(item,&["account_id","signer"])?;let id=account(field(item,"account_id")?)?;let signer=item["signer"].as_bool().context("INVALID_PROGRAM_SIGNER")?;if signer{signers+=1;accounts.push(Identity::Public(id));}else{accounts.push(Identity::PublicNoSign(id));}}
 ensure!(signers>0,"PROGRAM_CALL_REQUIRES_WALLET_SIGNER");Ok((pid,words,accounts))
}
fn program_binary(value:&Value)->Result<(Vec<u8>,lee::ProgramId)>{
 exact(value,&["binary_path"])?;let raw=field(value,"binary_path")?;let path=PathBuf::from(raw);ensure!(path.is_absolute(),"PROGRAM_BINARY_PATH_REQUIRED");
 let root=PathBuf::from(std::env::var("COMMONS_RELAY_PROGRAM_ROOT").map_err(|_|anyhow::anyhow!("PROGRAM_ROOT_REQUIRED"))?);
 let canonical_root=root.canonicalize()?;let canonical=path.canonicalize()?;let meta=fs::symlink_metadata(&path)?;
 ensure!(meta.is_file()&&!meta.file_type().is_symlink()&&canonical.starts_with(&canonical_root)&&meta.len()>0&&meta.len()<=64*1024*1024,"PROGRAM_BINARY_DENIED");
 let bytes=fs::read(canonical)?;let program=Program::new(bytes.clone().into())?;Ok((bytes,program.id()))
}
fn recipient(value:&Value)->Result<Identity>{
 exact(value,&["account_id","npk","vpk_borsh","identifier"])?;
 let npk=NullifierPublicKey(hex::decode(field(value,"npk")?)?.try_into().map_err(|_|anyhow::anyhow!("INVALID_RECIPIENT_NPK"))?);
 let raw=hex::decode(field(value,"vpk_borsh")?)?;ensure!(raw.len()<=4096,"RECIPIENT_KEY_TOO_LARGE");
 let vpk:ViewingPublicKey=borsh::from_slice(&raw)?;let identifier=bounded_amount(&value["identifier"])?;
 let id=AccountId::for_regular_private_account(&npk,&vpk,identifier);
 ensure!(id==account(field(value,"account_id")?)?,"RECIPIENT_IDENTITY_MISMATCH");
 Ok(Identity::PrivateForeign{npk,vpk,identifier})
}
fn selected_input(w:&WalletCore,amount:u128)->Result<AccountId>{
 let mut candidates:Vec<_>=w.storage().key_chain().private_accounts().filter(|e|e.account.balance>=amount&&e.account.program_owner==programs::authenticated_transfer().id())
  .map(|e|(e.account.balance,AccountId::for_private_account(&e.key_chain.nullifier_public_key,&e.key_chain.viewing_public_key,e.kind))).collect();
 candidates.sort_by_key(|x|x.0);candidates.first().map(|x|x.1).context("NO_SINGLE_NOTE_WITH_SUFFICIENT_FUNDS")
}
fn ensure_no_pending(root:&Path,current:&str)->Result<()>{
 for entry in fs::read_dir(dir(root)?)?.take(2000){let p=entry?.path();if p.extension().and_then(|s|s.to_str())!=Some("json"){continue;}
  let op:Operation=serde_json::from_value(read_json(&p,65536)?)?;
  if op.id!=current&&matches!(op.state.as_str(),"preparing"|"prepared"|"broadcasting"|"submitted"|"unknown") {bail!("WALLET_HAS_UNRECONCILED_OPERATION");}
 }
 Ok(())
}
fn transaction_from_public(am:&AccountManager,program:ProgramId,instruction:Vec<u32>)->Result<LeeTransaction>{
 let message=lee::public_transaction::Message::new_preserialized(program,am.public_account_ids(),am.public_account_nonces(),instruction);
 let signatures=am.sign_message(message.hash())?;
 Ok(LeeTransaction::Public(lee::PublicTransaction::new(message,lee::public_transaction::WitnessSet::from_raw_parts(signatures))))
}
fn transaction_from_private(am:&AccountManager,program:ProgramWithDependencies,instruction:Vec<u32>)->Result<LeeTransaction>{
 ensure!(std::env::var("RISC0_DEV_MODE").as_deref()==Ok("0"),"REAL_PROOFS_REQUIRED");
 ensure!(std::env::var("RISC0_PROVER").as_deref()==Ok("ipc"),"LOCAL_PROVER_REQUIRED");
 let(output,proof)=lee::privacy_preserving_transaction::circuit::execute_and_prove_with_padded_inputs(
  am.pre_states(),instruction,am.account_identities(),am.dummy_inputs_default(),&program)?;
 let message=lee::privacy_preserving_transaction::message::Message::from_circuit_output(am.public_account_nonces(),output);
 let sigs=am.sign_message(message.hash())?;
 Ok(LeeTransaction::PrivacyPreserving(lee::PrivacyPreservingTransaction::new(message,
   lee::privacy_preserving_transaction::witness_set::WitnessSet::from_raw_parts(sigs,proof))))
}

pub async fn prepare(w:&mut WalletCore,root:&Path,id:&str,intent:Value)->Result<Value>{
 op_id(id)?;exact(&intent,&["kind","arguments","expires_at"])?;
 let expires=intent["expires_at"].as_u64().context("EXPIRY_REQUIRED")?;
 ensure!(expires>now()&&expires<=now()+24*3600,"OPERATION_AUTHORIZATION_EXPIRED");
 let ih=hash(&intent)?;
 let record=record_path(root,id)?;
 if record.exists(){let op=load(root,id)?;ensure!(op.intent_sha256==ih,"OPERATION_ID_REUSED");if op.state!="preparing" {return Ok(view(&op));}}
 ensure_no_pending(root,id)?;
 let kind=field(&intent,"kind")?;let arguments=&intent["arguments"];
 let marker=read_json(&root.join("relay-wallet.json"),8192)?;
 let payer=account(field(&marker,"payer")?)?;let root_account=account(field(&marker,"root_account")?)?;
 let program_ids=w.get_program_ids().await?;
 ensure!(program_ids.get("authenticated_transfer")==Some(&programs::authenticated_transfer().id()),"TRANSFER_IMAGE_MISMATCH");
 w.sync_to_latest_block().await?;w.store_persistent_data()?;
 if kind=="program-deploy" {
  let(bytes,pid)=program_binary(arguments)?;let tx=LeeTransaction::ProgramDeployment(lee::ProgramDeploymentTransaction::new(lee::program_deployment_transaction::Message::new(bytes)));
  let mut op=Operation{version:1,id:id.into(),intent:intent.clone(),intent_sha256:ih,network:w.helm_url().to_string(),state:"preparing".into(),private:false,maximum_spend:"0".into(),transaction_hash:None,transaction_file:None,program_id:Some(hex::encode(pid.iter().flat_map(|x|x.to_le_bytes()).collect::<Vec<_>>())),confirmed_block:None,proof_seconds:None,error:None};
  store(root,&op)?;let start=std::time::Instant::now();let raw=borsh::to_vec(&tx)?;ensure!(raw.len()<=64*1024*1024,"TRANSACTION_TOO_LARGE");
  let txhash=tx.hash().to_string();let filename=format!("{id}.borsh");write_private(&dir(root)?.join(&filename),&raw)?;op.transaction_hash=Some(txhash);op.transaction_file=Some(filename);op.proof_seconds=Some(start.elapsed().as_secs_f64());op.state="prepared".into();store(root,&op)?;return Ok(view(&op));
 }
 if kind=="program-call-public" {
  let(pid,instruction,accounts)=public_call_arguments(arguments)?;
  let mut op=Operation{version:1,id:id.into(),intent:intent.clone(),intent_sha256:ih,network:w.helm_url().to_string(),state:"preparing".into(),private:false,maximum_spend:"0".into(),transaction_hash:None,transaction_file:None,program_id:Some(hex::encode(pid.iter().flat_map(|x|x.to_le_bytes()).collect::<Vec<_>>())),confirmed_block:None,proof_seconds:None,error:None};
  store(root,&op)?;let start=std::time::Instant::now();let am=AccountManager::new(w,accounts).await?;let tx=transaction_from_public(&am,pid,instruction)?;
  let raw=borsh::to_vec(&tx)?;ensure!(raw.len()<=64*1024*1024,"TRANSACTION_TOO_LARGE");let txhash=tx.hash().to_string();let filename=format!("{id}.borsh");write_private(&dir(root)?.join(&filename),&raw)?;op.transaction_hash=Some(txhash);op.transaction_file=Some(filename);op.proof_seconds=Some(start.elapsed().as_secs_f64());op.state="prepared".into();store(root,&op)?;return Ok(view(&op));
 }
 let(accounts,instruction,program,private,spend)=match kind {
  "initialize-public"=>{
   exact(arguments,&[])?;ensure!(w.get_account_public(payer).await?==lee_core::account::Account::default(),"PAYER_ALREADY_INITIALIZED");
   (vec![Identity::Public(payer)],Program::serialize_instruction(authenticated_transfer_core::Instruction::Initialize)?,programs::authenticated_transfer(),false,0)
  },
  "initialize-private"=>{
   exact(arguments,&[])?;ensure!(w.get_account_private(root_account).context("MISSING_PRIVATE_ACCOUNT")?==lee_core::account::Account::default(),"PRIVATE_ALREADY_INITIALIZED");
   (vec![Identity::PrivateOwned(root_account)],Program::serialize_instruction(authenticated_transfer_core::Instruction::Initialize)?,programs::authenticated_transfer(),true,0)
  },
  "transfer-public"=>{
   exact(arguments,&["recipient","amount"])?;let amount=bounded_amount(&arguments["amount"])?;let to=account(field(arguments,"recipient")?)?;
   ensure!(amount>0&&w.get_account_public(payer).await?.balance>=amount,"INSUFFICIENT_PUBLIC_BALANCE");
   ensure!(w.get_account_public(to).await?.program_owner==programs::authenticated_transfer().id(),"RECIPIENT_MUST_BE_INITIALIZED");
   (vec![Identity::Public(payer),Identity::PublicNoSign(to)],Program::serialize_instruction(authenticated_transfer_core::Instruction::Transfer{amount})?,programs::authenticated_transfer(),false,amount)
  },
  // Genesis SupplyAccount credits a vault PDA, not the payer balance. Claim
  // that deposit through the normal on-chain vault program before shielding.
  "claim-public-vault" => {
   exact(arguments, &["amount"])?;
   let amount = bounded_amount(&arguments["amount"])?;
   // The pinned getProgramIds RPC omits vault. Its image is still pinned by
   // programs::vault() and the transaction; reject a conflicting advertised ID.
   if let Some(advertised) = program_ids.get("vault") {
    ensure!(*advertised == programs::vault().id(), "VAULT_IMAGE_MISMATCH");
   }
   let vault = vault_core::compute_vault_account_id(programs::vault().id(), payer);
   let deposit = w.get_account_public(vault).await?;
   ensure!(deposit.program_owner == programs::authenticated_transfer().id(), "VAULT_OWNER_MISMATCH");
   ensure!(amount > 0 && deposit.balance >= amount, "INSUFFICIENT_VAULT_BALANCE");
   (vec![Identity::Public(payer), Identity::PublicNoSign(vault)],
    Program::serialize_instruction(vault_core::Instruction::Claim { amount })?,
    programs::vault(), false, 0)
  },
  "shield"=>{
   exact(arguments,&["amount"])?;let amount=bounded_amount(&arguments["amount"])?;ensure!(amount>0&&w.get_account_public(payer).await?.balance>=amount,"INSUFFICIENT_PUBLIC_BALANCE");
   (vec![Identity::Public(payer),Identity::PrivateOwned(root_account)],Program::serialize_instruction(authenticated_transfer_core::Instruction::Transfer{amount})?,programs::authenticated_transfer(),true,amount)
  },
  "transfer-private"=>{
   exact(arguments,&["recipient","amount"])?;let amount=bounded_amount(&arguments["amount"])?;ensure!(amount>0,"ZERO_PAYMENT");
   let from=selected_input(w,amount)?;let to=recipient(&arguments["recipient"])?;
   (vec![Identity::PrivateOwned(from),to],Program::serialize_instruction(authenticated_transfer_core::Instruction::Transfer{amount})?,programs::authenticated_transfer(),true,amount)
  },
  "faucet"=>{
   exact(arguments,&["solution"])?;ensure!(program_ids.get("pinata")==Some(&programs::pinata().id()),"FAUCET_IMAGE_MISMATCH");
   let pinata=system_accounts::pinata_account_id();let solution=bounded_amount(&arguments["solution"])?;
   let state=w.get_account_public(pinata).await?;ensure!(state.data.len()==33&&state.balance>=150,"FAUCET_UNAVAILABLE");
   ensure!(valid_solution(&state.data,solution),"STALE_FAUCET_SOLUTION");
   ensure!(w.get_account_public(payer).await?.program_owner==programs::authenticated_transfer().id(),"PAYER_MUST_BE_INITIALIZED");
   (vec![Identity::PublicNoSign(pinata),Identity::PublicNoSign(payer)],Program::serialize_instruction(solution)?,programs::pinata(),false,0)
  },
  _=>bail!("UNSUPPORTED_PREPARATION_KIND")
 };
 let mut op=Operation{version:1,id:id.into(),intent:intent.clone(),intent_sha256:ih,network:w.helm_url().to_string(),state:"preparing".into(),private,maximum_spend:spend.to_string(),transaction_hash:None,transaction_file:None,program_id:None,confirmed_block:None,proof_seconds:None,error:None};
 store(root,&op)?;
 let start=std::time::Instant::now();
 let am=AccountManager::new(w,accounts).await?;
 eprintln!("Relay wallet: preparing {}, real_proofs={}, RISC0_DEV_MODE=0",kind,private);
 let tx=if private{transaction_from_private(&am,program.into(),instruction)?}else{transaction_from_public(&am,program.id(),instruction)?};
 let raw=borsh::to_vec(&tx)?;ensure!(raw.len()<=64*1024*1024,"TRANSACTION_TOO_LARGE");
 let hash=tx.hash().to_string();let filename=format!("{id}.borsh");write_private(&dir(root)?.join(&filename),&raw)?;
 op.transaction_hash=Some(hash);op.transaction_file=Some(filename);op.proof_seconds=Some(start.elapsed().as_secs_f64());
 op.state="prepared".into();store(root,&op)?;
 Ok(view(&op))
}
pub async fn reconcile(w:&mut WalletCore,root:&Path,id:&str)->Result<Value>{
 let mut op=load(root,id)?;ensure!(op.network==w.helm_url().as_str(),"OPERATION_NETWORK_MISMATCH");
 if op.state=="confirmed" {return Ok(view(&op));}
 let Some(hash)=op.transaction_hash.as_ref() else{return Ok(view(&op));};
 let parsed=common::HashType(hex::decode(hash)?.try_into().map_err(|_|anyhow::anyhow!("INVALID_TX_HASH"))?);
 if let Some((tx,block))=w.helm_owned().get_transaction(parsed).await?{
  ensure!(tx.hash()==parsed,"RECEIPT_HASH_MISMATCH");op.state="confirmed".into();op.confirmed_block=Some(block);op.error=None;store(root,&op)?;
  w.sync_to_block(block).await?;w.store_persistent_data()?;
 }
 Ok(view(&op))
}
pub fn abandon_expired(root:&Path,id:&str)->Result<Value>{
 let mut op=load(root,id)?;
 ensure!(op.state=="prepared","ONLY_PREPARED_OPERATION_CAN_BE_ABANDONED");
 ensure!(op.confirmed_block.is_none(),"CONFIRMED_OPERATION_CANNOT_BE_ABANDONED");
 let expires=op.intent["expires_at"].as_u64().context("OPERATION_EXPIRY_MISSING")?;
 ensure!(expires<=now(),"OPERATION_AUTHORIZATION_STILL_ACTIVE");
 op.state="rejected".into();op.error=Some("EXPIRED_PREPARED_OPERATION_ABANDONED".into());store(root,&op)?;
 Ok(view(&op))
}

pub async fn broadcast(w:&mut WalletCore,root:&Path,id:&str)->Result<Value>{
 let mut op=load(root,id)?;ensure!(op.network==w.helm_url().as_str(),"OPERATION_NETWORK_MISMATCH");
 if matches!(op.state.as_str(),"broadcasting"|"submitted"|"unknown"|"confirmed") {return reconcile(w,root,id).await;}
 ensure!(op.state=="prepared","OPERATION_NOT_PREPARED");
 ensure!(op.intent["expires_at"].as_u64().unwrap_or(0)>now(),"OPERATION_AUTHORIZATION_EXPIRED");
 let file=op.transaction_file.as_deref().context("TRANSACTION_FILE_MISSING")?;ensure!(file==format!("{id}.borsh"),"INVALID_TRANSACTION_FILE");
 let path=dir(root)?.join(file);let meta=fs::symlink_metadata(&path)?;ensure!(meta.is_file()&&!meta.file_type().is_symlink()&&meta.len()<=64*1024*1024,"INVALID_TRANSACTION_FILE");
 let tx:LeeTransaction=borsh::from_slice(&fs::read(path)?)?;let txhash=tx.hash();ensure!(Some(txhash.to_string())==op.transaction_hash,"TRANSACTION_HASH_CHANGED");
 // Commit this before touching the network. A lost reply never triggers a new send.
 op.state="broadcasting".into();store(root,&op)?;
 match w.helm_owned().send_transaction(tx).await {
  Ok(hash)=>{ensure!(hash==txhash,"SEQUENCER_HASH_MISMATCH");op.state="submitted".into();store(root,&op)?;},
  Err(_)=>{op.state="unknown".into();op.error=Some("NETWORK_ACCEPTANCE_UNKNOWN".into());store(root,&op)?;return Ok(view(&op));}
 }
 for _ in 0..60 {let current=reconcile(w,root,id).await?;if current["state"]=="confirmed"{return Ok(current);}tokio::time::sleep(Duration::from_millis(500)).await;}
 Ok(view(&load(root,id)?))
}
fn valid_solution(data:&[u8],candidate:u128)->bool{
 if data.len()!=33||data[0]>4{return false;}
 let mut hasher=Sha256::new();hasher.update(&data[1..]);hasher.update(candidate.to_le_bytes());let digest=hasher.finalize();digest[..usize::from(data[0])].iter().all(|b|*b==0)
}
pub async fn faucet_solution(w:&WalletCore)->Result<Value>{
 let state=w.get_account_public(system_accounts::pinata_account_id()).await?;ensure!(state.data.len()==33,"FAUCET_DATA_INVALID");let difficulty=state.data[0];ensure!(difficulty<=3,"FAUCET_DIFFICULTY_EXCEEDS_LOCAL_LIMIT");
 let start=std::time::Instant::now();for candidate in 0..100_000_000u128{
  if candidate%65536==0&&start.elapsed()>Duration::from_secs(120){bail!("FAUCET_SEARCH_TIME_LIMIT");}
  if valid_solution(&state.data,candidate){return Ok(json!({"solution":candidate.to_string(),"difficulty":difficulty,"seconds":start.elapsed().as_secs_f64(),"testnet_only":true}));}
 }
 bail!("FAUCET_SEARCH_LIMIT")
}

#[cfg(test)]mod tests{
 use super::*;
 #[test]fn view_uses_integer_proof_millis_not_a_json_float(){let op=Operation{version:1,id:"x".into(),intent:json!({}),intent_sha256:"x".into(),network:"test".into(),state:"prepared".into(),private:true,maximum_spend:"1".into(),transaction_hash:None,transaction_file:None,program_id:None,confirmed_block:None,proof_seconds:Some(1.2346),error:None};let v=view(&op);assert_eq!(v["proof_millis"],json!(1235));assert!(v.get("proof_seconds").is_none());}
 #[test]fn rejects_ambiguous_amounts(){for value in [json!(1),json!(true),json!("01"),json!("-1"),json!("1e2"),json!(" 1")]{assert!(bounded_amount(&value).is_err());}}
 #[test]fn operation_ids_are_path_safe(){for x in ["../a","/tmp/a","a/b","","a.b"]{assert!(op_id(x).is_err());}assert!(op_id("task-123_a").is_ok());}
 #[test]fn exact_fields_reject_extras(){assert!(exact(&json!({"amount":"1","extra":1}),&["amount"]).is_err());}
 #[test]fn recipient_identity_is_verified(){let npk=NullifierPublicKey([1;32]);let vpk=ViewingPublicKey::from_seed(&[2;32],&[3;32]);let id=AccountId::for_regular_private_account(&npk,&vpk,4);let mut r=json!({"account_id":hex::encode(id.as_ref()),"npk":hex::encode(npk.0),"vpk_borsh":hex::encode(borsh::to_vec(&vpk).unwrap()),"identifier":"4"});assert!(recipient(&r).is_ok());r["identifier"]=json!("5");assert!(recipient(&r).is_err());}
 #[test]fn pinata_input_is_bounded(){assert!(!valid_solution(&[],0));let mut d=[0u8;33];assert!(valid_solution(&d,0));d[0]=255;assert!(!valid_solution(&d,0));}
 #[test]fn program_id_hex_is_little_endian_words(){let p=program_id_hex("0100000002000000030000000400000005000000060000000700000008000000").unwrap();assert_eq!(p,[1,2,3,4,5,6,7,8]);}
 #[test]fn public_call_rejects_no_wallet_signer(){let v=json!({"program_id":"00".repeat(32),"instruction":"00000000","params":{"accounts":[{"account_id":"00".repeat(32),"signer":false}]}});assert!(public_call_arguments(&v).is_err());}
 #[test]fn public_call_instruction_requires_u32_word_alignment(){let v=json!({"program_id":"00".repeat(32),"instruction":"00","params":{"accounts":[{"account_id":"00".repeat(32),"signer":true}]}});assert!(public_call_arguments(&v).is_err());}
 #[test]fn abandon_requires_expired_prepared_operation(){
  let root=std::env::temp_dir().join(format!("commons-relay-abandon-{}",std::process::id()));let _=fs::remove_dir_all(&root);fs::create_dir_all(&root).unwrap();
  let mut op=Operation{version:1,id:"expired-op".into(),intent:json!({"expires_at":now().saturating_sub(1)}),intent_sha256:"x".into(),network:"test".into(),state:"prepared".into(),private:true,maximum_spend:"1".into(),transaction_hash:Some("00".repeat(32)),transaction_file:Some("expired-op.borsh".into()),program_id:None,confirmed_block:None,proof_seconds:Some(1.5),error:None};
  store(&root,&op).unwrap();let v=abandon_expired(&root,"expired-op").unwrap();assert_eq!(v["state"],"rejected");assert_eq!(v["error"],"EXPIRED_PREPARED_OPERATION_ABANDONED");
  op.id="active-op".into();op.intent=json!({"expires_at":now()+3600});op.state="prepared".into();op.error=None;store(&root,&op).unwrap();assert!(abandon_expired(&root,"active-op").is_err());
  op.id="unknown-op".into();op.intent=json!({"expires_at":now().saturating_sub(1)});op.state="unknown".into();store(&root,&op).unwrap();assert!(abandon_expired(&root,"unknown-op").is_err());
  let _=fs::remove_dir_all(&root);
 }
}
