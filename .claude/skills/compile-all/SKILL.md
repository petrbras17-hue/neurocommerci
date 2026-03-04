---
name: compile-all
description: Compile-check all Python files in the project to verify no syntax errors
allowed-tools: Bash, Glob
---

# Compile All Python Files

Find and compile-check every .py file in the project (excluding venv/).

```bash
cd "$CLAUDE_PROJECT_DIR"
ERRORS=0
for f in $(find . -name "*.py" -not -path "./venv/*" -not -path "./.claude/*"); do
  OUTPUT=$(python3 -m py_compile "$f" 2>&1)
  if [ $? -ne 0 ]; then
    echo "FAIL: $f"
    echo "  $OUTPUT"
    ERRORS=$((ERRORS+1))
  fi
done
if [ $ERRORS -eq 0 ]; then
  echo "All Python files compile successfully."
else
  echo "$ERRORS file(s) have syntax errors."
fi
```

Report the result to the user.
