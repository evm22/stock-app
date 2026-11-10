#!/bin/zsh
# Wrapper launchd calls once a day: run daily.py from the project folder
# with the project's venv, appending output to logs/daily.log.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
{
  echo ""
  echo "##### $(date '+%Y-%m-%d %H:%M:%S') #####"
  ./venv/bin/python -u -W ignore daily.py "$@"
} >> logs/daily.log 2>&1
