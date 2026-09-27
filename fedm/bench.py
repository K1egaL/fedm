"""Async benchmarking core. Pure asyncio, no Qt here."""
from __future__ import annotations

import asyncio
import ipaddress
import secrets
import ssl
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable

import dns.asyncquery
import dns.message
import dns.rdatatype

try:
    from aioquic.asyncio.client import connect as quic_connect
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.events import StreamDataReceived
    HAVE_AIOQUIC = True
except ImportError:
    HAVE_AIOQUIC = False

try:
    from icmplib import async_ping
    HAVE_ICMPLIB = True
except ImportError:
    HAVE_ICMPLIB = False

from .providers import PROVIDERS

DNS_PROTOCOLS = ("plain", "dot", "doh", "doq")


# ----------------------------- data model ------------------------------


@dataclass
class Target:
    provider: str
    protocol: str
    address: str

    @property
    def key(self) -> str:
        return f"{self.provider}|{self.protocol}|{self.address}"

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.protocol}"


@dataclass
class Result:
    target: Target
    samples: list[float] = field(default_factory=list)
    losses: int = 0

    @property
    def total(self) -> int:
        return len(self.samples) + self.losses

    @property
    def loss_pct(self) -> float:
        return 0.0 if self.total == 0 else 100.0 * self.losses / self.total

    @property
    def median(self) -> float | None:
        return statistics.median(self.samples) if self.samples else None

    @property
    def p95(self) -> float | None:
        if not self.samples:
            return None
        s = sorted(self.samples)
        idx = max(0, round(0.95 * (len(s) - 1)))
        return s[idx]


# ---------------------------- parsing helpers --------------------------


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def parse_custom(raw: str) -> Target | None:
    s = raw.strip()
    if not s or s.startswith("#"):
        return None
    low = s.lower()
    if low.startswith("https://"):
        return Target("custom", "doh", s)
    if low.startswith("tls://"):
        host = s[6:].split(":", 1)[0]
        return Target("custom", "dot", host)
    if low.startswith("quic://"):
        host = s[7:].split(":", 1)[0]
        return Target("custom", "doq", host)
    if low.startswith("udp://"):
        return Target("custom", "plain", s[6:].split(":", 1)[0])
    if low.startswith("tcp://"):
        host_port = s[6:].split(":", 1)
        host = host_port[0]
        port = host_port[1] if len(host_port) > 1 else "443"
        return Target("custom", "tcp", f"{host}:{port}")
    return Target("custom", "plain", s)


def build_targets(
    selected_providers: list[str],
    custom_targets: list[str],
    enable_icmp: bool,
) -> list[Target]:
    targets: list[Target] = []
    for prov in selected_providers:
        data = PROVIDERS.get(prov, {})
        for proto, addrs in data.items():
            for a in addrs:
                targets.append(Target(prov, proto, a))
    for raw in custom_targets:
        t = parse_custom(raw)
        if t is not None:
            targets.append(t)
    if enable_icmp and HAVE_ICMPLIB:
        extra = [
            Target(t.provider, "icmp", t.address)
            for t in targets
            if t.protocol == "plain" and _is_ip(t.address)
        ]
        targets.extend(extra)
    return targets


# ----------------------------- probes ----------------------------------


async def _query_plain_udp(host: str, name: str, timeout: float) -> float:
    q = dns.message.make_query(name, dns.rdatatype.A)
    t0 = time.perf_counter()
    await asyncio.wait_for(
        dns.asyncquery.udp(q, host, timeout=timeout, raise_on_truncation=False),
        timeout=timeout + 0.5,
    )
    return (time.perf_counter() - t0) * 1000.0


async def _query_dot(host: str, name: str, timeout: float) -> float:
    q = dns.message.make_query(name, dns.rdatatype.A)
    t0 = time.perf_counter()
    await asyncio.wait_for(
        dns.asyncquery.tls(q, host, timeout=timeout),
        timeout=timeout + 0.5,
    )
    return (time.perf_counter() - t0) * 1000.0


async def _query_doh(url: str, name: str, timeout: float) -> float:
    q = dns.message.make_query(name, dns.rdatatype.A)
    t0 = time.perf_counter()
    await asyncio.wait_for(
        dns.asyncquery.https(q, url, timeout=timeout),
        timeout=timeout + 0.5,
    )
    return (time.perf_counter() - t0) * 1000.0


if HAVE_AIOQUIC:

    class _DoQProtocol(QuicConnectionProtocol):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._buf = bytearray()
            self._stream_id: int | None = None
            self._done = asyncio.Event()

        def quic_event_received(self, event) -> None:
            if isinstance(event, StreamDataReceived):
                if self._stream_id is None:
                    self._stream_id = event.stream_id
                if event.stream_id != self._stream_id:
                    return
                self._buf.extend(event.data)
                if event.end_stream:
                    self._done.set()

        async def ask(self, wire: bytes, timeout: float) -> bytes:
            sid = self._quic.get_next_available_stream_id()
            self._stream_id = sid
            payload = len(wire).to_bytes(2, "big") + wire
            self._quic.send_stream_data(sid, payload, end_stream=True)
            self.transmit()
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return bytes(self._buf[2:])


