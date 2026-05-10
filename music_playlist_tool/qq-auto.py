import argparse
from dataclasses import dataclass, field
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from urllib.parse import quote

import requests


QQ_MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
QQ_SMARTBOX_URL = "https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg"
QQ_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
QQ_PLAYLIST_MAP_URL = "https://c.y.qq.com/splcloud/fcgi-bin/fcg_musiclist_getmyfav.fcg"
QQ_REFERER = "https://y.qq.com/"
QQ_COOKIE_FILE = "qq_music_cookie.txt"
QQ_LEGACY_G_TK = 5381
KEEP_DUPLICATE_SONGS = True
REQUEST_TIMEOUT = 25
REQUEST_RETRY_COUNT = 3
REQUEST_RETRY_DELAY = 2
ALBUM_PAGE_SIZE = 80
ALBUM_PROGRESS_LOG_INTERVAL = 1
QQ_ADD_BATCH_SIZE = 50
QQ_ADD_RETRY = 3
QQ_ADD_RETRY_DELAY = 2
QQ_NEXT_BATCH_STABILIZE_DELAY = 2
QQ_PLAYLIST_READY_RETRY = 8
QQ_PLAYLIST_READY_DELAY = 2

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": QQ_REFERER,
        "Accept": "application/json, text/plain, */*",
    }
)
RUNTIME_STATE = {
    "log_callback": None,
    "verbose_details": False,
}
QQ_COOKIE = ""


class CookieExpiredError(Exception):
    pass


@dataclass
class AlbumSummary:
    name: str
    count: int
    identity: str
    publish_time: int | str = ""
    songs: list[str] = field(default_factory=list)


@dataclass
class QQRunResult:
    playlist_id: str
    playlist_url: str
    target_count: int
    added_song_count: int
    unadded_song_mids: list[str] = field(default_factory=list)
    added_album_summaries: list[AlbumSummary] = field(default_factory=list)
    missing_report_path: str = ""
    missing_reason_lines: list[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    artist_name: str
    singer_mid: str
    total_songs: int
    album_summaries: list[AlbumSummary] = field(default_factory=list)
    qq: QQRunResult | None = None


def get_cookie_store_path(cookie_path=None):
    if cookie_path:
        return cookie_path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), QQ_COOKIE_FILE)


