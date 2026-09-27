# Gargantuan adoption and physical-PC migration

Source provenance is recorded in [PROVENANCE.json](../PROVENANCE.json), including
hashes of the original untracked working implementation. No source history was
invented; the extraction base commit identifies the surrounding Gargantuan tree.
Transport/journal and legacy control logic were extracted without changing v1
messages or Foundation 3L acceptance. Gargantuan retains validation, LocalRun,
staging, exact artifact/probe/capture hook, evidence policy and result acceptance.

Gargantuan pins an exact commit using a bootstrap/install script into an ignored
local directory. Its adapter imports the pinned library; packages include that
library and preserve old standalone file layout. No submodule or unpinned main
dependency is required. Installed bundles on both PCs remain usable as-is.

1. Keep current installed coordinator, endpoint and capture service on both PCs.
2. Bootstrap the pinned source on the development checkout and run both suites,
   the hook simulation and packaging integrity checks.
3. Package a new bundle into a fresh sibling location on each PC; verify its
   manifest and exact probe/hook identity. Do not replace the installed bundle.
4. Locally qualify the extracted legacy profile under the existing one-client
   readiness authorization and reconcile evidence/cleanup before selecting it.
5. Keep the original bundle/service as rollback until both PCs are qualified.
6. Separately install reviewed schemas/capabilities/bootstrap identities before
   adopting assignment pull for a project workflow. Generic examples cannot start
   Gargantuan's probe. The current Gargantuan legacy stage still writes per-run
   config; adapting fresh assignment pull requires a separately qualified bounded
   project adapter, not a silent change to Foundation 3L synchronization semantics.

No acceptance status, POOLED_SERVICE, physical gate, KI-006 or 3M changes here.
