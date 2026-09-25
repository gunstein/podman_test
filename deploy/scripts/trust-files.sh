#!/bin/sh
# Exact-file fapolicyd trust for the project's own files. This step runs
# through the RPM-trusted shell, not project Python: the files it trusts
# are that Python. Run as root.
#   sh trust-files.sh install DEST MODE < base64-content
#   sh trust-files.sh trust TRUST_FILE PATH...
set -eu

usage() {
    echo "usage: trust-files.sh install DEST MODE | trust TRUST_FILE PATH..." >&2
    exit 2
}

# Atomic root-owned install; prints changed or unchanged.
install_file() {
    target=$1
    mode=$2
    temporary=$(mktemp "$target.XXXXXX")
    trap 'rm -f "$temporary"' EXIT HUP INT TERM
    base64 --decode > "$temporary"
    chown root:root "$temporary"
    chmod "$mode" "$temporary"
    if [ -f "$target" ] && cmp -s "$temporary" "$target" &&
        [ "$(stat -c %a "$target")" = "${mode#0}" ] &&
        [ "$(stat -c %U:%G "$target")" = root:root ]; then
        echo unchanged
        return
    fi
    mv -f "$temporary" "$target"
    trap - EXIT HUP INT TERM
    echo changed
}

# The exact line `fapolicyd-cli --dump-db` prints once PATH is trusted.
entry() {
    path=$(readlink -e -- "$1") && [ -f "$path" ] || {
        echo "not a regular file: $1" >&2
        return 1
    }
    size=$(stat -c %s -- "$path")
    digest=$(sha256sum -- "$path")
    printf 'filedb %s %s %s\n' "$path" "$size" "${digest%% *}"
}

# Update or add exact trust, reload, then wait until the daemon serves every
# exact entry. `--update` only notifies the daemon; it does not wait.
trust_files() {
    trust_file=$1
    shift
    systemctl is-active --quiet fapolicyd || {
        echo "fapolicyd is not active" >&2
        exit 1
    }
    expected=
    for path do
        line=$(entry "$path") || exit 1
        expected="$expected$line
"
    done
    result=unchanged
    for path do
        path=$(readlink -e -- "$path")
        if ! fapolicyd-cli --file update "$path" --trust-file "$trust_file" > /dev/null 2>&1; then
            fapolicyd-cli --file add "$path" --trust-file "$trust_file" > /dev/null
            result=changed
        fi
    done
    fapolicyd-cli --update > /dev/null
    database=$(mktemp)
    trap 'rm -f "$database"' EXIT HUP INT TERM
    attempt=1
    while :; do
        fapolicyd-cli --dump-db > "$database" 2> /dev/null || : > "$database"
        # Whole-line fixed match: a stale hash or a path prefix is not trust.
        if ! printf '%s' "$expected" | grep -Fxvq -f "$database"; then
            echo "$result"
            return
        fi
        [ "$attempt" -lt "${TRUST_ATTEMPTS:-30}" ] || {
            echo "fapolicyd did not load the exact trust entries" >&2
            exit 1
        }
        attempt=$((attempt + 1))
        sleep "${TRUST_DELAY:-1}"
    done
}

case "${1:-}" in
    install) [ "$#" -eq 3 ] || usage; install_file "$2" "$3" ;;
    trust) [ "$#" -ge 3 ] || usage; shift; trust_files "$@" ;;
    *) usage ;;
esac
