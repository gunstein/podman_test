#!/bin/sh
set -efu

tls_directory=/var/lib/todo-tls
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