async def _query_doq(host: str, name: str, timeout: float) -> float:
    if not HAVE_AIOQUIC:
        raise RuntimeError("aioquic not installed")
    q = dns.message.make_query(name, dns.rdatatype.A)
    wire = q.to_wire()

    cfg = QuicConfiguration(is_client=True, alpn_protocols=["doq"])
    cfg.server_name = host
    cfg.verify_mode = ssl.CERT_NONE
    # Ограничиваем idle_timeout, чтобы close-handshake не висел вечно
    cfg.idle_timeout = timeout

    t0 = time.perf_counter()

    async def _do() -> None:
        # wait_connected=False — возвращаемся сразу, ждём соединение сами
        async with quic_connect(
            host, 853, configuration=cfg, create_protocol=_DoQProtocol,
            wait_connected=False,
        ) as proto:
            await proto.wait_connected(timeout=timeout)
            await proto.ask(wire, timeout=timeout)

    # Внешний жёсткий таймаут на всё
    await asyncio.wait_for(_do(), timeout=timeout * 2)
    return (time.perf_counter() - t0) * 1000.0


async def _probe_tcp(host_port: str, timeout: float) -> float:
    host, _, port_s = host_port.partition(":")
    port = int(port_s) if port_s else 443
    t0 = time.perf_counter()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return (time.perf_counter() - t0) * 1000.0


async def _probe_icmp(host: str, timeout: float) -> float:
    if not HAVE_ICMPLIB:
        raise RuntimeError("icmplib not installed")
    r = await async_ping(host, count=1, timeout=timeout)
    if not r.is_alive:
        raise TimeoutError("icmp timeout")
    return float(r.avg_rtt)


async def _probe_once(t: Target, name: str, timeout: float) -> float:
    p = t.protocol
    if p == "plain":
        return await _query_plain_udp(t.address, name, timeout)
    if p == "dot":
        return await _query_dot(t.address, name, timeout)
    if p == "doh":
        return await _query_doh(t.address, name, timeout)
    if p == "doq":
        return await _query_doq(t.address, name, timeout)
    if p == "tcp":
        return await _probe_tcp(t.address, timeout)
    if p == "icmp":
        return await _probe_icmp(t.address, timeout)
    raise ValueError(f"unknown protocol: {p}")


# ----------------------------- runner ----------------------------------


async def _gather_with_timeout(coros, timeout: float) -> list:
    """asyncio.gather с жёстким таймаутом.

    Returns a list where each item is either the task's result
    (on success) or an Exception instance (on failure/timeout).
    Never returns None.
    """
    tasks = [asyncio.create_task(c) for c in coros]
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    out: list = []
    for t in tasks:
        if t.cancelled():
            out.append(asyncio.TimeoutError())
            continue
        exc = t.exception()
        if exc is not None:
            out.append(exc)
        else:
            out.append(t.result())
    return out


async def run_bench(
    targets: list[Target],
    domains: list[str],
    iterations: int,
    timeout: float,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Result]:
    results: dict[str, Result] = {t.key: Result(t) for t in targets}

    dns_targets = [t for t in targets if t.protocol in DNS_PROTOCOLS]
    other_targets = [t for t in targets if t.protocol not in DNS_PROTOCOLS]

    total = len(dns_targets) * len(domains) * iterations + len(other_targets) * iterations
    done = 0
    progress_lock = asyncio.Lock()

    async def tick(n: int) -> None:
        nonlocal done
        async with progress_lock:
            done += n
            if on_progress is not None:
                on_progress(done, total)

    for _ in range(iterations):
        # --- DNS round: one random subdomain, same for every target ---
        for domain in domains:
            name = f"{secrets.token_hex(4)}.{domain}"
            # Жёсткий таймаут на весь раунд
            outcomes = await _gather_with_timeout(
                [_probe_once(t, name, timeout) for t in dns_targets],
                timeout=timeout * 2 + 2.0,
            )
            for t, o in zip(dns_targets, outcomes):
                r = results[t.key]
                if isinstance(o, Exception) or not isinstance(o, (int, float)):
                    r.losses += 1
                else:
                        r.samples.append(float(o))
            await tick(len(dns_targets))

        # --- ICMP / TCP round ---
        if other_targets:
            outcomes = await _gather_with_timeout(
                [_probe_once(t, "", timeout) for t in other_targets],
                timeout=timeout * 2 + 2.0,
            )
            for t, o in zip(other_targets, outcomes):
                r = results[t.key]
                if isinstance(o, Exception):
                    r.losses += 1
                else:
                    r.samples.append(o)
            await tick(len(other_targets))

    return list(results.values())