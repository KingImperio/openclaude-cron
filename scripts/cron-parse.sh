#!/usr/bin/env bash
# cron-parse.sh — POSIX-compliant 5-field cron expression parser
# Usage: source this file, then call: cron_matches "expr" <minute> <hour> <dom> <month> <dow>
# Returns 0 if the expression matches the given time, 1 if not.
#
# Fields: minute hour day-of-month month day-of-week
# Supports: * , - / (wildcard, list, range, step)
# DOM and DOW use OR semantics (vixie-cron): if both are restricted,
# a match in EITHER field qualifies.
# DOW: 0 and 7 both represent Sunday.

set -euo pipefail

# ── Expand a single cron field into a set of matching values ───────────────────
# _cron_expand_field <field> <min> <max>
# Returns a space-separated list of integers that the field matches.
_cron_expand_field() {
    local field="$1" lo="$2" hi="$3"
    local result="" part field_val

    IFS=',' read -ra parts <<< "$field"
    for part in "${parts[@]}"; do
        # Trim whitespace
        part=$(echo "$part" | tr -d ' ')
        [ -z "$part" ] && continue

        local step=""
        local range_part="$part"

        # Extract step: */2 or 1-5/3
        if [[ "$part" == */* ]]; then
            step="${part##*/}"
            range_part="${part%/*}"
        fi

        if [ "$range_part" = "*" ]; then
            # Wildcard: lo to hi
            if [ -n "$step" ]; then
                local i="$lo"
                while [ "$i" -le "$hi" ]; do
                    result="$result $i"
                    i=$((i + step))
                done
            else
                local i="$lo"
                while [ "$i" -le "$hi" ]; do
                    result="$result $i"
                    i=$((i + 1))
                done
            fi
        elif [[ "$range_part" == *-* ]]; then
            # Range: e.g. 1-5
            local range_lo="${range_part%-*}"
            local range_hi="${range_part#*-}"
            if [ -n "$step" ]; then
                local i="$range_lo"
                while [ "$i" -le "$range_hi" ]; do
                    result="$result $i"
                    i=$((i + step))
                done
            else
                local i="$range_lo"
                while [ "$i" -le "$range_hi" ]; do
                    result="$result $i"
                    i=$((i + 1))
                done
            fi
        else
            # Single value or list element (already split by comma)
            result="$result $range_part"
        fi
    done

    # Deduplicate and sort
    echo "$result" | tr ' ' '\n' | sort -un | tr '\n' ' '
}

# ── Check if a value is in a set ──────────────────────────────────────────────
# _cron_value_in_set <value> <set_string>
_cron_value_in_set() {
    local value="$1" set_str="$2"
    # Normalize: leading/trailing spaces
    set_str=" $set_str "
    echo "$set_str" | grep -q " $value "
}

# ── Main matching function ────────────────────────────────────────────────────
# cron_matches <cron_expr> <minute> <hour> <dom> <month> <dow>
# Returns 0 if the current time matches the cron expression, 1 otherwise.
cron_matches() {
    local expr="$1"
    local cur_min="$2" cur_hour="$3" cur_dom="$4" cur_month="$5" cur_dow="$6"

    # Split the cron expression into 5 fields
    local f_min f_hour f_dom f_month f_dow
    read -r f_min f_hour f_dom f_month f_dow <<< "$expr"

    # Expand each field
    local exp_min exp_hour exp_dom exp_month exp_dow
    exp_min=$(_cron_expand_field "$f_min" 0 59)
    exp_hour=$(_cron_expand_field "$f_hour" 0 23)
    exp_dom=$(_cron_expand_field "$f_dom" 1 31)
    exp_month=$(_cron_expand_field "$f_month" 1 12)
    exp_dow=$(_cron_expand_field "$f_dow" 0 7)

    # Normalize DOW: 7 -> 0 (Sunday)
    if echo "$exp_dow" | grep -q " 7"; then
        exp_dow="$exp_dow 0"
        exp_dow=$(echo "$exp_dow" | tr ' ' '\n' | sort -un | tr '\n' ' ')
    fi

    # Check minute, hour, month (must match)
    _cron_value_in_set "$cur_min" "$exp_min" || return 1
    _cron_value_in_set "$cur_hour" "$exp_hour" || return 1
    _cron_value_in_set "$cur_month" "$exp_month" || return 1

    # DOM/DOW OR semantics (vixie-cron):
    # If both DOM and DOW are restricted (not *), match if EITHER matches.
    # If only one is restricted, that one must match.
    local dom_restricted dow_restricted
    dom_restricted=$([ "$f_dom" = "*" ] && echo "no" || echo "yes")
    dow_restricted=$([ "$f_dow" = "*" ] && echo "no" || echo "yes")

    if [ "$dom_restricted" = "yes" ] && [ "$dow_restricted" = "yes" ]; then
        # Both restricted: OR semantics
        if _cron_value_in_set "$cur_dom" "$exp_dom" || _cron_value_in_set "$cur_dow" "$exp_dow"; then
            return 0
        else
            return 1
        fi
    elif [ "$dom_restricted" = "yes" ]; then
        _cron_value_in_set "$cur_dom" "$exp_dom" || return 1
    elif [ "$dow_restricted" = "yes" ]; then
        _cron_value_in_set "$cur_dow" "$exp_dow" || return 1
    fi

    return 0
}

# ── Convenience: check a cron expression against current time ─────────────────
# cron_matches_now <cron_expr>
cron_matches_now() {
    local expr="$1"
    local cur_min cur_hour cur_dom cur_month cur_dow
    cur_min=$(date '+%-M')
    cur_hour=$(date '+%-H')
    cur_dom=$(date '+%-d')
    cur_month=$(date '+%-m')
    cur_dow=$(date '+%-w')  # 0=Sunday in POSIX

    cron_matches "$expr" "$cur_min" "$cur_hour" "$cur_dom" "$cur_month" "$cur_dow"
}

# ── If executed directly (not sourced), test immediately ────────────────────────
# Only runs when called as: bash cron-parse.sh "*/5 * * * *"
# Does NOT run when sourced via: . cron-parse.sh
if [ "${BASH_SOURCE[0]}" = "$0" ] && [ $# -eq 1 ]; then
    if cron_matches_now "$1"; then
        echo "MATCH"
        exit 0
    else
        echo "NO MATCH"
        exit 1
    fi
fi
