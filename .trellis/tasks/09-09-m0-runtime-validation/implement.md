# Execution

1. Reuse detector and persistence research from the bootstrap task. Lock versions only after smoke checks.
2. Add failing adapter/API boundary tests, implement minimum code, run targeted tests.
3. Install native persistent services, bind loopback, create project databases; test process restart with a waiting workflow.
4. Record commands, observed versions, actual success/failure, and pending provider details in this task; independent review before marking complete.

Write isolation: root owns packaging/configuration/workflow integration and jj; delegated workers own explicitly named files only. No nested agents or competing task ledgers. Stop only the dependency branch requiring missing API configuration; do not label T02 fully complete until live checks are done.
