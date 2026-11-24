"""
X → Instagram 自動転載bot

自分のXの投稿のうち「写真または動画が付いたもの」だけをInstagramに転載します。
リポスト・引用リポスト・リプライは転載しません。

環境変数（GitHub Actions の Secrets に登録）:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
  IG_USER_ID, IG_ACCESS_TOKEN
任意:
  DRY_RUN=1   … Instagramには投稿せず、何が投稿されるかだけ表示
"""

from __future__ import annotations

import html
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from requests_oauthlib import OAuth1

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
MEDIA_DIR = ROOT / "media"

JST = timezone(timedelta(hours=9))
X_API = "https://api.x.com/2"
IG_CAPTION_MAX = 2200
IG_CAROUSEL_MAX = 10
# Instagramのフィード画像が受け付ける縦横比（幅÷高さ）
IG_MIN_RATIO = 4 / 5      # 縦長の限界 4:5
IG_MAX_RATIO = 1.91       # 横長の限界 1.91:1
MAX_ATTEMPTS = 3
HTTP_TIMEOUT = 60


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- 設定・状態

def load_json(path: pathlib.Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: pathlib.Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_paused(config: dict, now: datetime) -> bool:
    """config.json の paused が true、または今日（日本時間）が pause_dates に入っていれば停止。"""
    if config.get("paused"):
        return True
    return now.astimezone(JST).strftime("%Y-%m-%d") in set(config.get("pause_dates", []))


# ---------------------------------------------------------------- X

def x_auth() -> OAuth1:
    return OAuth1(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def x_get(path: str, auth, params: dict | None = None) -> dict:
    r = requests.get(f"{X_API}{path}", auth=auth, params=params, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"X API error {r.status_code}: {r.text[:500]}")
    return r.json()


def get_my_user_id(auth, state: dict) -> str:
    if state.get("x_user_id"):
        return state["x_user_id"]
    data = x_get("/users/me", auth)
    state["x_user_id"] = data["data"]["id"]
    log(f"Xのユーザー: @{data['data'].get('username')} (id {state['x_user_id']})")
    return state["x_user_id"]


def fetch_posts(auth, user_id: str, since_id: str | None, max_results: int = 20, max_pages: int = 5):
    """since_id より新しい自分の投稿を取得（リポスト・リプライはAPI側で除外）。
    戻り値: (投稿リスト 古い順, media_key→media の辞書)"""
    params = {
        "max_results": max_results,
        "exclude": "retweets,replies",
        "tweet.fields": "created_at,referenced_tweets,attachments,entities,note_tweet",
        "expansions": "attachments.media_keys",
        "media.fields": "type,url,variants,width,height,preview_image_url",
    }
    if since_id:
        params["since_id"] = since_id
    tweets, media = [], {}
    for _ in range(max_pages):
        data = x_get(f"/users/{user_id}/tweets", auth, params)
        tweets.extend(data.get("data", []))
        for m in data.get("includes", {}).get("media", []):
            media[m["media_key"]] = m
        token = data.get("meta", {}).get("next_token")
        if not token or not since_id:
            break
        params["pagination_token"] = token
    tweets.sort(key=lambda t: int(t["id"]))
    return tweets, media


def eligible_media(tweet: dict, media_map: dict) -> list[dict]:
    """転載対象なら添付メディアのリストを返す。対象外なら空リスト。"""
    # 引用・リポスト・リプライは除外
    if tweet.get("referenced_tweets"):
        return []
    keys = tweet.get("attachments", {}).get("media_keys", [])
    items = [media_map[k] for k in keys if k in media_map]
    items = [m for m in items if m.get("type") in ("photo", "video", "animated_gif")]
    return items[:IG_CAROUSEL_MAX]


def build_caption(tweet: dict, footer: str = "") -> str:
    note = tweet.get("note_tweet") or {}
    text = note.get("text") or tweet.get("text", "")
    entities = note.get("entities") or tweet.get("entities") or {}
    for u in entities.get("urls", []):
        short = u.get("url")
        if not short:
            continue
        expanded = u.get("expanded_url", "")
        # 添付メディア自体へのリンク（pic.x.com 等）は消す。その他のリンクは元のURLに戻す
        if u.get("media_key") or re.search(r"(twitter|x)\.com/[^/]+/status/\d+/(photo|video)/", expanded):
            text = text.replace(short, "")
        else:
            text = text.replace(short, expanded or short)
    text = html.unescape(text).strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    if footer:
        text = f"{text}\n\n{footer}".strip()
    return text[:IG_CAPTION_MAX]


def photo_url(media: dict) -> str:
    base = re.sub(r"\.(jpg|jpeg|png|webp)$", "", media["url"])
    return f"{base}?format=jpg&name=large"


def best_video_url(media: dict) -> str:
    mp4s = [v for v in media.get("variants", []) if v.get("content_type") == "video/mp4"]
    if not mp4s:
        raise RuntimeError(f"動画のmp4が見つかりません: {media.get('media_key')}")
    return max(mp4s, key=lambda v: v.get("bit_rate", 0))["url"]


# ---------------------------------------------------------------- 画像の縦横比調整

def needs_padding(media: dict) -> bool:
    w, h = media.get("width"), media.get("height")
    if not w or not h:
        return False
    ratio = w / h
    return ratio < IG_MIN_RATIO or ratio > IG_MAX_RATIO


def pad_image(data: bytes, color: str = "#FFFFFF") -> bytes:
    """Instagramの許容範囲に収まるよう、余白を足して縦横比を調整（画像は切らない）。"""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    ratio = w / h
    if ratio < IG_MIN_RATIO:          # 縦長すぎ → 左右に余白
        new_w, new_h = int(round(h * IG_MIN_RATIO)) + 1, h
    elif ratio > IG_MAX_RATIO:        # 横長すぎ → 上下に余白
        new_w, new_h = w, int(round(w / IG_MAX_RATIO)) + 1
    else:
        new_w, new_h = w, h
    canvas = Image.new("RGB", (new_w, new_h), color)
    canvas.paste(img, ((new_w - w) // 2, (new_h - h) // 2))
    canvas.thumbnail((2048, 2048))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


def publish_to_repo(paths: list[pathlib.Path]) -> None:
    """加工した画像をリポジトリにpushし、Instagramが取りに来られる公開URLにする。"""
    rel = [str(p.relative_to(ROOT)) for p in paths]
    subprocess.run(["git", "add", *rel], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-m", "Add padded images for Instagram"], cwd=ROOT, check=True)
    subprocess.run(["git", "pull", "--rebase"], cwd=ROOT, check=True)
    subprocess.run(["git", "push"], cwd=ROOT, check=True)
    time.sleep(10)  # raw.githubusercontent.com への反映待ち


def raw_url(path: pathlib.Path) -> str:
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path.relative_to(ROOT).as_posix()}"


def resolve_image_urls(tweet_id: str, items: list[dict], config: dict, dry_run: bool) -> dict:
    """写真ごとにInstagramへ渡すURLを決める。縦横比が範囲外なら余白を足した画像を用意する。"""
    urls, to_push = {}, []
    for i, m in enumerate(items):
        if m["type"] != "photo":
            continue
        if not needs_padding(m):
            urls[m["media_key"]] = photo_url(m)
            continue
        path = MEDIA_DIR / f"{tweet_id}_{i}.jpg"
        if dry_run:
            urls[m["media_key"]] = f"(余白を足した画像) {path.name}"
            continue
        r = requests.get(photo_url(m), timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        MEDIA_DIR.mkdir(exist_ok=True)
        path.write_bytes(pad_image(r.content, config.get("pad_color", "#FFFFFF")))
        to_push.append(path)
        urls[m["media_key"]] = raw_url(path)
    if to_push:
        publish_to_repo(to_push)
    return urls


# ---------------------------------------------------------------- Instagram

class Instagram:
    def __init__(self, user_id: str, token: str, version: str = "v23.0"):
        self.user_id = user_id
        self.token = token
        self.base = f"https://graph.instagram.com/{version}"

    def _post(self, path: str, data: dict) -> dict:
        r = requests.post(f"{self.base}{path}", data={**data, "access_token": self.token}, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"Instagram API error {r.status_code}: {r.text[:500]}")
        return r.json()

    def _get(self, path: str, params: dict) -> dict:
        r = requests.get(f"{self.base}{path}", params={**params, "access_token": self.token}, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"Instagram API error {r.status_code}: {r.text[:500]}")
        return r.json()

    def create(self, **params) -> str:
        return self._post(f"/{self.user_id}/media", params)["id"]

    def wait_ready(self, container_id: str, timeout: int = 600) -> None:
        deadline = time.time() + timeout
        delay = 3
        while time.time() < deadline:
            info = self._get(f"/{container_id}", {"fields": "status_code,status"})
            code = info.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagramでの処理に失敗: {info}")
            time.sleep(delay)
            delay = min(delay * 2, 20)
        raise RuntimeError("Instagramでの動画処理がタイムアウトしました")

    def publish(self, container_id: str) -> str:
        return self._post(f"/{self.user_id}/media_publish", {"creation_id": container_id})["id"]

    def post(self, items: list[dict], image_urls: dict, caption: str) -> str:
        if len(items) == 1:
            m = items[0]
            if m["type"] == "photo":
                cid = self.create(image_url=image_urls[m["media_key"]], caption=caption)
            else:
                cid = self.create(media_type="REELS", video_url=best_video_url(m),
                                  caption=caption, share_to_feed="true")
            self.wait_ready(cid)
            return self.publish(cid)

        children = []
        for m in items:
            if m["type"] == "photo":
                c = self.create(image_url=image_urls[m["media_key"]], is_carousel_item="true")
            else:
                c = self.create(media_type="VIDEO", video_url=best_video_url(m), is_carousel_item="true")
            self.wait_ready(c)
            children.append(c)
        cid = self.create(media_type="CAROUSEL", children=",".join(children), caption=caption)
        self.wait_ready(cid)
        return self.publish(cid)


# ---------------------------------------------------------------- メイン処理

def describe(items: list[dict]) -> str:
    kinds = {"photo": "写真", "video": "動画", "animated_gif": "GIF"}
    return "・".join(kinds.get(m["type"], m["type"]) for m in items)


def run(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    config = load_json(CONFIG_PATH, {})
    state = load_json(STATE_PATH, {})
    dry_run = os.environ.get("DRY_RUN") == "1"
    paused = is_paused(config, now)

    auth = x_auth()
    user_id = get_my_user_id(auth, state)
    since_id = state.get("since_id")

    # 初回：過去の投稿をまとめて転載しないよう、最新の投稿IDを記録するだけで終わる
    if not since_id:
        tweets, _ = fetch_posts(auth, user_id, None, max_results=5)
        if tweets:
            state["since_id"] = tweets[-1]["id"]
        save_json(STATE_PATH, state)
        log(f"初回実行：基準点を記録しました（since_id={state.get('since_id')}）。これ以降の投稿から転載します。")
        return 0

    tweets, media_map = fetch_posts(auth, user_id, since_id)
    log(f"新しい投稿: {len(tweets)}件" + ("（一時停止中）" if paused else ""))

    ig = None
    if not dry_run and not paused:
        ig = Instagram(os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"],
                       config.get("graph_api_version", "v23.0"))

    posted = state.setdefault("posted", {})
    attempts = state.setdefault("attempts", {})
    failed = False
    max_per_run = int(config.get("max_posts_per_run", 5))
    done_this_run = 0

    for t in tweets:
        tid = t["id"]
        if tid in posted:
            state["since_id"] = tid
            continue

        items = eligible_media(t, media_map)
        if not items:
            log(f"スキップ {tid}: 写真・動画なし、または引用/リプライ")
            state["since_id"] = tid
            continue

        if paused:
            # 停止中の投稿は、再開後にも転載しない（投票日の投稿が後から流れるのを防ぐ）
            log(f"一時停止中のため転載しません {tid}")
            state["since_id"] = tid
            continue

        if done_this_run >= max_per_run:
            log("1回あたりの上限に達したので、残りは次回に回します")
            break

        caption = build_caption(t, config.get("caption_footer", ""))
        try:
            urls = resolve_image_urls(tid, items, config, dry_run)
            if dry_run:
                log(f"[DRY RUN] 転載予定 {tid}（{describe(items)}）\n---\n{caption}\n---")
                for m in items:
                    log("   " + (urls.get(m["media_key"]) or best_video_url(m)))
                state["since_id"] = tid
                continue
            ig_id = ig.post(items, urls, caption)
            posted[tid] = ig_id
            attempts.pop(tid, None)
            state["since_id"] = tid
            done_this_run += 1
            log(f"転載しました {tid} → Instagram {ig_id}（{describe(items)}）")
        except Exception as e:  # noqa: BLE001
            failed = True
            attempts[tid] = attempts.get(tid, 0) + 1
            log(f"失敗 {tid}（{attempts[tid]}回目）: {e}")
            if attempts[tid] >= MAX_ATTEMPTS:
                log(f"{MAX_ATTEMPTS}回失敗したのでこの投稿は諦めます {tid}")
                state.setdefault("gave_up", []).append(tid)
                attempts.pop(tid, None)
                state["since_id"] = tid
                continue
            break  # 順番を守るため、次回この投稿からやり直す

    # 状態ファイルが大きくなりすぎないよう古い記録を整理
    state["posted"] = dict(sorted(posted.items(), key=lambda kv: int(kv[0]))[-300:])
    state["gave_up"] = state.get("gave_up", [])[-100:]
    if not dry_run:
        save_json(STATE_PATH, state)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