def load_qq_cookie(cookie_path=None):
    path = os.environ.get("QQ_MUSIC_COOKIE_FILE", get_cookie_store_path(cookie_path))
    if cookie_path:
        path = get_cookie_store_path(cookie_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as file:
                value = file.read().strip()
            if value:
                return value
        except OSError:
            pass
    env_cookie = os.environ.get("QQ_MUSIC_COOKIE", "").strip()
    if env_cookie:
        return env_cookie
    return ""


def get_qq_cookie():
    return QQ_COOKIE


def set_qq_cookie(cookie, persist=False, cookie_path=None):
    global QQ_COOKIE
    QQ_COOKIE = (cookie or "").strip()
    if persist:
        path = get_cookie_store_path(cookie_path)
        with open(path, "w", encoding="utf-8") as file:
            file.write(QQ_COOKIE)
    return QQ_COOKIE


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


def summarize_response_text(response, limit=200):
    text = (response.text or "").strip()
    if not text:
        return "<empty>"
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def parse_json_response(response):
    response.raise_for_status()
    text = (response.text or "").strip()
    if not text:
        return {}
    try:
        return response.json()
    except ValueError:
        match = re.match(r"^[\w$]+\((.*)\)\s*;?$", text, re.S)
        if match:
            return json.loads(match.group(1))
        raise ValueError(
            f"接口未返回 JSON: {response.url} | status={response.status_code} | "
            f"content-type={response.headers.get('content-type', '')} | "
            f"body={summarize_response_text(response)}"
        )


def build_qq_headers(referer=None, form=False):
    headers = {
        "Referer": referer or QQ_REFERER,
        "Origin": "https://y.qq.com",
    }
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    cookie = get_qq_cookie().strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def build_qq_legacy_headers(referer=None, form=False):
    headers = {
        "Referer": referer or QQ_REFERER,
    }
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    cookie = get_qq_cookie().strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def parse_cookie_pairs(cookie=None):
    cookie = get_qq_cookie() if cookie is None else cookie
    pairs = {}
    for part in str(cookie or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        if key:
            pairs[key] = value
    return pairs


def get_cookie_value(*names):
    pairs = parse_cookie_pairs()
    for name in names:
        value = pairs.get(name)
        if value not in (None, ""):
            return value
    return ""


def hash33(value):
    h = 5381
    for ch in str(value or ""):
        h += (h << 5) + ord(ch)
    return h & 0x7fffffff


def get_qq_g_tk():
    token = get_cookie_value("p_skey", "skey", "qqmusic_key", "qm_keyst")
    if not token:
        return 5381
    return hash33(token)


def get_qq_music_key():
    return get_cookie_value("qqmusic_key", "qm_keyst", "psrf_qqaccess_token")


def get_qq_login_type(musickey=None):
    musickey = musickey if musickey is not None else get_qq_music_key()
    return 1 if str(musickey or "").startswith("W_X") else 2


def build_android_write_comm():
    musicid = to_int(extract_uin_from_cookie(), 0)
    musickey = get_qq_music_key()
    guid = uuid.uuid4().hex
    return {
        "ct": 11,
        "cv": 14090008,
        "v": 14090008,
        "chid": "10003505",
        "qq": str(musicid) if musicid else "",
        "authst": musickey,
        "tmeAppID": "qqmusic",
        "tmeLoginType": get_qq_login_type(musickey),
        "QIMEI": "",
        "QIMEI36": "",
        "OpenUDID": guid,
        "udid": guid,
        "OpenUDID2": guid,
        "aid": guid[:16],
        "os_ver": "13",
        "phonetype": "Pixel 7",
        "devicelevel": "33",
        "newdevicelevel": "33",
        "rom": "qqmusic-api-python-compatible",
    }


def qq_musicu_api_request(
    module,
    method,
    param,
    *,
    preserve_bool=False,
    allowed_ret_codes=None,
    action="QQ 音乐 musicu 接口",
):
    payload = {
        "comm": build_android_write_comm(),
        "req_0": {
            "module": module,
            "method": method,
            "param": param if preserve_bool else bool_to_int_payload(param),
        },
    }
    response = request_json(
        "POST",
        QQ_MUSICU_URL,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "QQMusic 14090008(android 13)",
        },
        timeout=REQUEST_TIMEOUT,
    )
    item = response.get("req_0") if isinstance(response, dict) else None
    if not isinstance(item, dict):
        raise ValueError(f"{action}返回异常：{response}")
    if response_indicates_cookie_expired(item):
        raise CookieExpiredError("QQ 音乐 Cookie 已过期，请先运行：python qq-auto.py --refresh-cookie")
    code = item.get("code")
    code_value = to_int(code, code) if code is not None else None
    data = item.get("data") or {}
    if response_indicates_cookie_expired(data):
        raise CookieExpiredError("QQ 音乐 Cookie 已过期，请先运行：python qq-auto.py --refresh-cookie")
    ret_code = data.get("retCode") if isinstance(data, dict) else None
    ret_code_value = to_int(ret_code, ret_code) if ret_code is not None else None
    if code_value not in (None, 0):
        if code_value == 1000:
            raise CookieExpiredError(
                "QQ 音乐登录态失效（musicu code=1000），请先运行：python qq-auto.py --refresh-cookie"
            )
        raise ValueError(f"{action}失败：module={module}, method={method}, response={item}")
    allowed = {0} if allowed_ret_codes is None else set(allowed_ret_codes)
    if ret_code_value is not None and ret_code_value not in allowed:
        raise ValueError(f"{action}失败：module={module}, method={method}, response={item}")
    return data


def qq_musicu_write_request(module, method, param, *, preserve_bool=False, allowed_ret_codes=None):
    return qq_musicu_api_request(
        module,
        method,
        param,
        preserve_bool=preserve_bool,
        allowed_ret_codes=allowed_ret_codes,
        action="QQ 音乐 musicu 写接口",
    )


def bool_to_int_payload(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return [bool_to_int_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: bool_to_int_payload(item) for key, item in value.items()}
    return value


def request_json(method, url, *, params=None, data=None, headers=None, timeout=None):
    timeout = timeout or REQUEST_TIMEOUT
    last_error = None
    for attempt in range(1, REQUEST_RETRY_COUNT + 1):
        try:
            response = HTTP_SESSION.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
            )
            return parse_json_response(response)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError, ValueError) as exc:
            last_error = exc
            if attempt == REQUEST_RETRY_COUNT:
                break
            time.sleep(REQUEST_RETRY_DELAY)
    raise last_error


def qq_get_json(url, params, referer=None):
    params = dict(params or {})
    params.setdefault("format", "json")
    params.setdefault("inCharset", "utf8")
    params.setdefault("outCharset", "utf-8")
    params.setdefault("g_tk", str(get_qq_g_tk()))
    return request_json("GET", url, params=params, headers=build_qq_headers(referer))


def qq_musicu_request(payload, referer=None):
    params = {
        "format": "json",
        "inCharset": "utf8",
        "outCharset": "utf-8",
        "data": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }
    return qq_get_json(QQ_MUSICU_URL, params, referer=referer)


def clean_song_name(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "_", str(name or "")).strip()


