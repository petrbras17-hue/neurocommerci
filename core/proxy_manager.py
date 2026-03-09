"""
Менеджер прокси-серверов.
Загрузка, валидация, назначение прокси аккаунтам.
Поддержка статических и ротируемых (sticky session) прокси.
"""

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Optional

import aiohttp
from python_socks import ProxyType

from config import settings
from utils.logger import log


@dataclass
class ProxyConfig:
    """Конфигурация прокси для Telethon."""
    proxy_type: str  # socks5, http
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

    def to_telethon_proxy(self) -> tuple:
        """Формат для Telethon: (type, host, port, True, user, pass)."""
        ptype = {
            "socks5": 2,  # python_socks.ProxyType.SOCKS5
            "socks4": 1,
            "http": 3,
        }.get(self.proxy_type.lower(), 2)

        return (ptype, self.host, self.port, True, self.username, self.password)

    @property
    def url(self) -> str:
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.proxy_type}://{auth}{self.host}:{self.port}"

    def with_sticky_session(self, session_id: str) -> "ProxyConfig":
        """Создать копию прокси со sticky session username.

        Формат username подставляется из settings.PROXY_STICKY_FORMAT.
        Например: 'user123-session-abc' → каждый session_id получает свой IP.
        """
        if not self.username:
            log.warning("Sticky session невозможен без username в прокси")
            return self

        fmt = settings.PROXY_STICKY_FORMAT
        sticky_user = fmt.format(user=self.username, session_id=session_id)
        return replace(self, username=sticky_user)


# Паттерн: type://user:pass@host:port или host:port:user:pass
PROXY_URL_PATTERN = re.compile(
    r"^(?:(?P<type>\w+)://)?(?:(?P<user>[^:@]+):(?P<pass>[^@]+)@)?(?P<host>[^:]+):(?P<port>\d+)$"
)
PROXY_COLON_PATTERN = re.compile(
    r"^(?P<host>[^:]+):(?P<port>\d+):(?P<user>[^:]+):(?P<pass>.+)$"
)


def _validate_port(port_str: str) -> Optional[int]:
    """Валидация порта: 1-65535."""
    try:
        port = int(port_str)
        if 1 <= port <= 65535:
            return port
    except (ValueError, TypeError):
        pass
    return None


