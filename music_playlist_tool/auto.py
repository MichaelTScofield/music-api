import argparse
from dataclasses import dataclass, field
from datetime import datetime
import importlib.util
import os
import re
import sys
import time

import requests


os.environ["NO_PROXY"] = "127.0.0.1,localhost"


NETEASE_API = "http://localhost:3001"
DEFAULT_NETEASE_COOKIE = "MUSIC_A_T=1774709183398; MUSIC_R_T=1774709183575; _ga=GA1.1.553266414.1764746510; _ga_EPDQHDTJH5=GS2.1.s1764749200$o2$g0$t1764749200$j60$l0$h0; _ntes_nnid=4462d33bd189250d4cc38257b2a0f2d4,1766644984665; _ntes_nuid=4462d33bd189250d4cc38257b2a0f2d4; NMTID=00O0C0cnao4YCyqekaXs86mvLV_48MAAAGbVD8r6A; WEVNSM=1.0.0; WNMCID=ffzoii.1766644987153.01.0; __remember_me=true; NTES_P_UTID=D8t7lRd51owm3cN14rVrayQi2l5JxkFw|1774333336; P_INFO=jiuyaotech3@163.com|1774333336|0|mail163|00&99|hun&1774237928&unireg#hun&430100#10#0#0|&0|mail163&unireg|jiuyaotech3@163.com; JSESSIONID-WYYY=o3spdC%2FJGa3i1KuVfG2FosvAHtIrzGIDozgojP6Tt79b1P0Dups9v%2FopI%2F1WCkSfVi72tkfC1OhKxqaCKJSU2g8F%2FyNNsQ838zYICWonvOGo%5CUZ9E0UnIU9n2Y5o0WplgwoufJ6vNhGQ0wlhks3ggtNboEpMIq4kY31nNW9Th0c%2FnrKO%3A1774711675707; _iuqxldmzr_=32; Hm_lvt_1483fb4774c02a30ffa6f0e2945e9b70=1774709876; HMACCOUNT=F4569E7AA0FB7F73; sDeviceId=YD-G3UQYqInrSFBUxUERAOX4h5gqlqi4ZoN; __csrf=b66754dd635db9433d67a285764972a5; MUSIC_U=00611DEEFA988B76DA413E2146D96E10BC80CF0868805013CAE4542260D2926B49B936417F20EB2371A9ABBDB49E6A3804B55588657B7AB655568CAD12CC556E83BD0F4DF78925FBFB09D9928F170B2816D6EE09CF3709A1804FAE65681723A1B5BB07634BA577869C175F010EC8BB7E2CB58EAAD5807D3CFEA204423E642636BD77396797892D3E1823D885AFF862D31F257F50AE7CCCCD84890C7D9855F14F4C4AA1652F1F8798BADDAA603DF64F2E05E132059AC8E196BE4082068A1D47490E8134B5E70649383F4FAD64B772D6CDA9D99E4368C977F1D17AE6AF7002B83B5F02B24AC995B70A17AEBB4BE8F2E9CC1876A92D516A1648A4A76335D16240E7105291A7BF4130DF8750691C39FE40BC180D8E00120053A9DB70A331E08843E5204521C9A46CD82A63FDC94E1A5B8F5AD87732CD6CBAB6DC029EDFA7525A94BCD9A3952CE26007580A1C1666E6B08722440A8E819D0EDBF95CCEF70DF450837292584CB2A23C5793B064CD1F3DB94ABCA3BE4387E5B3E90927F45ED0D5650197D1D520D8A6BB6BC1A6784DA98E62196DD2; ntes_kaola_ad=1; Hm_lpvt_1483fb4774c02a30ffa6f0e2945e9b70=1774709906"
NETEASE_COOKIE_FILE = "netease_cookie.txt"
KEEP_DUPLICATE_SONGS = True
REQUEST_TIMEOUT = 20
LOCAL_REQUEST_TIMEOUT = 60
REQUEST_RETRY_COUNT = 3
REQUEST_RETRY_DELAY = 2
NETEASE_ADD_RETRY = 3
NETEASE_ADD_RETRY_DELAY = 2
NETEASE_ADD_BATCH_SIZE = 50
NETEASE_NEXT_BATCH_STABILIZE_DELAY = 2
NETEASE_FINAL_RECOVERY_DELAY = 8
ALBUM_PROGRESS_LOG_INTERVAL = 1
NETEASE_MATCH_PROGRESS_INTERVAL = 25

HTTP_SESSION = requests.Session()
RUNTIME_STATE = {
    "log_callback": None,
    "verbose_details": False,
}
NETEASE_COOKIE = ""


class CookieExpiredError(Exception):
    pass


class QQCookieExpiredError(Exception):
    pass


@dataclass
class AlbumSummary:
    name: str
    count: int
    identity: str
    publish_time: int | str = 0
    songs: list[str] = field(default_factory=list)


@dataclass
class NeteaseRunResult:
    playlist_id: str
    playlist_url: str
    matched_count: int
    missing_count: int
    missing_songs: list[str] = field(default_factory=list)
    unadded_track_ids: list[str] = field(default_factory=list)
    added_song_count: int = 0
    added_album_summaries: list[AlbumSummary] = field(default_factory=list)
    missing_report_path: str = ""
    missing_reason_lines: list[str] = field(default_factory=list)


@dataclass
class AlbumDiffItem:
    name: str
    year: str
    publish_date: str
    publish_time: int
    size: int
    netease_album_id: str


@dataclass
class AlbumDiffResult:
    artist_name: str
    qq_singer_mid: str
    qq_album_count: int
    netease_album_count: int
    missing_count: int
    qq_albums: list[AlbumDiffItem] = field(default_factory=list)
    source_albums: list[AlbumDiffItem] = field(default_factory=list)
    missing_albums: list[AlbumDiffItem] = field(default_factory=list)
    report_path: str = ""
    source_platform: str = "qq"
    source_label: str = "QQ"
    source_name: str = "QQ 音乐"
    missing_platform: str = "qq"
    missing_name: str = "QQ 音乐"
    missing_summary: str = "QQ 音乐缺失但网易云存在"
    report_suffix: str = "QQ缺失网易云专辑报告"