def normalize_album_name(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def album_name_matches(target_name, candidate_name):
    target = normalize_album_name(target_name)
    candidate = normalize_album_name(candidate_name)
    if not target or not candidate:
        return False
    return target == candidate or target in candidate or candidate in target


def extract_version_tag(name):
    matches = re.findall(r"\((.*?)\)|（(.*?)）", str(name or ""))
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
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    version_tag = extract_version_tag(name)
    name = re.sub(r"\(.*?\)|（.*?）", "", name)
    normalized = re.sub(r"\s+", " ", name).strip().lower()
    if version_tag:
        return f"{normalized}__{version_tag}"
    return normalized


def normalize_artist_name(name):
    name = re.sub(r"\s+", "", str(name or "")).strip().lower()
    return re.sub(r"[()（）\\[\\]【】'\"`·._-]", "", name)


def artist_name_matches(target_name, candidate_name):
    target = normalize_artist_name(target_name)
    candidate = normalize_artist_name(candidate_name)
    if not target or not candidate:
        return False
    return target == candidate or candidate.startswith(target) or target.startswith(candidate)


def first_value(obj, keys, default=None):
    if not isinstance(obj, dict):
        return default
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return default


def to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def looks_like_qq_singer_mid(value):
    text = (value or "").strip()
    if text.lower().startswith("mid:"):
        return True
    return bool(re.fullmatch(r"[0-9A-Za-z_-]{10,}", text))


def search_qq_singers(keyword):
    search_referer = f"https://y.qq.com/n/ryqq/search?w={quote(keyword)}"
    smartbox = qq_get_json(
        QQ_SMARTBOX_URL,
        {"key": keyword},
        referer=search_referer,
    )
    singers = []
    smartbox_singers = (((smartbox.get("data") or {}).get("singer") or {}).get("itemlist") or [])
    for item in smartbox_singers:
        mid = first_value(item, ["mid", "singermid", "singer_mid"], "")
        name = first_value(item, ["name", "singer"], "")
        if mid and name:
            singers.append({"mid": str(mid).strip(), "name": str(name).strip(), "raw": item})
    if singers:
        return singers

    try:
        legacy = qq_get_json(
            QQ_SEARCH_URL,
            {
                "w": keyword,
                "p": 1,
                "n": 20,
                "cr": 1,
                "t": 9,
                "remoteplace": "txt.yqq.singer",
            },
            referer=search_referer,
        )
    except Exception:
        return []

    singer_data = (legacy.get("data") or {}).get("singer") or {}
    legacy_singers = singer_data.get("list") or []
    result = []
    for item in legacy_singers:
        mid = first_value(item, ["singermid", "singerMID", "singerMid", "singer_mid", "mid", "Fsinger_mid"], "")
        name = first_value(item, ["singername", "singerName", "name", "Fsinger_name", "singer_name"], "")
        if mid and name:
            result.append({"mid": str(mid).strip(), "name": str(name).strip(), "raw": item})
    return result


def resolve_qq_singer(artist_name, singer_mid=None):
    if singer_mid:
        return {"mid": singer_mid.strip(), "name": artist_name}

    artist_name = (artist_name or "").strip()
    if artist_name.lower().startswith("mid:"):
        mid = artist_name.split(":", 1)[1].strip()
        return {"mid": mid, "name": artist_name}
    if looks_like_qq_singer_mid(artist_name):
        return {"mid": artist_name, "name": artist_name}

    singers = search_qq_singers(artist_name)
    if not singers:
        raise ValueError(f"QQ音乐未找到歌手：{artist_name}")

    target_key = normalize_artist_name(artist_name)
    exact = [
        singer
        for singer in singers
        if normalize_artist_name(singer.get("name", "")) == target_key
        or artist_name_matches(artist_name, singer.get("name", ""))
    ]
    chosen = exact[0] if exact else singers[0]
    log_message(f"已获取 QQ 音乐歌手 MID：{chosen['name']} ({chosen['mid']})")
    return chosen


def extract_qq_album_item(album):
    album_mid = first_value(album, ["album_mid", "albumMid", "albummid", "mid", "Falbum_mid", "albumMID"], "")
    album_id = first_value(album, ["album_id", "albumID", "albumId", "albumid", "id", "Falbum_id"], "")
    album_name = first_value(album, ["album_name", "albumName", "albumname", "name", "Falbum_name"], "")
    publish_date = first_value(
        album,
        ["publish_time", "publishTime", "publish_date", "public_time", "publicTime", "pub_time", "pubTime", "Fpublic_time", "date"],
        "",
    )
    track_count = to_int(
        first_value(
            album,
            ["song_count", "songCount", "song_num", "songNum", "total_song_num", "totalSongNum", "songnum", "Fsong_num"],
            0,
        )
    )
    latest_song = album.get("latest_song")
    if isinstance(latest_song, dict) and not track_count:
        track_count = to_int(latest_song.get("song_count"), 0)
    return {
        "id": str(album_mid or album_id).strip(),
        "album_mid": str(album_mid).strip(),
        "album_id": str(album_id).strip(),
        "name": str(album_name).strip(),
        "publish_time": str(publish_date or "").strip(),
        "size": track_count,
    }


def get_albums(singer_mid, page_size=ALBUM_PAGE_SIZE, max_albums=None):
    result_list = []
    seen = set()
    begin = 0
    total = None
    while True:
        payload = {
            "comm": {"ct": 24, "cv": 10000},
            "singerAlbum": {
                "module": "music.web_singer_info_svr",
                "method": "get_singer_album",
                "param": {
                    "singermid": singer_mid,
                    "order": "time",
                    "begin": begin,
                    "num": page_size,
                    "exstatus": 1,
                },
            },
        }
        response = qq_musicu_request(payload, referer=f"https://y.qq.com/n/ryqq/singer/{singer_mid}")
        data = ((response.get("singerAlbum") or {}).get("data") or {})
        albums = data.get("list") or data.get("albumList") or []
        if total is None:
            total = to_int(first_value(data, ["total", "totalNum", "total_num"], len(albums)), len(albums))
        if not albums:
            break
        for raw_album in albums:
            album = extract_qq_album_item(raw_album)
            if not album["id"] or not album["name"] or album["id"] in seen:
                continue
            seen.add(album["id"])
            result_list.append(album)
            if max_albums and len(result_list) >= max_albums:
                return result_list
        begin += len(albums)
        if begin >= (total or 0) or len(albums) < page_size:
            break
    return result_list


def extract_song_info(song_item):
    return (
        song_item.get("songInfo")
        or song_item.get("song_info")
        or song_item.get("song")
        or song_item
    )


def normalize_singer_mid(value):
    text = str(value or "").strip()
    if text.lower().startswith("mid:"):
        return text.split(":", 1)[1].strip()
    return text


def extract_qq_song(song_item, album, artist_name, singer_mid=None):
    song = extract_song_info(song_item)
    name = clean_song_name(first_value(song, ["name", "songname", "songName", "title", "songorig", "songOrig"], ""))
    song_mid = first_value(song, ["mid", "songmid", "songMid"], "")
    song_id = first_value(song, ["id", "songid", "songId"], "")
    song_type = to_int(first_value(song, ["type", "songtype", "songType"], 0), 0)
    singers = song.get("singer") or song.get("singers") or []
    source_artists = []
    source_mids = []
    if isinstance(singers, list):
        source_artists = [clean_song_name(first_value(item, ["name", "singer_name", "title"], "")) for item in singers]
        source_artists = [item for item in source_artists if item]
        source_mids = [normalize_singer_mid(first_value(item, ["mid", "singer_mid", "singerMid"], "")) for item in singers]
        source_mids = [item for item in source_mids if item]
    target_mid = normalize_singer_mid(singer_mid or artist_name)
    has_name_match = any(artist_name_matches(artist_name, item) for item in source_artists)
    has_mid_match = bool(target_mid and target_mid in source_mids)
    if (source_artists or source_mids) and not (has_name_match or has_mid_match):
        return None
    if not name or not song_mid:
        return None
    album_obj = song.get("album") if isinstance(song.get("album"), dict) else {}
    album_name = clean_song_name(first_value(album_obj, ["name", "title"], "")) or album["name"]
    album_mid = first_value(album_obj, ["mid"], "") or album.get("album_mid") or album["id"]
    publish_time = first_value(song, ["time_public", "pub_time"], "") or album.get("publish_time", "")
    return {
        "name": name,
        "artist": artist_name,
        "source_artists": source_artists,
        "song_mid": str(song_mid),
        "song_id": str(song_id),
        "song_type": song_type,
        "album": album_name,
        "album_id": album_mid,
        "album_song_count": album.get("size", 0),
        "album_publish_time": publish_time,
    }


def get_album_songs(album, artist_name, singer_mid=None):
    payload = {
        "comm": {"ct": 24, "cv": 10000},
        "albumSonglist": {
            "module": "music.musichallAlbum.AlbumSongList",
            "method": "GetAlbumSongList",
            "param": {
                "albumMid": album.get("album_mid") or "",
                "albumID": to_int(album.get("album_id")),
                "begin": 0,
                "num": 999,
                "order": 2,
            },
        },
    }
    response = qq_musicu_request(
        payload,
        referer=f"https://y.qq.com/n/ryqq/albumDetail/{album.get('album_mid')}" if album.get("album_mid") else QQ_REFERER,
    )
    data = ((response.get("albumSonglist") or {}).get("data") or {})
    songs = data.get("songList") or data.get("songlist") or data.get("list") or []
    album_count = to_int(first_value(data, ["totalNum", "total_num", "total"], 0), 0) or len(songs) or album.get("size", 0)
    album = dict(album)
    album["size"] = album_count
    result_list = []
    for item in songs:
        song = extract_qq_song(item, album, artist_name, singer_mid=singer_mid)
        if song:
            song["album_song_count"] = album_count
            result_list.append(song)
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


def fetch_all_songs_from_albums(albums, artist_name, singer_mid=None):
    total_albums = len(albums)
    log_message(f"已获取 QQ 音乐专辑列表，共 {total_albums} 张，开始抓取歌曲...")
    all_songs = []
    for index, album in enumerate(albums, start=1):
        if (
            index == 1
            or index == total_albums
            or index % ALBUM_PROGRESS_LOG_INTERVAL == 0
        ):
            log_message(f"抓取专辑进度：{index}/{total_albums} - {album['name']}")
        all_songs.extend(get_album_songs(album, artist_name, singer_mid=singer_mid))
    return dedupe_songs(all_songs)


def fetch_all_songs(singer_mid, artist_name, page_size=ALBUM_PAGE_SIZE, max_albums=None):
    albums = get_albums(singer_mid, page_size=page_size, max_albums=max_albums)
    return fetch_all_songs_from_albums(albums, artist_name, singer_mid=singer_mid)


def response_indicates_cookie_expired(response):
    if not isinstance(response, dict):
        return False

    def status_is(value, statuses):
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value in statuses
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"-?\d+", value):
                return int(value) in statuses
        return False

    status_values = [
        response.get(key)
        for key in ("code", "result", "retCode", "retcode", "subcode")
    ]
    message = " ".join(
        str(response.get(key, ""))
        for key in ("message", "msg", "errMsg", "error", "retmsg")
    ).lower()
    if any(status_is(value, {301, 401}) for value in status_values):
        return True
    if any(status_is(value, {403}) for value in status_values):
        return any(keyword in message for keyword in ["登录", "登陆", "cookie", "auth", "token", "未登录", "未登陆"])
    # musicu/部分接口仅以 code=1000、且无 message 字段表示会话失效或未登录
    if any(status_is(value, {1000}) for value in status_values):
        auth_hints = ["登录", "登陆", "cookie", "auth", "token", "未登录", "未登陆"]
        return (not message.strip()) or any(keyword in message for keyword in auth_hints)
    keywords = ["登录", "登陆", "失效", "过期", "cookie", "先登录", "未登陆", "未登录"]
    return any(keyword in message for keyword in keywords)


