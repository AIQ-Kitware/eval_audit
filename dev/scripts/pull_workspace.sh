#!/usr/bin/env bash
#
# Pull a Git workspace that may contain many submodules.
#
# The script is repo-agnostic: copy it to the root of any Git repository. It
# discovers the repository root, reads .gitmodules, and treats positional
# arguments as include filters for submodule paths.
#
# Examples:
#   ./pull_workspace.sh
#   ./pull_workspace.sh formalizations submodules
#   ./pull_workspace.sh --submodules-only third_party
#   ./pull_workspace.sh --current-branches vendor
#   ./pull_workspace.sh --remote external/foo

set -u
set -o pipefail

script_name="$(basename "$0")"
JOBS="${JOBS:-8}"
PULL_PARENT=1
PARENT_ONLY=0
MODE="recorded"
DRY_RUN=0
LIST_ONLY=0

if [ "${DRYRUN:-}" = "1" ]; then
    DRY_RUN=1
fi

usage() {
    cat <<USAGE
Usage:
  ./$script_name [options] [PATH_PREFIX ...]

Default behavior:
  Pull the superproject with 'git pull --ff-only', then update selected root
  submodules recursively to the commits recorded by the superproject.

Path filters:
  Positional arguments are include prefixes relative to the repository root.
  With no filters, every root submodule in .gitmodules is selected.

  ./$script_name formalizations submodules
      selects submodules at formalizations/* and submodules/*, but not ta1/*.

Options:
  --submodules-only, --no-parent
      Do not pull the superproject.

  --parent-only
      Pull only the superproject; do not touch submodules.

  --checkout-recorded
      Update submodules to the commits recorded by the superproject.
      This is the default.

  --current-branches
      Preserve initialized submodule checkouts. Fetch each selected submodule
      and fast-forward its current branch when it is on a branch. Detached
      submodules are fetched but not moved. Uninitialized submodules are first
      initialized to their recorded commits.

  --remote
      Update selected submodules using their configured remote branch.
      This can change submodule gitlink state in the superproject.

  --list
      Print the selected root submodules and exit.

  --dry-run
      Print what would happen without running mutating Git commands.

  -j, --jobs N
      Parallel jobs for 'git submodule update'. Default: $JOBS.

  -h, --help
      Show this help.

Environment:
  JOBS=N
      Default parallelism for submodule update.

  DRYRUN=1
      Alias for --dry-run.

  LOCAL_SUBMODULE_SEARCH_ROOTS=DIR[:DIR...]
      Extra directories to search when a submodule URL is an absolute local
      path that no longer exists. The parent directory of the current repo is
      always searched by default.
USAGE
}

filters=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --submodules-only|--no-parent)
            PULL_PARENT=0
            shift
            ;;
        --parent-only)
            PARENT_ONLY=1
            shift
            ;;
        --checkout-recorded)
            MODE="recorded"
            shift
            ;;
        --current-branches|--preserve-checkouts|--preserve-checkout)
            MODE="current"
            shift
            ;;
        --remote)
            MODE="remote"
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
        -j|--jobs)
            if [ "$#" -lt 2 ]; then
                echo "error: missing value for $1" >&2
                exit 2
            fi
            JOBS="$2"
            shift 2
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
    # True when a root submodule should be selected. Matching is intentionally
    # two-way so a filter inside a root submodule selects that root submodule.
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

read_root_submodule_paths() {
    if [ ! -f .gitmodules ]; then
        return 0
    fi
    git config --file .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null | while IFS= read -r line; do
        printf '%s\n' "${line#* }"
    done
}

is_worktree() {
    # `git -C empty-submodule-dir rev-parse --is-inside-work-tree` can report
    # the parent repository. Confirm that the Git root is the path itself.
    local path="$1"
    local top actual
    top="$(git -C "$path" rev-parse --show-toplevel 2>/dev/null)" || return 1
    actual="$(cd "$path" 2>/dev/null && pwd -P)" || return 1
    top="$(cd "$top" 2>/dev/null && pwd -P)" || return 1
    [ "$actual" = "$top" ]
}

is_dirty_repo() {
    [ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ]
}

is_dir_empty() {
    local path="$1"
    [ -d "$path" ] || return 1
    [ -z "$(find "$path" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]
}

is_git_source() {
    # Accept both normal worktrees and bare repositories as local clone sources.
    local path="$1"
    [ -e "$path" ] || return 1
    git -C "$path" rev-parse --git-dir >/dev/null 2>&1 && return 0
    git --git-dir="$path" rev-parse --is-bare-repository >/dev/null 2>&1 && return 0
    return 1
}

submodule_config_prefix_for_path() {
    local sub_path="$1"
    local line key value

    if [ ! -f .gitmodules ]; then
        return 1
    fi

    while IFS= read -r line; do
        key="${line%% *}"
        value="${line#* }"
        if [ "$value" = "$sub_path" ]; then
            printf '%s\n' "${key%.path}"
            return 0
        fi
    done < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null || true)

    return 1
}

submodule_url_for_path() {
    local sub_path="$1"
    local prefix
    prefix="$(submodule_config_prefix_for_path "$sub_path")" || return 1
    git config --file .gitmodules --get "$prefix.url" 2>/dev/null
}

url_to_local_path() {
    local url="$1"
    local rel_path dir base

    case "$url" in
        /*)
            printf '%s\n' "$url"
            return 0
            ;;
        file://*)
            printf '%s\n' "${url#file://}"
            return 0
            ;;
        ~/*)
            printf '%s\n' "$HOME/${url#~/}"
            return 0
            ;;
        ./*|../*)
            rel_path="$repo_root/$url"
            dir="$(dirname "$rel_path")"
            base="$(basename "$rel_path")"
            if [ -d "$dir" ]; then
                printf '%s/%s\n' "$(cd "$dir" && pwd -P)" "$base"
            else
                printf '%s\n' "$rel_path"
            fi
            return 0
            ;;
    esac

    return 1
}

candidate_local_sources() {
    local configured_path="$1"
    local base repo_parent root candidate
    base="$(basename "$configured_path")"
    repo_parent="$(dirname "$repo_root")"

    printf '%s\n' "$repo_parent/$base"

    if [ -n "${LOCAL_SUBMODULE_SEARCH_ROOTS:-}" ]; then
        local old_ifs="$IFS"
        IFS=':'
        for root in ${LOCAL_SUBMODULE_SEARCH_ROOTS}; do
            [ -n "$root" ] || continue
            candidate="$root/$base"
            printf '%s\n' "$candidate"
        done
        IFS="$old_ifs"
    fi
}

find_local_source_fallback() {
    local configured_path="$1"
    local candidate seen="\n"

    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        case "$seen" in
            *"\n$candidate\n"*)
                continue
                ;;
        esac
        seen="$seen$candidate\n"
        if is_git_source "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done < <(candidate_local_sources "$configured_path")

    return 1
}

repair_or_diagnose_local_url() {
    local sub_path="$1"
    local initialized="$2"
    local url local_path fallback prefix

    url="$(submodule_url_for_path "$sub_path" || true)"
    [ -n "$url" ] || return 0

    local_path="$(url_to_local_path "$url" || true)"
    [ -n "$local_path" ] || return 0

    if is_git_source "$local_path"; then
        return 0
    fi

    status_line "warn" "configured local URL is not a Git source: $url"
    record "WARN" "$sub_path" "configured local URL is not a Git source: $url"

    fallback="$(find_local_source_fallback "$local_path" || true)"
    if [ -n "$fallback" ]; then
        prefix="$(submodule_config_prefix_for_path "$sub_path")" || return 0
        if [ "$DRY_RUN" -eq 1 ]; then
            status_line "dry" "would use local fallback: $fallback"
            record "DRY" "$sub_path" "would use local fallback $fallback"
        else
            git config "$prefix.url" "$fallback"
            status_line "ok" "using local fallback: $fallback"
            record "WARN" "$sub_path" "using local fallback $fallback for missing URL $url"
        fi
        return 0
    fi

    status_line "note" "searched sibling repo: $(dirname "$repo_root")/$(basename "$local_path")"
    if [ -n "${LOCAL_SUBMODULE_SEARCH_ROOTS:-}" ]; then
        status_line "note" "also searched LOCAL_SUBMODULE_SEARCH_ROOTS"
    fi

    if [ "$initialized" -eq 1 ]; then
        status_line "warn" "continuing because the submodule is already initialized"
        record "WARN" "$sub_path" "local URL missing, but submodule is already initialized"
        return 0
    fi

    status_line "fail" "cannot clone: local URL does not exist"
    record "FAIL" "$sub_path" "cannot clone; configured local URL does not exist: $url"
    return 1
}

selected_paths=()
if [ "$PARENT_ONLY" -eq 0 ]; then
    while IFS= read -r sub_path; do
        [ -n "$sub_path" ] || continue
        if path_matches_filters "$sub_path"; then
            selected_paths+=("$sub_path")
        fi
    done < <(read_root_submodule_paths)
fi

if [ "$LIST_ONLY" -eq 0 ] && [ "${#norm_filters[@]}" -gt 0 ] && [ "$PARENT_ONLY" -eq 0 ] && [ "${#selected_paths[@]}" -eq 0 ]; then
    echo "error: no root submodules matched the requested path filters" >&2
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
    printf '  mode     %s\n' "$MODE"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  dry-run  yes\n'
    else
        printf '  dry-run  no\n'
    fi
    if [ "$PULL_PARENT" -eq 1 ]; then
        printf '  parent   pull if clean and attached\n'
    else
        printf '  parent   disabled\n'
    fi

    printf '  filters '
    if [ "${#norm_filters[@]}" -eq 0 ]; then
        printf ' <all root submodules>\n'
    else
        local filter
        for filter in "${norm_filters[@]}"; do
            printf ' %s' "${filter:-.}"
        done
        printf '\n'
    fi

    printf '\nSelected root submodules:\n'
    if [ "${#selected_paths[@]}" -eq 0 ]; then
        printf '  <none>\n'
    else
        local sub_path
        for sub_path in "${selected_paths[@]}"; do
            printf '  - %s\n' "$sub_path"
        done
    fi
}

pull_parent_repo() {
    section "Superproject"

    if [ "$PULL_PARENT" -eq 0 ]; then
        status_line "skip" "disabled by --submodules-only"
        record "SKIP" "." "superproject pull disabled"
        return 0
    fi

    if is_dirty_repo "."; then
        status_line "skip" "local changes present"
        record "SKIP" "." "superproject has local changes"
        return 0
    fi

    local branch
    branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [ -z "$branch" ]; then
        status_line "skip" "detached HEAD"
        record "SKIP" "." "superproject is detached"
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        status_line "dry" "would run: $(format_command git pull --ff-only --recurse-submodules=no)"
        record "DRY" "." "would pull superproject branch $branch"
        return 0
    fi

    status_line "run" "git pull --ff-only --recurse-submodules=no"
    if git pull --ff-only --recurse-submodules=no; then
        status_line "ok" "superproject is up to date on $branch"
        record "OK" "." "pulled superproject branch $branch"
    else
        status_line "fail" "git pull failed on $branch"
        record "FAIL" "." "superproject pull failed on $branch"
    fi
}

sync_submodule_url() {
    local sub_path="$1"

    if [ "$DRY_RUN" -eq 1 ]; then
        status_line "dry" "would sync submodule URL"
        return 0
    fi

    if git submodule sync --recursive -- "$sub_path" >/dev/null; then
        status_line "ok" "URL synced"
        return 0
    fi

    status_line "fail" "URL sync failed"
    record "FAIL" "$sub_path" "submodule URL sync failed"
    return 1
}

run_submodule_update() {
    local sub_path="$1"
    shift
    local args=("$@")

    if [ "$DRY_RUN" -eq 1 ]; then
        status_line "dry" "would run: $(format_command git "${args[@]}" -- "$sub_path")"
        record "DRY" "$sub_path" "would initialize/update"
        return 0
    fi

    status_line "run" "$(format_command git "${args[@]}" -- "$sub_path")"
    if git "${args[@]}" -- "$sub_path"; then
        status_line "ok" "initialized/updated"
        record "OK" "$sub_path" "initialized/updated in $MODE mode"
        return 0
    fi

    status_line "fail" "could not initialize/update"
    record "FAIL" "$sub_path" "could not initialize/update in $MODE mode"
    return 1
}

pull_current_branch_repo() {
    local path="$1"
    local label="$2"
    local branch

    if ! is_worktree "$path"; then
        status_line "fail" "not an initialized worktree"
        record "FAIL" "$label" "not an initialized worktree"
        return 0
    fi

    if is_dirty_repo "$path"; then
        status_line "skip" "local changes present"
        record "SKIP" "$label" "local changes"
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        status_line "dry" "would fetch and fast-forward current branch if attached"
        record "DRY" "$label" "would fetch/pull current branch"
        return 0
    fi

    status_line "run" "git remote update --prune"
    if ! git -C "$path" remote update --prune; then
        status_line "fail" "remote update failed"
        record "FAIL" "$label" "remote update failed"
        return 0
    fi

    branch="$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [ -z "$branch" ]; then
        status_line "ok" "detached HEAD; fetched only"
        record "OK" "$label" "detached HEAD; fetched only"
        return 0
    fi

    status_line "run" "git pull --ff-only --recurse-submodules=no"
    if git -C "$path" pull --ff-only --recurse-submodules=no; then
        status_line "ok" "branch $branch is up to date"
        record "OK" "$label" "fast-forwarded branch $branch"
    else
        status_line "fail" "pull failed on branch $branch"
        record "FAIL" "$label" "pull failed on branch $branch"
    fi
}

update_one_submodule() {
    local sub_path="$1"
    local initialized=0
    local update_args=()

    section "$sub_path"

    if is_worktree "$sub_path"; then
        initialized=1
        if is_dirty_repo "$sub_path"; then
            status_line "skip" "local changes present"
            record "SKIP" "$sub_path" "local changes before update"
            return 0
        fi
    elif [ -d "$sub_path" ]; then
        if is_dir_empty "$sub_path"; then
            status_line "note" "empty placeholder directory; submodule is not cloned"
        else
            status_line "fail" "directory exists but is not a Git worktree"
            record "FAIL" "$sub_path" "directory exists but is not an initialized submodule"
            return 0
        fi
    else
        status_line "note" "not initialized yet"
    fi

    sync_submodule_url "$sub_path" || return 0
    repair_or_diagnose_local_url "$sub_path" "$initialized" || return 0

    case "$MODE" in
        recorded)
            update_args=(submodule update --init --recursive --jobs "$JOBS")
            ;;
        remote)
            update_args=(submodule update --init --recursive --remote --jobs "$JOBS")
            ;;
        current)
            if [ "$initialized" -eq 0 ]; then
                update_args=(submodule update --init --recursive --jobs "$JOBS")
            else
                update_args=()
            fi
            ;;
        *)
            echo "internal error: unknown mode: $MODE" >&2
            exit 2
            ;;
    esac

    if [ "${#update_args[@]}" -gt 0 ]; then
        run_submodule_update "$sub_path" "${update_args[@]}" || return 0
    else
        status_line "ok" "already initialized; preserving checkout"
        record "OK" "$sub_path" "already initialized; preserved checkout"
    fi

    if [ "$MODE" = "current" ]; then
        pull_current_branch_repo "$sub_path" "$sub_path"
        if is_worktree "$sub_path"; then
            while IFS= read -r nested_path; do
                [ -n "$nested_path" ] || continue
                section "$sub_path/$nested_path"
                pull_current_branch_repo "$sub_path/$nested_path" "$sub_path/$nested_path"
            done < <(git -C "$sub_path" submodule foreach --recursive --quiet 'printf "%s\n" "$displaypath"' 2>/dev/null || true)
        fi
    fi
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

pull_parent_repo

if [ "$PARENT_ONLY" -eq 0 ]; then
    if [ "${#selected_paths[@]}" -eq 0 ]; then
        section "Submodules"
        status_line "none" "no root submodules found"
    else
        for sub_path in "${selected_paths[@]}"; do
            update_one_submodule "$sub_path"
        done
    fi
fi

print_summary
exit $?
