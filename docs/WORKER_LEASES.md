# Host-local worker admission v1

Use one ledger on the hosting PC instead of chat-based reservation/queue polling.
SQLite `BEGIN IMMEDIATE` transactions serialize admission; FULL synchronous
commits preserve the single reservation across caller crashes. Keep the database
on local disk, never on a network share or copied per client. All heavy native,
Docker and benchmark users of the same host share `worker-heavy`. Existing
parallelism limits still apply inside the exclusive reservation.

On dockerbox the installed entrypoint is
`C:\Sandbox\Codex\Tools\WorkerLeases\worker_leases.py`, run with
`C:\Sandbox\Codex\Tools\python-3.12.6-controlled-v1\python.exe` over existing SSH.
The database is `C:\Sandbox\Codex\Coordinator\WorkerLeases\State.sqlite3`.

```
python worker_leases.py status
python worker_leases.py acquire --chat-id <current-chat-UUID> --owner <campaign> --mode build --ttl 900
python worker_leases.py renew --token <returned-token> --ttl 900
python worker_leases.py release --token <returned-token> --acknowledge-idle
```

Acquire is the authority for cooperative admission; a Free status alone is not
a reservation. Busy exits 3 immediately and reports owning ChatId, campaign,
mode, acquisition/heartbeat/expiry UTC and generation. Work on something else;
check again when ready to use the host, without waking chats or tight polling.
Errors exit 2 and deny admission; wrong/stale tokens exit 4. Keep the owner token
in a task-local file, out of chat/logs. Renew before expiry while work is active,
including during long tools. Release only after all owned child jobs/containers
finish. A generation increases at each acquisition; old tokens cannot release or
renew a later owner. Records are one bounded row, not an unbounded event history.

Overdue means stale heartbeat, not idle hardware. It remains Busy until the owner
reconciles and releases. An abandoned reservation requires host-local inspection
of the recorded campaign's jobs followed by an explicit token-authorized release,
or `reconcile --chat-id <recorded-chat> --generation <observed-generation>
--acknowledge-idle` for an expired owner. Reconciliation atomically checks expiry,
chat and generation so a concurrent renewal or replacement denies the operation;
do not delete/reset the database or steal an expired lease. Wall-clock jumps can
change the diagnostic, but cannot free a live reservation. There is no force or
automatic takeover. ChatId/time diagnose ownership; they do not confer
OS authority. This is cooperative admission, not an OS scheduler, remote process
launcher, or fencing of non-participating workloads.

Migration starts with a hold covering pre-existing unregistered workloads. Keep
it until those jobs finish and their host ownership is reconciled. Do not replace
active coordinator packages/services or terminate any workload during adoption.
The installed ledger needs no daemon, service, port, new runtime or firewall rule.
