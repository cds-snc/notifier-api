#!/bin/bash
#
# Run project tests
#
# NOTE: This script expects to be run from the project root with
# ./scripts/run_tests.sh

set -o pipefail

function display_result {
  RESULT=$1
  EXIT_STATUS=$2
  TEST=$3

  if [ "$RESULT" -ne 0 ]; then
    echo -e "\033[31m$TEST failed\033[0m"
    exit "$EXIT_STATUS"
  else
    echo -e "\033[32m$TEST passed\033[0m"
  fi
}

make test-requirements
display_result $? 1 "Requirements check"

ruff check .
display_result $? 1 "Code style check"

ruff check --select I .
display_result $? 1 "Import order check"

mypy .
display_result $? 1 "Type check"

# Run tests that need serial execution.
if ! docker info > /dev/null 2>&1; then
  echo "This test uses docker, and it isn't running - please start docker and try again."
  exit 1
fi
py.test --disable-pytest-warnings --cov=app --cov-report=term-missing tests/ --junitxml=test_results_serial.xml -v --maxfail=10 -m "serial"
display_result $? 2 "Unit tests [serial]"

# Run with four concurrent threads.
py.test --disable-pytest-warnings --cov=app --cov-report=term-missing tests/ --junitxml=test_results.xml -n4 -v --maxfail=10 -m "not serial"
display_result $? 2 "Unit tests [concurrent]"
