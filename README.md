# tldextract
Extract domains with specific TLD

## Installation
```shell
pip install -r requirements.txt
```
## Usage
```
usage: tldextract.py [-h] -t TLD [-o OUTPUT] [-w WORKERS] [-p PROXIES] [--timeout TIMEOUT] [--retries RETRIES]
                     [--backoff BACKOFF]

Extract domains with specific TLD from Common Crawl CDX API

options:
  -h, --help            show this help message and exit
  -t, --tld TLD         TLD to extract (e.g., 'ir', 'com') (default: None)
  -o, --output OUTPUT   Output file path (default: domains.txt)
  -w, --workers WORKERS
                        Number of concurrent workers (default: 10)
  -p, --proxies PROXIES
                        Path to file containing proxies (default: None)
  --timeout TIMEOUT     Request timeout in seconds (default: 60)
  --retries RETRIES     Max retries per request (default: 5)
  --backoff BACKOFF     Backoff factor between retries (default: 2.0)
```
