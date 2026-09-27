# fedm

[![CI](https://github.com/K1egaL/fedm/actions/workflows/ci.yml/badge.svg)](https://github.com/K1egaL/fedm/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational.svg)](#)

**fedm** — быстрый бенчмарк DNS-резолверов для Linux.
Измеряет **реальную задержку рекурсии** для обычного DNS, DoT, DoH и DoQ,
а не «пинг до сервера».

<p align="center">
  <img src="screenshot.png" alt="fedm в действии" width="900">
</p>

## Зачем

Публичные «DNS-тесты» в браузере врут: они меряют кэш CDN или пинг до
ближайшего anycast-узла. Реальная задержка резолва зависит от:
- вашего провайдера;
- наличия VPN;
- страны, где вы находитесь;
- того, как маршрутизируется именно ваш трафик до резолвера.

**fedm** спрашивает у каждого резолвера уникальный случайный субдомен —
так резолвер не может ответить из кэша и обязан идти в реальную рекурсию.
Это честный замер.

## Возможности

- Протоколы: **Plain UDP:53**, **DoT (TLS:853)**, **DoH (HTTPS)**,
  **DoQ (QUIC)**, **ICMP ping**, **TCP connect**.
- 9 готовых провайдеров: Cloudflare, Google, Quad9, AdGuard, Yandex,
  Mullvad, DNS.SB, OpenDNS, LibreDNS.
- Свои серверы: `1.1.1.1`, `tls://host`, `https://host/dns-query`,
  `quic://host`, `tcp://host:port`.
- Свои тестовые домены (по умолчанию 9 популярных).
- Цветовая индикация скорости: от зелёного (< 30 мс) до красного (> 300 мс).
- Победители в каждом протоколе подсвечиваются жирным.
- ПКМ по строке → копировать IP / DoH / DoQ / адрес.
- Экспорт в **JSON** и **CSV**, копирование таблицы как **Markdown**.

## Установка

Требуется Python **3.14+**.

### Arch / CachyOS

```bash
sudo pacman -S --needed python python-pyside6 python-dnspython \
    python-httpx python-aioquic python-icmplib

git clone https://github.com/K1egaL/fedm.git
cd fedm
python -m fedm