@dataclass
class WorkflowResult:
    artist_name: str
    total_songs: int
    album_summaries: list[AlbumSummary] = field(default_factory=list)
    netease: NeteaseRunResult | None = None
    qq: object | None = None
    qq_singer_mid: str = ""
    qq_total_songs: int = 0
    qq_album_summaries: list[object] = field(default_factory=list)
    album_diff: AlbumDiffResult | None = None
    album_diff_playlist: NeteaseRunResult | None = None
    auto_selected_platform: str = ""


def get_cookie_store_path(cookie_path=None):
    if cookie_path:
        return cookie_path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), NETEASE_COOKIE_FILE)


def load_netease_cookie(cookie_path=None):
    path = get_cookie_store_path(cookie_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as file:
                value = file.read().strip()
            if value:
                return value
        except OSError:
            pass
    return DEFAULT_NETEASE_COOKIE.strip()


def get_netease_cookie():
    return NETEASE_COOKIE


def set_netease_cookie(cookie, persist=False, cookie_path=None):
    global NETEASE_COOKIE
    NETEASE_COOKIE = (cookie or "").strip()
    if persist:
        path = get_cookie_store_path(cookie_path)
        with open(path, "w", encoding="utf-8") as file:
            file.write(NETEASE_COOKIE)
    return NETEASE_COOKIE


def configure_runtime(log_callback=None, verbose_details=False):
    RUNTIME_STATE["log_callback"] = log_callback
    RUNTIME_STATE["verbose_details"] = verbose_details


def log_message(message, detail=False):
    if detail and not RUNTIME_STATE["verbose_details"]:
        return
    callback = RUNTIME_STATE["log_callback"]
    if callback:
        callback(message)
    else:
        print(message)


QQ_WORKFLOW_MODULE = None


def resolve_qq_workflow_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "qq-auto.py"),
        os.path.join(base_dir, "QQ自动建立歌单.py"),
        os.path.join(base_dir, "_internal", "qq-auto.py"),
        os.path.join(base_dir, "_internal", "QQ自动建立歌单.py"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        "未找到 QQ 工作流脚本 qq-auto.py/QQ自动建立歌单.py。"
        f" 已检查: {', '.join(candidates)}"
    )


