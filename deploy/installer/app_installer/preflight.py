"""Host address parsing shared by the installer and the DR tools."""


def ipv4_addresses(ip_output):
    """Addresses from `ip -4 -o address show`, without prefix lengths."""
    addresses = []
    for line in ip_output.splitlines():
        fields = line.split()
        if 'inet' in fields[:-1]:
            addresses.append(fields[fields.index('inet') + 1].split('/')[0])
    return addresses
