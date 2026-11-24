import io
import json
import sys
import pathlib
from datetime import datetime, timezone

import pytest
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import bot  # noqa: E402


# ------------------------------------------------------------ フィクスチャ

def photo(key, w=1200, h=1200):
    return {"media_key": key, "type": "photo", "url": f"https://pbs.twimg.com/media/{key}.jpg", "width": w, "height": h}


def video(key):
    return {"media_key": key, "type": "video", "width": 1080, "height": 1920, "variants": [
        {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/x.m3u8"},
        {"content_type": "video/mp4", "bit_rate": 832000, "url": "https://video.twimg.com/low.mp4"},
        {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://video.twimg.com/high.mp4"},
    ]}


class FakeResp:
    def __init__(self, data, status=200, content=b""):
        self._data, self.status_code, self.content = data, status, content
        self.text = json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]:
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("IG_USER_ID", "IG1")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "nami/x2ig")
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(bot, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(bot, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(bot, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(bot, "ROOT", tmp_path)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    bot.save_json(tmp_path / "config.json", {"pause_dates": ["2027-04-25"]})
    return tmp_path


class FakeAPI:
    """XとInstagramのAPIを真似る"""

    def __init__(self, tweets, media, fail_publish=0):
        self.tweets, self.media = tweets, media
        self.ig_calls = []
        self.fail_publish = fail_publish
        self.n = 0

    def get(self, url, params=None, **kw):
        if url.endswith("/users/me"):
            return FakeResp({"data": {"id": "42", "username": "nami"}})
        if "/users/42/tweets" in url:
            since = int(params.get("since_id") or 0)
            ts = [t for t in self.tweets if int(t["id"]) > since]
            ts = sorted(ts, key=lambda t: -int(t["id"]))[: params["max_results"]]
            return FakeResp({"data": ts, "includes": {"media": self.media}, "meta": {}})
        if "graph.instagram.com" in url:
            return FakeResp({"status_code": "FINISHED"})
        if "pbs.twimg.com" in url:
            buf = io.BytesIO()
            Image.new("RGB", (1000, 2000), "red").save(buf, "JPEG")
            return FakeResp({}, content=buf.getvalue())
        raise AssertionError(url)

    def post(self, url, data=None, **kw):
        self.ig_calls.append((url, dict(data)))
        if url.endswith("/media_publish"):
            if self.fail_publish:
                self.fail_publish -= 1
                return FakeResp({"error": "boom"}, status=500)
            return FakeResp({"id": "IGPOST"})
        self.n += 1
        return FakeResp({"id": f"C{self.n}"})


def install(monkeypatch, api):
    monkeypatch.setattr(bot.requests, "get", api.get)
    monkeypatch.setattr(bot.requests, "post", api.post)


# ------------------------------------------------------------ 単体テスト

def test_filtering():
    m = {"p1": photo("p1"), "v1": video("v1")}
    assert bot.eligible_media({"id": "1", "attachments": {"media_keys": ["p1"]}}, m)
    assert not bot.eligible_media({"id": "2", "text": "文字だけ"}, m)
    quoted = {"id": "3", "attachments": {"media_keys": ["p1"]}, "referenced_tweets": [{"type": "quoted", "id": "9"}]}
    assert not bot.eligible_media(quoted, m)


def test_caption_cleanup():
    t = {
        "text": "吉祥寺で駅頭 &amp; 対話会 https://t.co/aaa 詳細 https://t.co/bbb",
        "entities": {"urls": [
            {"url": "https://t.co/aaa", "expanded_url": "https://x.com/nami/status/1/photo/1", "media_key": "p1"},
            {"url": "https://t.co/bbb", "expanded_url": "https://namihoshino.jp/"},
        ]},
    }
    cap = bot.build_caption(t, "#武蔵野市")
    assert cap == "吉祥寺で駅頭 & 対話会  詳細 https://namihoshino.jp/\n\n#武蔵野市"


def test_long_post_uses_note_tweet():
    t = {"text": "短縮版…", "note_tweet": {"text": "長文の全文です"}}
    assert bot.build_caption(t) == "長文の全文です"


def test_media_urls():
    assert bot.photo_url(photo("abc")) == "https://pbs.twimg.com/media/abc?format=jpg&name=large"
    assert bot.best_video_url(video("v")) == "https://video.twimg.com/high.mp4"


def test_padding_ratio():
    assert bot.needs_padding(photo("a", 1000, 1414))   # A4縦チラシ
    assert not bot.needs_padding(photo("b", 1080, 1350))
    buf = io.BytesIO()
    Image.new("RGB", (1000, 2000)).save(buf, "JPEG")
    out = Image.open(io.BytesIO(bot.pad_image(buf.getvalue())))
    assert bot.IG_MIN_RATIO <= out.width / out.height <= bot.IG_MAX_RATIO


def test_pause_dates():
    cfg = {"pause_dates": ["2027-04-25"]}
    assert bot.is_paused(cfg, datetime(2027, 4, 24, 16, 0, tzinfo=timezone.utc))  # 4/25 1:00 JST
    assert not bot.is_paused(cfg, datetime(2027, 4, 24, 14, 0, tzinfo=timezone.utc))


# ------------------------------------------------------------ 通しのテスト

TWEETS = [
    {"id": "100", "text": "古い投稿"},
    {"id": "101", "text": "写真1枚 https://t.co/a", "attachments": {"media_keys": ["p1"]},
     "entities": {"urls": [{"url": "https://t.co/a", "expanded_url": "https://x.com/n/status/101/photo/1", "media_key": "p1"}]}},
    {"id": "102", "text": "文字だけ"},
    {"id": "103", "text": "引用", "attachments": {"media_keys": ["p2"]}, "referenced_tweets": [{"type": "quoted", "id": "5"}]},
    {"id": "104", "text": "動画", "attachments": {"media_keys": ["v1"]}},
    {"id": "105", "text": "写真2枚", "attachments": {"media_keys": ["p2", "p3"]}},
]
MEDIA = [photo("p1"), photo("p2"), photo("p3"), video("v1")]


def test_first_run_sets_baseline_only(env, monkeypatch):
    api = FakeAPI(TWEETS, MEDIA)
    install(monkeypatch, api)
    assert bot.run() == 0
    assert json.loads((env / "state.json").read_text())["since_id"] == "105"
    assert api.ig_calls == []


def test_full_flow(env, monkeypatch):
    bot.save_json(env / "state.json", {"x_user_id": "42", "since_id": "100"})
    api = FakeAPI(TWEETS, MEDIA)
    install(monkeypatch, api)
    assert bot.run(datetime(2026, 12, 1, tzinfo=timezone.utc)) == 0
    state = json.loads((env / "state.json").read_text())
    assert set(state["posted"]) == {"101", "104", "105"}
    assert state["since_id"] == "105"
    creates = [d for u, d in api.ig_calls if u.endswith("/media")]
    assert creates[0]["caption"] == "写真1枚"
    assert creates[1]["media_type"] == "REELS"
    assert creates[1]["video_url"] == "https://video.twimg.com/high.mp4"
    assert creates[-1]["media_type"] == "CAROUSEL"
    # 2回目は何もしない
    api.ig_calls.clear()
    assert bot.run(datetime(2026, 12, 1, tzinfo=timezone.utc)) == 0
    assert api.ig_calls == []


def test_paused_day_skips_permanently(env, monkeypatch):
    bot.save_json(env / "state.json", {"x_user_id": "42", "since_id": "100"})
    api = FakeAPI(TWEETS, MEDIA)
    install(monkeypatch, api)
    assert bot.run(datetime(2027, 4, 25, 3, tzinfo=timezone.utc)) == 0
    state = json.loads((env / "state.json").read_text())
    assert api.ig_calls == [] and state["since_id"] == "105" and not state["posted"]


def test_retry_then_success(env, monkeypatch):
    bot.save_json(env / "state.json", {"x_user_id": "42", "since_id": "100"})
    api = FakeAPI(TWEETS[:2], MEDIA, fail_publish=1)
    install(monkeypatch, api)
    assert bot.run() == 1
    state = json.loads((env / "state.json").read_text())
    assert state["since_id"] == "100" and state["attempts"]["101"] == 1
    assert bot.run() == 0
    state = json.loads((env / "state.json").read_text())
    assert state["posted"]["101"] == "IGPOST"


def test_tall_flyer_is_padded_and_hosted(env, monkeypatch):
    bot.save_json(env / "state.json", {"x_user_id": "42", "since_id": "100"})
    tweets = [{"id": "200", "text": "チラシ", "attachments": {"media_keys": ["f1"]}}]
    api = FakeAPI(tweets, [photo("f1", 1000, 1414)])
    install(monkeypatch, api)
    pushed = []
    monkeypatch.setattr(bot, "publish_to_repo", lambda paths: pushed.extend(paths))
    assert bot.run() == 0
    assert pushed and pushed[0].name == "200_0.jpg"
    create = [d for u, d in api.ig_calls if u.endswith("/media")][0]
    assert create["image_url"] == "https://raw.githubusercontent.com/nami/x2ig/main/media/200_0.jpg"
