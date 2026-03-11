Run a full SaaS code review on recent changes.

Steps:
1. Run `git diff HEAD~3..HEAD --stat` and `git log --oneline -5` to understand scope
2. Launch the `saas-code-reviewer` agent to review all changes
3. If critical issues found, list them with file:line references
4. Give a final verdict: APPROVE / REQUEST CHANGES
