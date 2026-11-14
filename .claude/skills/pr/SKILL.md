---
name: pr
description: Open a pull request for the current branch and request a review from Copilot
allowed-tools: Bash(gh pr create *) Bash(gh pr edit *) Bash(gh pr view *)
---

1. Make sure the current branch has been pushed.
2. Open the PR: `gh pr create --fill --reviewer @copilot`
3. If a PR already exists for this branch: `gh pr edit --add-reviewer @copilot`
