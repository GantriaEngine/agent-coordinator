# Transport v1 and control profiles

UTF-8 newline-delimited JSON, maximum 16,384 bytes including newline. TCP_NODELAY,
bounded send timeout, one connection per endpoint, no reconnect/resume/retry.
The extracted transport envelope preserves `Version`, canonical `RunId`, exact
incrementing integer `Sequence`, `TimestampUnixMs`, `Token`, `Type`; timestamps
are evidence, monotonic clocks enforce local budgets. A 256-bit token authenticates
each envelope. Sequences begin at 1 independently per direction and increment
exactly by 1 even when bootstrap identity changes to run identity.

Transport version, schema ID/version, capability `.vN` and control profile are
independent. Legacy readiness v1 retains its established messages and transition
checks; it is not wire-compatible with the schema-control profile. Both use
transport v1. Consumer selects a profile locally, never by an agent message.

For the legacy readiness profile, a trusted local coordinator config may set
`ResultClassification` to an uppercase identifier of at most 64 characters.
With this key present, both endpoint `*_DONE` reports must carry that exact
classification. A mismatch produces `RUN_DONE Success=false` and a failed
top-level result under the configured classification. Without the key, the
historical `ONE_CLIENT_READINESS_ONLY` label and success behavior are preserved.
This is result reconciliation, not a wire capability or acceptance gate.

## Schema-control v1

The machine-readable [envelope schema](../agent_coordinator/schemas/envelope.json)
and executable `control.FIELDS`, `Workflow` validation define the exact format.
Unknown categories/fields reject; no free-text diagnostic is a control command.

| Message | Direction | Additional fields |
| --- | --- | --- |
| PULL_ASSIGNMENT | endpoint → host | EndpointId |
| ASSIGNMENT | host → endpoint | Assignment |
| REGISTER | endpoint → host | Role, SchemaId, SchemaVersion, SchemaHash, Capabilities |
| REGISTERED | host → endpoint | Role |
| STAGED | endpoint → host | Role |
| ARMED | host → all staged endpoints | ExecutionTimeout |
| START | host → assigned role | Index, Role, Capability, From, To, Parameters, Timeout, ExecutionTimeout |
| LIVE / RESULT | endpoint → host | Index, Role, Success, Evidence |
| COMPLETE | host → endpoints | Success |
| CLEANED | endpoint → host | Role, Success |
| ABORT | either | Detail (diagnostic only) |

Assignment fields: RunId, Token, Role, SchemaId, SchemaVersion, SchemaHash,
Parameters, ExpiresUnixMs. Coordinator address comes from local bootstrap config,
not from a redirected wire destination. Artifact identities belong to locally
installed project adapters, not this numeric-parameter example profile.

Lifecycle: CREATED → assignment pulled → endpoints REGISTERED/STAGED → ARMED →
execution deadline starts → ordered START/LIVE/RESULT transitions → COMPLETE →
all CLEANED → success. Missing/failed result, stale index, wrong emitter, expired
assignment, bad token/run/sequence, disconnect or malformed input causes ABORT.
No heartbeat is needed for these bounded short-lived runs; socket disconnect and
deadlines bound silence. Status of legacy probe/capture is local adapter state;
there is no remotely executable diagnostic/status text.

Evidence metadata is Path, SHA256, Bytes; maximum 32 entries per result, bounded
by the frame limit. Journals/logs are capped at 16 MiB; manifests hash every local
evidence file except themselves. Bulk archive transfer is a separate project-owned
operation after cleanup. Metadata paths are never opened/executed by the host.
