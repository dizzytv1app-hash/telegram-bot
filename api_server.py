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

Talab qilinadigan paketlar (requirements.txt):
    fastapi
    uvicorn
    httpx
"""

import os
import sqlite3
import hashlib
import time
from contextlib import closing
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.path.abspath("anime.db")

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


app = FastAPI(title="AniNavo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ishga tushgandan keyin faqat sayt domenini yozib qo'yish tavsiya etiladi
    allow_methods=["GET"],
    allow_headers=["*"],
)


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
        "totalEpisodes": row["total_episodes"] or 0,
        "isFeatured": bool(row["is_featured"]),
        "isPopular": bool(row["is_popular"]),
        "isNew": is_new,
    }


def _seasons_for(conn: sqlite3.Connection, code: int) -> list:
    season_rows = conn.execute(
        "SELECT id, season_num FROM seasons WHERE anime_code=? ORDER BY season_num",
        (code,),
    ).fetchall()
    thumb_color = gradient_for(code)[0]
    seasons = []
    for s in season_rows:
        ep_rows = conn.execute(
            "SELECT episode_num FROM episodes WHERE season_id=? ORDER BY episode_num",
            (s["id"],),
        ).fetchall()
        episodes = [
            {
                "id": f"{code}-s{s['season_num']}-ep-{e['episode_num']}",
                "number": e["episode_num"],
                "animeCode": str(code),
                "title": f"{e['episode_num']}-qism",
                "duration": "—",
                "thumbnailColor": thumb_color,
            }
            for e in ep_rows
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
    base_url = str(request.base_url).rstrip("/")
    with closing(get_db()) as conn:
        rows = conn.execute("SELECT * FROM animes ORDER BY id").fetchall()
        items = []
        for r in rows:
            item = _row_to_summary(r, base_url)
            item["seasons"] = _seasons_for(conn, int(r["code"]))
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


@app.get("/api/animes/{code}")
def get_anime(code: int, request: Request):
    base_url = str(request.base_url).rstrip("/")
    with closing(get_db()) as conn:
        row = conn.execute("SELECT * FROM animes WHERE code=?", (code,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Anime topilmadi")
        item = _row_to_summary(row, base_url)
        item["seasons"] = _seasons_for(conn, code)
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("IP", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
