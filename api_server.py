"""
AniNavo API server
===================
Bot bilan BIR XIL papkada, bir xil `anime.db` faylini FAQAT O'QISH (read-only)
rejimida ochadi — botning yozishiga hech qanday xalaqit bermaydi.

Sayt (React) shu serverga HTTP so'rov yuboradi va JSON ko'rinishida anime
ma'lumotlarini oladi. Video fayllarning o'zi bu yerdan HECH QACHON
uzatilmaydi — faqat bot orqali (Telegram) tomosha qilinadi.

ISHGA TUSHIRISH (AlwaysData "Dasturiy sayt" / User program):
    uvicorn api_server:app --host :: --port $PORT --proxy-headers

Muhit o'zgaruvchilari (bot bilan bir xil bo'lishi kerak):
    BOT_TOKEN   — botning Telegram tokeni (poster rasmlarni olish uchun)
    PUBLIC_BASE_URL — (ixtiyoriy) masalan https://ixlosbek.alwaysdata.net
                       Berilsa, poster/banner manzillari doim shundan
                       tuziladi (proksi HTTPS sarlavhasini to'g'ri
                       uzatmasa ham HTTP/HTTPS aralashish xavfi bo'lmaydi).

Talab qilinadigan paketlar (requirements.txt):
    fastapi
    uvicorn
    httpx
"""

import os
import sqlite3
import hashlib
import hmac
import json
import time
import random
from contextlib import closing
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.path.abspath("anime.db")

# Botdagi ADMIN_ID bilan bir xil bo'lishi kerak — asosiy admin sifatida
# har doim ruxsat beriladi. Boshqa adminlar `admins` jadvalidan tekshiriladi
# (bot ular bilan bir xil jadvalni ishlatadi).
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6222096713"))

# Agar muhit o'zgaruvchisi berilmagan bo'lsa ham, poster/banner manzillari
# HAR DOIM https bilan tuzilishi uchun standart (fallback) manzil.
# Proksi orqasida ishlaganda ba'zan so'rov "http" deb noto'g'ri aniqlanishi
# mumkin — shu sabab bu qattiq belgilangan manzil ustunlik qiladi.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://ixlosbek.alwaysdata.net").rstrip("/")

ANIME_STATUSES = {"ongoing", "finished", "soon", "paused"}

# Haqiqiy poster rasm bo'lmagan/bo'la olmagan hollarda saytda ko'rsatiladigan
# gradient rang juftliklari (poster, banner). Anime kodiga qarab doimiy
# (deterministik) tanlanadi — bir xil anime doim bir xil rangda chiqadi.
GRADIENT_PALETTE = [
    ("from-emerald-500 via-teal-600 to-cyan-800", "from-slate-900 via-emerald-950 to-cyan-950"),
    ("from-violet-500 via-purple-600 to-indigo-800", "from-slate-900 via-purple-950 to-indigo-950"),
    ("from-rose-600 via-red-700 to-slate-800", "from-slate-900 via-red-950 to-slate-950"),
    ("from-amber-500 via-orange-600 to-red-700", "from-slate-900 via-amber-950 to-orange-950"),
    ("from-orange-500 via-red-600 to-rose-800", "from-slate-900 via-orange-950 to-red-950"),
    ("from-pink-500 via-rose-500 to-emerald-700", "from-slate-900 via-pink-950 to-rose-950"),
    ("from-sky-400 via-cyan-500 to-blue-700", "from-slate-900 via-sky-950 to-cyan-950"),
    ("from-teal-600 via-cyan-700 to-slate-800", "from-slate-900 via-teal-950 to-slate-950"),
    ("from-cyan-400 via-teal-500 to-green-600", "from-slate-900 via-cyan-950 to-teal-950"),
    ("from-violet-400 via-indigo-500 to-blue-700", "from-slate-900 via-violet-950 to-indigo-950"),
]


def gradient_for(code: int):
    idx = int(hashlib.md5(str(code).encode()).hexdigest(), 16) % len(GRADIENT_PALETTE)
    return GRADIENT_PALETTE[idx]


