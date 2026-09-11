//! Offline public-program diagnostic: no wallet loading, signing or submission.
use anyhow::{Context, Result, ensure};
use lee::program::Program;
use lee_core::{account::AccountWithMetadata, program::{ProgramId, ProgramOutput}};
use risc0_zkvm::{ExecutorEnv, default_executor};
use serde::Deserialize;
use serde_json::json;
use std::{fs, path::Path};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input { accounts: Vec<AccountWithMetadata>, instruction: Vec<u32> }
fn read(path: &Path, maximum: u64) -> Result<Vec<u8>> {
    let meta=fs::symlink_metadata(path)?;
    ensure!(meta.is_file() && !meta.file_type().is_symlink() && meta.len() <= maximum, "invalid diagnostic input");
    Ok(fs::read(path)?)
}
fn main() -> Result<()> {
    let mut args=std::env::args().skip(1);
    let file=args.next().context("usage: public-program-inspect PROGRAM PUBLIC_INPUT_JSON")?;
    let input=args.next().context("public input JSON required")?;
    let transaction_path=args.next();
    ensure!(args.next().is_none(), "unexpected argument");
    let program=Program::new(read(Path::new(&file),64*1024*1024)?.into())?;
    let input:Input=serde_json::from_slice(&read(Path::new(&input),64000)?)?;
    ensure!(!input.accounts.is_empty() && input.accounts.len()<=16 && input.instruction.len()<=16384,"input limit");
    let mut builder=ExecutorEnv::builder();
    builder.session_limit(Some(32*1024*1024));
    builder.write(&program.id())?.write(&None::<ProgramId>)?.write(&input.accounts)?.write(&input.instruction)?;
    let started=std::time::Instant::now();
    let session=default_executor().execute(builder.build()?,program.elf())?;
    let output:ProgramOutput=session.journal.decode()?;
    ensure!(output.self_program_id==program.id() && output.caller_program_id.is_none()
        && output.pre_states==input.accounts && output.instruction_data==input.instruction,"guest output binding mismatch");
    let unchanged=output.chained_calls.is_empty() && output.post_states.len()==input.accounts.len()
        && output.post_states.iter().zip(&input.accounts).all(|(after,before)|after.account()==&before.account && after.required_claim().is_none());
    let mut transaction_check=serde_json::Value::Null;
    if let Some(path)=transaction_path {
        let recorded:common::transaction::LeeTransaction=borsh::from_slice(&read(Path::new(&path),64*1024*1024)?)?;
        let hash=recorded.hash().to_string();
        let common::transaction::LeeTransaction::Public(tx)=recorded else {anyhow::bail!("only public transactions may be inspected")};
        let snapshot=lee::V03State::new()
            .with_public_accounts(input.accounts.iter().map(|a|(a.account_id,a.account.clone())))
            .with_programs([program.clone()]);
        let checked=lee::ValidatedStateDiff::from_public_transaction(&tx,&snapshot,0,0);
        transaction_check=json!({"transaction_hash":hash,"valid_against_snapshot":checked.is_ok(),
            "error":checked.err().map(|e|e.to_string()),"state_applied":false,
            "note":"Local validation with supplied public accounts; not a network receipt"});
    }
    println!("{}",serde_json::to_string_pretty(&json!({
        "transaction_check":transaction_check,
        "program_id":hex::encode(program.id().iter().flat_map(|n|n.to_le_bytes()).collect::<Vec<_>>()),
        "guest_executed":true,"guest_user_cycles":session.cycles(),"accounts_unchanged":unchanged,
        "account_count":input.accounts.len(),"elapsed_millis":started.elapsed().as_millis(),
        "wallet_loaded":false,"network_requests":0,"transaction_submitted":false,
        "note":"Guest execution only, not sequencer acceptance or a zero-knowledge proof"}))?);
    Ok(())
}
