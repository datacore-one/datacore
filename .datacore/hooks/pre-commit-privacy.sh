#!/usr/bin/env bash
# Pre-commit hook: scan staged files for private content patterns.
# Install: cp .datacore/hooks/pre-commit-privacy.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

PRIVATE_PATTERNS=(
    "0-personal/"
    "notes/journals/"
    "OURA_PERSONAL_ACCESS"
    "KRAKEN_"
    "password"
    "secret_key"
    "private_key"
)

ERRORS=0

for pattern in "${PRIVATE_PATTERNS[@]}"; do
    matches=$(git diff --cached --name-only -z | xargs -0 grep -l "$pattern" 2>/dev/null || true)
    if [ -n "$matches" ]; then
        echo "WARNING: Private pattern '$pattern' found in staged files:"
        echo "$matches" | sed 's/^/  /'
        ERRORS=$((ERRORS + 1))
    fi
done

if [ $ERRORS -gt 0 ]; then
    echo ""
    echo "Found $ERRORS private pattern(s) in staged files."
    echo "Review the files above. To proceed anyway: git commit --no-verify"
    exit 1
fi
