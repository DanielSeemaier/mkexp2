# End-to-End Scenarios

The end-to-end suite covers these workflows:

1. `init` and discoverability
   - `mkexp2 init Default` creates `Experiment`
   - `.gitignore` gets `.mkexp2/` and `logs/`
   - list commands expose bundled presets and partitioners, including the test-only `TestHarness` plugin

2. Local install + generate + submit with multiple algorithms
   - multiple algorithms in one experiment
   - derived algorithms from plugin aliases and user-defined aliases
   - algorithm-specific property overrides affecting generated commands
   - CLI build flags such as `-j`
   - build reuse for algorithms that share the same build identity
   - generated jobs execute successfully and produce logs for every algorithm/graph combination

3. Parse end-to-end from generated logs
   - bundled parser selection by algorithm base
   - `mkexp2 parse` creates CSV output for base and derived algorithms

4. Validation and plugin introspection
   - `mkexp2 check` rejects invalid closed-set property values
   - `mkexp2 describe` shows defaults and aliases for the test-only plugin
