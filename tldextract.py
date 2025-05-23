import argparse
import asyncio
import aiohttp
import json
import os
import random
import sys
from typing import List, Set, Optional, Tuple, Dict, Any
from urllib.parse import urlparse
from colorama import Fore, Style, init
from proxygen import ProxyCollector

init()

def log(message: str, level: str = "i") -> None:
    levels = {
        "i": f"{Fore.LIGHTBLUE_EX}[*]{Style.RESET_ALL}",  # info
        "s": f"{Fore.LIGHTGREEN_EX}[+]{Style.RESET_ALL}",  # success
        "w": f"{Fore.LIGHTYELLOW_EX}[!]{Style.RESET_ALL}",  # warning
        "e": f"{Fore.LIGHTRED_EX}[-]{Style.RESET_ALL}",  # error
    }
    print(f"{levels.get(level, levels['i'])} {message}")

async def get_fresh_proxies(proxy_manager, min_count=20):
    log("Collecting fresh proxies ...", "i")
    proxies = proxy_manager.get("proxies", [])
    proxies = [p for p in proxies if p not in proxy_manager.get("blacklisted_proxies", set())]
    needed = max(0, min_count - len(proxies))
    if needed > 0:
        collector = ProxyCollector(timeout=5, concurrency=100, progress=True)
        async for proxy in collector.iter_working_proxies():
            if proxy not in proxies:
                proxies.append(proxy)
            if len(proxies) >= min_count:
                break
    proxy_manager["proxies"] = proxies
    proxy_manager["blacklisted_proxies"] = set()
    proxy_manager["current_proxy"] = None
    log(f"Loaded {len(proxies)} fresh proxies", "s")
    return proxies

def create_proxy_manager(proxies: List[str]) -> Dict[str, Any]:
    return {
        "proxies": proxies,
        "current_proxy": None,
        "blacklisted_proxies": set()
    }

def proxy_rotate(proxy_manager: Dict[str, Any]) -> Optional[str]:
    proxies = proxy_manager["proxies"]
    blacklisted = proxy_manager["blacklisted_proxies"]
    current_proxy = proxy_manager["current_proxy"]

    if not proxies or len(proxies) == len(blacklisted):
        return None

    available_proxies = [p for p in proxies if p not in blacklisted]

    if not available_proxies:
        return None

    if current_proxy in blacklisted or current_proxy not in available_proxies:
        current_proxy = None

    if current_proxy is None:
        current_proxy = random.choice(available_proxies)
    else:
        try:
            current_index = available_proxies.index(current_proxy)
            next_index = (current_index + 1) % len(available_proxies)
            current_proxy = available_proxies[next_index]
        except ValueError:
            current_proxy = random.choice(available_proxies)

    proxy_manager["current_proxy"] = current_proxy
    log(f"Using proxy: {current_proxy}", "i")
    return current_proxy

def proxy_blacklist(proxy_manager: Dict[str, Any], proxy: str) -> None:
    if proxy and proxy in proxy_manager["proxies"]:
        proxy_manager["blacklisted_proxies"].add(proxy)
        log(f"Blacklisted proxy: {proxy}", "w")