def parse_proxy_line(line: str, default_type: str = "socks5") -> Optional[ProxyConfig]:
    """Разобрать строку прокси в разных форматах."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Формат: host:port:user:pass
    m = PROXY_COLON_PATTERN.match(line)
    if m:
        port = _validate_port(m.group("port"))
        if port is None:
            log.warning(f"Невалидный порт в прокси: {line}")
            return None
        return ProxyConfig(
            proxy_type=default_type,
            host=m.group("host"),
            port=port,
            username=m.group("user"),
            password=m.group("pass"),
        )

    # Формат: type://user:pass@host:port или host:port
    m = PROXY_URL_PATTERN.match(line)
    if m:
        port = _validate_port(m.group("port"))
        if port is None:
            log.warning(f"Невалидный порт в прокси: {line}")
            return None
        return ProxyConfig(
            proxy_type=m.group("type") or default_type,
            host=m.group("host"),
            port=port,
            username=m.group("user"),
            password=m.group("pass"),
        )

    log.warning(f"Не удалось разобрать прокси: {line}")
    return None


class ProxyManager:
    """Управление пулом прокси.

    Два режима:
    - Статический (PROXY_ROTATING=False): N прокси → N аккаунтов, round-robin.
    - Ротируемый (PROXY_ROTATING=True): 1 прокси-эндпоинт → N аккаунтов,
      каждому присваивается sticky session ID для стабильного IP.
    """

    def __init__(self):
        self.proxies: list[ProxyConfig] = []
        self._assignments: dict[str, ProxyConfig] = {}  # account_phone -> proxy

    @property
    def is_rotating(self) -> bool:
        return settings.PROXY_ROTATING

    def load_from_file(self) -> int:
        """Загрузить прокси из файла. Возвращает количество загруженных."""
        path = settings.proxy_list_path
        if not path.exists():
            log.warning(f"Файл прокси не найден: {path}")
            return 0

        self.proxies.clear()
        self._assignments.clear()  # Старые assignments невалидны после reload
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                proxy = parse_proxy_line(line, settings.PROXY_TYPE)
                if proxy:
                    self.proxies.append(proxy)

        mode = "ротируемый" if self.is_rotating else "статический"
        log.info(f"Загружено прокси: {len(self.proxies)} (режим: {mode})")
        return len(self.proxies)

    def _make_session_id(self, phone: str) -> str:
        """Генерировать детерминированный session ID из номера телефона.

        Один и тот же номер всегда получает один session ID →
        один и тот же IP при переподключении.
        """
        digest = hashlib.md5(phone.encode()).hexdigest()[:8]
        return digest

    async def validate_proxy(self, proxy: ProxyConfig, timeout: int = 10) -> bool:
        """Проверить доступность прокси (HTTP и SOCKS5)."""
        if proxy.proxy_type.lower() in ("socks5", "socks4"):
            return await self._validate_socks(proxy, timeout)
        return await self._validate_http(proxy, timeout)

    async def _validate_http(self, proxy: ProxyConfig, timeout: int = 10) -> bool:
        """Проверить HTTP прокси через aiohttp."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.ipify.org",
                    proxy=proxy.url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        ip = await resp.text()
                        log.debug(f"HTTP прокси {proxy.host}:{proxy.port} OK, IP: {ip}")
                        return True
        except Exception as e:
            log.debug(f"HTTP прокси {proxy.host}:{proxy.port} недоступен: {e}")
        return False

    async def _validate_socks(self, proxy: ProxyConfig, timeout: int = 10) -> bool:
        """Проверить SOCKS5 прокси через python_socks."""
        import asyncio
        from python_socks.async_.asyncio import Proxy

        sock = None
        try:
            ptype = ProxyType.SOCKS5 if proxy.proxy_type.lower() == "socks5" else ProxyType.SOCKS4
            p = Proxy(ptype, proxy.host, proxy.port, proxy.username, proxy.password)
            sock = await asyncio.wait_for(
                p.connect(dest_host="api.ipify.org", dest_port=80),
                timeout=timeout,
            )
            loop = asyncio.get_running_loop()
            # Отправить HTTP запрос через SOCKS туннель (в executor чтобы не блокировать)
            await loop.run_in_executor(
                None, sock.sendall,
                b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n",
            )
            data = await asyncio.wait_for(
                loop.run_in_executor(None, sock.recv, 1024),
                timeout=timeout,
            )
            if b"200" in data:
                body = data.split(b"\r\n\r\n", 1)[-1].decode(errors="ignore").strip()
                log.debug(f"SOCKS5 прокси {proxy.host}:{proxy.port} OK, IP: {body}")
                return True
        except asyncio.TimeoutError:
            log.debug(f"SOCKS5 прокси {proxy.host}:{proxy.port}: таймаут")
        except Exception as e:
            log.debug(f"SOCKS5 прокси {proxy.host}:{proxy.port} недоступен: {e}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        return False

    async def validate_all(self) -> dict[str, bool]:
        """Проверить все прокси параллельно. Возвращает {url: is_valid}."""
        import asyncio
        tasks = [self.validate_proxy(proxy) for proxy in self.proxies]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        results = {}
        for proxy, outcome in zip(self.proxies, outcomes):
            results[proxy.url] = outcome is True
        return results

    def assign_to_account(self, phone: str) -> Optional[ProxyConfig]:
        """Назначить прокси аккаунту.

        Статический режим: round-robin из пула.
        Ротируемый режим: один эндпоинт + sticky session per account.
        """
        if not self.proxies:
            log.warning("Нет доступных прокси для назначения")
            return None

        # Если уже назначен — вернуть тот же
        if phone in self._assignments:
            return self._assignments[phone]

        if self.is_rotating:
            # Ротируемый: берём первый (единственный) прокси,
            # создаём sticky session для каждого аккаунта
            base_proxy = self.proxies[0]
            session_id = self._make_session_id(phone)
            proxy = base_proxy.with_sticky_session(session_id)
            self._assignments[phone] = proxy
            log.info(
                f"Прокси {proxy.host}:{proxy.port} (sticky: {session_id}) "
                f"назначен аккаунту {phone}"
            )
            return proxy

        # Статический: round-robin (сравниваем по host:port:user вместо id())
        used_keys = {(p.host, p.port, p.username) for p in self._assignments.values()}
        for proxy in self.proxies:
            if (proxy.host, proxy.port, proxy.username) not in used_keys:
                self._assignments[phone] = proxy
                log.info(f"Прокси {proxy.host}:{proxy.port} назначен аккаунту {phone}")
                return proxy

        # Если все заняты — переиспользовать
        if settings.STRICT_PROXY_PER_ACCOUNT:
            log.warning(
                f"STRICT_PROXY_PER_ACCOUNT=true: уникального прокси для {phone} нет, "
                "подключение заблокировано."
            )
            return None

        proxy = self.proxies[len(self._assignments) % len(self.proxies)]
        self._assignments[phone] = proxy
        log.info(f"Прокси {proxy.host}:{proxy.port} переназначен аккаунту {phone}")
        return proxy

    def get_for_account(self, phone: str) -> Optional[ProxyConfig]:
        """Получить прокси, назначенный аккаунту."""
        return self._assignments.get(phone)

    def get_status_info(self) -> dict:
        """Информация о состоянии прокси для дашборда."""
        return {
            "total": len(self.proxies),
            "assigned": len(self._assignments),
            "mode": "rotating" if self.is_rotating else "static",
        }