def get_db() -> sqlite3.Connection:
    # mode=ro — faqat o'qish. Bot shu paytda yozayotgan bo'lsa ham xavfsiz.
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_db_write() -> sqlite3.Connection:
    """Faqat admin yozish amallari (tahrirlash/o'chirish/banner/ko'rishlar
    hisoblagichi) uchun — oddiy (yozish huquqli) ulanish."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_admin_tables() -> None:
    """Admin panel uchun kerakli qo'shimcha jadvallarni (agar hali
    bo'lmasa) yaratadi. Botning mavjud jadvallariga (animes, seasons,
    episodes, admins) HECH TEGMAYDI — faqat yangi, mustaqil jadvallar."""
    with closing(get_db_write()) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS anime_views (code INTEGER PRIMARY KEY, views INTEGER DEFAULT 0)"
        )
        conn.commit()


app = FastAPI(title="AniNavo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ishga tushgandan keyin faqat sayt domenini yozib qo'yish tavsiya etiladi
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _on_startup():
    ensure_admin_tables()


def _resolve_base_url(request: Request) -> str:
    """Poster/banner manzillari uchun bazaviy URL.
    PUBLIC_BASE_URL ustunlik qiladi (proksi sxema xatosidan himoya qiladi);
    faqat u berilmagan hollarda so'rovning o'z manzili ishlatiladi."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


def _verify_telegram_init_data(init_data: str) -> dict:
    """Telegram Web App yuborgan `initData`ni tekshiradi (rasmiy Telegram
    algoritmi). Bu — parolsiz, soxta qilib bo'lmaydigan tasdiqlash: faqat
    Telegram'ning o'zi, bot tokenini bilgan holda, shu imzoni hosil qila
    oladi. Muvaffaqiyatli bo'lsa, ichidagi foydalanuvchi ma'lumotini
    qaytaradi; aks holda 401 xato beradi."""
    if not init_data or not BOT_TOKEN:
        raise HTTPException(status_code=401, detail="initData berilmagan")

    pairs = [p.split("=", 1) for p in init_data.split("&") if "=" in p]
    from urllib.parse import unquote

    data = {k: unquote(v) for k, v in pairs}
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Imzo topilmadi")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="Imzo noto'g'ri")

    auth_date = int(data.get("auth_date", 0))
    if time.time() - auth_date > 24 * 3600:
        raise HTTPException(status_code=401, detail="Sessiya eskirgan, botni qayta oching")

    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="Foydalanuvchi topilmadi")
    return json.loads(user_raw)


def require_admin(x_telegram_init_data: str = Header(default="")) -> int:
    """FastAPI dependency: so'rov sarlavhasidagi Telegram initData'ni
    tekshiradi va yuboruvchi admin ekanini tasdiqlaydi. Admin bo'lmasa —
    403 xato. Muvaffaqiyatli bo'lsa, Telegram user_id ni qaytaradi."""
    user = _verify_telegram_init_data(x_telegram_init_data)
    user_id = user.get("id")
    if user_id == ADMIN_ID:
        return user_id
    with closing(get_db()) as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Bu amal uchun admin huquqi kerak")
    return user_id


def _row_to_summary(row: sqlite3.Row, base_url: str) -> dict:
    code = row["code"]
    poster_gradient, banner_gradient = gradient_for(code)
    genres = [g.strip() for g in (row["genre"] or "").split(",") if g.strip()]
    has_photo_poster = row["poster_type"] == "photo" and bool(row["poster_file_id"])
    added_at = row["added_at"] or 0
    is_new = (time.time() - added_at) < 48 * 3600
    poster_url = f"{base_url}/api/poster/{code}" if has_photo_poster else None
    return {
        "id": str(code),
        "code": str(code),
        "title": row["name"],
        "originalTitle": row["original_title"] or None,
        "description": row["description"] or "",
        "posterColor": poster_gradient,
        "bannerColor": banner_gradient,
        "posterUrl": poster_url,
        "bannerUrl": poster_url,
        "rating": row["rating"],
        "year": row["year"] or None,
        "status": row["status"] if row["status"] in ANIME_STATUSES else "ongoing",
        "genres": genres,
        # Admin qo'lda kiritgan reja/rejalashtirilgan son — pastda, haqiqiy
        # yuklangan epizodlar soni ma'lum bo'lsa, shu bilan ustidan yoziladi.
        "totalEpisodes": row["total_episodes"] or 0,
        "isFeatured": bool(row["is_featured"]),
        "isPopular": bool(row["is_popular"]),
        "isNew": is_new,
    }


