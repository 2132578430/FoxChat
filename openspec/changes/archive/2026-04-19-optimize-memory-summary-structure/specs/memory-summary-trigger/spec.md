## ADDED Requirements

### Requirement: Summary trigger uses configurable threshold

Memory summary SHALL be triggered based on a configurable message count threshold, not hardcoded values.

#### Scenario: Threshold configuration
- **WHEN** the system initializes
- **THEN** `SUMMARY_TRIGGER_THRESHOLD` SHALL be defined as a configurable variable

#### Scenario: Trigger condition check
- **WHEN** recent_msg_size >= SUMMARY_TRIGGER_THRESHOLD
- **THEN** summary process SHALL be triggered

### Requirement: Recent message keep size is configurable

The number of messages kept in recent_msg after summary SHALL be configurable.

#### Scenario: Keep size configuration
- **WHEN** the system initializes
- **THEN** `RECENT_MSG_KEEP_SIZE` SHALL be defined as a configurable variable

#### Scenario: Boundary relationship
- **WHEN** variables are configured
- **THEN** `SUMMARY_TRIGGER_THRESHOLD` SHALL be greater than `RECENT_MSG_KEEP_SIZE`

### Requirement: Memory boundary is clear and complete

Memory summary SHALL ensure no message is lost between recent_msg and summary storage.

#### Scenario: Clear boundary
- **WHEN** summary is triggered
- **THEN** messages from index `RECENT_MSG_KEEP_SIZE` to end SHALL be summarized
- **AND** messages from index 0 to `RECENT_MSG_KEEP_SIZE - 1` SHALL remain in recent_msg

#### Scenario: No message loss
- **WHEN** summary completes
- **THEN** every message SHALL exist either in recent_msg or in summary storage
- **AND** no message SHALL exist in both places (no overlap)

### Requirement: Summary trigger based on conversation rounds

Summary trigger threshold SHALL be calculated based on conversation rounds (user + AI messages).

#### Scenario: Round-based threshold
- **WHEN** `SUMMARY_TRIGGER_ROUNDS = 7`
- **THEN** `SUMMARY_TRIGGER_THRESHOLD = 14` (7 rounds × 2 messages per round)