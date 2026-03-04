#!/bin/bash
# py-compile-check.sh — проверяет что Python файл компилируется после редактирования

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Только для .py файлов
if [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

# Проверить что файл существует
if [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

# Попробовать скомпилировать
OUTPUT=$(python3 -m py_compile "$FILE_PATH" 2>&1)
if [ $? -ne 0 ]; then
  echo "SYNTAX ERROR в $FILE_PATH: $OUTPUT" >&2
  exit 2
fi

exit 0
