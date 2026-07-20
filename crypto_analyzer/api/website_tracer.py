"""
網站來源 IP 溯源模組

功能：透過憑證透明度日誌、被動 DNS 歷史、子網域 DNS 解析，
找出隱藏在 Cloudflare 後方的真實來源 IP 位址。

僅使用免費公開資料來源，不直接對目標主機發送探測封包。
適用於司法鑑識中對詐騙網站或非法交易平台的主機溯源調查。

使用前必須取得合法授權，僅得對經授權之調查對象執行。
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import re
import socket
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Cloudflare CIDR 備援清單（離線用）────────────────────────────────────────

_CF_FALLBACK: List[str] = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
    "103.31.4.0/22",   "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
    "198.41.128.0/17", "162.158.0.0/15",  "104.16.0.0/13",
    "104.24.0.0/14",   "172.64.0.0/13",   "131.0.72.0/22",
    "2400:cb00::/32",  "2606:4700::/32",  "2803:f800::/32",
    "2405:b500::/32",  "2405:8100::/32",  "2a06:98c0::/29",
    "2c0f:f248::/32",
]

_CF_CACHE = Path(__file__).parent.parent / "data" / "cf_ranges.txt"

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"

ProgressCB = Optional[Callable[[str, str], None]]


# ── HTTP 工具 ─────────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    s.headers.update({
        "User-Agent":      _UA,
        "Accept":          "application/json, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


_S = _make_session()


def _get(url: str, params=None, headers=None, timeout: int = 20) -> requests.Response:
    time.sleep(0.2)
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    return _S.get(url, params=params, headers=h, timeout=timeout)


# ── Cloudflare 範圍管理 ───────────────────────────────────────────────────────

def load_cf_ranges() -> List[str]:
    """讀取快取 CF 範圍，若不存在則嘗試下載，失敗則使用內建備援。"""
    if _CF_CACHE.exists():
        lines = [l.strip() for l in _CF_CACHE.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            return lines
    try:
        r = _get("https://api.cloudflare.com/client/v4/ips", timeout=10)
        data = r.json()
        if data.get("success"):
            res = data["result"]
            ranges = res.get("ipv4_cidrs", []) + res.get("ipv6_cidrs", [])
            if ranges:
                _CF_CACHE.parent.mkdir(exist_ok=True)
                _CF_CACHE.write_text("\n".join(ranges), encoding="utf-8")
                return ranges
    except Exception:
        pass
    return list(_CF_FALLBACK)


def is_cf_ip(ip: str, cf_ranges: List[str]) -> bool:
    """判斷 IP 是否屬於 Cloudflare 範圍。"""
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(c, strict=False) for c in cf_ranges)
    except ValueError:
        return False


# ── DNS 解析 ──────────────────────────────────────────────────────────────────

def resolve_host(hostname: str) -> Optional[str]:
    """將主機名稱解析為 IPv4 位址，失敗回傳 None。"""
    try:
        results = socket.getaddrinfo(hostname.strip(), None, socket.AF_INET, socket.SOCK_STREAM)
        if results:
            return results[0][4][0]
    except Exception:
        pass
    return None


def check_wildcard(domain: str) -> bool:
    """偵測萬用字元 DNS（返回 True 表示存在萬用字元）。"""
    probe = f"cloudfalixyz99182test.{domain}"
    return resolve_host(probe) is not None


def resolve_bulk(hostnames: List[str], max_workers: int = 10) -> Dict[str, Optional[str]]:
    """並行解析多個主機名稱，回傳 {hostname: ip_or_None} 字典。"""
    out: Dict[str, Optional[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        fmap = {ex.submit(resolve_host, h): h for h in hostnames}
        for fut in concurrent.futures.as_completed(fmap):
            h = fmap[fut]
            try:
                out[h] = fut.result()
            except Exception:
                out[h] = None
    return out


# ── 憑證透明度來源 ────────────────────────────────────────────────────────────

def _certspotter(domain: str) -> Set[str]:
    found: Set[str] = set()
    try:
        r = _get(
            "https://api.certspotter.com/v1/issuances",
            params={"domain": domain, "include_subdomains": "true", "expand": "dns_names"},
            timeout=30,
        )
        if r.status_code == 200:
            for cert in r.json():
                for n in cert.get("dns_names", []):
                    n = n.strip().lstrip("*.")
                    if n and domain in n:
                        found.add(n.lower())
    except Exception:
        pass
    return found


def _crtsh(domain: str) -> Set[str]:
    found: Set[str] = set()
    for url in [
        f"https://crt.sh/?q=%.{domain}&output=json",
        f"https://crt.sh/?q={domain}&output=json",
    ]:
        if found:
            break
        for attempt in range(4):
            try:
                r = _get(url, timeout=45)
                if r.status_code == 200:
                    data = r.json()
                    for e in data:
                        for n in str(e.get("name_value", "")).split("\n"):
                            n = n.strip().lstrip("*.")
                            if n and domain in n and " " not in n:
                                found.add(n.lower())
                    if found:
                        break
                elif r.status_code in (429, 500, 502, 503):
                    time.sleep(2 ** attempt)
                else:
                    break
            except Exception:
                if attempt < 3:
                    time.sleep(2 ** attempt)
    return found


def _anubisdb(domain: str) -> Set[str]:
    found: Set[str] = set()
    try:
        r = _get(f"https://jldc.me/anubis/subdomains/{domain}", timeout=20)
        if r.status_code == 200:
            for n in r.json():
                n = str(n).strip().lstrip("*.")
                if n and domain in n:
                    found.add(n.lower())
    except Exception:
        pass
    return found


def _rapiddns(domain: str) -> Set[str]:
    found: Set[str] = set()
    try:
        r = _get(
            f"https://rapiddns.io/subdomain/{domain}?full=1&down=1",
            headers={"Accept": "text/html"},
            timeout=20,
        )
        if r.status_code == 200:
            pat = r"<td>([a-zA-Z0-9._-]+\." + re.escape(domain) + r")</td>"
            for n in re.findall(pat, r.text):
                found.add(n.strip().lower())
    except Exception:
        pass
    return found


def _threatminer(domain: str) -> Set[str]:
    found: Set[str] = set()
    try:
        r = _get(
            "https://api.threatminer.org/v2/domain.php",
            params={"q": domain, "rt": "5"},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            if str(data.get("status_code", "")) == "200":
                for n in data.get("results", []):
                    n = str(n).strip().lstrip("*.")
                    if n and domain in n:
                        found.add(n.lower())
    except Exception:
        pass
    return found


def _urlscan(domain: str) -> Set[str]:
    found: Set[str] = set()
    try:
        r = _get(
            "https://urlscan.io/api/v1/search/",
            params={"q": f"domain:{domain}", "size": 100},
            timeout=20,
        )
        if r.status_code == 200:
            for result in r.json().get("results", []):
                h = result.get("page", {}).get("domain", "")
                if h and domain in h:
                    found.add(h.lower().lstrip("*."))
    except Exception:
        pass
    return found


def _wayback_cdx(domain: str) -> Set[str]:
    found: Set[str] = set()
    try:
        r = _get(
            "https://web.archive.org/cdx/search/cdx",
            params={
                "url": f"*.{domain}",
                "output": "json",
                "fl": "original",
                "collapse": "urlkey",
                "limit": 3000,
            },
            timeout=30,
        )
        if r.status_code == 200:
            pat = re.compile(
                r"(?:https?://)?([a-zA-Z0-9._-]+\." + re.escape(domain) + r")"
            )
            for row in r.json():
                for cell in row:
                    m = pat.search(str(cell))
                    if m:
                        found.add(m.group(1).strip().lower())
    except Exception:
        pass
    return found


def _collect_ct_names(domain: str, cb: ProgressCB) -> List[str]:
    """並行呼叫所有 CT / 子網域列舉來源，回傳去重後的名稱清單。"""
    sources = {
        "CertSpotter": _certspotter,
        "crt.sh":      _crtsh,
        "AnubisDB":    _anubisdb,
        "RapidDNS":    _rapiddns,
        "ThreatMiner": _threatminer,
        "URLScan":     _urlscan,
        "WaybackCDX":  _wayback_cdx,
    }
    all_found: Set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        fmap = {ex.submit(fn, domain): name for name, fn in sources.items()}
        for fut in concurrent.futures.as_completed(fmap):
            name = fmap[fut]
            try:
                result = fut.result()
                all_found.update(result)
                if cb:
                    cb("Phase2", f"[{name}] 找到 {len(result)} 個名稱")
            except Exception as e:
                if cb:
                    cb("Phase2", f"[{name}] 查詢錯誤：{e}")
    return sorted(all_found)


# ── 被動 DNS 歷史 ─────────────────────────────────────────────────────────────

def _hackertarget_pdns(domain: str) -> List[str]:
    ips: List[str] = []
    try:
        r = _get(f"https://api.hackertarget.com/hostsearch/?q={domain}", timeout=15)
        if r.status_code == 200 and "," in r.text and not r.text.startswith("error"):
            for line in r.text.splitlines():
                parts = line.split(",")
                if len(parts) == 2:
                    ip = parts[1].strip()
                    if ip and "." in ip and ip[0].isdigit():
                        ips.append(ip)
    except Exception:
        pass
    return ips


def _alienvault_pdns(domain: str) -> List[str]:
    ips: List[str] = []
    for attempt in range(3):
        try:
            r = _get(
                f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns",
                timeout=20,
            )
            if r.status_code == 200:
                for e in r.json().get("passive_dns", []):
                    addr = e.get("address", "").strip()
                    if addr and "." in addr and ":" not in addr:
                        ips.append(addr)
                return ips
            elif r.status_code == 429:
                time.sleep(4 * (attempt + 1))
        except Exception:
            return []
    return ips


def _viewdns_pdns(domain: str) -> List[str]:
    ips: List[str] = []
    try:
        r = _get(
            f"https://viewdns.info/iphistory/?domain={domain}",
            headers={"Accept": "text/html"},
            timeout=15,
        )
        if r.status_code == 200:
            for ip in re.findall(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", r.text):
                if not ip.startswith(("127.", "10.", "192.168.", "172.")):
                    ips.append(ip)
    except Exception:
        pass
    return ips


def _collect_passive_ips(domain: str, cb: ProgressCB) -> List[str]:
    """並行查詢被動 DNS 歷史，回傳去重後的 IP 清單。"""
    sources = {
        "HackerTarget": _hackertarget_pdns,
        "AlienVault":   _alienvault_pdns,
        "ViewDNS":      _viewdns_pdns,
    }
    all_ips: Set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fmap = {ex.submit(fn, domain): name for name, fn in sources.items()}
        for fut in concurrent.futures.as_completed(fmap):
            name = fmap[fut]
            try:
                result = fut.result()
                all_ips.update(result)
                if cb:
                    cb("Phase2", f"[{name}] 歷史 IP：{', '.join(result) if result else '無'}")
            except Exception:
                pass
    return list(all_ips)


# ── ASN 查詢 & IP 分析 ────────────────────────────────────────────────────────

def asn_for_ip(ip: str) -> str:
    """透過 HackerTarget 查詢 IP 的 ASN 資訊，格式例如 'AS13335 CLOUDFLARENET'。"""
    try:
        r = _get(f"https://api.hackertarget.com/aslookup/?q={ip}", timeout=10)
        if r.status_code == 200 and "," in r.text:
            parts = r.text.strip().split(",")
            if len(parts) >= 2:
                return parts[1].strip().strip('"')
    except Exception:
        pass
    return "UNKNOWN"


def isp_for_ip(ip: str) -> str:
    """透過 ip-api.com 查詢 IP 所屬業者（ISP／組織）資訊，格式例如
    'Amazon.com, Inc. (AWS) · US'。查無資料時回傳 'UNKNOWN'。"""
    try:
        r = _get(f"http://ip-api.com/json/{ip}",
                  params={"fields": "status,isp,org,country"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                isp = (data.get("isp") or "").strip()
                org = (data.get("org") or "").strip()
                country = (data.get("country") or "").strip()
                label = isp or org or "UNKNOWN"
                if org and org != isp:
                    label = f"{label} ({org})"
                if country:
                    label = f"{label} · {country}"
                return label
    except Exception:
        pass
    return "UNKNOWN"


def enrich_ip_list(ips: List[str], cf_ranges: List[str]) -> List[Dict]:
    """為每個 IP 查詢 ASN、業者資訊並標記是否為 Cloudflare，回傳分析結果清單。"""
    cf_ips = [ip for ip in ips if is_cf_ip(ip, cf_ranges)]
    non_cf = [ip for ip in ips if not is_cf_ip(ip, cf_ranges)]

    result: List[Dict] = []
    for ip in cf_ips:
        result.append({
            "ip": ip, "asn": "AS13335 CLOUDFLARENET", "isp": "Cloudflare, Inc.",
            "is_cf": True, "confidence": 95,
        })

    def _enrich(ip: str) -> Dict:
        asn = asn_for_ip(ip)
        isp = isp_for_ip(ip)
        by_asn = "AS13335" in asn.upper() or "CLOUDFLARE" in asn.upper()
        return {"ip": ip, "asn": asn, "isp": isp, "is_cf": by_asn,
                "confidence": 60 if by_asn else 90}

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        fmap = {ex.submit(_enrich, ip): ip for ip in non_cf}
        for fut in concurrent.futures.as_completed(fmap):
            ip = fmap[fut]
            try:
                result.append(fut.result())
            except Exception:
                result.append({"ip": ip, "asn": "UNKNOWN", "isp": "UNKNOWN",
                                "is_cf": False, "confidence": 0})

    return result


# ── Phase 5：主動頁面偵察 ────────────────────────────────────────────────────

_PAGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SKIP_TLDS = {
    'cloudflare.com', 'fastly.com', 'akamaized.net', 'akamai.com',
    'google.com', 'gstatic.com', 'googleapis.com', 'googletagmanager.com',
    'facebook.com', 'fbcdn.net', 'twitter.com', 'twimg.com',
    'youtube.com', 'ytimg.com', 'doubleclick.net', 'googlesyndication.com',
    'amazon.com', 'amazonaws.com', 'cloudfront.net',
    'jquery.com', 'jsdelivr.net', 'bootstrapcdn.com', 'unpkg.com',
}
_MACCMS_PLAY_IDS: List[int] = [1, 2, 3, 5, 10, 20, 21, 100, 500]


def _skip_domain(d: str) -> bool:
    d = d.lower()
    return any(d == s or d.endswith('.' + s) for s in _SKIP_TLDS)


def _extract_domains_from_text(text: str, base_domain: str) -> Set[str]:
    """從 HTML / JSON / JS 文字提取所有外部域名參照。"""
    found: Set[str] = set()
    # 匹配 https?:// 後方或引號內的域名
    pat = re.compile(
        r'(?:https?://|["\'\x60])([a-zA-Z0-9][a-zA-Z0-9._-]{1,253}'
        r'\.[a-zA-Z]{2,}(?:/[^\s"\'<>\x60]*)?)'
    )
    for m in pat.finditer(text):
        raw = m.group(1).split('/')[0].lower().rstrip('.')
        if (raw
                and raw != base_domain
                and not raw.endswith('.' + base_domain)
                and not _skip_domain(raw)
                and '.' in raw
                and not re.match(r'^\d+\.\d+', raw)):
            found.add(raw)
    return found


def _fetch_and_extract(domain: str, cb: ProgressCB) -> tuple[List[str], str]:
    """
    Phase 5 — 主動抓取目標首頁及 CMS 播放頁，提取所有外部域名參照。
    自動偵測 CMS（MacCMS / WordPress / Joomla / Drupal）並進行針對性深層追蹤。
    回傳 (排序後外部域名清單, CMS名稱)。
    """
    ext: Set[str] = set()
    cms = ''
    hdrs = {
        'User-Agent':      _PAGE_UA,
        'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8',
        'DNT':             '1',
    }

    # ── 首頁 ──────────────────────────────────────────────────────────────
    for scheme in ('https', 'http'):
        try:
            r = requests.get(
                f'{scheme}://{domain}/', headers=hdrs,
                timeout=12, allow_redirects=True, stream=True,
            )
            body = b''
            for chunk in r.iter_content(8192):
                body += chunk
                if len(body) > 512 * 1024:
                    break
            r.close()
            html = body.decode('utf-8', errors='ignore')

            # CMS 偵測
            if 'player_data' in html or (
                    '/template/' in html and re.search(r'maccms|MacCMS', html)):
                cms = 'MacCMS'
            elif 'player_data' in html:
                cms = 'MacCMS'
            elif 'wp-content' in html or 'wp-includes' in html:
                cms = 'WordPress'
            elif 'joomla' in html.lower():
                cms = 'Joomla'
            elif 'drupal' in html.lower():
                cms = 'Drupal'

            new = _extract_domains_from_text(html, domain)
            ext.update(new)
            if cb:
                cb("Phase5",
                   f"[首頁 {scheme}] HTTP {r.status_code}"
                   f" | 外部域名 {len(new)} 個 | CMS: {cms or '未知'}")
            break
        except Exception as e:
            if cb:
                cb("Phase5", f"[首頁 {scheme}] 連線失敗：{e}")

    # ── MacCMS 播放頁追蹤 ───────────────────────────────────────────────
    if 'MacCMS' in cms:
        if cb:
            cb("Phase5", "[MacCMS] 追蹤影片播放頁 player_data 中的影片 CDN 來源…")
        found_player = False
        for vid_id in _MACCMS_PLAY_IDS:
            if found_player:
                break
            for play_path in (
                f'/play/{vid_id}-1-1.html',
                f'/vodplay/{vid_id}-1-1.html',
            ):
                try:
                    import json as _json
                    r = requests.get(
                        f'https://{domain}{play_path}',
                        headers={**hdrs, 'Referer': f'https://{domain}/'},
                        timeout=10, allow_redirects=True,
                    )
                    if r.status_code != 200:
                        continue
                    m = re.search(
                        r'var\s+player_data\s*=\s*(\{[^<]{10,3000}\})',
                        r.text,
                    )
                    if not m:
                        continue
                    pd = _json.loads(m.group(1))
                    url_str = pd.get('url', '')
                    frm     = pd.get('from', '')
                    if url_str:
                        new = _extract_domains_from_text(url_str, domain)
                        ext.update(new)
                        if cb:
                            cb("Phase5",
                               f"[MacCMS] ID={vid_id} from={frm}"
                               f" → CDN 域名：{', '.join(new) or '（無新域名）'}")
                        found_player = bool(new)
                        if found_player:
                            break
                except Exception:
                    pass

    result = sorted(d for d in ext if not re.match(r'^\d+\.\d+\.\d+\.\d+$', d))
    return result, cms


# ── Phase 6：SSL 憑證比對 ─────────────────────────────────────────────────────

def _probe_ssl_match(ip: str, domain: str, timeout: int = 6) -> bool:
    """
    對候選 IP 發起 TLS 連線（SNI = domain），在 DER 二進位憑證中
    搜尋目標域名字串，確認該 IP 是否持有目標域名 SSL 憑證。
    （不驗證憑證有效性，僅比對 CN / SAN 文字內容。）
    """
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip, 443), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=domain) as tls:
                cert_bin = tls.getpeercert(binary_form=True)
                if not cert_bin:
                    return False
                # ASN.1 DER 中 ASCII 域名以原始 bytes 儲存，直接子字串搜尋
                if domain.lower().encode('ascii') in cert_bin:
                    return True
                # 萬用字元憑證：*.example.com
                parts = domain.split('.')
                if len(parts) > 2:
                    wildcard = ('.' + '.'.join(parts[1:])).encode('ascii')
                    if wildcard in cert_bin:
                        return True
    except Exception:
        pass
    return False


# ── 主掃描函式 ────────────────────────────────────────────────────────────────

def scan_domain(
    target: str,
    passive_only: bool = False,
    threads: int = 10,
    progress: ProgressCB = None,
) -> Dict:
    """
    掃描目標網域，回傳完整結果字典。

    參數：
        target       — 目標網域（不含 http://）
        passive_only — True 時僅執行被動偵察，跳過子網域 DNS 解析
        threads      — 子網域解析並行執行緒數
        progress     — 進度回呼 progress(phase: str, message: str)

    回傳值鍵值：
        target, resolved_ip, is_cloudflare, cf_ranges_count,
        ct_names, passive_ips, subdomain_hits, non_cf_ips, has_wildcard
    """
    def _p(phase: str, msg: str) -> None:
        if progress:
            progress(phase, msg)

    # ── Phase 1：初始化 ───────────────────────────────────────────────────────
    _p("Phase1", "正在載入 Cloudflare IP 範圍清單…")
    cf_ranges = load_cf_ranges()
    _p("Phase1", f"載入 {len(cf_ranges)} 個 Cloudflare CIDR 區塊")

    resolved_ip = resolve_host(target)
    if not resolved_ip:
        return {"error": f"無法解析網域：{target}"}

    in_cf = is_cf_ip(resolved_ip, cf_ranges)
    _p("Phase1", (
        f"解析結果：{target} → {resolved_ip}"
        f"（{'Cloudflare 保護中' if in_cf else '非 Cloudflare / 直接對外 IP'}）"
    ))

    all_candidate: Set[str] = set()

    # ── Phase 2：CT 日誌 + 被動 DNS ──────────────────────────────────────────
    _p("Phase2", "開始憑證透明度日誌 + 被動 DNS 偵察（7 個來源並行）…")
    ct_names = _collect_ct_names(target, _p)
    _p("Phase2", f"共找到 {len(ct_names)} 個子網域名稱")

    passive_ips = _collect_passive_ips(target, _p)
    all_candidate.update(passive_ips)
    _p("Phase2", f"被動 DNS 歷史 IP 合計：{len(passive_ips)} 個")

    # ── Phase 3：子網域 DNS 解析（主動，可選）────────────────────────────────
    subdomain_hits: List[Dict] = []
    has_wildcard = False

    if not passive_only:
        hostnames: List[str] = list(ct_names)

        _p("Phase3", f"正在從 CT 來源取得的 {len(hostnames)} 個名稱進行 DNS 解析…")

        _p("Phase3", "偵測萬用字元 DNS…")
        has_wildcard = check_wildcard(target)
        if has_wildcard:
            _p("Phase3", "⚠ 偵測到萬用字元 DNS — 暴力結果可信度降低，CT 名稱仍有效")
        else:
            _p("Phase3", "未偵測到萬用字元 DNS，結果可信")

        if not hostnames:
            _p("Phase3", "（CT 來源無新名稱可解析，Phase 3 略過）")
        else:
            hostnames = sorted(set(hostnames))
            _p("Phase3", f"解析 {len(hostnames)} 個子網域（{threads} 執行緒）…")
            resolved = resolve_bulk(hostnames, max_workers=threads)

            for host, ip in resolved.items():
                if ip is None:
                    continue
                behind_cf = is_cf_ip(ip, cf_ranges)
                all_candidate.add(ip)
                subdomain_hits.append({
                    "host":           host,
                    "ip":             ip,
                    "behind_cloudflare": behind_cf,
                })

            subdomain_hits.sort(key=lambda h: h["behind_cloudflare"])
            non_cf_sub = sum(1 for h in subdomain_hits if not h["behind_cloudflare"])
            _p("Phase3", f"解析完成：{len(subdomain_hits)} 筆，其中 {non_cf_sub} 個非 CF IP")

    # ── Phase 4：主動頁面偵察 ────────────────────────────────────────────────
    _p("Phase4", "正在抓取目標頁面提取外部 CDN 域名參照…")
    page_domains_list, cms_detected = _fetch_and_extract(target, _p)
    _p("Phase4",
       f"發現 {len(page_domains_list)} 個外部域名"
       f"（CMS：{cms_detected or '未知'}）")
    page_hits: List[Dict] = []
    if page_domains_list:
        page_resolved = resolve_bulk(page_domains_list, max_workers=threads)
        for host, ip in page_resolved.items():
            if ip is None:
                continue
            behind_cf = is_cf_ip(ip, cf_ranges)
            all_candidate.add(ip)
            page_hits.append({
                "host":             host,
                "ip":               ip,
                "behind_cloudflare": behind_cf,
                "source":           "page",
            })
        page_hits.sort(key=lambda h: h["behind_cloudflare"])
        non_cf_page = sum(1 for h in page_hits if not h["behind_cloudflare"])
        _p("Phase4",
           f"頁面參照解析：{len(page_hits)} 筆，{non_cf_page} 個非 CF")

    # ── Phase 5：IP 分析 & ASN 查詢 ──────────────────────────────────────────
    _p("Phase5", f"分析 {len(all_candidate)} 個候選 IP（ASN 查詢中）…")
    enriched = enrich_ip_list(list(all_candidate), cf_ranges)
    non_cf_ips = [e for e in enriched if not e["is_cf"]]
    non_cf_ips.sort(key=lambda x: x["confidence"], reverse=True)
    _p("Phase5", f"分析完成：找到 {len(non_cf_ips)} 個非 Cloudflare 候選 IP")

    # ── Phase 6：SSL 憑證確認 ─────────────────────────────────────────────────
    ssl_confirmed: List[str] = []
    if non_cf_ips:
        _p("Phase6", f"SSL 憑證比對（最多 8 個非 CF IP）…")
        for entry in non_cf_ips[:8]:
            ip = entry["ip"]
            matched = _probe_ssl_match(ip, target)
            entry["ssl_confirmed"] = matched
            if matched:
                ssl_confirmed.append(ip)
                _p("Phase6", f"✔ SSL 匹配：{ip}（持有 {target} 憑證）")
        if ssl_confirmed:
            _p("Phase6", f"共 {len(ssl_confirmed)} 個 IP 通過 SSL 憑證比對")
        else:
            _p("Phase6", "無 IP 通過 SSL 比對（CF 後端可能使用 CF 通用憑證）")

    return {
        "target":           target,
        "resolved_ip":      resolved_ip,
        "is_cloudflare":    in_cf,
        "cf_ranges_count":  len(cf_ranges),
        "ct_names":         ct_names,
        "passive_ips":      passive_ips,
        "subdomain_hits":   subdomain_hits,
        "non_cf_ips":       non_cf_ips,
        "has_wildcard":     has_wildcard,
        "page_hits":        page_hits,
        "cms_detected":     cms_detected,
    }
