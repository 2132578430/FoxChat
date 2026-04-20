## Requirements

### Requirement: Event structure includes actor field

Memory events SHALL include an `actor` field to identify the subject of the event (USER or AI).

#### Scenario: User action event
- **WHEN** an event is extracted from conversation where user performs an action
- **THEN** the event SHALL have `actor` field set to "USER"

#### Scenario: AI action event
- **WHEN** an event is extracted from conversation where AI performs an action
- **THEN** the event SHALL have `actor` field set to "AI"

### Requirement: Event structure includes optional action field

Memory events MAY include an `action` field to categorize the type of action performed.

#### Scenario: Action categorization
- **WHEN** an event describes a specific action (e.g., "go to wash", "wait")
- **THEN** the event MAY have `action` field with a short identifier

### Requirement: Event structure includes optional keywords field

Memory events MAY include a `keywords` field for future keyword-based retrieval mechanism.

#### Scenario: Keywords extraction
- **WHEN** an event is extracted
- **THEN** the event MAY have `keywords` field with relevant keywords array

### Requirement: Event JSON format is valid

All memory events SHALL be valid JSON with required fields: `time`, `type`, `actor`, `content`.

#### Scenario: Valid JSON output
- **WHEN** event extraction completes
- **THEN** the output SHALL be parseable JSON array with valid structure