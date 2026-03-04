"""
NEURO COMMENTING — CLI интерфейс (Rich).
Альтернативный интерфейс управления через терминал.
Запуск: python main.py --cli
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich import box

from config import settings
from core.proxy_manager import ProxyManager
from core.session_manager import SessionManager
from core.rate_limiter import RateLimiter
from core.account_manager import AccountManager
from utils.logger import log

console = Console()

BANNER = """
[bold cyan]
 ███╗   ██╗███████╗██╗   ██╗██████╗  ██████╗
 ████╗  ██║██╔════╝██║   ██║██╔══██╗██╔═══██╗
 ██╔██╗ ██║█████╗  ██║   ██║██████╔╝██║   ██║
 ██║╚██╗██║██╔══╝  ██║   ██║██╔══██╗██║   ██║
 ██║ ╚████║███████╗╚██████╔╝██║  ██║╚██████╔╝
 ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝
      [bold yellow]C O M M E N T I N G[/bold yellow]
[/bold cyan]
[dim]Telegram Auto-Commenting System[/dim]
"""


def show_main_menu():
    table = Table(title="[bold]Главное меню[/bold]", box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column("Номер", style="bold cyan", width=4)
    table.add_column("Действие", style="white")
    for num, text in [
        ("1", "Аккаунты"), ("2", "Прокси"), ("3", "Каналы"),
        ("4", "Мониторинг"), ("5", "Комментарии"), ("6", "Дашборд"),
        ("7", "Настройки"), ("0", "Выход"),
    ]:
        table.add_row(num, text)
    console.print(table)
    return Prompt.ask("[bold cyan]Выбор[/bold cyan]", choices=["0","1","2","3","4","5","6","7"])


async def main():
    console.print(BANNER)

    proxy_mgr = ProxyManager()
    session_mgr = SessionManager()
    rate_limiter = RateLimiter()
    account_mgr = AccountManager(session_mgr, proxy_mgr, rate_limiter)

    if settings.proxy_list_path.exists():
        proxy_mgr.load_from_file()

    try:
        while True:
            choice = show_main_menu()
            if choice == "0":
                console.print("[bold yellow]Выход...[/bold yellow]")
                await account_mgr.disconnect_all()
                break
            elif choice == "6":
                summary = await account_mgr.get_status_summary()
                console.print(Panel(
                    f"Аккаунтов: {summary['total']} | Комментариев сегодня: {summary['total_comments_today']}",
                    title="Дашборд", box=box.DOUBLE,
                ))
            elif choice == "7":
                console.print(f"AI: {settings.GEMINI_MODEL}")
                console.print(f"Product: {settings.PRODUCT_NAME} ({settings.PRODUCT_BOT_LINK})")
                console.print(f"Лимит: {settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}/день")
                Prompt.ask("Enter")
            else:
                console.print("[yellow]Используйте Telegram-бота для полного функционала[/yellow]")
    except KeyboardInterrupt:
        await account_mgr.disconnect_all()