def ensure_valid_qq_response(response, action="QQ 音乐操作"):
    if response_indicates_cookie_expired(response):
        raise CookieExpiredError("QQ 音乐 Cookie 已过期，请先运行：python qq-auto.py --refresh-cookie")
    code = response.get("code") if isinstance(response, dict) else None
    if code not in (None, 0):
        raise ValueError(f"{action}失败，QQ 音乐接口返回：{response}")


def extract_uin_from_cookie():
    pairs = parse_cookie_pairs()
    for key in ("qqmusic_uin", "uin", "wxuin", "p_uin", "euin"):
        value = pairs.get(key)
        if value:
            return re.sub(r"^o(?=\d+$)", "", value)
    return "0"


def ensure_cookie_ready():
    cookie = get_qq_cookie().strip()
    if not cookie:
        raise CookieExpiredError("QQ 音乐 Cookie 为空，请先运行：python qq-auto.py --refresh-cookie")
    if not to_int(extract_uin_from_cookie(), 0) or not get_qq_music_key().strip():
        raise CookieExpiredError("QQ 音乐 Cookie 缺少 uin 或 qqmusic_key，请先运行：python qq-auto.py --refresh-cookie")
    qq_musicu_api_request(
        "music.UserInfo.userInfoServer",
        "GetLoginUserInfo",
        {},
        action="验证 QQ 音乐登录",
    )


