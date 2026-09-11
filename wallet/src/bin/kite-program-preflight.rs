//! Execute the no-change example locally on a saved PUBLIC account snapshot.
//! No wallet is opened, no keys are loaded and no transaction is submitted.
use std::{env, fs, path::PathBuf};
use anyhow::{Context, Result, ensure};
use lee::program::Program;
use lee_core::{account::{Account, AccountId, AccountWithMetadata}, program::{ProgramId, ProgramOutput}};
use risc0_zkvm::{ExecutorEnv, default_executor};
use serde_json::{Value, json};

fn main() -> Result<()> {
    let mut args = env::args_os().skip(1);
    let binary = PathBuf::from(args.next().context("program file required")?);
    let snapshot = PathBuf::from(args.next().context("public account snapshot required")?);
    ensure!(args.next().is_none(), "unexpected argument");
    ensure!(fs::metadata(&binary)?.len() <= 64 * 1024 * 1024, "program too large");
    ensure!(fs::metadata(&snapshot)?.len() <= 65536, "snapshot too large");
    let bytes = fs::read(binary)?;
    let program = Program::new(bytes.clone().into())?;
    let value: Value = serde_json::from_slice(&fs::read(snapshot)?)?;
    let account: Account = serde_json::from_value(value["account"].clone())?;
    let id_bytes: [u8; 32] = hex::decode(value["account_id"].as_str().context("account id missing")?)?
        .try_into().map_err(|_| anyhow::anyhow!("bad account id"))?;
    let pre = vec![AccountWithMetadata::new(account.clone(), true, AccountId::new(id_bytes))];
    let words = vec![0x4b495445_u32];
    let mut builder = ExecutorEnv::builder();
    builder.session_limit(Some(32 * 1024 * 1024));
    builder.write(&program.id())?.write(&None::<ProgramId>)?.write(&pre)?.write(&words)?;
    let started = std::time::Instant::now();
    let session = default_executor().execute(builder.build()?, &bytes)?;
    let result: ProgramOutput = session.journal.decode()?;
    ensure!(result.self_program_id == program.id(), "program id changed");
    ensure!(result.pre_states == pre && result.instruction_data == words, "input binding changed");
    ensure!(result.post_states.len() == 1, "unexpected output length");
    ensure!(result.post_states[0].account() == &account, "example changed account state");
    ensure!(result.chained_calls.is_empty(), "unexpected chained calls");
    println!("{}", json!({"local_execution":true,"transaction_submitted":false,
        "wallet_opened":false,"account_state_unchanged":true,
        "program_id":hex::encode(program.id().iter().flat_map(|x|x.to_le_bytes()).collect::<Vec<_>>()),
        "elapsed_millis":started.elapsed().as_millis(),"session":format!("{session:?}")}));
    Ok(())
}