def _fetch_all_seasons_and_episodes(conn: sqlite3.Connection):
    """Barcha fasl va epizodlarni ATIGI 2 ta so'rov bilan olib, Python
    lug'atlariga joylaydi. Har bir anime uchun alohida so'rov qilish
    (N+1 muammosi) o'rniga shu — katalog javobini sezilarli tezlashtiradi."""
    season_rows = conn.execute(
        "SELECT id, anime_code, season_num FROM seasons ORDER BY anime_code, season_num"
    ).fetchall()

    episode_rows = conn.execute(
        "SELECT season_id, episode_num FROM episodes ORDER BY season_id, episode_num"
    ).fetchall()

    episodes_by_season = {}
    for e in episode_rows:
        episodes_by_season.setdefault(e["season_id"], []).append(e["episode_num"])

    seasons_by_anime = {}
    for s in season_rows:
        seasons_by_anime.setdefault(s["anime_code"], []).append(s)

    return seasons_by_anime, episodes_by_season


def _build_seasons_json(code: int, season_rows: list, episodes_by_season: dict) -> list:
    thumb_color = gradient_for(code)[0]
    seasons = []
    for s in season_rows:
        ep_nums = episodes_by_season.get(s["id"], [])
        episodes = [
            {
                "id": f"{code}-s{s['season_num']}-ep-{n}",
                "number": n,
                "animeCode": str(code),
                "title": f"{n}-qism",
                "duration": "—",
                "thumbnailColor": thumb_color,
            }
            for n in ep_nums
        ]
        seasons.append(
            {
                "id": str(s["id"]),
                "number": s["season_num"],
                "title": f"{s['season_num']}-fasl",
                "episodes": episodes,
            }
        )
    return seasons


def _attach_seasons_and_fix_count(item: dict, code: int, seasons_by_anime: dict, episodes_by_season: dict) -> None:
    season_rows = seasons_by_anime.get(code, [])
    seasons_json = _build_seasons_json(code, season_rows, episodes_by_season)
    item["seasons"] = seasons_json

    actual_total = sum(len(s["episodes"]) for s in seasons_json)
    if actual_total > 0:
        item["totalEpisodes"] = actual_total


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/animes")
def list_animes(
    request: Request,
    search: Optional[str] = None,
    genre: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = Query("rating", pattern="^(rating|year|title)$"),
):
    base_url = _resolve_base_url(request)
    with closing(get_db()) as conn:
        rows = conn.execute("SELECT * FROM animes ORDER BY id").fetchall()
        seasons_by_anime, episodes_by_season = _fetch_all_seasons_and_episodes(conn)

        items = []
        for r in rows:
            item = _row_to_summary(r, base_url)
            _attach_seasons_and_fix_count(item, int(r["code"]), seasons_by_anime, episodes_by_season)
            items.append(item)

    if search:
        q = search.lower()
        items = [
            a
            for a in items
            if q in a["title"].lower()
            or (a["originalTitle"] and q in a["originalTitle"].lower())
            or any(q in g.lower() for g in a["genres"])
        ]
    if genre:
        items = [a for a in items if genre in a["genres"]]
    if status:
        items = [a for a in items if a["status"] == status]

    if sort == "rating":
        items.sort(key=lambda a: a["rating"] or 0, reverse=True)
    elif sort == "year":
        items.sort(key=lambda a: a["year"] or 0, reverse=True)
    else:
        items.sort(key=lambda a: a["title"].lower())

    return items


