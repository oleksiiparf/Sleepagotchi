from typing import Dict

HEADERS = {
    'accept': '*/*',
    'accept-language': 'ru,en-US;q=0.9,en;q=0.8',
    'cache-control': 'no-cache',
    'dnt': '1',
    'origin': 'https://tgcf.sleepagotchi.com',
    'pragma': 'no-cache',
    'priority': 'u=1, i',
    'referer': 'https://tgcf.sleepagotchi.com/',
    'sec-ch-ua': '"Chromium";v="131", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
}

def get_headers(user_agent: str = None) -> Dict[str, str]:
    headers = HEADERS.copy()
    if user_agent:
        headers['user-agent'] = user_agent
    return headers
