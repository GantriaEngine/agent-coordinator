# Workflow schema v1

See the runnable [client/server example](../examples/readiness.json).
`Workflow` accepts only SchemaId, SchemaVersion, Roles, States, Transitions,
Parameters, RegistrationTimeout, ExecutionTimeout and Abort. SchemaVersion is
independent of transport Version and each capability version. Only supported,
locally installed `(SchemaId, SchemaVersion)` entries can join; canonical JSON
SHA-256 must match the installed schema. A new identity/version requires explicit
local installation, not receipt of JSON over the coordination protocol.

Roles map symbolic names to required versioned capabilities (1–16 roles and
1–16 capabilities each). States form an ordered list of 2–65 states. Every edge
has From, To, Role, Capability, Response (LIVE or RESULT), Parameters (names of
declared numeric inputs) and Timeout (1–90 seconds). Edges must match successive
states, approved role/capability and declared inputs. Completion requires every
edge's successful response and all cleanup acknowledgements.

Parameters define integer Minimum/Maximum, bounded to 0–1,000,000. Run assignments
must supply exactly those inputs. Wire commands cannot modify them. There are no
script strings, executable paths, argv, templates, imports, expressions or callbacks
in a schema. Unknown properties reject, including command-smuggling properties.

Registration and execution timeouts are independently 1–600 seconds. Registration
is bounded by assignment expiry; execution starts at the explicit all-role barrier.
Each operation is bounded by the smaller of its timeout and remaining execution
budget. Abort is exactly `local-cleanup`; it cannot define arbitrary actions.
Loops, branches, dynamic role allocation and richer typed project parameters are
future work requiring a separate schema version and review.
