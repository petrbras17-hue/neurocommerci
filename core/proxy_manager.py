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


def parse_proxy_line(line: str, default_type: str = "socks5") -> Optional[ProxyConfig]:
    """Разобрать строку прокси в разных форматах."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Формат: host:port:user:pass
    m = PROXY_COLON_PATTERN.match(line)
    if m:
        return ProxyConfig(
            proxy_type=default_type,
            host=m.group("host"),
            port=int(m.group("port")),
            username=m.group("user"),
            password=m.group("pass"),
        )

    # Формат: type://user:pass@host:port или host:port
    m = PROXY_URL_PATTERN.match(line)
    if m:
        return ProxyConfig(
            proxy_type=m.group("type") or default_type,
            host=m.group("host"),
            port=int(m.group("port")),
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
        with open(path, "r") as f:
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
        """Проверить доступность прокси."""
        try:
            connector = aiohttp.TCPConnector()
            proxy_url = proxy.url if proxy.proxy_type == "http" else None

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://api.ipify.org",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        ip = await resp.text()
                        log.debug(f"Прокси {proxy.host}:{proxy.port} OK, IP: {ip}")
                        return True
        except Exception as e:
            log.debug(f"Прокси {proxy.host}:{proxy.port} недоступен: {e}")
        return False

    async def validate_all(self) -> dict[str, bool]:
        """Проверить все прокси. Возвращает {url: is_valid}."""
        results = {}
        for proxy in self.proxies:
            is_valid = await self.validate_proxy(proxy)
            results[proxy.url] = is_valid
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

        # Статический: round-robin
        used_proxies = set(id(p) for p in self._assignments.values())
        for proxy in self.proxies:
            if id(proxy) not in used_proxies:
                self._assignments[phone] = proxy
                log.info(f"Прокси {proxy.host}:{proxy.port} назначен аккаунту {phone}")
                return proxy

        # Если все заняты — переиспользовать
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
