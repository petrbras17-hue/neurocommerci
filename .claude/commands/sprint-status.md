Show full sprint status across all dimensions.

Launch in parallel:
1. Read change register and show current sprint state
2. Run `git status` and `git log --oneline -5`
3. Run `pytest --co -q` to list available tests
4. Check docker container status if applicable

Synthesize into a status dashboard:
- Sprint: [name]
- Branch: [branch] @ [commit]
- Local tests: [count] collected, [pass/fail]
- VPS: [deployed/pending]
- Next action: [what to do next]
