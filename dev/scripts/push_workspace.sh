#!/usr/bin/env bash
#
# Push a Git workspace that may contain many submodules.
#
# The script is repo-agnostic: copy it to the root of any Git repository. It
# discovers initialized submodules recursively and treats positional arguments
# as include filters for submodule paths.
#
# Examples:
#   ./push_workspace.sh
#   ./push_workspace.sh formalizations submodules
#   ./push_workspace.sh --submodules-only third_party
#   ./push_workspace.sh --dry-run vendor

set -u
set -o pipefail

script_name="$(basename "$0")"
PUSH_PARENT=1
PARENT_ONLY=0
DRY_RUN=0
LIST_ONLY=0
SET_UPSTREAM=1

if [ "${DRYRUN:-}" = "1" ]; then
    DRY_RUN=1
fi

usage() {
    cat <<USAGE
Usage:
  ./$script_name [options] [PATH_PREFIX ...]

Default behavior:
  Push selected initialized submodule branches first, then push the
  superproject branch. Submodules that are detached or have no work to push are
  skipped cleanly.

Path filters:
  Positional arguments are include prefixes relative to the repository root.
  With no filters, every initialized submodule is selected.

  ./$script_name formalizations submodules
      selects initialized submodules at formalizations/* and submodules/*,
      but not ta1/*. The superproject still pushes unless --submodules-only is
      used.

Options:
  --submodules-only, --no-parent
      Do not push the superproject.

  --parent-only
      Push only the superproject; do not touch submodules.

  --no-set-upstream
      If a branch has no upstream, skip it instead of pushing with
      'git push -u origin <branch>'.

  --list
      Print selected initialized submodules and exit.

  --dry-run
      Use 'git push --dry-run' and avoid mutating commands.

  -h, --help
      Show this help.

Environment:
  DRYRUN=1    Alias for --dry-run.
USAGE
}

filters=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --submodules-only|--no-parent)
            PUSH_PARENT=0
            shift
            ;;
        --parent-only)
            PARENT_ONLY=1
            shift
            ;;
        --no-set-upstream)
            SET_UPSTREAM=0
            shift
            ;;
        --list)
            LIST_ONLY=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            while [ "$#" -gt 0 ]; do
                filters+=("$1")
                shift
            done
            ;;
        --*)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            filters+=("$1")
            shift
            ;;
    esac
done

repo_error_file="$(mktemp)"
repo_root="$(git rev-parse --show-toplevel 2>"$repo_error_file")"
repo_status=$?
if [ "$repo_status" -ne 0 ]; then
    echo "error: could not determine Git repository root" >&2
    sed 's/^/  /' "$repo_error_file" >&2
    rm -f "$repo_error_file"
    exit 2
fi
rm -f "$repo_error_file"
cd "$repo_root" || exit 2

summary_file="$(mktemp)"
trap 'rm -f "$summary_file"' EXIT

record() {
    # status, target, message
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$summary_file"
}

status_line() {
    # status, message
    printf '  %-6s %s\n' "$1" "$2"
}

section() {
    printf '\n== %s ==\n' "$1"
}

format_command() {
    printf '%q' "$1"
    shift
    while [ "$#" -gt 0 ]; do
        printf ' %q' "$1"
        shift
    done
}

normalize_filter() {
    local path="$1"
    case "$path" in
        "$repo_root")
            path=""
            ;;
        "$repo_root"/*)
            path="${path#"$repo_root"/}"
            ;;
    esac
    while [ "${path#./}" != "$path" ]; do
        path="${path#./}"
    done
    while [ "${path%/}" != "$path" ]; do
        path="${path%/}"
    done
    if [ "$path" = "." ]; then
        path=""
    fi
    printf '%s\n' "$path"
}

norm_filters=()
for filter in "${filters[@]}"; do
    norm_filters+=("$(normalize_filter "$filter")")
done

path_matches_filters() {
    # True when a submodule path should be selected. Matching is intentionally
    # two-way so a filter inside a submodule selects that containing submodule.
    local sub_path="$1"
    local filter

    if [ "${#norm_filters[@]}" -eq 0 ]; then
        return 0
    fi

    for filter in "${norm_filters[@]}"; do
        if [ -z "$filter" ]; then
            return 0
        fi
        if [ "$sub_path" = "$filter" ]; then
            return 0
        fi
        case "$sub_path" in
            "$filter"/*)
                return 0
                ;;
        esac
        case "$filter" in
            "$sub_path"/*)
                return 0
                ;;
        esac
    done

    return 1
}

is_dirty_repo() {
    [ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ]
}

read_initialized_submodule_paths() {
    if [ ! -f .gitmodules ]; then
        return 0
    fi
    git submodule foreach --recursive --quiet 'printf "%s\n" "$displaypath"' 2>/dev/null || true
}

selected_paths=()
if [ "$PARENT_ONLY" -eq 0 ]; then
    while IFS= read -r sub_path; do
        [ -n "$sub_path" ] || continue
        if path_matches_filters "$sub_path"; then
            selected_paths+=("$sub_path")
        fi
    done < <(read_initialized_submodule_paths)
fi

if [ "$LIST_ONLY" -eq 0 ] && [ "${#norm_filters[@]}" -gt 0 ] && [ "$PARENT_ONLY" -eq 0 ] && [ "${#selected_paths[@]}" -eq 0 ]; then
    echo "error: no initialized submodules matched the requested path filters" >&2
    printf 'filters:' >&2
    for filter in "${norm_filters[@]}"; do
        printf ' %s' "${filter:-.}" >&2
    done
    printf '\n' >&2
    exit 2
fi

print_plan() {
    section "Plan"
    printf '  repo     %s\n' "$repo_root"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  dry-run  yes\n'
    else
        printf '  dry-run  no\n'
    fi
    if [ "$PUSH_PARENT" -eq 1 ]; then
        printf '  parent   push after submodules\n'
    else
        printf '  parent   disabled\n'
    fi

    printf '  filters '
    if [ "${#norm_filters[@]}" -eq 0 ]; then
        printf ' <all initialized submodules>\n'
    else
        local filter
        for filter in "${norm_filters[@]}"; do
            printf ' %s' "${filter:-.}"
        done
        printf '\n'
    fi

    printf '\nSelected initialized submodules:\n'
    if [ "${#selected_paths[@]}" -eq 0 ]; then
        printf '  <none>\n'
    else
        local sub_path
        for sub_path in "${selected_paths[@]}"; do
            printf '  - %s\n' "$sub_path"
        done
    fi
}

push_branch_if_needed() {
    local path="$1"
    local label="$2"
    local branch upstream ahead remote_url

    section "$label"

    branch="$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [ -z "$branch" ]; then
        status_line "skip" "detached HEAD"
        record "SKIP" "$label" "detached HEAD"
        return 0
    fi

    if is_dirty_repo "$path"; then
        status_line "warn" "local uncommitted changes present; push only sends commits"
        record "WARN" "$label" "local uncommitted changes present"
    fi

    upstream="$(git -C "$path" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    if [ -n "$upstream" ]; then
        ahead="$(git -C "$path" rev-list --count "$upstream..HEAD" 2>/dev/null || echo 0)"
        if [ "$ahead" -eq 0 ]; then
            status_line "ok" "no unpushed commits on $branch"
            record "OK" "$label" "no unpushed commits on $branch"
            return 0
        fi

        if [ "$DRY_RUN" -eq 1 ]; then
            status_line "dry" "would push $branch to $upstream ($ahead commit(s) ahead)"
            status_line "run" "$(format_command git -C "$path" push --dry-run)"
            if git -C "$path" push --dry-run; then
                record "DRY" "$label" "would push $branch to $upstream"
            else
                status_line "fail" "dry-run push failed"
                record "FAIL" "$label" "dry-run push failed for $branch"
            fi
            return 0
        fi

        status_line "run" "git push ($branch -> $upstream, $ahead commit(s) ahead)"
        if git -C "$path" push; then
            status_line "ok" "pushed $branch to $upstream"
            record "OK" "$label" "pushed $branch to $upstream"
        else
            status_line "fail" "push failed for $branch"
            record "FAIL" "$label" "push failed for $branch to $upstream"
        fi
        return 0
    fi

    remote_url="$(git -C "$path" remote get-url origin 2>/dev/null || true)"
    if [ -z "$remote_url" ]; then
        status_line "skip" "no upstream and no origin remote for $branch"
        record "SKIP" "$label" "no upstream and no origin remote for $branch"
        return 0
    fi

    if [ "$SET_UPSTREAM" -eq 0 ]; then
        status_line "skip" "no upstream for $branch (--no-set-upstream)"
        record "SKIP" "$label" "no upstream for $branch"
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        status_line "dry" "would push -u origin $branch"
        status_line "run" "$(format_command git -C "$path" push --dry-run -u origin "$branch")"
        if git -C "$path" push --dry-run -u origin "$branch"; then
            record "DRY" "$label" "would push -u origin $branch"
        else
            status_line "fail" "dry-run push -u origin failed"
            record "FAIL" "$label" "dry-run push -u origin failed for $branch"
        fi
        return 0
    fi

    status_line "run" "git push -u origin $branch"
    if git -C "$path" push -u origin "$branch"; then
        status_line "ok" "pushed $branch and set upstream"
        record "OK" "$label" "pushed -u origin $branch"
    else
        status_line "fail" "push -u origin failed for $branch"
        record "FAIL" "$label" "push -u origin failed for $branch"
    fi
}

push_parent_repo() {
    if [ "$PUSH_PARENT" -eq 0 ]; then
        section "Superproject"
        status_line "skip" "disabled by --submodules-only"
        record "SKIP" "." "superproject push disabled"
        return 0
    fi

    push_branch_if_needed "." "."
}

count_status() {
    local status="$1"
    awk -F '\t' -v s="$status" '$1 == s {n++} END {print n+0}' "$summary_file"
}

print_status_group() {
    local status="$1"
    local title="$2"
    local count
    count="$(count_status "$status")"
    if [ "$count" -gt 0 ]; then
        printf '\n%s:\n' "$title"
        awk -F '\t' -v s="$status" '$1 == s {printf "  - %s: %s\n", $2, $3}' "$summary_file"
    fi
}

print_summary() {
    section "Summary"

    local ok_count dry_count warn_count skip_count fail_count
    ok_count="$(count_status OK)"
    dry_count="$(count_status DRY)"
    warn_count="$(count_status WARN)"
    skip_count="$(count_status SKIP)"
    fail_count="$(count_status FAIL)"

    printf '  %-6s %s\n' "ok" "$ok_count"
    printf '  %-6s %s\n' "dry" "$dry_count"
    printf '  %-6s %s\n' "warn" "$warn_count"
    printf '  %-6s %s\n' "skip" "$skip_count"
    printf '  %-6s %s\n' "fail" "$fail_count"

    print_status_group WARN "Warnings"
    print_status_group SKIP "Skipped"
    print_status_group FAIL "Failures"

    [ "$fail_count" -eq 0 ]
}

print_plan
if [ "$LIST_ONLY" -eq 1 ]; then
    exit 0
fi

if [ "$PARENT_ONLY" -eq 0 ]; then
    if [ "${#selected_paths[@]}" -eq 0 ]; then
        section "Submodules"
        status_line "none" "no initialized submodules found"
    else
        for sub_path in "${selected_paths[@]}"; do
            push_branch_if_needed "$sub_path" "$sub_path"
        done
    fi
fi

push_parent_repo

print_summary
exit $?