def create_qq_playlist(name):
    data = qq_musicu_write_request(
        "music.musicasset.PlaylistBaseWrite",
        "AddPlaylist",
        {"dirName": name},
    )
    result = data.get("result") if isinstance(data, dict) else {}
    dirid = first_value(result, ["dirId", "dirid"], "")
    tid = first_value(result, ["tid", "id"], "")
    actual_name = first_value(result, ["dirName", "name"], name)
    if not dirid:
        raise ValueError(f"QQ 音乐歌单创建失败：{data}")
    return {
        "dirid": str(dirid),
        "tid": str(tid or dirid),
        "name": str(actual_name or name),
    }


def get_qq_playlist_song_mids(playlist_id):
    response = request_json(
        "GET",
        QQ_PLAYLIST_MAP_URL,
        params={"dirid": playlist_id, "dirinfo": 1, "g_tk": QQ_LEGACY_G_TK, "format": "json", "_t": int(time.time() * 1000)},
        headers=build_qq_legacy_headers("https://y.qq.com/n/yqq/playlist"),
    )
    ensure_valid_qq_response(response, action="读取 QQ 音乐歌单")
    mapmid = response.get("mapmid") or response.get("mid") or {}
    if isinstance(mapmid, dict):
        return {str(value) for value in mapmid.values() if value}
    if isinstance(mapmid, list):
        return {str(value) for value in mapmid if value}
    return set()


