Run code review + QA audit + test suite in parallel.

Launch these three agents simultaneously:
1. `saas-code-reviewer` — review code quality, security, tenant isolation
2. `qa-tenant-auditor` — audit RLS, migrations, API contracts, sprint acceptance
3. Run `pytest` in bash — verify all tests pass

After all three complete, synthesize a combined report:
- Code review verdict
- QA audit verdict
- Test results
- Overall: SHIP / HOLD / FIX
