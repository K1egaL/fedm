"""Static provider lists + default domains. No logic here, just data."""

PROVIDERS: dict[str, dict[str, list[str]]] = {
    "cloudflare": {
        "plain": ["1.1.1.1", "1.0.0.1"],
        "dot":   ["1.1.1.1", "1.0.0.1"],
        "doh":   ["https://cloudflare-dns.com/dns-query",
                  "https://1.1.1.1/dns-query"],
        "doq":   ["cloudflare-dns.com"],
    },
    "google": {
        "plain": ["8.8.8.8", "8.8.4.4"],
        "dot":   ["8.8.8.8", "8.8.4.4"],
        "doh":   ["https://dns.google/dns-query"],
        "doq":   ["dns.google"],
    },
    "quad9": {
        "plain": ["9.9.9.9", "149.112.112.112"],
        "dot":   ["9.9.9.9", "149.112.112.112"],
        "doh":   ["https://dns.quad9.net/dns-query"],
        "doq":   ["dns.quad9.net"],
    },
    "adguard": {
        "plain": ["94.140.14.14", "94.140.15.15"],
        "dot":   ["94.140.14.14", "94.140.15.15"],
        "doh":   ["https://dns.adguard-dns.com/dns-query"],
        "doq":   ["dns.adguard-dns.com"],
    },
    "yandex": {
        "plain": ["77.88.8.8", "77.88.8.1"],
        "dot":   ["common.dot.dns.yandex.net"],
        "doh":   ["https://common.dot.dns.yandex.net/dns-query"],
        "doq":   [],
    },
    "mullvad": {
        "plain": ["194.242.2.2"],
        "dot":   ["dns.mullvad.net"],
        "doh":   ["https://dns.mullvad.net/dns-query"],
        "doq":   ["dns.mullvad.net"],
    },
    "dnssb": {
        "plain": ["185.222.222.222", "45.11.45.11"],
        "dot":   ["dns.sb"],
        "doh":   ["https://doh.sb/dns-query"],
        "doq":   ["dns.sb"],
    },
    "opendns": {
        "plain": ["208.67.222.222", "208.67.220.220"],
        "dot":   ["dns.opendns.com"],
        "doh":   ["https://doh.opendns.com/dns-query"],
        "doq":   [],
    },
    "libredns": {
        "plain": ["116.202.176.26"],
        "dot":   ["doh.libredns.gr"],
        "doh":   ["https://doh.libredns.gr/dns-query"],
        "doq":   [],
    },
}

DEFAULT_DOMAINS = [
    "github.com",
    "wikipedia.org",
    "cloudflare.com",
    "yandex.ru",
    "google.com",
    "dzen.ru",
    "archlinux.org",
    "microsoft.com",
    "apple.com",
]

PROTOCOLS = ("plain", "dot", "doh", "doq")
PROTOCOL_LABEL = {
    "plain": "Plain UDP:53",
    "dot":   "DoT TLS:853",
    "doh":   "DoH HTTPS",
    "doq":   "DoQ QUIC",
    "icmp":  "ICMP ping",
    "tcp":   "TCP connect",
}