def wait_for_qq_playlist_ready(playlist_id, playlist_tid=0):
    last_error = None
    for attempt in range(1, QQ_PLAYLIST_READY_RETRY + 1):
        try:
            get_qq_playlist_song_ids(playlist_id, playlist_tid=playlist_tid)
            return
        except Exception as exc:
            last_error = exc
            if attempt < QQ_PLAYLIST_READY_RETRY:
                log_message(f"QQ 音乐歌单尚未就绪，等待 {QQ_PLAYLIST_READY_DELAY} 秒后重试 ({attempt}/{QQ_PLAYLIST_READY_RETRY})")
                time.sleep(QQ_PLAYLIST_READY_DELAY)
    if last_error:
        raise last_error


def add_qq_batch(playlist_id, batch, playlist_tid=0):
    data = qq_musicu_write_request(
        "music.musicasset.PlaylistDetailWrite",
        "AddSonglist",
        {
            "dirId": to_int(playlist_id),
            "tid": to_int(playlist_tid, 0),
            "bFmtUtf8": True,
            "v_songInfo": [
                {
                    "songId": to_int(item["song_id"]),
                    "songType": to_int(item.get("song_type"), 0),
                }
                for item in batch
            ],
        },
        preserve_bool=True,
        allowed_ret_codes={0, 80092},
    )
    return data


def get_missing_song_mids(target_mids, playlist_mids):
    return [song_mid for song_mid in target_mids if song_mid not in playlist_mids]


def extract_qq_playlist_song_ids_from_detail(data):
    songlist = []
    if isinstance(data, dict):
        songlist = data.get("songlist") or data.get("songList") or data.get("songs") or []
    result = set()
    for item in songlist:
        song = extract_song_info(item) if isinstance(item, dict) else {}
        song_id = first_value(song, ["id", "songid", "songId"], "")
        if song_id:
            result.add(str(song_id))
    return result


def get_qq_playlist_song_ids(playlist_id, playlist_tid=0):
    if to_int(playlist_tid, 0):
        data = qq_musicu_api_request(
            "music.srfDissInfo.DissInfo",
            "CgiGetDiss",
            {
                "disstid": to_int(playlist_tid),
                "dirid": to_int(playlist_id),
                "tag": True,
                "song_begin": 0,
                "song_num": 9999,
                "userinfo": True,
                "orderlist": True,
                "onlysonglist": True,
            },
            preserve_bool=True,
            action="读取 QQ 音乐歌单",
        )
        return extract_qq_playlist_song_ids_from_detail(data)

    response = request_json(
        "GET",
        QQ_PLAYLIST_MAP_URL,
        params={"dirid": playlist_id, "dirinfo": 1, "g_tk": QQ_LEGACY_G_TK, "format": "json", "_t": int(time.time() * 1000)},
        headers=build_qq_legacy_headers("https://y.qq.com/n/yqq/playlist"),
    )
    ensure_valid_qq_response(response, action="读取 QQ 音乐歌单")
    song_map = response.get("map") or {}
    if isinstance(song_map, dict):
        return {str(value) for value in song_map.values() if value}
    if isinstance(song_map, list):
        return {str(value) for value in song_map if value}
    return set()


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
                "publish_time": song.get("album_publish_time", ""),
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
        key=lambda item: (str(item["publish_time"]), item["name"], item["identity"]),
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
        f"未添加到 QQ 音乐歌单：{len(missing_reason_lines)}",
        "",
    ]
    lines.extend(missing_reason_lines)
    return "\n".join(lines).rstrip() + "\n"


def write_missing_report(artist_name, missing_reason_lines, output_directory=None):
    if not missing_reason_lines:
        return ""
    output_directory = output_directory or os.getcwd()
    os.makedirs(output_directory, exist_ok=True)
    path = os.path.join(output_directory, f"{sanitize_filename(artist_name)}_未添加QQ音乐报告.txt")
    with open(path, "w", encoding="utf-8") as file:
        file.write(build_missing_report_text(artist_name, missing_reason_lines))
    return os.path.abspath(path)