def get_qq_workflow_module():
    global QQ_WORKFLOW_MODULE
    if QQ_WORKFLOW_MODULE is not None:
        return QQ_WORKFLOW_MODULE
    path = resolve_qq_workflow_path()
    spec = importlib.util.spec_from_file_location("qq_music_workflow_combined", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    QQ_WORKFLOW_MODULE = module
    return module


def get_qq_cookie():
    return get_qq_workflow_module().get_qq_cookie()


def load_qq_cookie(cookie_path=None):
    return get_qq_workflow_module().load_qq_cookie(cookie_path)


def set_qq_cookie(cookie, persist=False, cookie_path=None):
    return get_qq_workflow_module().set_qq_cookie(cookie, persist=persist, cookie_path=cookie_path)


def refresh_qq_cookie_interactive(cookie_path=None):
    return get_qq_workflow_module().refresh_cookie_interactive(cookie_path)


def ensure_qq_cookie_ready():
    qq_workflow = get_qq_workflow_module()
    try:
        qq_workflow.ensure_cookie_ready()
    except qq_workflow.CookieExpiredError as exc:
        raise QQCookieExpiredError(str(exc)) from exc


def summarize_response_text(response, limit=200):
    text = (response.text or "").strip()
    if not text:
        return "<empty>"
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def request_json(method, url, **kwargs):
    timeout = kwargs.pop("timeout", None)
    if timeout is None:
        timeout = LOCAL_REQUEST_TIMEOUT if url.startswith(NETEASE_API) else REQUEST_TIMEOUT
    last_error = None
    for attempt in range(1, REQUEST_RETRY_COUNT + 1):
        try:
            response = HTTP_SESSION.request(method, url, timeout=timeout, **kwargs)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                last_error = ValueError(
                    f"接口未返回 JSON: {url} | status={response.status_code} | "
                    f"content-type={response.headers.get('content-type', '')} | "
                    f"body={summarize_response_text(response)}"
                )
                if attempt == REQUEST_RETRY_COUNT:
                    raise last_error from exc
                time.sleep(REQUEST_RETRY_DELAY)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            if attempt == REQUEST_RETRY_COUNT:
                break
            time.sleep(REQUEST_RETRY_DELAY)
    raise last_error


def clean_song_name(name):
    return re.sub(r"\s+", " ", name).strip()


def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def normalize_album_name(name):
    return re.sub(r"\s+", " ", name).strip().lower()


def album_name_matches(target_name, candidate_name):
    target = normalize_album_name(target_name)
    candidate = normalize_album_name(candidate_name)
    if not target or not candidate:
        return False
    return target == candidate or target in candidate or candidate in target


def normalize_album_compare_name(name):
    text = re.sub(r"\s+", "", str(name or "")).strip().lower()
    text = text.replace("version", "版")
    return re.sub(r"[()（）\\[\\]【】《》<>〈〉'\"`·._:：,，!！?？/\\\\|\\-—–]", "", text)


def album_compare_names_match(left, right):
    left_key = normalize_album_compare_name(left)
    right_key = normalize_album_compare_name(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    min_length = min(len(left_key), len(right_key))
    if min_length < 2:
        return False
    return left_key in right_key or right_key in left_key


def to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def format_netease_publish_date(value):
    timestamp = to_int(value, 0)
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return str(value)


def extract_year_from_publish_date(value):
    text = str(value or "").strip()
    match = re.search(r"\d{4}", text)
    return match.group(0) if match else ""


def normalize_publish_date_text(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{13}", text):
        return format_netease_publish_date(int(text))
    if re.fullmatch(r"\d{10}", text):
        return format_netease_publish_date(int(text) * 1000)
    match = re.search(r"(\d{4})(?:[-/.年]?(\d{1,2}))?(?:[-/.月]?(\d{1,2}))?", text)
    if not match:
        return text
    year = match.group(1)
    month = match.group(2)
    day = match.group(3)
    if month and day:
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


def build_publish_sort_key(value):
    text = normalize_publish_date_text(value)
    match = re.search(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text)
    if not match:
        return (0, 0, 0, text)
    return (
        to_int(match.group(1), 0),
        to_int(match.group(2), 0),
        to_int(match.group(3), 0),
        text,
    )


def extract_version_tag(name):
    matches = re.findall(r"\((.*?)\)|（(.*?)）", name)
    parts = []
    for left, right in matches:
        content = clean_song_name(left or right)
        if not content:
            continue
        normalized = re.sub(r"\s+", " ", content).strip().lower()
        if re.search(
            r"remix|mix|ver(?:sion)?|live|acoustic|demo|instrumental|inst|伴奏|纯音乐|演奏版|钢琴版|弦乐版|合唱版|版",
            normalized,
        ):
            parts.append(normalized)
    return "__".join(parts)


def normalize_song_name(name):
    name = re.sub(r"\s+", " ", name).strip()
    version_tag = extract_version_tag(name)
    name = re.sub(r"\(.*?\)|（.*?）", "", name)
    normalized = re.sub(r"\s+", " ", name).strip().lower()
    if version_tag:
        return f"{normalized}__{version_tag}"
    return normalized


def normalize_artist_name(name):
    name = re.sub(r"\s+", "", name).strip().lower()
    return re.sub(r"[()（）\\[\\]【】'\"`·._-]", "", name)


def artist_name_matches(target_name, candidate_name):
    target = normalize_artist_name(target_name)
    candidate = normalize_artist_name(candidate_name)
    if not target or not candidate:
        return False
    return target == candidate


def build_title_candidates(title):
    candidates = []
    full_title = clean_song_name(title)
    short_title = clean_song_name(re.sub(r"\(.*?\)|（.*?）", "", title))
    for item in [full_title, short_title]:
        if item and item not in candidates:
            candidates.append(item)
    return candidates


def netease_song_has_target_artist(song, target_artist):
    artists = [item.get("name", "").strip() for item in song.get("ar", [])]
    return any(artist_name_matches(target_artist, item) for item in artists if item)


def get_artist_id(name):
    result = request_json(
        "GET",
        f"{NETEASE_API}/search",
        params={"keywords": name, "type": 100, "limit": 1},
    )
    artists = result.get("result", {}).get("artists", [])
    if not artists:
        raise ValueError(f"未找到歌手：{name}")
    artist_id = artists[0]["id"]
    artist_name = artists[0].get("name", "").strip() or name
    log_message(f"已获取歌手ID：{artist_name} ({artist_id})")
    return artist_id


def get_albums(artist_id):
    result = request_json(
        "GET",
        f"{NETEASE_API}/artist/album",
        params={"id": artist_id, "limit": 1000},
    )
    albums = result.get("hotAlbums", [])
    seen = set()
    result_list = []
    for album in albums:
        album_id = album["id"]
        if album_id in seen:
            continue
        seen.add(album_id)
        result_list.append(
            {
                "id": album_id,
                "name": album.get("name", "").strip(),
                "publish_time": album.get("publishTime", 0),
                "size": album.get("size", 0),
            }
        )
    return result_list


def get_album_songs(album, artist_name):
    result = request_json("GET", f"{NETEASE_API}/album", params={"id": album["id"]})
    song_list = result.get("songs", [])
    album_song_count = len(song_list) or album.get("size", 0)
    result_list = []
    for song in song_list:
        name = clean_song_name(song.get("name", ""))
        if not name:
            continue
        artists = [item.get("name", "").strip() for item in song.get("ar", [])]
        if not any(artist_name_matches(artist_name, item) for item in artists):
            continue
        result_list.append(
            {
                "name": name,
                "song_id": str(song.get("id") or ""),
                "source_platform": "netease",
                "artist": artist_name,
                "source_artists": artists,
                "album": album["name"],
                "album_id": album["id"],
                "album_song_count": album_song_count,
                "album_publish_time": album["publish_time"],
            }
        )
    return result_list


def dedupe_songs(songs):
    if KEEP_DUPLICATE_SONGS:
        return songs
    grouped = {}
    for song in songs:
        key = normalize_song_name(song["name"])
        grouped.setdefault(key, []).append(song)
    result_list = []
    for group in grouped.values():
        group.sort(
            key=lambda item: (
                1 if item["album_song_count"] > 1 else 0,
                item["album_song_count"],
                item["album_publish_time"],
            ),
            reverse=True,
        )
        result_list.append(group[0])
    return result_list


def fetch_all_songs_from_albums(albums, artist_name):
    total_albums = len(albums)
    log_message(f"已获取网易云专辑列表，共 {total_albums} 张，开始抓取歌曲...")
    all_songs = []
    for index, album in enumerate(albums, start=1):
        if (
            index == 1
            or index == total_albums
            or index % ALBUM_PROGRESS_LOG_INTERVAL == 0
        ):
            log_message(f"抓取专辑进度：{index}/{total_albums} - {album['name']}")
        all_songs.extend(get_album_songs(album, artist_name))
    return dedupe_songs(all_songs)


def fetch_all_songs(artist_id, artist_name):
    albums = get_albums(artist_id)
    return fetch_all_songs_from_albums(albums, artist_name)


def score_netease_song(song, title_candidates, artist, album=None):
    score = 0
    name = clean_song_name(song.get("name", "")).lower()
    album_name = clean_song_name(song.get("al", {}).get("name", "")).lower()
    normalized_name = normalize_song_name(song.get("name", ""))
    if netease_song_has_target_artist(song, artist):
        score += 50
    else:
        return -1
    for index, title in enumerate(title_candidates):
        title_lower = title.lower()
        if not title_lower:
            continue
        if name == title_lower:
            score += 50 if index == 0 else 35
        elif title_lower in name:
            score += 30 if index == 0 else 20
        if normalized_name == normalize_song_name(title):
            score += 25 if index == 0 else 15
    if album and album_name_matches(album, album_name):
        score += 80 if normalize_album_name(album) == normalize_album_name(album_name) else 45
    return score


def search_netease_song(title, artist, album=None):
    try:
        title_candidates = build_title_candidates(title)
        normalized_target_album = normalize_album_name(album or "")
        queries = []
        if album:
            queries.append(f"{title_candidates[0]} {artist} {album}")
        queries.append(f"{title_candidates[0]} {artist}")
        if len(title_candidates) > 1:
            queries.append(f"{title_candidates[1]} {artist}")
        best_id = None
        best_score = -1
        seen_ids = set()
        for query in queries:
            result = request_json(
                "GET",
                f"{NETEASE_API}/cloudsearch",
                params={"keywords": query, "limit": 30},
            )
            songs = result.get("result", {}).get("songs", [])
            for song in songs:
                song_id = song["id"]
                if song_id in seen_ids:
                    continue
                seen_ids.add(song_id)
                score = score_netease_song(song, title_candidates, artist, album)
                if (
                    album
                    and normalized_target_album
                    and normalize_album_name(song.get("al", {}).get("name", "")) == normalized_target_album
                    and normalize_song_name(song.get("name", "")) == normalize_song_name(title_candidates[0])
                    and score >= 120
                ):
                    return song_id
                if score > best_score:
                    best_score = score
                    best_id = song_id
        return best_id if best_score >= 60 else None
    except Exception:
        return None


def extract_response_message(response):
    if not isinstance(response, dict):
        return str(response)
    parts = []
    for key in ("message", "msg"):
        value = response.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("message", "msg"):
            value = data.get(key)
            if value not in (None, ""):
                parts.append(str(value))
    return " | ".join(parts)


def response_indicates_cookie_expired(response):
    if not isinstance(response, dict):
        return False
    code = response.get("code")
    message = extract_response_message(response).lower()
    keywords = ["登录", "登陆", "失效", "过期", "cookie", "无权限", "请先", "重新登录"]
    if code in {301, 302, 400, 401, 403, 405, 406, 411, 512}:
        return True
    return any(keyword in message for keyword in keywords)


def ensure_cookie_ready():
    cookie = get_netease_cookie().strip()
    if not cookie:
        raise CookieExpiredError("网易云 Cookie 为空，请输入新的 Cookie。")
    try:
        response = request_json(
            "GET",
            f"{NETEASE_API}/login/status",
            params={"cookie": cookie, "timestamp": int(time.time() * 1000)},
        )
    except Exception:
        return
    if response_indicates_cookie_expired(response):
        raise CookieExpiredError("网易云 Cookie 已过期，请输入新的 Cookie。")
    data = response.get("data")
    if isinstance(data, dict) and not data.get("account") and not data.get("profile"):
        raise CookieExpiredError("网易云 Cookie 已过期，请输入新的 Cookie。")


def ensure_valid_netease_response(response):
    if response_indicates_cookie_expired(response):
        raise CookieExpiredError("网易云 Cookie 已过期，请输入新的 Cookie。")


def create_netease_playlist(name):
    response = request_json(
        "POST",
        f"{NETEASE_API}/playlist/create",
        data={"name": name, "cookie": get_netease_cookie()},
    )
    ensure_valid_netease_response(response)
    playlist = response.get("playlist", {})
    playlist_id = playlist.get("id")
    if not playlist_id:
        raise ValueError(f"网易云歌单创建失败: {response}")
    return playlist_id


def get_netease_playlist_song_ids(playlist_id):
    response = request_json(
        "GET",
        f"{NETEASE_API}/playlist/detail",
        params={"id": playlist_id, "_t": int(time.time() * 1000)},
        headers={"x-apicache-force-fetch": "1"},
    )
    track_ids = response.get("playlist", {}).get("trackIds", [])
    return {str(item.get("id")) for item in track_ids if item.get("id")}


def add_netease_batch(playlist_id, batch):
    response = request_json(
        "POST",
        f"{NETEASE_API}/playlist/tracks",
        data={
            "op": "add",
            "pid": playlist_id,
            "tracks": ",".join(batch),
            "cookie": get_netease_cookie(),
        },
    )
    ensure_valid_netease_response(response)
    return response


def get_missing_track_ids(target_ids, playlist_ids):
    return [track_id for track_id in target_ids if track_id not in playlist_ids]


def build_album_summaries(songs):
    grouped = {}
    for song in songs:
        identity = str(song.get("album_id") or song["album"])
        grouped.setdefault(
            identity,
            {
                "name": song["album"],
                "count": 0,
                "identity": identity,
                "publish_time": song.get("album_publish_time", 0),
                "songs": [],
                "_song_keys": set(),
            },
        )
        grouped[identity]["count"] += 1
        song_name = song.get("name", "").strip()
        song_key = normalize_song_name(song_name)
        if song_name and song_key not in grouped[identity]["_song_keys"]:
            grouped[identity]["_song_keys"].add(song_key)
            grouped[identity]["songs"].append(song_name)
    ordered = sorted(
        grouped.values(),
        key=lambda item: (item["publish_time"], item["name"], item["identity"]),
        reverse=True,
    )
    result = []
    for item in ordered:
        item.pop("_song_keys", None)
        result.append(AlbumSummary(**item))
    return result


def build_playlist_name(artist_name, suffix):
    return f"{artist_name}_{suffix}"


def build_missing_report_text(artist_name, missing_reason_lines):
    lines = [
        f"歌手：{artist_name}",
        f"未匹配到网易云曲库：{len(missing_reason_lines)}",
        "",
    ]
    lines.extend(missing_reason_lines)
    return "\n".join(lines).rstrip() + "\n"


def write_missing_report(artist_name, missing_reason_lines, output_directory=None):
    if not missing_reason_lines:
        return ""
    output_directory = output_directory or os.getcwd()
    os.makedirs(output_directory, exist_ok=True)
    path = os.path.join(output_directory, f"{sanitize_filename(artist_name)}_未匹配网易云报告.txt")
    with open(path, "w", encoding="utf-8") as file:
        file.write(build_missing_report_text(artist_name, missing_reason_lines))
    return os.path.abspath(path)


def add_netease_songs(playlist_id, songs):
    matched_song_pairs = []
    missing_songs = []
    missing_reason_lines = []
    total_songs = len(songs)
    log_message(f"开始匹配网易云曲库，共 {total_songs} 首...")
    for index, song in enumerate(songs, start=1):
        if (
            index == 1
            or index == total_songs
            or index % NETEASE_MATCH_PROGRESS_INTERVAL == 0
        ):
            log_message(
                f"网易云匹配进度：{index}/{total_songs}，当前已匹配 {len(matched_song_pairs)} 首，未匹配 {len(missing_songs)} 首"
            )
        song_id = ""
        if song.get("source_platform") == "netease":
            song_id = str(song.get("song_id") or "").strip()
        if not song_id:
            song_id = search_netease_song(song["name"], song["artist"], song["album"])
        if song_id:
            matched_song_pairs.append((song, str(song_id)))
        else:
            missing_songs.append(f"{song['name']} | {song['album']}")
            missing_reason_lines.append(f"{song['name']} | {song['album']} | 未匹配到网易云曲库")

    selected_song_by_track_id = {}
    target_ids = []
    for song, song_id in matched_song_pairs:
        if song_id in selected_song_by_track_id:
            continue
        selected_song_by_track_id[song_id] = song
        target_ids.append(song_id)

    unadded_track_ids = []
    total_batches = (len(target_ids) + NETEASE_ADD_BATCH_SIZE - 1) // NETEASE_ADD_BATCH_SIZE
    for batch_index, index in enumerate(range(0, len(target_ids), NETEASE_ADD_BATCH_SIZE), start=1):
        batch = target_ids[index:index + NETEASE_ADD_BATCH_SIZE]
        log_message(f"网易云添加 {index} ~ {index + len(batch)}")
        remaining = list(batch)
        for _ in range(1, NETEASE_ADD_RETRY + 1):
            add_netease_batch(playlist_id, remaining)
            time.sleep(NETEASE_ADD_RETRY_DELAY)
            playlist_ids = get_netease_playlist_song_ids(playlist_id)
            remaining = get_missing_track_ids(remaining, playlist_ids)
            if not remaining:
                break
        if remaining:
            time.sleep(NETEASE_FINAL_RECOVERY_DELAY)
            add_netease_batch(playlist_id, remaining)
            time.sleep(NETEASE_ADD_RETRY_DELAY)
            playlist_ids = get_netease_playlist_song_ids(playlist_id)
            remaining = get_missing_track_ids(remaining, playlist_ids)
        if remaining:
            unadded_track_ids.extend(remaining)
        elif batch_index < total_batches and NETEASE_NEXT_BATCH_STABILIZE_DELAY > 0:
            log_message(f"网易云等待 {NETEASE_NEXT_BATCH_STABILIZE_DELAY} 秒后继续下一批")
            time.sleep(NETEASE_NEXT_BATCH_STABILIZE_DELAY)

    unadded_track_id_set = set(unadded_track_ids)
    added_song_records = [
        selected_song_by_track_id[track_id]
        for track_id in target_ids
        if track_id not in unadded_track_id_set
    ]

    return {
        "matched_count": len(target_ids),
        "missing_count": len(missing_songs),
        "missing_songs": missing_songs,
        "unadded_track_ids": sorted(set(unadded_track_ids)),
        "added_song_count": len(added_song_records),
        "added_album_summaries": build_album_summaries(added_song_records),
        "missing_reason_lines": missing_reason_lines,
    }


def run_netease(artist_name, songs, playlist_name=None, output_directory=None):
    ensure_cookie_ready()
    playlist_name = playlist_name or build_playlist_name(artist_name, "全专辑")
    log_message("创建网易云歌单...")
    playlist_id = create_netease_playlist(playlist_name)
    add_result = add_netease_songs(playlist_id, songs)
    playlist_url = f"https://music.163.com/#/playlist?id={playlist_id}"
    log_message(f"网易云：{playlist_url}")
    report_path = write_missing_report(
        artist_name,
        add_result["missing_reason_lines"],
        output_directory=output_directory,
    )
    return NeteaseRunResult(
        playlist_id=str(playlist_id),
        playlist_url=playlist_url,
        matched_count=add_result["matched_count"],
        missing_count=add_result["missing_count"],
        missing_songs=add_result["missing_songs"],
        unadded_track_ids=add_result["unadded_track_ids"],
        added_song_count=add_result["added_song_count"],
        added_album_summaries=add_result["added_album_summaries"],
        missing_report_path=report_path,
        missing_reason_lines=add_result["missing_reason_lines"],
    )


def build_album_diff_item(album):
    publish_time = to_int(album.get("publish_time"), 0)
    publish_date = format_netease_publish_date(publish_time)
    return AlbumDiffItem(
        name=str(album.get("name") or "").strip(),
        year=extract_year_from_publish_date(publish_date),
        publish_date=publish_date,
        publish_time=publish_time,
        size=to_int(album.get("size"), 0),
        netease_album_id=str(album.get("id") or ""),
    )


def build_qq_album_diff_item(album):
    publish_date = normalize_publish_date_text(album.get("publish_time", ""))
    return AlbumDiffItem(
        name=str(album.get("name") or "").strip(),
        year=extract_year_from_publish_date(publish_date),
        publish_date=publish_date,
        publish_time=0,
        size=to_int(album.get("size"), 0),
        netease_album_id="",
    )


def format_album_timeline_line(label, index, album):
    if not album:
        return ""
    label_text = pad_album_timeline_left(str(label), 4)
    return f"{label_text} {index:>3} | {album.publish_date or '-'} | {album.size or '-'} 首 | {album.name}"


def album_timeline_text_width(text):
    width = 0
    for char in text:
        code = ord(char)
        if (
            0x1100 <= code <= 0x115F
            or 0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3
            or 0xF900 <= code <= 0xFAFF
            or 0xFE10 <= code <= 0xFE19
            or 0xFE30 <= code <= 0xFE6F
            or 0xFF00 <= code <= 0xFF60
            or 0xFFE0 <= code <= 0xFFE6
        ):
            width += 2
        else:
            width += 1
    return width


def pad_album_timeline_left(text, width):
    return text + (" " * max(width - album_timeline_text_width(text), 0))


def build_album_timeline_lines(source_albums, missing_albums, source_label="QQ", missing_label="缺失"):
    rows = []
    source_index = 0
    missing_index = 0
    entries = []
    for album in source_albums:
        entries.append(("source", album, build_publish_sort_key(album.publish_date)))
    for album in missing_albums:
        entries.append(("missing", album, build_publish_sort_key(album.publish_date)))
    entries.sort(
        key=lambda entry: (
            entry[2][0],
            entry[2][1],
            entry[2][2],
            1 if entry[0] == "source" else 0,
            entry[1].name,
        ),
        reverse=True,
    )
    for source, album, _sort_key in entries:
        if source == "source":
            source_index += 1
            rows.append([format_album_timeline_line(source_label, source_index, album), ""])
        else:
            missing_index += 1
            missing_text = format_album_timeline_line(missing_label, missing_index, album)
            if rows and rows[-1][0] and not rows[-1][1]:
                rows[-1][1] = missing_text
            else:
                rows.append(["", missing_text])

    left_width = max(
        78,
        *(album_timeline_text_width(left) for left, _right in rows if left),
    )
    timeline = []
    for left, right in rows:
        if left and right:
            timeline.append(f"{pad_album_timeline_left(left, left_width)}  {right}")
        elif left:
            timeline.append(left)
        else:
            timeline.append(f"{' ' * (left_width + 2)}{right}")
    return timeline


def write_album_diff_report(album_diff, output_directory=None):
    output_directory = output_directory or os.getcwd()
    os.makedirs(output_directory, exist_ok=True)
    report_suffix = album_diff.report_suffix or f"{album_diff.missing_name}缺失{album_diff.source_name}专辑报告"
    path = os.path.join(output_directory, f"{sanitize_filename(album_diff.artist_name)}_{sanitize_filename(report_suffix)}.txt")
    lines = [
        f"歌手：{album_diff.artist_name}",
        f"QQ 音乐专辑数：{album_diff.qq_album_count}",
        f"网易云专辑数：{album_diff.netease_album_count}",
        f"{album_diff.missing_summary}：{album_diff.missing_count}",
        "",
        "时间线对照（按发行日期倒序）：",
        f"{album_diff.source_label} = {album_diff.source_name}已获取到的专辑",
        f"缺失 = {album_diff.missing_summary}的专辑",
        "",
        "来源 序号 | 发布日期 | 曲目数 | 专辑名",
    ]
    timeline_lines = build_album_timeline_lines(
        album_diff.source_albums or album_diff.qq_albums,
        album_diff.missing_albums,
        source_label=album_diff.source_label,
        missing_label="缺失",
    )
    if timeline_lines:
        lines.extend(timeline_lines)
    else:
        lines.append("无")
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines).rstrip() + "\n")
    return os.path.abspath(path)


def build_album_diff_result(
    artist_name,
    qq_singer_mid,
    qq_albums,
    netease_albums,
    output_directory=None,
    source_platform="qq",
):
    qq_album_names = [album.get("name", "") for album in qq_albums if album.get("name", "")]
    netease_album_names = [album.get("name", "") for album in netease_albums if album.get("name", "")]
    qq_album_items = [
        build_qq_album_diff_item(album)
        for album in qq_albums
        if album.get("name", "")
    ]
    if source_platform == "netease":
        source_albums = [
            build_album_diff_item(album)
            for album in netease_albums
            if album.get("name", "")
        ]
        missing_albums = [
            build_qq_album_diff_item(album)
            for album in qq_albums
            if not any(album_compare_names_match(album.get("name", ""), netease_name) for netease_name in netease_album_names)
        ]
        source_label = "网易云"
        source_name = "网易云"
        missing_platform = "netease"
        missing_name = "网易云"
        missing_summary = "网易云缺失但 QQ 音乐存在"
        report_suffix = "网易云缺失QQ音乐专辑报告"
    else:
        source_albums = [
            build_qq_album_diff_item(album)
            for album in qq_albums
            if album.get("name", "")
        ]
        missing_albums = [
            build_album_diff_item(album)
            for album in netease_albums
            if not any(album_compare_names_match(album.get("name", ""), qq_name) for qq_name in qq_album_names)
        ]
        source_platform = "qq"
        source_label = "QQ"
        source_name = "QQ 音乐"
        missing_platform = "qq"
        missing_name = "QQ 音乐"
        missing_summary = "QQ 音乐缺失但网易云存在"
        report_suffix = "QQ缺失网易云专辑报告"
    missing_albums.sort(key=lambda item: (build_publish_sort_key(item.publish_date), item.name), reverse=True)
    album_diff = AlbumDiffResult(
        artist_name=artist_name,
        qq_singer_mid=qq_singer_mid,
        qq_album_count=len(qq_albums),
        netease_album_count=len(netease_albums),
        missing_count=len(missing_albums),
        qq_albums=qq_album_items,
        source_albums=source_albums,
        missing_albums=missing_albums,
        source_platform=source_platform,
        source_label=source_label,
        source_name=source_name,
        missing_platform=missing_platform,
        missing_name=missing_name,
        missing_summary=missing_summary,
        report_suffix=report_suffix,
    )
    album_diff.report_path = write_album_diff_report(album_diff, output_directory=output_directory)
    return album_diff


def run_album_diff(artist_name, output_directory=None, source_platform="netease"):
    qq_workflow = get_qq_workflow_module()
    qq_workflow.configure_runtime(
        log_callback=RUNTIME_STATE["log_callback"],
        verbose_details=RUNTIME_STATE["verbose_details"],
    )
    log_message("获取 QQ 音乐专辑信息...")
    singer = qq_workflow.resolve_qq_singer(artist_name)
    qq_albums = qq_workflow.get_albums(singer["mid"])
    log_message(f"QQ 音乐专辑数：{len(qq_albums)}")

    log_message("获取网易云音乐专辑信息...")
    artist_id = get_artist_id(artist_name)
    netease_albums = get_albums(artist_id)
    log_message(f"网易云音乐专辑数：{len(netease_albums)}")

    album_diff = build_album_diff_result(
        artist_name,
        singer["mid"],
        qq_albums,
        netease_albums,
        output_directory=output_directory,
        source_platform=source_platform,
    )
    log_message(f"{album_diff.missing_summary}：{album_diff.missing_count}")
    log_message(f"专辑差异报告：{album_diff.report_path}")
    return album_diff


def add_netease_direct_songs(playlist_id, songs):
    selected_song_by_track_id = {}
    target_ids = []
    for song in songs:
        song_id = str(song.get("song_id") or "").strip()
        if not song_id or song_id in selected_song_by_track_id:
            continue
        selected_song_by_track_id[song_id] = song
        target_ids.append(song_id)

    unadded_track_ids = []
    total_batches = (len(target_ids) + NETEASE_ADD_BATCH_SIZE - 1) // NETEASE_ADD_BATCH_SIZE
    for batch_index, index in enumerate(range(0, len(target_ids), NETEASE_ADD_BATCH_SIZE), start=1):
        batch = target_ids[index:index + NETEASE_ADD_BATCH_SIZE]
        log_message(f"网易云差异歌单添加 {index + 1} ~ {index + len(batch)}")
        remaining = list(batch)
        for _ in range(1, NETEASE_ADD_RETRY + 1):
            add_netease_batch(playlist_id, remaining)
            time.sleep(NETEASE_ADD_RETRY_DELAY)
            playlist_ids = get_netease_playlist_song_ids(playlist_id)
            remaining = get_missing_track_ids(remaining, playlist_ids)
            if not remaining:
                break
        if remaining:
            unadded_track_ids.extend(remaining)
        elif batch_index < total_batches and NETEASE_NEXT_BATCH_STABILIZE_DELAY > 0:
            log_message(f"网易云等待 {NETEASE_NEXT_BATCH_STABILIZE_DELAY} 秒后继续下一批")
            time.sleep(NETEASE_NEXT_BATCH_STABILIZE_DELAY)

    unadded_track_id_set = set(unadded_track_ids)
    added_song_records = [
        selected_song_by_track_id[track_id]
        for track_id in target_ids
        if track_id not in unadded_track_id_set
    ]
    return {
        "matched_count": len(target_ids),
        "unadded_track_ids": sorted(set(unadded_track_ids)),
        "added_song_count": len(added_song_records),
        "added_album_summaries": build_album_summaries(added_song_records),
    }


def create_album_diff_playlist(album_diff, playlist_name=None, output_directory=None):
    raise RuntimeError("当前版本只生成 QQ 缺失网易云专辑报告，不再创建网易云补全歌单。")


def fetch_auto_album_context(artist_name):
    qq_workflow = get_qq_workflow_module()
    qq_workflow.configure_runtime(
        log_callback=RUNTIME_STATE["log_callback"],
        verbose_details=RUNTIME_STATE["verbose_details"],
    )

    log_message("自动创建：获取 QQ 音乐专辑信息...")
    singer = qq_workflow.resolve_qq_singer(artist_name)
    qq_albums = qq_workflow.get_albums(singer["mid"])
    log_message(f"QQ 音乐专辑数：{len(qq_albums)}")

    log_message("自动创建：获取网易云音乐专辑信息...")
    artist_id = get_artist_id(artist_name)
    netease_albums = get_albums(artist_id)
    log_message(f"网易云音乐专辑数：{len(netease_albums)}")

    return qq_workflow, singer, qq_albums, artist_id, netease_albums


def choose_auto_platform(qq_album_count, netease_album_count):
    if netease_album_count > qq_album_count:
        return "netease"
    return "qq"


def run_auto_create(
    result,
    artist_name,
    *,
    netease_playlist_name=None,
    qq_playlist_name=None,
    output_directory=None,
    log_callback=None,
    verbose_details=False,
):
    qq_workflow, singer, qq_albums, _artist_id, netease_albums = fetch_auto_album_context(artist_name)
    result.qq_singer_mid = singer["mid"]
    selected_platform = choose_auto_platform(len(qq_albums), len(netease_albums))
    result.auto_selected_platform = selected_platform
    selected_name = "网易云" if selected_platform == "netease" else "QQ 音乐"
    if len(qq_albums) == len(netease_albums):
        log_message(f"自动创建：两个平台专辑数相同（{len(qq_albums)} 张），默认创建 QQ 音乐歌单。")
    else:
        log_message(f"自动创建：{selected_name}专辑数更多，创建{selected_name}歌单。")

    result.album_diff = build_album_diff_result(
        artist_name,
        singer["mid"],
        qq_albums,
        netease_albums,
        output_directory=output_directory,
        source_platform=selected_platform,
    )
    log_message(f"{result.album_diff.missing_summary}：{result.album_diff.missing_count}")
    log_message(f"专辑差异报告：{result.album_diff.report_path}")

    if selected_platform == "netease":
        ensure_cookie_ready()
        songs = fetch_all_songs_from_albums(netease_albums, artist_name)
        result.total_songs = len(songs)
        result.album_summaries = build_album_summaries(songs)
        result.netease = run_netease(
            artist_name,
            songs,
            playlist_name=netease_playlist_name,
            output_directory=output_directory,
        )
    else:
        try:
            qq_workflow.ensure_cookie_ready()
            songs = qq_workflow.fetch_all_songs_from_albums(qq_albums, artist_name, singer_mid=singer["mid"])
            result.qq_total_songs = len(songs)
            result.qq_album_summaries = qq_workflow.build_album_summaries(songs)
            result.qq = qq_workflow.run_qq(
                artist_name,
                songs,
                playlist_name=qq_playlist_name,
                output_directory=output_directory,
            )
        except qq_workflow.CookieExpiredError as exc:
            raise QQCookieExpiredError(str(exc)) from exc
    return result


def run_workflow(
    artist_name,
    *,
    execute_netease=True,
    execute_qq=False,
    compare_album_diff=False,
    auto_create=False,
    create_diff_playlist=False,
    netease_playlist_name=None,
    qq_playlist_name=None,
    diff_playlist_name=None,
    output_directory=None,
    log_callback=None,
    verbose_details=False,
):
    configure_runtime(log_callback=log_callback, verbose_details=verbose_details)
    result = WorkflowResult(
        artist_name=artist_name,
        total_songs=0,
    )
    if auto_create:
        return run_auto_create(
            result,
            artist_name,
            netease_playlist_name=netease_playlist_name,
            qq_playlist_name=qq_playlist_name,
            output_directory=output_directory,
            log_callback=log_callback,
            verbose_details=verbose_details,
        )

    if not compare_album_diff and not execute_netease and not execute_qq:
        raise ValueError("请至少选择一个平台：网易云音乐或 QQ 音乐。")

    # 先校验登录态，避免一个平台已创建歌单后，另一个平台才发现 Cookie 失效。
    if execute_netease:
        ensure_cookie_ready()
    if execute_qq:
        ensure_qq_cookie_ready()

    if compare_album_diff:
        result.album_diff = run_album_diff(artist_name, output_directory=output_directory)
        if create_diff_playlist:
            log_message("当前版本只生成 QQ 缺失网易云专辑报告，不再创建网易云补全歌单。")

    if execute_netease:
        log_message("获取网易云歌手ID...")
        artist_id = get_artist_id(artist_name)
        songs = fetch_all_songs(artist_id, artist_name)
        result.total_songs = len(songs)
        result.album_summaries = build_album_summaries(songs)
        result.netease = run_netease(
            artist_name,
            songs,
            playlist_name=netease_playlist_name,
            output_directory=output_directory,
        )
    if execute_qq:
        qq_workflow = get_qq_workflow_module()
        try:
            qq_result = qq_workflow.run_workflow(
                artist_name,
                execute_qq=True,
                qq_playlist_name=qq_playlist_name,
                output_directory=output_directory,
                log_callback=log_callback,
                verbose_details=verbose_details,
            )
        except qq_workflow.CookieExpiredError as exc:
            raise QQCookieExpiredError(str(exc)) from exc
        result.qq = qq_result.qq
        result.qq_singer_mid = getattr(qq_result, "singer_mid", "")
        result.qq_total_songs = getattr(qq_result, "total_songs", 0)
        result.qq_album_summaries = getattr(qq_result, "album_summaries", [])
    return result


def build_result_text(result):
    lines = [
        f"歌手：{result.artist_name}",
    ]
    if result.auto_selected_platform:
        selected_name = "网易云" if result.auto_selected_platform == "netease" else "QQ 音乐"
        lines.extend(
            [
                "",
                f"自动创建选择：{selected_name}",
            ]
        )
    if result.album_diff:
        lines.extend(
            [
                "",
                "专辑差异报告：",
                f"报告路径：{result.album_diff.report_path}",
                f"QQ 音乐专辑数：{result.album_diff.qq_album_count}",
                f"网易云专辑数：{result.album_diff.netease_album_count}",
                f"{result.album_diff.missing_summary}：{result.album_diff.missing_count}",
            ]
        )
        preview = result.album_diff.missing_albums[:12]
        if preview:
            lines.append("")
            lines.append("差异专辑预览：")
            for index, album in enumerate(preview, start=1):
                lines.append(
                    f"{index}. {album.publish_date or '-'} | {album.name}"
                )
            if result.album_diff.missing_count > len(preview):
                lines.append(f"... 其余 {result.album_diff.missing_count - len(preview)} 张见报告")
        elif result.album_diff.missing_count == 0:
            lines.append(f"差异专辑数为 0，报告中无{result.album_diff.missing_name}缺失专辑。")
    if result.netease or result.album_summaries:
        lines.extend(
            [
                "",
                "网易云源统计：",
                f"专辑总数：{len(result.album_summaries)}",
                f"专辑曲目总数：{result.total_songs}",
            ]
        )
    if result.netease:
        lines.extend(
            [
                "",
                "网易云结果：",
                f"歌单链接：{result.netease.playlist_url}",
                f"匹配成功：{result.netease.matched_count}",
                f"未匹配：{result.netease.missing_count}",
                f"已添加专辑总数：{len(result.netease.added_album_summaries)}",
                f"已添加歌曲数量：{result.netease.added_song_count}",
                f"其中专辑曲目总数：{result.total_songs}，匹配成功：{result.netease.matched_count}",
            ]
        )
    if result.qq or result.qq_album_summaries:
        lines.extend(
            [
                "",
                "QQ 音乐源统计：",
                f"专辑总数：{len(result.qq_album_summaries)}",
                f"专辑曲目总数：{result.qq_total_songs}",
            ]
        )
    if result.qq:
        lines.extend(
            [
                "",
                "QQ 音乐结果：",
                f"歌单链接：{result.qq.playlist_url}",
                f"准备添加：{result.qq.target_count}",
                f"已添加歌曲数量：{result.qq.added_song_count}",
                f"未添加：{len(result.qq.unadded_song_mids)}",
                f"已添加专辑总数：{len(result.qq.added_album_summaries)}",
            ]
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="按歌手抓取全部专辑歌曲并导入网易云/QQ 音乐歌单")
    parser.add_argument("artist_name", help="歌手名")
    parser.add_argument(
        "--mode",
        choices=["auto", "qq-and-diff", "qq", "netease"],
        default="auto",
        help="运行模式，默认自动创建专辑数更多的平台歌单并生成另一平台缺失报告",
    )
    parser.add_argument("--playlist-name", help="自定义歌单名；未分别指定时同时用于两个平台")
    parser.add_argument("--netease-playlist-name", help="自定义网易云歌单名")
    parser.add_argument("--qq-playlist-name", help="自定义 QQ 音乐歌单名")
    parser.add_argument("--diff-playlist-name", help=argparse.SUPPRESS)
    parser.add_argument("--create-diff-playlist", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output-directory", help="未匹配报告输出目录")
    parser.add_argument("--refresh-qq-cookie", action="store_true", help="手动输入并保存 QQ 音乐 Cookie")
    parser.add_argument("--qq-cookie-path", help="QQ 音乐 Cookie 文件路径，默认 qq_music_cookie.txt")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    args = parser.parse_args()

    if args.qq_cookie_path:
        os.environ["QQ_MUSIC_COOKIE_FILE"] = os.path.abspath(args.qq_cookie_path)
        set_qq_cookie(load_qq_cookie(args.qq_cookie_path))
    if args.refresh_qq_cookie:
        refresh_qq_cookie_interactive(args.qq_cookie_path)

    auto_create = args.mode in {"auto", "qq-and-diff"}
    execute_netease = args.mode == "netease"
    execute_qq = args.mode == "qq"
    compare_album_diff = False
    result = run_workflow(
        args.artist_name,
        execute_netease=execute_netease,
        execute_qq=execute_qq,
        compare_album_diff=compare_album_diff,
        auto_create=auto_create,
        netease_playlist_name=args.netease_playlist_name or args.playlist_name,
        qq_playlist_name=args.qq_playlist_name or args.playlist_name,
        output_directory=args.output_directory,
        log_callback=None,
        verbose_details=args.verbose,
    )
    print(build_result_text(result))


set_netease_cookie(load_netease_cookie())


if __name__ == "__main__":
    main()
