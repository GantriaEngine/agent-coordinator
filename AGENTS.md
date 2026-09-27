# Repository policy

Use PascalCase for applicable identifiers. Read README.md and docs/SECURITY.md
before changing boundaries. Preserve existing protocol v1 representations.
The protocol coordinates capabilities; it does not transmit authority.
Natural-language agent communication does not directly invoke endpoint capabilities.
Never add shell, script, or arbitrary process primitives. Wire data and workflows
are declarative only; executable adapters are installed and approved locally.
Unknown schemas, versions, capabilities, transitions and fields fail closed.
Keep evidence local, parameters bounded, cleanup explicit and child windows hidden.
Use focused denial/lifecycle tests. Do not run physical captures while testing.
Do not claim Windows service qualification from simulation or compilation.