def add_qq_songs(playlist_id, songs, playlist_tid=0):
    selected_song_by_id = {}
    target_items = []
    missing_reason_lines = []

    for song in songs:
        song_id = str(song.get("song_id") or "").strip()
        if not song_id:
            missing_reason_lines.append(f"{song['name']} | {song['album']} | 缺少 QQ 音乐 song id")
            continue
        if song_id in selected_song_by_id:
            continue
        selected_song_by_id[song_id] = song
        target_items.append(
            {
                "song_id": song_id,
                "song_type": to_int(song.get("song_type"), 0),
            }
        )

    target_ids = [item["song_id"] for item in target_items]
    log_message(f"开始添加到 QQ 音乐歌单，共 {len(target_items)} 首...")
    unadded_ids = []
    total_batches = (len(target_items) + QQ_ADD_BATCH_SIZE - 1) // QQ_ADD_BATCH_SIZE
    for batch_index, index in enumerate(range(0, len(target_items), QQ_ADD_BATCH_SIZE), start=1):
        batch = target_items[index:index + QQ_ADD_BATCH_SIZE]
        log_message(f"QQ 音乐添加 {index + 1} ~ {index + len(batch)}")
        remaining = list(batch)
        for _ in range(1, QQ_ADD_RETRY + 1):
            add_qq_batch(playlist_id, remaining, playlist_tid=playlist_tid)
            time.sleep(QQ_ADD_RETRY_DELAY)
            playlist_ids = get_qq_playlist_song_ids(playlist_id, playlist_tid=playlist_tid)
            remaining_ids = set(get_missing_song_mids([item["song_id"] for item in remaining], playlist_ids))
            remaining = [item for item in remaining if item["song_id"] in remaining_ids]
            if not remaining:
                break
        if remaining:
            unadded_ids.extend(item["song_id"] for item in remaining)
        elif batch_index < total_batches and QQ_NEXT_BATCH_STABILIZE_DELAY > 0:
            log_message(f"QQ 音乐等待 {QQ_NEXT_BATCH_STABILIZE_DELAY} 秒后继续下一批")
            time.sleep(QQ_NEXT_BATCH_STABILIZE_DELAY)

    for song_id in sorted(set(unadded_ids)):
        song = selected_song_by_id.get(song_id, {})
        missing_reason_lines.append(f"{song.get('name', song_id)} | {song.get('album', '')} | 已请求添加但歌单未出现")

    unadded_id_set = set(unadded_ids)
    added_song_records = [
        selected_song_by_id[song_id]
        for song_id in target_ids
        if song_id not in unadded_id_set
    ]

    return {
        "target_count": len(target_ids),
        "added_song_count": len(added_song_records),
        "unadded_song_mids": sorted(set(unadded_ids)),
        "added_album_summaries": build_album_summaries(added_song_records),
        "missing_reason_lines": missing_reason_lines,
    }


def create_unique_playlist(base_name):
    try:
        playlist = create_qq_playlist(base_name)
        return playlist, playlist.get("name") or base_name
    except ValueError as exc:
        if "歌单名已存在" not in str(exc):
            raise
    unique_name = f"{base_name}_{time.strftime('%Y%m%d-%H%M%S')}"
    log_message(f"QQ 音乐歌单名已存在，改用：{unique_name}")
    playlist = create_qq_playlist(unique_name)
    return playlist, playlist.get("name") or unique_name


def run_qq(artist_name, songs, playlist_name=None, output_directory=None):
    ensure_cookie_ready()
    playlist_name = playlist_name or build_playlist_name(artist_name, "全专辑")
    log_message("创建 QQ 音乐歌单...")
    playlist, actual_playlist_name = create_unique_playlist(playlist_name)
    playlist_dirid = playlist["dirid"]
    playlist_tid = playlist.get("tid") or playlist_dirid
    log_message(f"QQ 音乐歌单名称：{actual_playlist_name}")
    wait_for_qq_playlist_ready(playlist_dirid, playlist_tid=playlist_tid)
    add_result = add_qq_songs(playlist_dirid, songs, playlist_tid=playlist_tid)
    playlist_url = f"https://y.qq.com/n/ryqq/playlist/{playlist_tid}"
    log_message(f"QQ 音乐：{playlist_url}")
    report_path = write_missing_report(
        artist_name,
        add_result["missing_reason_lines"],
        output_directory=output_directory,
    )
    return QQRunResult(
        playlist_id=str(playlist_tid),
        playlist_url=playlist_url,
        target_count=add_result["target_count"],
        added_song_count=add_result["added_song_count"],
        unadded_song_mids=add_result["unadded_song_mids"],
        added_album_summaries=add_result["added_album_summaries"],
        missing_report_path=report_path,
        missing_reason_lines=add_result["missing_reason_lines"],
    )