async def fetch_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    proxy_manager: Dict[str, Any],
    timeout: int,
    max_retries: int = 5,
    backoff_factor: float = 1.0
) -> Tuple[Optional[str], bool]:
    last_error = None
    attempt = 0
    while True:
        if len(proxy_manager["proxies"]) == 0 or len(proxy_manager["blacklisted_proxies"]) >= len(proxy_manager["proxies"]):
            await get_fresh_proxies(proxy_manager)
            if not proxy_manager["proxies"]:
                log("No proxies available, aborting fetch.", "e")
                return None, False

        proxy = proxy_rotate(proxy_manager)
        try:
            async with session.get(
                url,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status == 200:
                    return await response.text(), True
                if response.status == 404:
                    return None, True

                error_msg = f"HTTP {response.status} for {url}"
                last_error = error_msg
                log(error_msg, "w")

                if response.status in [400, 403, 429, 500, 502, 503]:
                    if proxy is not None:
                        proxy_blacklist(proxy_manager, proxy)

                await asyncio.sleep(backoff_factor * (attempt + 1))
        except Exception as e:
            last_error = str(e)
            log(f"Attempt {attempt + 1} failed: {str(e)}", "w")
            if proxy is not None:
                proxy_blacklist(proxy_manager, proxy)
            await asyncio.sleep(backoff_factor * (attempt + 1))

        attempt += 1

async def get_cdx_indexes(
    session: aiohttp.ClientSession,
    proxy_manager: Dict[str, Any],
    timeout: int
) -> Tuple[List[str], bool]:
    url = "http://index.commoncrawl.org/collinfo.json"
    data, success = await fetch_with_retry(session, url, proxy_manager, timeout)

    if not success:
        log("Failed to fetch CDX indexes, trying direct connection...", "w")
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status == 200:
                    data = await response.text()
                    success = True
        except Exception as e:
            log(f"Direct connection also failed: {str(e)}", "e")
            return [], False

    if not data:
        return [], False

    try:
        indexes = json.loads(data)
        return [index['cdx-api'] for index in indexes], True
    except json.JSONDecodeError as e:
        log(f"Error parsing CDX indexes: {str(e)}", "e")
        return [], False

async def process_cdx_page(
    session: aiohttp.ClientSession,
    url: str,
    tld: str,
    found_domains: Set[str],
    proxy_manager: Dict[str, Any],
    timeout: int,
    output_file: str
) -> Tuple[Set[str], bool]:
    data, success = await fetch_with_retry(session, url, proxy_manager, timeout)

    if not success or not data:
        return found_domains, False

    new_domains = set()
    for line in data.strip().split('\n'):
        try:
            record = json.loads(line)
            if not (url := record.get('url', '')):
                continue

            if (parsed := urlparse(url)).netloc and parsed.netloc.endswith(f".{tld}"):
                domain = parsed.netloc.lower()
                if domain not in found_domains:
                    new_domains.add(domain)
        except json.JSONDecodeError:
            continue

    if new_domains:
        found_domains.update(new_domains)
        log(f"Found {len(new_domains)} new domains (total: {len(found_domains)})", "s")
        await save_domains(found_domains, output_file)

    return found_domains, True

async def process_cdx_api(
    session: aiohttp.ClientSession,
    cdx_api: str,
    tld: str,
    found_domains: Set[str],
    proxy_manager: Dict[str, Any],
    timeout: int,
    output_file: str,
    semaphore: asyncio.Semaphore
) -> Tuple[Set[str], bool]:
    async with semaphore:
        url = f"{cdx_api}?url=*.{tld}&output=json&fl=url&showNumPages=true"
        data, success = await fetch_with_retry(session, url, proxy_manager, timeout)

        if not success:
            return found_domains, False

        try:
            if data is not None:
                pages_info = json.loads(data)
                total_pages = pages_info.get('pages', 0)
                log(f"Found {total_pages} pages in {cdx_api}", "s")

                for page in range(total_pages):
                    page_url = f"{cdx_api}?url=*.{tld}&output=json&fl=url&page={page}"
                    found_domains, _ = await process_cdx_page(
                        session, page_url, tld, found_domains,
                        proxy_manager, timeout, output_file
                    )
            else:
                log(f"No data received from {cdx_api} for pages info", "w")
        except json.JSONDecodeError:
            found_domains, _ = await process_cdx_page(
                session, url, tld, found_domains,
                proxy_manager, timeout, output_file
            )

        return found_domains, True

async def save_domains(domains: Set[str], output_file: str) -> None:
    try:
        temp_file = f"{output_file}.tmp"
        with open(temp_file, 'w') as f:
            f.writelines(f"{domain}\n" for domain in sorted(domains))

        os.replace(temp_file, output_file)
    except Exception as e:
        log(f"Error saving domains: {str(e)}", "e")

async def main():
    parser = argparse.ArgumentParser(
        description="Extract domains with specific TLD from Common Crawl CDX API",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-t", "--tld", required=True, help="TLD to extract (e.g., 'ir', 'com')")
    parser.add_argument("-o", "--output", default="domains.txt", help="Output file path")
    parser.add_argument("-w", "--workers", type=int, default=10, help="Number of concurrent workers")
    parser.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds")
    parser.add_argument("--retries", type=int, default=5, help="Max retries per request")
    parser.add_argument("--backoff", type=float, default=2.0, help="Backoff factor between retries")

    args = parser.parse_args()

    proxy_manager = create_proxy_manager([])
    await get_fresh_proxies(proxy_manager)

    found_domains = set()
    semaphore = asyncio.Semaphore(args.workers)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    log(f"Starting extraction for TLD: {args.tld}", "s")
    log(f"Using {args.workers} workers", "i")
    log(f"Timeout set to {args.timeout} seconds", "i")
    log(f"Max retries: {args.retries}, Backoff factor: {args.backoff}", "i")
    log(f"Using auto-collected proxies", "i")

    connector = aiohttp.TCPConnector(
        limit=args.workers,
        force_close=True,
        enable_cleanup_closed=True
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        cdx_apis, success = await get_cdx_indexes(session, proxy_manager, args.timeout)
        if not success:
            log("Fatal: Could not retrieve CDX indexes after multiple attempts", "e")
            sys.exit(1)

        if not cdx_apis:
            log("No CDX APIs found!", "e")
            return

        log(f"Found {len(cdx_apis)} CDX APIs", "s")

        tasks = []
        for api in cdx_apis:
            task = process_cdx_api(
                session, api, args.tld, found_domains,
                proxy_manager, args.timeout,
                args.output, semaphore
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        success_count = sum(1 for _, success in results if success)
        log(f"Successfully processed {success_count}/{len(cdx_apis)} CDX APIs", "s")

        await save_domains(found_domains, args.output)
        log(f"Finished! Total domains found: {len(found_domains)}", "s")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Interrupted by user", "w")
        sys.exit(1)