#!/bin/sh
# Entry point of the nginx container. It writes Podman's DNS server as nginx's
# resolver, makes sure the persistent TLS volume holds a certificate for every
# public hostname, then runs nginx. The volume's tls-mode file says how:
#   local (no file): it creates the demo CA (10 years) and one leaf certificate
#     (397 days), and renews either when it is missing, expires within 30 days
#     or no longer fits.
#   provided: app_installer tls-install put a certificate from the
#     organisation's CA here. It is only checked, never issued or replaced,
#     and a missing or wrong file stops nginx: never a silent demo CA.
set -efu

tls_directory=${TODO_TLS_DIRECTORY:-/var/lib/todo-tls}
tls_hostname=${TODO_TLS_HOSTNAME:-localhost}

case "$tls_hostname" in
    ""|*[!A-Za-z0-9.-]*)
        echo "ERROR: invalid TODO_TLS_HOSTNAME: $tls_hostname" >&2
        exit 1
        ;;
esac

# One leaf certificate covers every registered public hostname.
tls_hostnames=${APP_TLS_HOSTNAMES:-$tls_hostname}
tls_san="DNS:$tls_hostname"
for name in $tls_hostnames; do
    case "$name" in
        ""|*[!A-Za-z0-9.-]*)
            echo "ERROR: invalid APP_TLS_HOSTNAMES entry: $name" >&2
            exit 1
            ;;
    esac
    if [ "$name" != "$tls_hostname" ]; then
        tls_san="$tls_san,DNS:$name"
    fi
done

# Use this pod's DNS, so one absent app cannot prevent other apps from starting.
awk '$1 == "nameserver" {
    address = index($2, ":") ? "[" $2 "]" : $2
    servers = servers " " address
} END {
    if (servers == "") exit 1
    print "resolver" servers " valid=5s;"
    print "resolver_timeout 2s;"
}' /etc/resolv.conf > /tmp/podman-resolver.conf

umask 077
mkdir -p "$tls_directory"

tls_mode=local
if [ -s "$tls_directory/tls-mode" ]; then
    tls_mode=$(cat "$tls_directory/tls-mode")
fi
case "$tls_mode" in
    local) ;;
    provided)
        for file in server.crt server.key ca.crt; do
            if [ ! -s "$tls_directory/$file" ]; then
                echo "ERROR: provided TLS mode, but $file is missing; run app_installer tls-install" >&2
                exit 1
            fi
        done
        if [ "$(openssl x509 -in "$tls_directory/server.crt" -noout -pubkey)" != \
            "$(openssl pkey -in "$tls_directory/server.key" -pubout)" ]; then
            echo "ERROR: provided TLS mode, but server.key does not belong to server.crt" >&2
            exit 1
        fi
        for name in $tls_hostname $tls_hostnames; do
            if ! openssl verify -no_check_time -CAfile "$tls_directory/ca.crt" \
                -untrusted "$tls_directory/server.crt" -verify_hostname "$name" \
                "$tls_directory/server.crt" >/dev/null 2>&1; then
                echo "ERROR: provided TLS mode, but server.crt is not valid for $name from ca.crt" >&2
                exit 1
            fi
        done
        # An expired certificate still starts nginx: browsers then name the cause.
        if ! openssl x509 -in "$tls_directory/server.crt" -noout -checkend 0 >/dev/null 2>&1; then
            echo "WARNING: the provided TLS certificate has expired; install the next one" >&2
        fi
        exec "$@"
        ;;
    *)
        echo "ERROR: unknown TLS mode in $tls_directory/tls-mode: $tls_mode" >&2
        exit 1
        ;;
esac

if [ ! -s "$tls_directory/ca.crt" ] || [ ! -s "$tls_directory/ca.key" ] || \
    ! openssl x509 -in "$tls_directory/ca.crt" -noout -checkend 2592000 >/dev/null 2>&1; then
    rm -f \
        "$tls_directory/ca.crt" \
        "$tls_directory/ca.key" \
        "$tls_directory/ca.srl" \
        "$tls_directory/server.crt" \
        "$tls_directory/server.key"

    openssl req \
        -quiet \
        -x509 \
        -newkey rsa:3072 \
        -nodes \
        -days 3650 \
        -sha256 \
        -subj "/CN=Todo Demo Local Root CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout "$tls_directory/ca.key" \
        -out "$tls_directory/ca.crt"
fi

renew_server_certificate=true
if [ -s "$tls_directory/server.crt" ] && \
    [ -s "$tls_directory/server.key" ] && \
    openssl verify -CAfile "$tls_directory/ca.crt" -verify_hostname "$tls_hostname" "$tls_directory/server.crt" >/dev/null 2>&1 && \
    openssl x509 -in "$tls_directory/server.crt" -noout -checkend 2592000 >/dev/null 2>&1 && \
    openssl verify -CAfile "$tls_directory/ca.crt" "$tls_directory/server.crt" >/dev/null 2>&1; then
    certificate_modulus=$(openssl x509 -in "$tls_directory/server.crt" -noout -modulus)
    key_modulus=$(openssl rsa -in "$tls_directory/server.key" -noout -modulus 2>/dev/null)
    if [ "$certificate_modulus" = "$key_modulus" ]; then
        renew_server_certificate=false
    fi
fi

for name in $tls_hostnames; do
    if ! openssl verify -CAfile "$tls_directory/ca.crt" -verify_hostname "$name" \
        "$tls_directory/server.crt" >/dev/null 2>&1; then
        renew_server_certificate=true
    fi
done

if [ "$renew_server_certificate" = true ]; then
    temporary_directory=$(mktemp -d "$tls_directory/.issue.XXXXXX")
    trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM

    openssl req \
        -quiet \
        -newkey rsa:3072 \
        -nodes \
        -sha256 \
        -subj "/CN=$tls_hostname" \
        -keyout "$temporary_directory/server.key" \
        -out "$temporary_directory/server.csr"

    {
        printf '%s\n' "subjectAltName=$tls_san"
        printf '%s\n' "basicConstraints=critical,CA:FALSE"
        printf '%s\n' "keyUsage=critical,digitalSignature,keyEncipherment"
        printf '%s\n' "extendedKeyUsage=serverAuth"
    } > "$temporary_directory/server.ext"

    openssl x509 \
        -req \
        -in "$temporary_directory/server.csr" \
        -CA "$tls_directory/ca.crt" \
        -CAkey "$tls_directory/ca.key" \
        -CAcreateserial \
        -days 397 \
        -sha256 \
        -extfile "$temporary_directory/server.ext" \
        -out "$temporary_directory/server.crt"

    mv "$temporary_directory/server.key" "$tls_directory/server.key"
    mv "$temporary_directory/server.crt" "$tls_directory/server.crt"
    rm -rf "$temporary_directory"
    trap - EXIT HUP INT TERM
fi

chmod 0600 "$tls_directory/ca.key" "$tls_directory/server.key"
chmod 0644 "$tls_directory/ca.crt" "$tls_directory/server.crt"

exec "$@"