def refresh_cookie_interactive(cookie_path=None):
    path = os.environ.get("QQ_MUSIC_COOKIE_FILE", get_cookie_store_path(cookie_path))
    if cookie_path:
        path = get_cookie_store_path(cookie_path)
    path = os.path.abspath(path)
    os.environ["QQ_MUSIC_COOKIE_FILE"] = path

    cookie = input("请粘贴新的 QQ 音乐 Cookie 后按 Enter：\n").strip()
    if not cookie:
        raise ValueError("未输入 QQ Cookie，已取消更新。")
    set_qq_cookie(cookie, persist=True, cookie_path=path)
    print(f"QQ Cookie 已保存：{path}")
    return path


def run_workflow(
    artist_name,
    *,
    execute_qq=True,
    qq_playlist_name=None,
    output_directory=None,
    log_callback=None,
    verbose_details=False,
    singer_mid=None,
    page_size=ALBUM_PAGE_SIZE,
    max_albums=None,
):
    configure_runtime(log_callback=log_callback, verbose_details=verbose_details)
    log_message("获取 QQ 音乐歌手 MID...")
    singer = resolve_qq_singer(artist_name, singer_mid=singer_mid)
    songs = fetch_all_songs(singer["mid"], artist_name, page_size=page_size, max_albums=max_albums)
    album_summaries = build_album_summaries(songs)
    result = WorkflowResult(
        artist_name=artist_name,
        singer_mid=singer["mid"],
        total_songs=len(songs),
        album_summaries=album_summaries,
    )
    if execute_qq:
        result.qq = run_qq(
            artist_name,
            songs,
            playlist_name=qq_playlist_name,
            output_directory=output_directory,
        )
    return result


def build_result_text(result):
    lines = [
        f"歌手：{result.artist_name}",
        f"QQ 音乐 singer MID：{result.singer_mid}",
        f"专辑总数：{len(result.album_summaries)}",
        f"专辑曲目总数：{result.total_songs}",
        "",
    ]
    if result.qq:
        lines.extend(
            [
                "QQ 音乐结果：",
                f"歌单链接：{result.qq.playlist_url}",
                f"准备添加：{result.qq.target_count}",
                f"已添加歌曲数量：{result.qq.added_song_count}",
                f"未添加：{len(result.qq.unadded_song_mids)}",
                f"已添加专辑总数：{len(result.qq.added_album_summaries)}",
            ]
        )
    return "\n".join(lines)


def launch_gui():
    gui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qq_gui_app.py")
    if not os.path.exists(gui_path):
        raise FileNotFoundError("未找到 qq_gui_app.py，无法打开可视化界面。")
    spec = importlib.util.spec_from_file_location("qq_gui_app_launcher", gui_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.main()


def main():
    parser = argparse.ArgumentParser(description="按歌手抓取 QQ 音乐全部专辑歌曲并导入 QQ 音乐歌单")
    parser.add_argument("artist_name", nargs="?", help="歌手名；也可填 mid:歌手MID")
    parser.add_argument("--singer-mid", help="直接指定 QQ 音乐 singer MID，避免搜索错歌手")
    parser.add_argument("--playlist-name", help="自定义 QQ 音乐歌单名")
    parser.add_argument("--output-directory", help="未添加报告输出目录")
    parser.add_argument("--cookie-path", help="QQ 音乐 Cookie 文件路径，默认 qq_music_cookie.txt")
    parser.add_argument("--refresh-cookie", action="store_true", help="手动输入并保存 QQ 音乐 Cookie")
    parser.add_argument("--dry-run", action="store_true", help="只抓取歌手专辑歌曲，不创建歌单、不添加歌曲")
    parser.add_argument("--page-size", type=int, default=ALBUM_PAGE_SIZE, help="歌手专辑分页大小")
    parser.add_argument("--max-albums", type=int, help="最多抓取多少张专辑，用于测试")
    parser.add_argument("--gui", action="store_true", help="打开 QQ 音乐可视化界面")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    args = parser.parse_args()

    if args.gui or len(sys.argv) == 1:
        launch_gui()
        return

    if args.cookie_path:
        os.environ["QQ_MUSIC_COOKIE_FILE"] = os.path.abspath(args.cookie_path)
    set_qq_cookie(load_qq_cookie(args.cookie_path))

    if args.refresh_cookie:
        refresh_cookie_interactive(args.cookie_path)
        if not args.artist_name:
            return

    if not args.artist_name:
        parser.error("请提供 artist_name；如果只是更新 Cookie，可只运行 --refresh-cookie")

    result = run_workflow(
        args.artist_name,
        execute_qq=not args.dry_run,
        qq_playlist_name=args.playlist_name,
        output_directory=args.output_directory,
        log_callback=None,
        verbose_details=args.verbose,
        singer_mid=args.singer_mid,
        page_size=max(1, args.page_size),
        max_albums=args.max_albums,
    )
    print(build_result_text(result))


set_qq_cookie(load_qq_cookie())


if __name__ == "__main__":
    main()
