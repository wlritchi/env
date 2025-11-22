#!/usr/bin/env bash
set -euo pipefail

# Get current month's first day in YYYYMM01 format
since_date=$(date +%Y%m01)

# Fetch usage data
usage=$(bunx ccusage --since "$since_date" -Oj 2>/dev/null || echo '{"daily":[],"totals":{"totalCost":0}}')

# Extract and format costs
daily=$(echo "$usage" | jq -r '.daily[-1].totalCost // 0')
monthly=$(echo "$usage" | jq -r '.totals.totalCost // 0')

# Format with 2 decimal places and output as JSON for waybar
printf '{"text":"$%.2f | $%.2f","tooltip":"Claude Code Usage\\nDaily: $%.2f\\nMonthly: $%.2f","class":"claude-usage"}' \
    "$daily" "$monthly" "$daily" "$monthly"
