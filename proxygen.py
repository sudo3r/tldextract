import asyncio
import aiohttp
import re
import time
from colorama import Fore, Style

class ProxyCollector:
    DEFAULT_SOURCES = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=https&timeout=10000&country=all&ssl=all&anonymity=all",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
        "https://raw.githubusercontent.com/a2u/free-proxy-list/master/free-proxy-list.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/all.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/all.txt",
        "https://raw.githubusercontent.com/officialputuid/KangProxy/main/xResults/RAW.txt",
        "https://raw.githubusercontent.com/dpangestuw/Free-Proxy/main/All_proxies.txt",
        "https://raw.githubusercontent.com/mmpx12/proxy-list/refs/heads/master/proxies.txt",
        "https://raw.githubusercontent.com/mzyui/proxy-list/refs/heads/main/all.txt",
        "https://raw.githubusercontent.com/sunny9577/proxy-scraper/refs/heads/master/proxies.txt",
        "https://raw.githubusercontent.com/r00tee/Proxy-List/refs/heads/main/Https.txt",
        "https://raw.githubusercontent.com/r00tee/Proxy-List/refs/heads/main/Socks4.txt",
        "https://raw.githubusercontent.com/r00tee/Proxy-List/refs/heads/main/Socks5.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/refs/heads/main/proxies/http.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/refs/heads/main/proxies/https.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/refs/heads/main/proxies/socks4.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/refs/heads/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/themiralay/Proxy-List-World/master/data.txt",
        "https://raw.githubusercontent.com/casals-ar/proxy-list/main/http",
        "https://raw.githubusercontent.com/casals-ar/proxy-list/main/socks4",
        "https://raw.githubusercontent.com/casals-ar/proxy-list/main/socks5",
    ]

    def __init__(self, sources=None, timeout=3, concurrency=30, progress=False):
        self.sources = sources or self.DEFAULT_SOURCES
        self.timeout = timeout
        self.concurrency = concurrency
        self.progress = progress
        self._checked = 0
        self._total = 0
        self._working = 0
        self._last_update_time = 0

    def _print_progress(self):
        if not self.progress:
            return
        current_time = time.time()
        if current_time - self._last_update_time < 1 and self._checked < self._total:
            return
        self._last_update_time = current_time
        remaining = self._total - self._checked
        percent = (self._checked / self._total) * 100 if self._total > 0 else 0
        print(f"\r{Fore.LIGHTBLUE_EX}[*]{Style.RESET_ALL} Progress: {self._checked}/{self._total} ({percent:.1f}%) | Working: {self._working} | Remaining: {remaining}", end="", flush=True)

    async def _fetch_proxies_from_url(self, session, url):
        try:
            async with session.get(url, timeout=self.timeout) as response:
                if response.status != 200:
                    return []
                content = await response.text()
                proxy_pattern = r'(?:(http|socks4|socks5):\/\/)?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5})'
                proxies = []
                for proto, ip_port in re.findall(proxy_pattern, content):
                    if proto:
                        proxies.append(f"{proto}://{ip_port}")
                    else:
                        proxies.extend([f"{scheme}://{ip_port}" for scheme in ["http", "socks4", "socks5"]])
                return proxies
        except Exception:
            return []

    async def _check_proxy(self, session, proxy):
        try:
            async with session.get("https://httpbin.org/ip", proxy=proxy, timeout=self.timeout) as response:
                if response.status == 200:
                    self._working += 1
                    return True
                return False
        except Exception:
            return False
        finally:
            self._checked += 1
            self._print_progress()

    async def _collect_proxies(self):
        async with aiohttp.ClientSession() as session:
            tasks = []
            semaphore = asyncio.Semaphore(self.concurrency)
            async def bounded_fetch(url):
                async with semaphore:
                    return await self._fetch_proxies_from_url(session, url)
            for source in self.sources:
                tasks.append(bounded_fetch(source))
            proxy_lists = await asyncio.gather(*tasks, return_exceptions=True)
            proxies = set()
            for proxy_list in proxy_lists:
                if isinstance(proxy_list, list):
                    proxies.update(proxy_list)
            return list(proxies)

    async def _check_proxies(self, proxies):
        self._total = len(proxies)
        self._checked = 0
        self._working = 0
        async with aiohttp.ClientSession() as session:
            tasks = []
            semaphore = asyncio.Semaphore(self.concurrency)
            async def bounded_check(proxy):
                async with semaphore:
                    is_working = await self._check_proxy(session, proxy)
                    return proxy if is_working else None
            for proxy in proxies:
                tasks.append(bounded_check(proxy))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [proxy for proxy in results if proxy]

    async def get_working_proxies(self):
        proxies = await self._collect_proxies()
        if not proxies:
            return []
        return await self._check_proxies(proxies)

    async def iter_working_proxies(self):
        proxies = await self._collect_proxies()
        if not proxies:
            return
        self._total = len(proxies)
        self._checked = 0
        self._working = 0
        async with aiohttp.ClientSession() as session:
            semaphore = asyncio.Semaphore(self.concurrency)
            async def bounded_check(proxy):
                async with semaphore:
                    is_working = await self._check_proxy(session, proxy)
                    if is_working:
                        yield proxy
            tasks = []
            for proxy in proxies:
                tasks.append(self._check_and_yield(session, proxy, semaphore))
            # gather returns a list of async generators, so we need to iterate over them
            for coro in asyncio.as_completed(tasks):
                async for working_proxy in await coro:
                    yield working_proxy

    async def _check_and_yield(self, session, proxy, semaphore):
        async with semaphore:
            is_working = await self._check_proxy(session, proxy)
            if is_working:
                yield proxy

# Example usage:
# async def main():
#     collector = ProxyCollector(progress=True)
#     proxies = await collector.get_working_proxies()
#     print("\nWorking proxies:", proxies)
#
# if __name__ == "__main__":
#     asyncio.run(main())