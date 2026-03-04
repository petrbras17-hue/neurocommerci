#!/bin/bash
# protect-files.sh — блокирует редактирование чувствительных файлов

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

PROTECTED_PATTERNS=(".env" ".session" "credentials.json" ".git/" "data/sessions/" "data/neuro_commenting.db")

for pattern in "${PROTECTED_PATTERNS[@]}"; do
  if [[ "$FILE_PATH" == *"$pattern"* ]]; then
    echo "BLOCKED: $FILE_PATH matches protected pattern '$pattern'. Нельзя редактировать чувствительные файлы." >&2
    exit 2
  fi
done

exit 0