def _fetch_anime_json(conn: sqlite3.Connection, code: int, base_url: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM animes WHERE code=?", (code,)).fetchone()
    if not row:
        return None
    item = _row_to_summary(row, base_url)
    season_rows = conn.execute(
        "SELECT id, anime_code, season_num FROM seasons WHERE anime_code=? ORDER BY season_num",
        (code,),
    ).fetchall()
    episode_rows = conn.execute(
        """SELECT e.season_id, e.episode_num FROM episodes e
           JOIN seasons s ON s.id = e.season_id
           WHERE s.anime_code=? ORDER BY e.season_id, e.episode_num""",
        (code,),
    ).fetchall()
    episodes_by_season = {}
    for e in episode_rows:
        episodes_by_season.setdefault(e["season_id"], []).append(e["episode_num"])

    seasons_json = _build_seasons_json(code, season_rows, episodes_by_season)
    item["seasons"] = seasons_json
    actual_total = sum(len(s["episodes"]) for s in seasons_json)
    if actual_total > 0:
        item["totalEpisodes"] = actual_total
    return item


@app.get("/api/animes/{code}")
def get_anime(code: int, request: Request):
    base_url = _resolve_base_url(request)
    with closing(get_db()) as conn:
        item = _fetch_anime_json(conn, code, base_url)
    if item is None:
        raise HTTPException(status_code=404, detail="Anime topilmadi")
    return item


@app.get("/api/genres")
def list_genres():
    with closing(get_db()) as conn:
        rows = conn.execute("SELECT genre FROM animes").fetchall()
    genres = set()
    for r in rows:
        for g in (r["genre"] or "").split(","):
            g = g.strip()
            if g:
                genres.add(g)
    return sorted(genres)


@app.get("/api/poster/{code}")
async def poster(code: int):
    """Telegramdagi poster rasmni bot tokeni orqali olib, brauzerga uzatadi.
    Bot tokeni HECH QACHON mijozga (brauzerga) ko'rinmaydi."""
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT poster_file_id, poster_type FROM animes WHERE code=?", (code,)
        ).fetchone()
    if not row or not row["poster_file_id"] or row["poster_type"] != "photo":
        raise HTTPException(status_code=404, detail="Poster topilmadi")

    file_id = row["poster_file_id"]
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
        )
        data = r.json()
        if not data.get("ok"):
            raise HTTPException(status_code=502, detail="Telegramdan fayl olinmadi")
        file_path = data["result"]["file_path"]

        img = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
        if img.status_code != 200:
            raise HTTPException(status_code=502, detail="Rasm yuklanmadi")

    return Response(
        content=img.content,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


@app.post("/api/track/view/{code}")
def track_view(code: int):
    """Ochiq (autentifikatsiyasiz) endpoint — anime tafsilot sahifasi
    ochilganda chaqiriladi, faqat ko'rishlar sonini oshiradi."""
    with closing(get_db_write()) as conn:
        conn.execute(
            "INSERT INTO anime_views (code, views) VALUES (?, 1) "
            "ON CONFLICT(code) DO UPDATE SET views = views + 1",
            (code,),
        )
        conn.commit()
    return {"ok": True}


@app.get("/api/banner")
def get_banner(request: Request):
    """Bosh sahifadagi banner uchun tayyor (hal qilingan) anime ro'yxati.
    Admin qo'lda tanlagan bo'lsa — o'sha animelar; aks holda kuniga bir
    marta yangilanadigan tasodifiy tanlov."""
    base_url = _resolve_base_url(request)
    with closing(get_db()) as conn:
        mode = _get_setting(conn, "banner_mode", "random")
        codes = []

        if mode == "manual":
            raw = _get_setting(conn, "banner_codes", "[]")
            try:
                codes = [int(c) for c in json.loads(raw)]
            except (ValueError, TypeError):
                codes = []

        if not codes:
            # Tasodifiy rejim — yoki qo'lda tanlov bo'sh bo'lsa, zaxira sifatida.
            all_codes = [r["code"] for r in conn.execute("SELECT code FROM animes").fetchall()]
            if all_codes:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                rng = random.Random(f"{today}-banner")
                rng.shuffle(all_codes)
                codes = all_codes[:5]

        items = []
        for code in codes:
            item = _fetch_anime_json(conn, code, base_url)
            if item:
                items.append(item)
    return items


@app.get("/api/admin/banner")
def admin_get_banner(_admin_id: int = Depends(require_admin)):
    with closing(get_db()) as conn:
        mode = _get_setting(conn, "banner_mode", "random")
        raw = _get_setting(conn, "banner_codes", "[]")
        try:
            codes = [int(c) for c in json.loads(raw)]
        except (ValueError, TypeError):
            codes = []
    return {"mode": mode, "codes": codes}


@app.post("/api/admin/banner")
async def admin_set_banner(request: Request, _admin_id: int = Depends(require_admin)):
    body = await request.json()
    mode = body.get("mode")
    codes = body.get("codes", [])
    if mode not in ("manual", "random"):
        raise HTTPException(status_code=422, detail="mode 'manual' yoki 'random' bo'lishi kerak")
    if not isinstance(codes, list) or len(codes) > 5:
        raise HTTPException(status_code=422, detail="codes ro'yxat bo'lishi va 5 tadan oshmasligi kerak")

    with closing(get_db_write()) as conn:
        _set_setting(conn, "banner_mode", mode)
        _set_setting(conn, "banner_codes", json.dumps([int(c) for c in codes]))
        conn.commit()
    return {"ok": True}


@app.get("/api/admin/dashboard")
def admin_dashboard(_admin_id: int = Depends(require_admin)):
    with closing(get_db()) as conn:
        total_anime = conn.execute("SELECT COUNT(*) c FROM animes").fetchone()["c"]

        seasons_by_anime, episodes_by_season = _fetch_all_seasons_and_episodes(conn)
        total_episodes = sum(len(v) for v in episodes_by_season.values())

        genre_counts = {}
        for r in conn.execute("SELECT genre FROM animes").fetchall():
            for g in (r["genre"] or "").split(","):
                g = g.strip()
                if g:
                    genre_counts[g] = genre_counts.get(g, 0) + 1

        try:
            total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        except sqlite3.OperationalError:
            total_users = None

        month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
        added_this_month = conn.execute(
            "SELECT COUNT(*) c FROM animes WHERE added_date LIKE ?", (f"{month_prefix}%",)
        ).fetchone()["c"]

        top_rows = conn.execute(
            """SELECT v.code, v.views, a.name FROM anime_views v
               JOIN animes a ON a.code = v.code
               ORDER BY v.views DESC LIMIT 3"""
        ).fetchall()
        top_viewed = [
            {"code": str(r["code"]), "title": r["name"], "views": r["views"]} for r in top_rows
        ]

    return {
        "totalAnime": total_anime,
        "totalEpisodes": total_episodes,
        "genreDistribution": genre_counts,
        "totalUsers": total_users,
        "addedThisMonth": added_this_month,
        "topViewed": top_viewed,
    }


@app.get("/api/admin/errors")
def admin_errors(limit: int = Query(default=50, ge=1, le=200), _admin_id: int = Depends(require_admin)):
    with closing(get_db()) as conn:
        try:
            rows = conn.execute(
                "SELECT id, created_at, error_text, update_summary FROM error_log "
                "ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    return {
        "errors": [
            {
                "id": r["id"],
                "createdAt": r["created_at"],
                "errorText": r["error_text"],
                "updateSummary": r["update_summary"],
            }
            for r in rows
        ]
    }


@app.get("/api/admin/user-growth")
def admin_user_growth(days: int = Query(default=30, ge=1, le=180), _admin_id: int = Depends(require_admin)):
    with closing(get_db()) as conn:
        try:
            rows = conn.execute(
                "SELECT substr(joined_date, 1, 10) AS d, COUNT(*) AS c "
                "FROM users WHERE joined_date IS NOT NULL "
                "GROUP BY d ORDER BY d"
            ).fetchall()
            counts_by_day = {r["d"]: r["c"] for r in rows}
        except sqlite3.OperationalError:
            counts_by_day = {}

    today = datetime.now(timezone.utc).date()
    series = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        key = day.isoformat()
        series.append({"date": key, "newUsers": counts_by_day.get(key, 0)})
    return {"series": series}


EDITABLE_FIELDS = {
    "title": "name",
    "originalTitle": "original_title",
    "description": "description",
    "year": "year",
    "rating": "rating",
    "status": "status",
    "isFeatured": "is_featured",
    "isPopular": "is_popular",
}


@app.patch("/api/admin/animes/{code}")
async def admin_edit_anime(code: int, request: Request, _admin_id: int = Depends(require_admin)):
    body = await request.json()
    updates = {}
    for api_field, db_column in EDITABLE_FIELDS.items():
        if api_field not in body:
            continue
        value = body[api_field]
        if api_field == "status" and value not in ANIME_STATUSES:
            raise HTTPException(status_code=422, detail=f"Noto'g'ri status: {value}")
        if api_field in ("isFeatured", "isPopular"):
            value = 1 if value else 0
        updates[db_column] = value

    if not updates:
        raise HTTPException(status_code=422, detail="Hech qanday tahrirlanadigan maydon berilmadi")

    with closing(get_db_write()) as conn:
        exists = conn.execute("SELECT 1 FROM animes WHERE code=?", (code,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Anime topilmadi")
        set_clause = ", ".join(f"{col}=?" for col in updates)
        conn.execute(f"UPDATE animes SET {set_clause} WHERE code=?", (*updates.values(), code))
        conn.commit()
        base_url = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
        item = _fetch_anime_json(conn, code, base_url)
    return item


@app.delete("/api/admin/animes/{code}")
def admin_delete_anime(code: int, _admin_id: int = Depends(require_admin)):
    with closing(get_db_write()) as conn:
        exists = conn.execute("SELECT 1 FROM animes WHERE code=?", (code,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Anime topilmadi")
        season_ids = [
            r["id"] for r in conn.execute("SELECT id FROM seasons WHERE anime_code=?", (code,)).fetchall()
        ]
        for sid in season_ids:
            conn.execute("DELETE FROM episodes WHERE season_id=?", (sid,))
        conn.execute("DELETE FROM seasons WHERE anime_code=?", (code,))
        conn.execute("DELETE FROM anime_views WHERE code=?", (code,))
        conn.execute("DELETE FROM animes WHERE code=?", (code,))
        conn.commit()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("IP", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
