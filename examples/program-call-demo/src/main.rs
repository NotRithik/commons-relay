//! Deliberately harmless reference for the required deploy/call skills.
//!
//! Accepts exactly one tagged instruction and returns every account unchanged.
//! It has no transfer, mint, custody or arbitrary-code operation. A successful
//! transaction still has the network's ordinary nonce/receipt behavior.
use lee_core::program::{ProgramInput, ProgramOutput, read_lee_inputs};

fn main() {
    let (
        ProgramInput { self_program_id, caller_program_id, pre_states, instruction },
        words,
    ) = read_lee_inputs::<u32>();
    assert_eq!(instruction, 0x4b495445, "KITE_TAG_REQUIRED");
    assert!(!pre_states.is_empty() && pre_states.len() <= 16, "ACCOUNT_LIMIT");
    let post_states = pre_states.iter()
        .map(|entry| lee_core::program::AccountPostState::new(entry.account.clone()))
        .collect();
    ProgramOutput::new(self_program_id, caller_program_id, words, pre_states, post_states).write();
}
