#!/usr/bin/env python3
"""STEP Football Intelligence System v3.2
   • Soccerway always-on (form + injuries, fuzzy name match)
   • Calibrated confidence (data-signal quality factor)
   • Low-data league filter
   • Real-time betting odds (The Odds API + BetExplorer fallback)
   • International Friendly / Nations League support
"""

import sys, json, os, webbrowser, time, math, urllib.parse, difflib, re, unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── BeautifulSoup (optional — needed for Soccerway scraping) ───────────────────
try:
    from bs4 import BeautifulSoup as _BS
    _SW_AVAIL = True
except ImportError:
    _SW_AVAIL = False
    try:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "beautifulsoup4", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from bs4 import BeautifulSoup as _BS
        _SW_AVAIL = True
    except Exception:
        pass

# ── Time & paths ───────────────────────────────────────────────────────────────
TZ7      = timezone(timedelta(hours=7))
_now     = datetime.now(TZ7)
TODAY    = _now.strftime("%Y-%m-%d")
TOMORROW = (_now + timedelta(days=1)).strftime("%Y-%m-%d")
SEASON   = _now.year if _now.month >= 7 else _now.year - 1
NOW      = _now.strftime("%Y-%m-%d %H:%M")
OUT_FILE = Path(__file__).parent / "step_result.html"
CACHE_DIR= Path(__file__).parent / ".step_cache" / TODAY
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CFG_FILE = Path(__file__).parent / ".step_config.json"

# ── Window 12:00 → 12:00 (UTC+7) ─────────────────────────────────────────────
# ตัดรอบที่ 12:00 ทุกวัน:
#   ก่อน 12:00 → แสดง 12:00 เมื่อวาน ถึง 12:00 วันนี้
#   หลัง 12:00 → แสดง 12:00 วันนี้ ถึง 12:00 พรุ่งนี้
_noon_today = _now.replace(hour=12, minute=0, second=0, microsecond=0)
if _now.hour < 12:
    _win_start = _noon_today - timedelta(days=1)
    _win_end   = _noon_today
else:
    _win_start = _noon_today
    _win_end   = _noon_today + timedelta(days=1)
WINDOW_START_TS = _win_start.timestamp()
WINDOW_END_TS   = _win_end.timestamp()
WINDOW_LABEL    = f"{_win_start.strftime('%d %b %H:%M')} – {_win_end.strftime('%d %b %H:%M')} (UTC+7)"

# ── Config (persist API key) ───────────────────────────────────────────────────
def load_cfg():
    try:
        return json.loads(CFG_FILE.read_text("utf-8")) if CFG_FILE.exists() else {}
    except Exception:
        return {}

def save_cfg(d):
    try:
        CFG_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_API_CALLS   = 500   # Pro plan: 7,500 calls/วัน → ใช้ 500 ต่อ run
MAX_PER_COL     = 20
MIN_SIGNALS     = 1.0   # ต่ำกว่านี้ = กรองออกจาก main columns

# จำนวน candidates ปรับตาม budget API:
# แต่ละแมตช์ใช้ ~2 calls (H2H + injuries)  เผื่อ 15 calls สำหรับ standings/odds
# → budget = (MAX_API_CALLS - 15) // 2  ≈ 37 แมตช์
_MATCH_API_COST = 2
_BUDGET_RESERVE = 50   # สำรองไว้สำหรับ standings + odds (Pro plan มีเยอะ)
DEEP_CANDIDATES = max(30, (MAX_API_CALLS - _BUDGET_RESERVE) // _MATCH_API_COST)

LEAGUE_GOALS = {
    1:2.65,                          # FIFA World Cup
    10:2.4,                          # International Friendly
    9:2.5,                           # UEFA Nations League
    5:2.6,                           # World Cup Qualification
    4:2.5,                           # Euro Qualification
    2:2.9, 3:2.7, 848:2.6,
    39:2.8, 140:2.5, 78:3.1, 135:2.6, 61:2.5,
    88:3.0, 94:2.7, 144:2.8, 203:2.7, 235:2.5,
    253:2.9, 262:2.7, 71:2.4, 128:2.5, 98:2.6, 188:2.8,
    290:2.6, 292:2.5,                # Thai League 1 & 2
    296:2.5,                         # Thai FA Cup
}
DEF_GOALS = 2.5

# TIER1 ใช้สำหรับเรียงลำดับ priority เท่านั้น — ไม่ใช่ whitelist กรองลีก
# ทุกลีกที่ API ส่งมาวันนี้จะถูกดึงมาวิเคราะห์ (Auto-discovery)
TIER1 = {
    # ทีมชาติ / รายการนานาชาติ (priority สูงสุด)
    1,    # FIFA World Cup
    10,   # International Friendly
    9,    # UEFA Nations League
    5,    # World Cup Qualification
    4,    # Euro Qualification (UEFA)
    # สโมสรยุโรป
    2, 3, 848,
    # ลีกใหญ่
    39,   # Premier League (England)
    140,  # La Liga (Spain)
    78,   # Bundesliga (Germany)
    135,  # Serie A (Italy)
    61,   # Ligue 1 (France)
    # ไทย
    290,  # Thai League 1
    292,  # Thai League 2
}

OU_LINES = [1.5, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 4.0]

_UCL  = {39:4, 140:4, 78:4, 135:4, 61:4, 290:1}
_EL   = {39:6, 140:6, 78:5, 135:6, 61:5}
_UECL = {39:7, 140:7, 78:6, 135:7, 61:6}
_REL  = 3

# ── The Odds API ───────────────────────────────────────────────────────────────
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
PREFER_BOOKS  = ["pinnacle", "betfair_ex_eu", "williamhill", "bet365",
                 "bwin", "unibet_eu", "1xbet"]

# API-Football league ID → The Odds API sport key
LEAGUE_TO_ODDS_SPORT = {
    1:   "soccer_fifa_world_cup",     # FIFA World Cup 2026
    2:   "soccer_uefa_champs_league",
    3:   "soccer_uefa_europa_league",
    848: "soccer_uefa_europa_conference_league",
    39:  "soccer_epl",
    140: "soccer_spain_la_liga",
    78:  "soccer_germany_bundesliga",
    135: "soccer_italy_serie_a",
    61:  "soccer_france_ligue_one",
    88:  "soccer_netherlands_eredivisie",
    94:  "soccer_portugal_primeira_liga",
    144: "soccer_belgium_first_div",
    203: "soccer_turkey_super_league",
    235: "soccer_russia_premier_league",
    253: "soccer_usa_mls",
    262: "soccer_mexico_ligamx",
    71:  "soccer_brazil_campeonato",
    128: "soccer_argentina_primera_division",
    98:  "soccer_japan_j_league",
}

# ── HTTP + disk cache (API) ────────────────────────────────────────────────────
_call_count = 0

def apicall(url, headers, cache_key=None, timeout=20):
    global _call_count
    if cache_key:
        cf = CACHE_DIR / f"{cache_key}.json"
        if cf.exists():
            try:
                return json.loads(cf.read_text("utf-8"))
            except Exception:
                pass
    if _call_count >= MAX_API_CALLS:
        return None
    time.sleep(0.35)
    _call_count += 1
    try:
        req = urllib.request.Request(url, headers={
            **headers,
            "User-Agent": "Mozilla/5.0",
            "Accept":     "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"      ⚠ HTTP error: {e}")
        return None
    if cache_key and data and not data.get("errors"):
        try:
            (CACHE_DIR / f"{cache_key}.json").write_text(
                json.dumps(data, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    return data

_hdrs = {}

def af_get(path, params="", cache_key=None):
    url = f"https://v3.football.api-sports.io/{path}?{params}"
    return apicall(url, _hdrs, cache_key)

def get_response(data):
    if not data:
        return []
    errs = data.get("errors", {})
    if errs and errs != [] and errs != {}:
        return []
    return data.get("response", [])

def detect_headers(api_key):
    """ลอง header ทั้งสองแบบ คืนแบบที่ใช้ได้"""
    candidates = [
        (f"https://v3.football.api-sports.io/fixtures?date={TODAY}",
         {"x-apisports-key": api_key}),
        (f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={TODAY}",
         {"x-rapidapi-key": api_key, "x-rapidapi-host": "api-football-v1.p.rapidapi.com"}),
        (f"https://v3.football.api-sports.io/fixtures?date={TODAY}",
         {"x-rapidapi-key": api_key, "x-rapidapi-host": "v3.football.api-sports.io"}),
    ]
    for url, h in candidates:
        data = apicall(url, h, cache_key=f"fixtures_all_{TODAY}")
        if data and not data.get("errors") and data.get("response") is not None:
            return h, data
    return None, None

# ── Fetch fixtures ─────────────────────────────────────────────────────────────
def fetch_fixtures(first_page_data):
    items = first_page_data.get("response", [])
    total_pages = first_page_data.get("paging", {}).get("total", 1)
    print(f"    page 1/{total_pages} — {len(items)} fixtures")
    for p in range(2, min(total_pages + 1, 20)):
        d = af_get("fixtures", f"date={TODAY}&page={p}", f"fixtures_p{p}_{TODAY}")
        if not d or d.get("errors"):
            break
        more = get_response(d)
        items.extend(more)
        print(f"    page {p}/{total_pages} — {len(more)} fixtures")
    return items

# ── Standings (รวม form string) ────────────────────────────────────────────────
_WC_SEASON = 2026   # FIFA World Cup 2026 ใช้ season=2026 เสมอ
WC_LEAGUE_IDS = {1}  # league IDs ที่ใช้ season ปีการแข่งขัน ไม่ใช่ปีลีก

def fetch_standings(league_id, hint_season=None):
    """ดึง standings — ใช้ hint_season จาก fixture ก่อน
    Cache-first: ถ้ามีในดิสก์แล้วไม่กิน API quota"""
    if hint_season:
        seasons_try = [hint_season, hint_season - 1]
    elif league_id in WC_LEAGUE_IDS:
        seasons_try = [_WC_SEASON]
    else:
        seasons_try = [SEASON, SEASON - 1, 2024]
    items = []
    for season in seasons_try:
        cache_key = f"standings_{league_id}_{season}"
        # ลองดิสก์ cache ก่อน (ไม่กิน API call)
        cf = CACHE_DIR / f"{cache_key}.json"
        if cf.exists():
            try:
                cached = json.loads(cf.read_text("utf-8"))
                items = get_response(cached)
                if items:
                    break
            except Exception:
                pass
        # ไม่มีใน cache → เรียก API จริง
        d = af_get("standings",
                   f"league={league_id}&season={season}",
                   cache_key)
        items = get_response(d)
        if items:
            break
    result = {}
    for entry in items:
        for grp in entry.get("league", {}).get("standings", []):
            for t in grp:
                tid = t.get("team", {}).get("id")
                if not tid:
                    continue
                all_  = t.get("all",  {})
                home  = t.get("home", {})
                away  = t.get("away", {})
                form_str = t.get("form", "")
                result[tid] = {
                    "rank":     t.get("rank", 99),
                    "pts":      t.get("points", 0),
                    "played":   all_.get("played", 0),
                    "w":        all_.get("win",  0),
                    "d":        all_.get("draw", 0),
                    "l":        all_.get("lose", 0),
                    "gf":       all_.get("goals", {}).get("for",     0),
                    "ga":       all_.get("goals", {}).get("against", 0),
                    "form_str": form_str,
                    "home_w":   home.get("win",  0),
                    "home_gf":  home.get("goals", {}).get("for",     0),
                    "home_ga":  home.get("goals", {}).get("against", 0),
                    "home_p":   home.get("played", 0),
                    "away_w":   away.get("win",  0),
                    "away_gf":  away.get("goals", {}).get("for",     0),
                    "away_ga":  away.get("goals", {}).get("against", 0),
                    "away_p":   away.get("played", 0),
                }
    return result

# ── H2H (season param — ไม่ใช้ last) ──────────────────────────────────────────
def fetch_h2h(home_id, away_id, hint_season=None):
    """ดึง H2H — cache-first, hint_season จาก fixture โดยตรง"""
    s1 = hint_season if hint_season else SEASON
    s2 = s1 - 1
    for season in [s1, s2]:
        ck = f"h2h_{home_id}_{away_id}_{season}"
        cf = CACHE_DIR / f"{ck}.json"
        if cf.exists():
            try:
                items = get_response(json.loads(cf.read_text("utf-8")))
                if items:
                    return items
            except Exception:
                pass
        d = af_get("fixtures/headtohead",
                   f"h2h={home_id}-{away_id}&season={season}&status=FT", ck)
        items = get_response(d)
        if items:
            return items
    return []

# ── Injuries ───────────────────────────────────────────────────────────────────
def fetch_injuries(fixture_id):
    d = af_get("injuries", f"fixture={fixture_id}", f"injuries_{fixture_id}")
    return get_response(d)

# ── Form จาก standings form string ────────────────────────────────────────────
def form_from_str(form_str, last_n=5):
    if not form_str:
        return []
    chars = [c for c in form_str.upper() if c in ("W", "D", "L")]
    return list(reversed(chars))[:last_n]

def form_pts(results):
    return sum(3 if r == "W" else 1 if r == "D" else 0 for r in results[:5])

def form_str_display(results):
    return "-".join(results[:5]) if results else "N/A"

# ── H2H stats ─────────────────────────────────────────────────────────────────
def h2h_stats(fixtures, home_id, away_id):
    hw = aw = draws = goals = n = 0
    for f in fixtures:
        t  = f.get("teams", {})
        g  = f.get("goals", {})
        gh, ga = g.get("home"), g.get("away")
        if gh is None or ga is None:
            continue
        n += 1
        goals += gh + ga
        hid = t.get("home", {}).get("id")
        if hid == home_id:
            if gh > ga: hw += 1
            elif gh < ga: aw += 1
            else: draws += 1
        else:
            if ga > gh: hw += 1
            elif ga < gh: aw += 1
            else: draws += 1
    return {"hw": hw, "aw": aw, "d": draws, "n": n,
            "avg_goals": round(goals / n, 1) if n else None}

def h2h_match_details(fixtures, home_id, away_id, limit=5):
    """แปลง h2h_raw เป็น list รายแมตช์ สำหรับแสดงใน UI"""
    _TH_M = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.",
              "ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
    results = []
    for f in fixtures[:limit]:
        try:
            fx   = f.get("fixture", {})
            tm   = f.get("teams", {})
            g    = f.get("goals", {})
            lg   = f.get("league", {})
            gh, ga = g.get("home"), g.get("away")
            if gh is None or ga is None:
                continue
            h_id   = tm.get("home", {}).get("id")
            a_id   = tm.get("away", {}).get("id")
            h_name = tm.get("home", {}).get("name", "?")
            a_name = tm.get("away", {}).get("name", "?")
            h_logo = tm.get("home", {}).get("logo", "")
            a_logo = tm.get("away", {}).get("logo", "")
            # ผลลัพธ์จากมุมมองทีมเจ้าบ้าน (home_id)
            if h_id == home_id:
                result = "W" if gh > ga else "L" if gh < ga else "D"
                score  = f"{gh}-{ga}"
            else:
                result = "W" if ga > gh else "L" if ga < gh else "D"
                score  = f"{ga}-{gh}"
                h_name, a_name = a_name, h_name
                h_logo, a_logo = a_logo, h_logo
                gh, ga = ga, gh
            # วันที่
            date_str = fx.get("date", "")
            try:
                dt  = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                dtl = dt.astimezone(TZ7)
                date_display = f"{dtl.day} {_TH_M[dtl.month]} {dtl.year}"
            except Exception:
                date_display = date_str[:10]
            results.append({
                "date":      date_display,
                "home":      h_name,
                "away":      a_name,
                "home_logo": h_logo,
                "away_logo": a_logo,
                "home_score": gh,
                "away_score": ga,
                "score":     score,
                "result":    result,   # W/D/L จากมุมมองเจ้าบ้าน (home_id)
                "league":    lg.get("name", ""),
                "league_flag": lg.get("flag", "") or "",
                "league_logo": lg.get("logo", "") or "",
                "season":    lg.get("season", ""),
            })
        except Exception:
            continue
    return results

def safe_div(a, b, default=0.0):
    return a / b if b else default

# ── Team situation + motivation ────────────────────────────────────────────────
def team_situation(rank, pts, league_id, standings_dict):
    if not rank or rank >= 90 or not standings_dict:
        return "", "", 0
    srt = sorted(standings_dict.values(), key=lambda x: x.get("rank", 99))
    n   = len(srt)
    if n == 0:
        return "", "", 0

    def bpts(cut):
        return srt[cut - 1].get("pts", 0) if cut and cut <= n else 0

    ucl_cut   = _UCL.get(league_id, 0)
    el_cut    = _EL.get(league_id, 0)
    uecl_cut  = _UECL.get(league_id, 0)
    rel_start = n - _REL + 1
    playoff_r = rel_start - 1
    safe_pts  = bpts(playoff_r) if playoff_r > 0 else 0
    label = gap_text = ""
    mot   = 0

    if rank == 1:
        gap2 = pts - (srt[1].get("pts", 0) if n >= 2 else 0)
        label, gap_text, mot = ("🥇 ลุ้นแชมป์", f"นำอันดับ 2 เพียง {gap2} แต้ม", 8) if gap2 <= 3 \
                                else ("🥇 นำตาราง", f"นำห่าง {gap2} แต้ม", 5)
    elif ucl_cut and rank <= ucl_cut:
        gap_out = pts - (srt[ucl_cut].get("pts", 0) if n > ucl_cut else 0)
        label, gap_text, mot = "🏆 ลุ้น UCL", f"ห่างขอบ UCL {gap_out} แต้ม", 7 if gap_out <= 3 else 5
    elif ucl_cut and rank == ucl_cut + 1:
        gap_in = bpts(ucl_cut) - pts
        label, gap_text, mot = "🏆 ไล่ลุ้น UCL", f"ห่างจาก UCL {gap_in} แต้ม", 8 if gap_in <= 3 else 6
    elif el_cut and rank <= el_cut:
        gap_out = pts - (srt[el_cut].get("pts", 0) if n > el_cut else 0)
        label, gap_text, mot = "🥈 ลุ้น Europa", f"ห่างขอบ Europa {gap_out} แต้ม", 5 if gap_out <= 3 else 3
    elif uecl_cut and rank <= uecl_cut:
        label, mot = "🥉 ลุ้น Conference", 3
    elif rank >= n:
        gap_safe = safe_pts - pts if safe_pts else 0
        label, gap_text, mot = "💀 ตกชั้นแล้ว", f"ห่างโซนปลอดภัย {gap_safe} แต้ม", 2
    elif rank >= rel_start:
        gap_safe = safe_pts - pts if safe_pts else 0
        label, gap_text, mot = "⚠️ หนีตกชั้น", f"ห่างโซนปลอดภัย {gap_safe} แต้ม", 8 if gap_safe <= 3 else 7
    elif rank == playoff_r:
        gap_safe = safe_pts - pts if safe_pts else 0
        label, gap_text, mot = "⚠️ เพลย์ออฟตกชั้น", f"ห่างโซนปลอดภัย {gap_safe} แต้ม", 7
    else:
        if _now.month >= 5 and ucl_cut:
            gap_up   = bpts(ucl_cut) - pts
            gap_down = pts - safe_pts if safe_pts else 99
            if gap_up > 10 and gap_down > 10:
                label, gap_text, mot = "✓ จบซีซันอย่างสบาย", "ไม่มีแรงจูงใจพิเศษ", -5

    return label, gap_text, mot

# ══════════════════════════════════════════════════════════════════════════════
# ── ODDS MODULE (The Odds API + BetExplorer fallback) ─────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _odds_api_get(path, params, cache_key, odds_key):
    """Fetch from The Odds API with disk cache"""
    cf = CACHE_DIR / f"odds_{cache_key}.json"
    if cf.exists():
        try:
            return json.loads(cf.read_text("utf-8"))
        except Exception:
            pass
    url = f"{ODDS_API_BASE}/{path}?apiKey={odds_key}&{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            remaining = r.headers.get("x-requests-remaining", "?")
            data = json.loads(r.read().decode("utf-8"))
        print(f"      Odds API remaining: {remaining} req")
        if isinstance(data, list) and data:
            cf.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        return data
    except Exception as e:
        print(f"      ⚠ Odds API error: {e}")
        return None

def _best_book(bookmakers):
    """เลือก bookmaker ที่ดีที่สุดจากรายการ (Pinnacle ก่อน)"""
    if not bookmakers:
        return None
    for pref in PREFER_BOOKS:
        for b in bookmakers:
            if b.get("key", "").lower() == pref:
                return b
    return bookmakers[0]   # fallback: first available

def _process_odds_event(event, result_dict):
    """แปลง 1 event จาก The Odds API → เก็บใน result_dict"""
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    book = _best_book(event.get("bookmakers", []))
    if not book or not home or not away:
        return
    entry = {"home": home, "away": away, "bookmaker": book.get("title", "?")}
    for mkt in book.get("markets", []):
        key      = mkt.get("key", "")
        outcomes = mkt.get("outcomes", [])
        if key == "h2h":
            for o in outcomes:
                n = o.get("name", ""); p = round(o.get("price", 0), 2)
                if n == home:           entry["h_odds"] = p
                elif n == away:         entry["a_odds"] = p
                elif n.lower()=="draw": entry["d_odds"] = p
        elif key == "totals":
            # หา line ที่ใกล้ 2.5 ที่สุด
            ou_lines = {}
            for o in outcomes:
                pt = o.get("point", 2.5)
                ou_lines.setdefault(pt, {})
                ou_lines[pt][o.get("name","")] = round(o.get("price",0), 2)
            # เลือก line ที่ใกล้ 2.5
            best_pt = min(ou_lines.keys(), key=lambda x: abs(x - 2.5))
            entry["ou_line_mkt"] = best_pt
            entry["ou_over"]     = ou_lines[best_pt].get("Over", 0)
            entry["ou_under"]    = ou_lines[best_pt].get("Under", 0)
        elif key == "spreads":
            # Asian HCP — เก็บ home point & price
            for o in outcomes:
                if o.get("name", "") == home:
                    entry["hcp_pt"]    = o.get("point", 0)
                    entry["hcp_price"] = round(o.get("price", 0), 2)
    key_pair = (_norm_name(home), _norm_name(away))
    result_dict[key_pair] = entry

def _norm_name(name):
    """normalize team name สำหรับ fuzzy matching"""
    return name.lower().replace("fc","").replace("ac","").replace("sc","") \
               .replace(".","").replace("-"," ").strip()

def fetch_odds_for_leagues(league_ids, odds_key):
    """ดึง odds จาก The Odds API สำหรับลีกทั้งหมดที่เกี่ยวข้องวันนี้
    คืน dict: (_norm_name(home), _norm_name(away)) → odds entry"""
    if not odds_key:
        return {}
    sport_keys = set()
    for lid in league_ids:
        sk = LEAGUE_TO_ODDS_SPORT.get(lid)
        if sk:
            sport_keys.add(sk)
    if not sport_keys:
        print("    ⚠ ไม่มีลีกที่ map กับ Odds API (ดูใน LEAGUE_TO_ODDS_SPORT)")
        return {}

    all_odds = {}
    for sk in sorted(sport_keys):
        print(f"    Odds API: {sk}...", end=" ", flush=True)
        params = "regions=eu&markets=h2h,totals,spreads&oddsFormat=decimal&dateFormat=iso"
        data   = _odds_api_get(f"sports/{sk}/odds", params,
                               f"{sk}_{TODAY}", odds_key)
        if not data:
            print("– ไม่มีข้อมูล")
            continue
        for event in data:
            _process_odds_event(event, all_odds)
        print(f"✓ {len(data)} แมตช์")
    return all_odds

def find_match_odds(all_odds, home_name, away_name):
    """หา odds ที่ตรงกับคู่นี้ด้วย fuzzy name matching"""
    if not all_odds:
        return None
    hn = _norm_name(home_name)
    an = _norm_name(away_name)
    # 1. exact
    if (hn, an) in all_odds:
        return all_odds[(hn, an)]
    # 2. fuzzy — SequenceMatcher ratio ≥ 0.72 สำหรับทั้งสองทีม
    best, best_r = None, 0.0
    for (oh, oa), entry in all_odds.items():
        r = (difflib.SequenceMatcher(None, hn, oh).ratio() +
             difflib.SequenceMatcher(None, an, oa).ratio()) / 2
        if r > best_r and r >= 0.72:
            best_r, best = r, entry
    return best

def odds_implied(price):
    """Decimal odds → implied probability (ไม่ตัด vig)"""
    return round(1 / price, 4) if price and price > 1 else 0.0

def value_edge(model_pct, implied_prob):
    """value edge = (model prob / implied prob) - 1
    บวก = model เห็นว่า edge ดีกว่าตลาด"""
    if not implied_prob or implied_prob <= 0:
        return None
    return round((model_pct / 100) / implied_prob - 1, 3)

# ── BetExplorer fallback (static HTML) ────────────────────────────────────────
BE_BASE = "https://www.betexplorer.com"

def betexplorer_today_odds():
    """ดึง pre-match odds จาก BetExplorer สำหรับแมตช์วันนี้
    คืน dict เดียวกับ find_match_odds"""
    if not _SW_AVAIL:
        return {}
    cache_k = f"be_today_{TODAY}"
    cf      = CACHE_DIR / f"odds_{cache_k}.html"
    if cf.exists():
        html = cf.read_text("utf-8")
    else:
        html = None
        try:
            req = urllib.request.Request(
                f"{BE_BASE}/soccer/",
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
            with urllib.request.urlopen(req, timeout=12) as r:
                html = r.read().decode("utf-8", errors="replace")
            if html:
                cf.write_text(html, "utf-8")
        except Exception as e:
            print(f"      ⚠ BetExplorer error: {e}")
    if not html:
        return {}
    result = {}
    try:
        soup = _BS(html, "html.parser")
        for row in soup.select("tr[data-matchid]"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            teams_el = row.select_one(".table-main__tt")
            if not teams_el:
                continue
            team_txt = teams_el.get_text(" ", strip=True)
            if " - " not in team_txt:
                continue
            home_t, away_t = [x.strip() for x in team_txt.split(" - ", 1)]
            try:
                h_o = float(cells[1].get_text(strip=True))
                d_o = float(cells[2].get_text(strip=True))
                a_o = float(cells[3].get_text(strip=True))
            except (ValueError, IndexError):
                continue
            entry = {"home": home_t, "away": away_t, "bookmaker": "BetExplorer",
                     "h_odds": h_o, "d_odds": d_o, "a_odds": a_o}
            key_pair = (_norm_name(home_t), _norm_name(away_t))
            result[key_pair] = entry
    except Exception:
        pass
    return result

# ══════════════════════════════════════════════════════════════════════════════
# ── SOCCERWAY MODULE ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
SW_BASE        = "https://th.soccerway.com"
_sw_url_cache  = {}   # team_name → "/team/slug/id/"

def sw_fetch(url, cache_key=None, timeout=15):
    """Fetch Soccerway static HTML, disk-cached per day"""
    if cache_key:
        cf = CACHE_DIR / f"sw_{cache_key[:50]}.html"
        if cf.exists():
            try:
                return cf.read_text("utf-8")
            except Exception:
                pass
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "th,en-US;q=0.7,en;q=0.3",
            "Referer": SW_BASE + "/",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    if cache_key and html and len(html) > 500:
        try:
            (CACHE_DIR / f"sw_{cache_key[:50]}.html").write_text(html, "utf-8")
        except Exception:
            pass
    return html

def _sw_norm(name):
    """Normalize ชื่อทีมสำหรับ fuzzy matching: lowercase, ลบ accent, ลบ FC/United/ฯลฯ"""
    # ลบ accents (é→e, ü→u, ñ→n ฯลฯ)
    nfkd = unicodedata.normalize("NFD", name)
    name = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    name = name.lower()
    # ลบ common club suffixes/prefixes
    name = re.sub(r"\b(fc|cf|afc|bfc|sc|ac|rc|cd|ad|as|ss|bv|sv|rcd|"
                  r"united|city|club|football|soccer|sport|sporting|"
                  r"atletico|atletik|real|royal)\b", " ", name)
    # ลบ punctuation ยกเว้น space
    name = re.sub(r"[^\w\s]", " ", name)
    return " ".join(name.split())


def sw_search_team(name):
    """ค้นหา Soccerway URL สำหรับทีม — ใช้ fuzzy difflib หาผลที่ตรงที่สุด
    คืน path เช่น '/team/liverpool/lId4TMwf/' หรือ None"""
    if not _SW_AVAIL:
        return None
    if name in _sw_url_cache:
        return _sw_url_cache[name]

    def _do_search(q):
        """ค้นหา 1 query → [(display_text, path), ...]"""
        safe_q  = urllib.parse.quote(q)
        cache_k = f"swsearch_{re.sub(r'[^a-zA-Z0-9]','_',q)[:40]}"
        html    = sw_fetch(f"{SW_BASE}/search/?q={safe_q}", cache_k)
        if not html:
            return []
        found = []
        try:
            soup = _BS(html, "html.parser")
            for a in soup.select("a[href*='/team/']"):
                href  = a.get("href", "")
                parts = [p for p in href.split("/") if p]
                if len(parts) >= 3 and parts[0] == "team":
                    text = a.get_text(strip=True) or parts[1].replace("-", " ")
                    path = "/" + "/".join(parts[:3]) + "/"
                    if (text, path) not in found:
                        found.append((text, path))
        except Exception:
            pass
        return found

    # สร้าง list of queries ที่จะลอง (เรียงตามลำดับ)
    queries = [name]
    words   = name.split()
    if len(words) >= 2:
        queries.append(words[0])                  # คำแรกอย่างเดียว
    short = re.sub(r"\b(FC|CF|AFC|SC|AC|RC|CD|United|City|Club|Football)\b",
                   "", name).strip()
    if short and short != name and short not in queries:
        queries.append(short)

    # รวม results จากทุก query (ลอง query แรกก่อน; ถ้าว่างค่อยลองต่อ)
    all_results = []
    for q in queries:
        res = _do_search(q)
        all_results.extend(r for r in res if r not in all_results)
        if res:
            break   # ถ้าเจอแล้วหยุด (query ถัดไปจะซ้ำหรือแย่กว่า)

    if not all_results:
        _sw_url_cache[name] = None
        return None

    # Fuzzy match: หา path ที่ชื่อตรงกับ name มากที่สุด
    norm_name  = _sw_norm(name)
    best_path  = None
    best_score = 0.0
    for text, path in all_results:
        score = difflib.SequenceMatcher(None, norm_name, _sw_norm(text)).ratio()
        if score > best_score:
            best_score = score
            best_path  = path

    # threshold 0.35: ผ่อนสำหรับทีมชื่อสั้น/ต่างภาษา
    result = best_path if best_score >= 0.35 else None
    _sw_url_cache[name] = result
    return result

def sw_team_injuries(team_path):
    """ดึงรายชื่อผู้เล่นบาดเจ็บจากหน้าทีม Soccerway
    คืน list of str เช่น ['Salah (knee)', 'Alisson (calf)']"""
    if not _SW_AVAIL or not team_path:
        return []
    cache_k = f"swteam_{team_path.replace('/','-')[:40]}"
    html    = sw_fetch(f"{SW_BASE}{team_path}", cache_k)
    if not html:
        return []
    try:
        soup    = _BS(html, "html.parser")
        injured = []
        # Soccerway แสดง injuries ใน section ที่มีคำว่า injur/ บาดเจ็บ
        inj_section = soup.find(lambda t: t.name in ("section","div","table") and
                                "injur" in str(t.get("class","")).lower())
        if inj_section:
            for row in inj_section.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    pname  = cells[0].get_text(strip=True)
                    reason = cells[-1].get_text(strip=True)[:30]
                    if pname and len(pname) > 2:
                        injured.append(f"{pname} ({reason})" if reason else pname)
        # Fallback: หาข้อความที่มีวันที่หลังชื่อ (pattern ใน Liverpool page)
        if not injured:
            for el in soup.find_all(string=lambda t: t and "เจ็บ" in t):
                txt = el.strip()
                if 3 < len(txt) < 80:
                    injured.append(txt)
        return [i for i in injured if i][:5]
    except Exception:
        return []

def sw_team_form(team_path):
    """ดึงฟอร์ม 5 นัดล่าสุดจากหน้า results ของ Soccerway
    คืน list เช่น ['W','D','L','W','W']"""
    if not _SW_AVAIL or not team_path:
        return []
    results_path = team_path.rstrip("/") + "/results/"
    cache_k      = f"swform_{team_path.replace('/','-')[:40]}"
    html         = sw_fetch(f"{SW_BASE}{results_path}", cache_k)
    if not html:
        return []
    try:
        soup    = _BS(html, "html.parser")
        results = []
        # หา table แถวผล W/D/L
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            for cell in cells:
                txt = cell.get_text(strip=True).upper()
                if txt in ("W", "D", "L") or txt in ("WIN", "DRAW", "LOSS", "LOST"):
                    results.append("W" if txt in ("W","WIN") else
                                   "D" if txt in ("D","DRAW") else "L")
                    break
            if len(results) >= 5:
                break
        return results[:5]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# ── CORE ANALYSIS ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def analyze_match(fixture, standings, h2h_raw, injuries_raw,
                  sw_home_path=None, sw_away_path=None, odds=None):
    fx = fixture.get("fixture", {})
    lg = fixture.get("league",  {})
    tm = fixture.get("teams",   {})

    fid        = fx.get("id", 0)
    lg_id      = lg.get("id", 0)
    lg_name    = lg.get("name", "Unknown")
    lg_flag    = lg.get("flag") or ""
    lg_country = lg.get("country", "")
    home_id    = tm.get("home", {}).get("id", 0)
    away_id    = tm.get("away", {}).get("id", 0)
    home_name  = tm.get("home", {}).get("name", "?")
    away_name  = tm.get("away", {}).get("name", "?")
    home_logo  = tm.get("home", {}).get("logo", "")
    away_logo  = tm.get("away", {}).get("logo", "")

    _TH_MONTHS = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.",
                  "ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
    try:
        utc     = datetime.fromisoformat(fx.get("date", "").replace("Z", "+00:00"))
        local   = utc.astimezone(TZ7)
        kickoff = local.strftime("%H:%M")
        date_d  = f'{local.day} {_TH_MONTHS[local.month]}'
    except Exception:
        kickoff, date_d = "--:--", "--"

    # ── ข้อมูลจาก standings ──────────────────────────────────────────────────
    hst    = standings.get(home_id, {})
    ast    = standings.get(away_id, {})
    h_rank = hst.get("rank", 99)
    a_rank = ast.get("rank", 99)

    # ── Form จาก standings form_str (Free plan ได้) ───────────────────────────
    h_form    = form_from_str(hst.get("form_str", ""))
    a_form    = form_from_str(ast.get("form_str", ""))

    # ── Soccerway: ดึงทุกครั้ง (ไม่ใช่แค่ fallback) ────────────────────────────
    sw_h_form, sw_a_form = [], []
    sw_h_inj,  sw_a_inj  = [], []
    if _SW_AVAIL:
        if sw_home_path:
            sw_h_form = sw_team_form(sw_home_path)      # ดึงเสมอ
            sw_h_inj  = sw_team_injuries(sw_home_path)  # ดึงเสมอ
        if sw_away_path:
            sw_a_form = sw_team_form(sw_away_path)
            sw_a_inj  = sw_team_injuries(sw_away_path)

    # Blend form: API เป็น primary, SW เติมเมื่อ API ไม่มีหรือ SW ครบกว่า
    if not h_form:
        h_form = sw_h_form
    elif sw_h_form and len(sw_h_form) > len(h_form):
        h_form = sw_h_form          # SW ให้ครบ 5 นัด ดีกว่า API ที่ตัดสั้น
    if not a_form:
        a_form = sw_a_form
    elif sw_a_form and len(sw_a_form) > len(a_form):
        a_form = sw_a_form

    h_pts5    = form_pts(h_form)
    a_pts5    = form_pts(a_form)
    h_fstr    = form_str_display(h_form)
    a_fstr    = form_str_display(a_form)
    has_form  = bool(h_form or a_form)

    # ── H2H ──────────────────────────────────────────────────────────────────
    h2h = h2h_stats(h2h_raw, home_id, away_id)
    h2h_matches = h2h_match_details(h2h_raw, home_id, away_id, limit=5)

    # ── Injuries (API + Soccerway fallback) ───────────────────────────────────
    _inj = ("Missing Fixture", "Questionable", "Injured")
    home_inj_list = [(i.get("player",{}).get("name","?"), i.get("player",{}).get("reason",""))
                     for i in injuries_raw if i.get("team",{}).get("id") == home_id
                     and i.get("player",{}).get("type","") in _inj]
    away_inj_list = [(i.get("player",{}).get("name","?"), i.get("player",{}).get("reason",""))
                     for i in injuries_raw if i.get("team",{}).get("id") == away_id
                     and i.get("player",{}).get("type","") in _inj]
    # เสริม SW injuries ถ้า API ว่าง
    if not home_inj_list and sw_h_inj:
        home_inj_list = [(n, "") for n in sw_h_inj]
    if not away_inj_list and sw_a_inj:
        away_inj_list = [(n, "") for n in sw_a_inj]
    home_inj = len(home_inj_list)
    away_inj = len(away_inj_list)

    # ── Motivation ────────────────────────────────────────────────────────────
    has_standings = h_rank < 90 and a_rank < 90
    h_label, h_gap, h_mot = team_situation(h_rank, hst.get("pts",0), lg_id, standings) if standings else ("","",0)
    a_label, a_gap, a_mot = team_situation(a_rank, ast.get("pts",0), lg_id, standings) if standings else ("","",0)

    # ── DATA SIGNALS (คุณภาพข้อมูล 0-5) ──────────────────────────────────────
    signals = 0.0
    if has_standings:           signals += 2.0   # rank, pts, form_str ← สำคัญที่สุด
    if has_form:                signals += 1.5   # 5-game form
    if h2h["n"] >= 3:           signals += 1.0   # H2H history
    if home_inj or away_inj:    signals += 0.5   # injury info
    MAX_SIGNALS = 5.0

    # quality_factor: 0 signals → 0.35 (ยังมี home adv + league avg),
    #                 5 signals → 1.00 (ข้อมูลครบ)
    quality_factor = max(0.35, min(1.0, signals / MAX_SIGNALS))

    # ── Win confidence (raw → calibrated) ────────────────────────────────────
    score = 50.0

    if has_form:
        form_diff = (h_pts5 - a_pts5) / 15
        score += form_diff * 20

    if has_standings:
        rank_diff = min(15, max(-15, (a_rank - h_rank) * 0.9))
        score += rank_diff

    if h2h["n"] >= 3:
        h2h_adv = (h2h["hw"] - h2h["aw"]) / h2h["n"]
        score += h2h_adv * 10

    score += 5                                          # home advantage
    score += (away_inj - home_inj) * 2.5               # injury edge

    if hst.get("home_p", 0) >= 3 and ast.get("away_p", 0) >= 3:
        h_home_wr = safe_div(hst["home_w"], hst["home_p"])
        a_away_wr = safe_div(ast["away_w"], ast["away_p"])
        score += (h_home_wr - a_away_wr) * 8

    score += (h_mot - a_mot) * 1.2

    x        = score - 50
    raw_conf = int(min(95, max(50, 50 + x * 1.45 - x * abs(x) / 250)))
    # ← Calibrate: ลดลง proportional กับข้อมูลที่ขาดหาย
    win_conf = int(50 + (raw_conf - 50) * quality_factor)

    if score >= 56:
        win_label, win_team = "Home Win", home_name
    elif score <= 44:
        win_label, win_team = "Away Win", away_name
    else:
        win_label, win_team = "Draw", "Even Match"

    _hcp_table = [
        (87, home_name, "-1.5"), (80, home_name, "-1.0"),
        (72, home_name, "-0.75"), (64, home_name, "-0.5"),
        (57, home_name, "-0.25"), (50, "Level",    "0.0"),
        (43, away_name, "-0.25"), (36, away_name, "-0.5"),
        (28, away_name, "-0.75"), (0,  away_name, "-1.0"),
    ]
    hcp_fav, hcp_val = "Level", "0.0"
    for threshold, fav, val in _hcp_table:
        if int(score) >= threshold:
            hcp_fav, hcp_val = fav, val
            break
    hcp_str = f"{hcp_fav} {hcp_val}" if hcp_fav != "Level" else "Level (0.0)"

    # ── O/U model ─────────────────────────────────────────────────────────────
    base_g = LEAGUE_GOALS.get(lg_id, DEF_GOALS)
    h_gpg  = safe_div(hst.get("gf", 0), max(1, hst.get("played", 0)))
    a_gpg  = safe_div(ast.get("gf", 0), max(1, ast.get("played", 0)))
    h_gcg  = safe_div(hst.get("ga", 0), max(1, hst.get("played", 0)))
    a_gcg  = safe_div(ast.get("ga", 0), max(1, ast.get("played", 0)))

    if h_gpg > 0.2 and a_gpg > 0.2:
        team_exp = (h_gpg + a_gcg + a_gpg + h_gcg) / 2
        exp_g    = base_g * 0.35 + team_exp * 0.65
    elif h2h.get("avg_goals"):
        exp_g = base_g * 0.45 + h2h["avg_goals"] * 0.55
    else:
        exp_g = base_g

    exp_g   = max(0.8, min(5.0, exp_g))
    ou_line = min(OU_LINES, key=lambda x: abs(x - exp_g))
    sigmoid = 1 / (1 + math.exp(-(exp_g - ou_line) * 2.6))
    over_p  = max(0.33, min(0.87, sigmoid))
    under_p = 1 - over_p

    def scale_ou(v):
        x2 = v - 50
        raw = int(min(95, max(50, 50 + x2 * 1.3)))
        return int(50 + (raw - 50) * quality_factor)   # ← Calibrate O/U ด้วย

    ov_raw     = int(over_p  * 100)
    un_raw     = int(under_p * 100)
    ou_label   = "Over" if ov_raw > un_raw else "Under"
    over_conf  = scale_ou(ov_raw)
    under_conf = scale_ou(un_raw)

    # ── Analysis text ─────────────────────────────────────────────────────────
    lines = []

    h_w5 = h_form[:5].count("W")
    a_w5 = a_form[:5].count("W")
    sw_note = " (Soccerway)" if (not hst.get("form_str") and (sw_h_form or sw_a_form)) else ""
    if h_pts5 >= 12:
        lines.append(f"เจ้าบ้านฟอร์มร้อนแรง {h_w5}/5 ชนะ (เจ้าบ้าน: {h_fstr} | เยือน: {a_fstr}){sw_note}")
    elif a_pts5 >= 12:
        lines.append(f"เยือนฟอร์มยอดเยี่ยม {a_w5}/5 ชนะ (เยือน: {a_fstr} | เจ้าบ้าน: {h_fstr}){sw_note}")
    elif has_form and h_pts5 > a_pts5 + 3:
        lines.append(f"ฟอร์มเจ้าบ้าน {h_fstr} ({h_pts5} แต้ม) เหนือเยือน {a_fstr} ({a_pts5} แต้ม){sw_note}")
    elif has_form and a_pts5 > h_pts5 + 3:
        lines.append(f"ฟอร์มเยือน {a_fstr} ({a_pts5} แต้ม) เหนือเจ้าบ้าน {h_fstr} ({h_pts5} แต้ม){sw_note}")
    elif has_form:
        lines.append(f"ฟอร์มสมน้ำสมเนื้อ — เจ้าบ้าน: {h_fstr} / เยือน: {a_fstr}{sw_note}")
    else:
        lines.append("ไม่มีข้อมูลฟอร์ม (ลีกนอก Top-tier)")

    if has_standings:
        h_pts_val = hst.get("pts", 0)
        a_pts_val = ast.get("pts", 0)
        h_sit = f" {h_label}" if h_label else ""
        a_sit = f" {a_label}" if a_label else ""
        h_gp  = f" ({h_gap})" if h_gap else ""
        a_gp  = f" ({a_gap})" if a_gap else ""
        lines.append(f"ตาราง: #{h_rank} ({h_pts_val}pts){h_sit}{h_gp} vs #{a_rank} ({a_pts_val}pts){a_sit}{a_gp}")
    elif h_gpg > 0:
        lines.append(f"สถิติยิง — เจ้าบ้าน {h_gpg:.1f}/นัด เสีย {h_gcg:.1f} | เยือน {a_gpg:.1f}/นัด เสีย {a_gcg:.1f}")
    else:
        lines.append("ไม่มีข้อมูลตาราง (ลีกนี้ไม่รองรับบน Free plan)")

    # แสดง quality indicator ในบรรทัด analysis
    sig_label = f"[คุณภาพข้อมูล {signals:.1f}/{MAX_SIGNALS:.0f} · ความน่าเชื่อถือ {int(quality_factor*100)}%]"
    lines.append(sig_label)

    mot_parts = []
    if h_mot >= 7:
        goal = h_label.replace("🏆 ","").replace("🥇 ","").replace("⚠️ ","").replace("🥈 ","")
        mot_parts.append(f"เจ้าบ้านต้องชนะเพื่อ{goal} {h_gap}")
    elif h_mot >= 5:
        mot_parts.append(f"เจ้าบ้าน{h_label} {h_gap}")
    elif h_mot <= -3:
        mot_parts.append("เจ้าบ้านจบซีซันปลอดภัยแล้ว อาจโรเตชัน")
    if a_mot >= 7:
        goal = a_label.replace("🏆 ","").replace("🥇 ","").replace("⚠️ ","").replace("🥈 ","")
        mot_parts.append(f"เยือนต้องชนะเพื่อ{goal} {a_gap}")
    elif a_mot >= 5:
        mot_parts.append(f"เยือน{a_label} {a_gap}")
    elif a_mot <= -3:
        mot_parts.append("ทีมเยือนจบซีซันปลอดภัยแล้ว อาจโรเตชัน")
    if mot_parts:
        lines.append(" · ".join(mot_parts[:2]))

    extra = []
    if h2h["n"] >= 3:
        extra.append(f"H2H {h2h['n']} นัด — เจ้าบ้านชนะ {h2h['hw']} เสมอ {h2h['d']} แพ้ {h2h['aw']}" +
                     (f" เฉลี่ย {h2h['avg_goals']} ประตู/นัด" if h2h['avg_goals'] else ""))
    if away_inj >= 2:
        names = ", ".join(n for n, _ in away_inj_list[:3])
        extra.append(f"เยือนขาดตัวหลัก {away_inj} คน: {names}")
    elif away_inj == 1:
        extra.append(f"เยือนขาดตัวหลัก 1 คน ({away_inj_list[0][0]})")
    if home_inj >= 2:
        names = ", ".join(n for n, _ in home_inj_list[:3])
        extra.append(f"เจ้าบ้านขาดตัวหลัก {home_inj} คน: {names}")
    elif home_inj == 1:
        extra.append(f"เจ้าบ้านขาดตัวหลัก 1 คน ({home_inj_list[0][0]})")
    if not extra:
        txt = f"คาดการณ์สกอร์รวม ~{exp_g:.1f} ประตู {'เหนือ' if ou_label=='Over' else 'ต่ำกว่า'} Line {ou_line}"
        extra.append(txt)
    lines.append(" · ".join(extra[:2]))

    combined_conf = int(win_conf * 0.55 + max(over_conf, under_conf) * 0.45)
    combined_ou   = f"O {ou_line}" if ou_label == "Over" else f"U {ou_line}"

    # ── Odds processing ───────────────────────────────────────────────────────
    h_odds = d_odds = a_odds = ou_over = ou_under = ou_line_mkt = None
    hcp_pt = hcp_price = None
    bookmaker = None
    h_edge = d_edge = a_edge = None
    ou_edge = None

    if odds:
        h_odds      = odds.get("h_odds")
        d_odds      = odds.get("d_odds")
        a_odds      = odds.get("a_odds")
        ou_over     = odds.get("ou_over")
        ou_under    = odds.get("ou_under")
        ou_line_mkt = odds.get("ou_line_mkt")
        hcp_pt      = odds.get("hcp_pt")
        hcp_price   = odds.get("hcp_price")
        bookmaker   = odds.get("bookmaker", "")

        # implied probabilities
        h_impl = odds_implied(h_odds)
        d_impl = odds_implied(d_odds)
        a_impl = odds_implied(a_odds)

        # model win_conf is "home win probability"
        # Map score → away win prob (mirror of win_conf)
        model_home_pct = win_conf if win_label == "Home Win" else (100 - win_conf)
        model_away_pct = 100 - win_conf if win_label == "Home Win" else win_conf
        model_draw_pct = max(0, 100 - model_home_pct - model_away_pct + 10)  # rough draw estimate

        h_edge  = value_edge(model_home_pct, h_impl) if h_impl else None
        a_edge  = value_edge(model_away_pct, a_impl) if a_impl else None
        d_edge  = value_edge(model_draw_pct, d_impl) if d_impl else None

        # O/U edge using over_conf
        if ou_label == "Over" and ou_over:
            ou_edge = value_edge(over_conf, odds_implied(ou_over))
        elif ou_label == "Under" and ou_under:
            ou_edge = value_edge(under_conf, odds_implied(ou_under))

        # บรรทัด odds ใน analysis
        if h_odds and d_odds and a_odds:
            val_note = ""
            best_edge = max(
                [(h_edge or -9, f"Value: เจ้าบ้าน {h_odds} (+{h_edge*100:.0f}%)"),
                 (d_edge or -9, f"Value: เสมอ {d_odds} (+{d_edge*100:.0f}%)"),
                 (a_edge or -9, f"Value: เยือน {a_odds} (+{a_edge*100:.0f}%)")],
                key=lambda x: x[0]
            )
            if best_edge[0] >= 0.05:
                val_note = f" ← {best_edge[1]}"
            lines.append(
                f"ราคา [{bookmaker}]: เจ้าบ้าน {h_odds} | เสมอ {d_odds} | เยือน {a_odds}{val_note}")
        if ou_over and ou_under:
            lines.append(
                f"O/U {ou_line_mkt}: Over {ou_over} | Under {ou_under}"
                + (f"  ← value {'Over' if ou_edge and ou_edge>0.05 else 'Under'}" if ou_edge and abs(ou_edge)>0.05 else ""))

    return {
        "fixture_id":    fid,
        "league_id":     lg_id,
        "league":        lg_name,
        "country":       lg_country,
        "flag":          lg_flag,
        "home":          home_name,
        "away":          away_name,
        "home_logo":     home_logo,
        "away_logo":     away_logo,
        "kickoff":       kickoff,
        "date_d":        date_d,
        "win_label":     win_label,
        "win_team":      win_team,
        "win_conf":      win_conf,
        "hcp":           hcp_str,
        "home_rank":     h_rank if h_rank < 90 else None,
        "away_rank":     a_rank if a_rank < 90 else None,
        "home_pts":      hst.get("pts"),
        "away_pts":      ast.get("pts"),
        "home_form":     h_fstr,
        "away_form":     a_fstr,
        "ou_line":       ou_line,
        "ou_label":      ou_label,
        "over_conf":     over_conf,
        "under_conf":    under_conf,
        "exp_goals":     round(exp_g, 1),
        "combined_conf": combined_conf,
        "combined_ou":   combined_ou,
        "analysis":      lines,
        "raw_score":     score,
        "has_data":      has_standings or has_form,
        "data_signals":  signals,
        "quality_pct":   int(quality_factor * 100),
        "sw_used":       bool(sw_h_form or sw_a_form or sw_h_inj or sw_a_inj),
        # odds
        "h_odds":        h_odds,
        "d_odds":        d_odds,
        "a_odds":        a_odds,
        "ou_over":       ou_over,
        "ou_under":      ou_under,
        "ou_line_mkt":   ou_line_mkt,
        "hcp_pt":        hcp_pt,
        "hcp_price":     hcp_price,
        "bookmaker":     bookmaker,
        "h_edge":        h_edge,
        "a_edge":        a_edge,
        "ou_edge":       ou_edge,
        # H2H detail (รายแมตช์ 5 นัดล่าสุด)
        "h2h_matches":   h2h_matches,
        "home_injury":   home_inj,
        "away_injury":   away_inj,
        "home_inj_list": [f"{n}{' ('+r+')' if r else ''}" for n,r in home_inj_list[:5]],
        "away_inj_list": [f"{n}{' ('+r+')' if r else ''}" for n,r in away_inj_list[:5]],
    }

# ── HTML helpers ───────────────────────────────────────────────────────────────
def ccol(pct):
    if pct >= 82: return "#2ea043"
    if pct >= 72: return "#f7931a"
    return "#8b949e"

def bar(pct):
    c = ccol(pct)
    return (f'<div class="br"><div class="bf" style="width:{pct}%;background:{c}"></div>'
            f'</div><span class="pp" style="color:{c}">{pct}%</span>')

def rank_str(rank, pts):
    if rank is None: return ""
    return f"#{rank} ({pts}pts)" if pts is not None else f"#{rank}"

def analysis_html(lines):
    return "".join(f'<p class="al">{l}</p>' for l in lines if l)

# ── HTML helpers ────────────────────────────────────────────────────────────────
def ccol(pct):
    if pct >= 85: return "#2ea043"   # เขียว = มั่นใจสูง ≥85%
    if pct >= 70: return "#f7931a"   # ส้ม   = ปานกลาง 70-84%
    return "#8b949e"                  # เทา   = ต่ำกว่า 70%

def form_dots(form_str):
    if not form_str or form_str == "N/A":
        return '<span class="no-form">ไม่มีข้อมูล</span>'
    html = ""
    for ch in form_str.upper():
        if ch == "W": html += '<span class="fd fw">ชนะ</span>'
        elif ch == "D": html += '<span class="fd fd_">เสมอ</span>'
        elif ch == "L": html += '<span class="fd fl">แพ้</span>'
    return html or '<span class="no-form">ไม่มีข้อมูล</span>'

def pred_bar(pct, label, sub=""):
    c = ccol(pct)
    s = ('<span class="pb-sub">' + sub + "</span>") if sub else ""
    return (
        '<div class="pb-row">'
        '<div class="pb-lbl">' + label + s + '</div>'
        '<div class="pb-track"><div class="pb-fill" style="width:' + str(pct) + '%;background:' + c + '"></div></div>'
        '<div class="pb-pct" style="color:' + c + '">' + str(pct) + '%</div>'
        '</div>'
    )

def card(m):
    qpct = m.get("quality_pct", 0)
    if qpct >= 80:   qbadge = '<span class="badge q-hi">ข้อมูล ' + str(qpct) + '%</span>'
    elif qpct >= 55: qbadge = '<span class="badge q-md">ข้อมูล ' + str(qpct) + '%</span>'
    else:            qbadge = '<span class="badge q-lo">ข้อมูลน้อย ' + str(qpct) + '%</span>'
    sw_badge = '<span class="badge sw">SW</span>' if m.get("sw_used") else ""

    flag = ('<img class="flag" src="' + m["flag"] + '" onerror="this.style.display=\'none\'">') if m.get("flag") else ""
    h_logo = ('<img class="t-logo" src="' + m["home_logo"] + '" onerror="this.style.display=\'none\'">') if m.get("home_logo") else ""
    a_logo = ('<img class="t-logo" src="' + m["away_logo"] + '" onerror="this.style.display=\'none\'">') if m.get("away_logo") else ""

    # อันดับ
    hr = m.get("home_rank"); hpts = m.get("home_pts")
    ar = m.get("away_rank"); apts = m.get("away_pts")
    h_rank = ('<div class="t-rank">อันดับ ' + str(hr) + (" &bull; " + str(hpts) + " แต้ม" if hpts else "") + "</div>") if hr else ""
    a_rank = ('<div class="t-rank">อันดับ ' + str(ar) + (" &bull; " + str(apts) + " แต้ม" if apts else "") + "</div>") if ar else ""

    # ฟอร์ม
    h_form_html = form_dots(m.get("home_form", ""))
    a_form_html = form_dots(m.get("away_form", ""))

    # ทำนาย
    win_lbl  = m.get("win_label", "")
    win_team = m.get("win_team", "")
    if "Home" in win_lbl:   win_txt = "เจ้าบ้านชนะ — " + win_team;  win_icon = "🏠"
    elif "Away" in win_lbl: win_txt = "ทีมเยือนชนะ — " + win_team; win_icon = "✈️"
    else:                   win_txt = "เสมอกัน";                     win_icon = "🤝"

    ou_lbl  = m.get("ou_label", "Over")
    ou_line = m.get("ou_line", 2.5)
    exp_g   = m.get("exp_goals", "?")
    ou_dir  = "สูงกว่า" if ou_lbl == "Over" else "ต่ำกว่า"
    ou_icon = "📈" if ou_lbl == "Over" else "📉"
    ou_conf = m["over_conf"] if ou_lbl == "Over" else m["under_conf"]

    # ราคาต่อรอง
    odds_html = ""
    if m.get("h_odds") and m.get("d_odds") and m.get("a_odds"):
        ho = m["h_odds"]; do_ = m["d_odds"]; ao = m["a_odds"]
        bk = m.get("bookmaker", "") or ""
        h_ip = int(round(100/ho)) if ho else 0
        a_ip = int(round(100/ao)) if ao else 0

        def _ec(e):
            if e is None: return ""
            if e >= 0.08: return " style=\"color:#2ea043;font-weight:700\""
            if e >= 0.04: return " style=\"color:#f7931a\""
            return ""

        hec = _ec(m.get("h_edge")); aec = _ec(m.get("a_edge"))

        ou_odds = ""
        if m.get("ou_over") and m.get("ou_under"):
            ol = m.get("ou_line_mkt") or 2.5
            oec = _ec(m.get("ou_edge") if ou_lbl == "Over" else None)
            uec = _ec(m.get("ou_edge") if ou_lbl == "Under" else None)
            ou_odds = (
                '<div class="o-ou-row">'
                '<span class="ou-lbl">สูง/ต่ำ ' + str(ol) + '</span>'
                '<span class="ou-val"' + oec + '>Over ' + str(m["ou_over"]) + '</span>'
                '<span class="ou-sep"> | </span>'
                '<span class="ou-val"' + uec + '>Under ' + str(m["ou_under"]) + '</span>'
                '</div>'
            )
        hcp_row = ""
        if m.get("hcp_pt") is not None and m.get("hcp_price"):
            sign = "+" if m["hcp_pt"] > 0 else ""
            hcp_row = '<div class="o-hcp-row">แต้มต่อ: ' + m["home"] + " " + sign + str(m["hcp_pt"]) + " @ " + str(m["hcp_price"]) + '</div>'

        bk_txt = (' <span class="bk-name">(' + bk + ")</span>") if bk else ""
        odds_html = (
            '<div class="odds-sec">'
            '<div class="odds-title">💰 ราคาต่อรอง' + bk_txt + '</div>'
            '<div class="odds-1x2">'
            '<div class="o-cell"><div class="o-price"' + hec + '>' + str(ho) + '</div><div class="o-name">เจ้าบ้าน<br><small>' + str(h_ip) + '%</small></div></div>'
            '<div class="o-cell"><div class="o-price">' + str(do_) + '</div><div class="o-name">เสมอ<br><small>X</small></div></div>'
            '<div class="o-cell"><div class="o-price"' + aec + '>' + str(ao) + '</div><div class="o-name">เยือน<br><small>' + str(a_ip) + '%</small></div></div>'
            '</div>'
            + ou_odds + hcp_row +
            '</div>'
        )

    # วิเคราะห์
    ana = m.get("analysis", [])
    ana_html = "".join('<div class="al">' + l + "</div>" for l in ana if l)

    c_conf = ccol(m["combined_conf"])

    return (
        '<div class="mc" data-conf="' + str(m["combined_conf"]) + '" data-ou="' + str(ou_lbl) + '">'
        # ── header
        '<div class="mc-hdr">'
        '<div class="mc-lg">' + flag + ' ' + m["league"]
        + (' <span class="country">(' + m.get("country","") + ')</span>' if m.get("country") else "") + '</div>'
        '<div class="mc-time">⏰ ' + m["kickoff"] + ' น. &bull; ' + m["date_d"] + '</div>'
        '<div class="mc-badges">' + qbadge + sw_badge + '</div>'
        '</div>'
        # ── ทีม
        '<div class="mc-teams">'
        '<div class="team-col">'
        + h_logo +
        '<div class="t-name">' + m["home"] + '</div>'
        + h_rank +
        '<div class="form-row">' + h_form_html + '</div>'
        '</div>'
        '<div class="vs-col">'
        '<div class="vs-txt">VS</div>'
        '<div class="hcp-lbl">' + str(m.get("hcp","")) + '</div>'
        '</div>'
        '<div class="team-col away">'
        + a_logo +
        '<div class="t-name">' + m["away"] + '</div>'
        + a_rank +
        '<div class="form-row">' + a_form_html + '</div>'
        '</div>'
        '</div>'
        # ── ทำนาย
        '<div class="mc-pred">'
        '<div class="pred-title" style="color:' + c_conf + '">🎯 ทำนาย &nbsp;<span class="conf-num">' + str(m["combined_conf"]) + '%</span></div>'
        + pred_bar(m["win_conf"], win_icon + " " + win_txt)
        + pred_bar(ou_conf, ou_icon + " " + ou_dir + " " + str(ou_line) + " ประตู", " (คาด ~" + str(exp_g) + ")")
        + '</div>'
        # ── ราคา + วิเคราะห์
        '<div class="mc-bottom">'
        + odds_html +
        '<div class="mc-analysis"><div class="ana-title">📊 วิเคราะห์</div>' + ana_html + '</div>'
        '</div>'
        '</div>'
    )

def build_html(matches, total_raw, total_leagues, call_count):
    # TIER1 (ทีมชาติ/รายการใหญ่) แสดงเสมอแม้ข้อมูลน้อย
    good    = [m for m in matches
               if m.get("data_signals", 0) >= MIN_SIGNALS
               or m.get("league_id") in TIER1]
    skipped = len(matches) - len(good)
    # เรียง: TIER1 ก่อน (signals ใดก็ได้), แล้วตาม combined_conf
    good.sort(key=lambda x: (0 if x.get("league_id") in TIER1 else 1,
                              -x["combined_conf"]))

    high_conf  = sum(1 for m in good if m["combined_conf"] >= 85)
    has_data_n = sum(1 for m in good if m.get("has_data"))
    odds_n     = sum(1 for m in good if m.get("h_odds"))

    cards_html = "\n".join(card(m) for m in good)
    if not cards_html:
        cards_html = "<div class='empty'>ไม่มีคู่ผ่านเกณฑ์วันนี้</div>"

    return """<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>STEP วิเคราะห์บอล — """ + TODAY + """</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#0d1117;color:#e6edf3;
  font-family:'Segoe UI',Tahoma,sans-serif;min-height:100%}
/* ── Header */
#hdr{display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:8px;padding:10px 20px;
  background:linear-gradient(90deg,#0d1117,#161b22);
  border-bottom:2px solid #21262d;position:sticky;top:0;z-index:99}
#hdr h1{font-size:1.15em;color:#58a6ff;font-weight:700;white-space:nowrap}
.sub{color:#8b949e;font-size:.68em;margin-top:2px}
.stats{display:flex;gap:5px;flex-wrap:wrap;align-items:center}
.st{background:#161b22;border:1px solid #30363d;border-radius:12px;
  padding:2px 10px;font-size:.7em;white-space:nowrap}
.st b{color:#58a6ff}.st.ok b{color:#2ea043}.st.sk b{color:#da3633}
/* ── Filter bar */
#fbar{display:flex;gap:6px;padding:10px 20px;background:#0d1117;
  border-bottom:1px solid #21262d;flex-wrap:wrap;position:sticky;top:52px;z-index:98}
.fb{background:#161b22;border:1px solid #30363d;border-radius:16px;
  padding:4px 14px;font-size:.75em;cursor:pointer;color:#8b949e;
  transition:all .15s;white-space:nowrap}
.fb:hover{border-color:#58a6ff;color:#58a6ff}
.fb.active{background:#1f3a5f;border-color:#58a6ff;color:#58a6ff;font-weight:600}
/* ── Card grid */
#main{padding:14px 20px;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:14px}
@media(max-width:600px){#main{grid-template-columns:1fr;padding:8px}}
/* ── Match card */
.mc{background:#161b22;border:1px solid #21262d;border-radius:10px;
  overflow:hidden;transition:border-color .15s,box-shadow .15s}
.mc:hover{border-color:#30363d;box-shadow:0 2px 12px #00000055}
/* Card Header */
.mc-hdr{display:flex;align-items:center;gap:8px;padding:8px 12px;
  background:#0d1117;border-bottom:1px solid #21262d;flex-wrap:wrap}
.mc-lg{font-size:.72em;color:#4a90d9;font-weight:600;flex:1;min-width:0;
  display:flex;align-items:center;gap:4px}
.country{color:#8b949e;font-weight:400}
.mc-time{font-size:.68em;color:#8b949e;white-space:nowrap}
.mc-badges{display:flex;gap:4px;flex-wrap:wrap}
.flag{width:14px;height:10px;object-fit:cover;border-radius:1px}
.badge{font-size:.6em;padding:1px 6px;border-radius:4px;white-space:nowrap;font-weight:600}
.q-hi{background:#2ea04322;color:#2ea043;border:1px solid #2ea04355}
.q-md{background:#f7931a22;color:#f7931a;border:1px solid #f7931a55}
.q-lo{background:#da363322;color:#da3633;border:1px solid #da363355}
.sw{background:#58a6ff22;color:#58a6ff;border:1px solid #58a6ff55}
/* Teams */
.mc-teams{display:grid;grid-template-columns:1fr 60px 1fr;gap:0;
  padding:10px 12px;border-bottom:1px solid #21262d;align-items:center}
.team-col{display:flex;flex-direction:column;align-items:center;text-align:center;gap:4px}
.away{text-align:center}
.t-logo{width:32px;height:32px;object-fit:contain}
.t-name{font-size:.9em;font-weight:700;color:#e6edf3;line-height:1.3}
.t-rank{font-size:.65em;color:#58a6ff;background:#58a6ff18;
  border-radius:4px;padding:1px 6px;white-space:nowrap}
.form-row{display:flex;gap:3px;flex-wrap:wrap;justify-content:center;margin-top:2px}
.fd{font-size:.58em;padding:2px 5px;border-radius:3px;font-weight:700;letter-spacing:.3px}
.fw{background:#2ea04333;color:#2ea043;border:1px solid #2ea04355}
.fd_{background:#f7931a22;color:#f7931a;border:1px solid #f7931a44}
.fl{background:#da363322;color:#da3633;border:1px solid #da363344}
.no-form{font-size:.6em;color:#484f58;font-style:italic}
.vs-col{display:flex;flex-direction:column;align-items:center;gap:4px}
.vs-txt{font-size:.75em;font-weight:700;color:#484f58}
.hcp-lbl{font-size:.6em;color:#8b949e;text-align:center;line-height:1.3}
/* Prediction */
.mc-pred{padding:10px 12px;border-bottom:1px solid #21262d}
.pred-title{font-size:.75em;font-weight:700;margin-bottom:6px;display:flex;align-items:center;gap:6px}
.conf-num{font-size:1.1em}
.pb-row{display:flex;align-items:center;gap:8px;margin-bottom:5px}
.pb-lbl{font-size:.68em;color:#c9d1d9;flex:0 0 220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pb-sub{color:#8b949e;font-size:.85em}
.pb-track{flex:1;height:6px;background:#21262d;border-radius:3px;overflow:hidden}
.pb-fill{height:100%;border-radius:3px;transition:width .3s}
.pb-pct{font-size:.72em;font-weight:700;min-width:36px;text-align:right}
/* Bottom: odds + analysis */
.mc-bottom{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid #21262d}
@media(max-width:480px){.mc-bottom{grid-template-columns:1fr}}
.odds-sec{padding:10px 12px;border-right:1px solid #21262d}
.odds-title{font-size:.7em;font-weight:700;color:#4a90d9;margin-bottom:6px}
.bk-name{color:#8b949e;font-weight:400}
.odds-1x2{display:flex;gap:6px;margin-bottom:6px}
.o-cell{flex:1;text-align:center;background:#0d1929;border:1px solid #1f3a5f;
  border-radius:6px;padding:6px 4px}
.o-price{font-size:.85em;font-weight:700;color:#c9d1d9}
.o-name{font-size:.58em;color:#8b949e;margin-top:2px;line-height:1.4}
.o-ou-row{font-size:.65em;color:#8b949e;display:flex;gap:6px;align-items:center;margin-bottom:4px}
.ou-lbl{color:#4a90d9;font-weight:600}
.ou-val{color:#c9d1d9}
.ou-sep{color:#484f58}
.o-hcp-row{font-size:.62em;color:#8b949e}
.mc-analysis{padding:10px 12px}
.ana-title{font-size:.7em;font-weight:700;color:#8b949e;margin-bottom:5px}
.al{font-size:.65em;color:#8b949e;line-height:1.55;margin-bottom:2px;padding-left:6px;
  border-left:2px solid #21262d}
.al:first-child{color:#c9d1d9;border-left-color:#58a6ff}
.empty{color:#484f58;font-style:italic;padding:48px;text-align:center;font-size:.85em}
.mc.hidden{display:none}
</style>
</head>
<body>
<div id="hdr">
  <div>
    <h1>⚽ STEP วิเคราะห์บอล</h1>
    <div class="sub">โปรแกรมบอล """ + WINDOW_LABEL + """ &bull; อัพเดท """ + NOW + """</div>
  </div>
  <div class="stats">
    <div class="st">ทั้งหมด <b>""" + str(total_raw) + """</b></div>
    <div class="st ok">วิเคราะห์แล้ว <b>""" + str(len(good)) + """</b></div>
    <div class="st">มีข้อมูล <b>""" + str(has_data_n) + """</b></div>
    <div class="st">มั่นใจสูง <b>""" + str(high_conf) + """</b></div>
    <div class="st">ลีก <b>""" + str(total_leagues) + """</b></div>
    """ + (f'<div class="st">มีราคา <b>{odds_n}</b></div>' if odds_n else "") + """
    <div class="st sk">กรองออก <b>""" + str(skipped) + """</b></div>
    <div class="st" style="color:#484f58">API <b>""" + str(call_count) + """</b></div>
  </div>
</div>
<div id="fbar">
  <button class="fb active" onclick="filter('all')">ทั้งหมด <span id="cnt-all"></span></button>
  <button class="fb" onclick="filter('win')">🏠 ทีมชนะ <span id="cnt-win"></span></button>
  <button class="fb" onclick="filter('over')">📈 สกอร์สูง <span id="cnt-over"></span></button>
  <button class="fb" onclick="filter('under')">📉 สกอร์ต่ำ <span id="cnt-under"></span></button>
  <button class="fb" onclick="filter('best')">⭐ มั่นใจสูง ≥85% <span id="cnt-best"></span></button>
</div>
<div id="main">
""" + cards_html + """
</div>
<script>
function filter(mode){
  document.querySelectorAll('.fb').forEach(b=>b.classList.remove('active'));
  event.target.closest('.fb').classList.add('active');
  const cards = document.querySelectorAll('.mc');
  let counts = {all:0,win:0,over:0,under:0,best:0};
  cards.forEach(c=>{
    const conf = parseInt(c.dataset.conf)||0;
    const ou   = c.dataset.ou;
    const winLabel = c.dataset.win||'';
    const isWin   = winLabel.includes('Home') || winLabel.includes('Away');
    const isOver  = ou === 'Over';
    const isUnder = ou === 'Under';
    const isBest  = conf >= 85;
    counts.all++;
    if(isWin)  counts.win++;
    if(isOver) counts.over++;
    if(isUnder)counts.under++;
    if(isBest) counts.best++;
    let show = false;
    if(mode==='all')   show=true;
    if(mode==='win')   show=isWin;
    if(mode==='over')  show=isOver;
    if(mode==='under') show=isUnder;
    if(mode==='best')  show=isBest;
    c.classList.toggle('hidden',!show);
  });
  ['all','win','over','under','best'].forEach(k=>{
    const el=document.getElementById('cnt-'+k);
    if(el) el.textContent='('+counts[k]+')';
  });
}
filter('all');
</script>
</body>
</html>"""


def main():
    print(f"\n{'='*62}")
    print(f"  STEP Smart Dashboard v3.2  —  Free-plan + Soccerway Always-On + Odds")
    print(f"  {TODAY}  {NOW}  (season {SEASON})")
    sw_status = "✓ BeautifulSoup พร้อม" if _SW_AVAIL else "⚠ ไม่มี BeautifulSoup — ข้ามการดึง Soccerway"
    print(f"  Soccerway: {sw_status}")
    print(f"{'='*62}\n")

    # ── โหลด / บันทึก API key (API-Football) ──────────────────────────────
    cfg     = load_cfg()
    api_key = os.environ.get("RAPIDAPI_KEY", cfg.get("api_key", "")).strip()

    if not api_key:
        print("  ⚠️  ไม่พบ API key (API-Football)")
        print("  กรุณาใส่ API-Football key (จาก api-sports.io หรือ RapidAPI):")
        api_key = input("  API Key: ").strip()
        if not api_key:
            print("  ✗ ไม่มี key — ยกเลิก")
            return
        cfg["api_key"] = api_key
        save_cfg(cfg)
        print("  ✓ บันทึก key แล้ว (จะใช้อัตโนมัติครั้งต่อไป)\n")
    else:
        print(f"  ✓ API-Football key พร้อม (จาก {'env' if os.environ.get('RAPIDAPI_KEY') else 'config file'})")

    # ── โหลด / บันทึก Odds API key ────────────────────────────────────────
    odds_key = os.environ.get("ODDS_API_KEY", cfg.get("odds_api_key", "")).strip()
    if not odds_key:
        print("  ⚠️  ไม่มี Odds API key — ข้ามราคาต่อรอง")
        if sys.stdin.isatty():
            # รันตรง terminal → ถามได้
            print("     สมัครฟรีที่ theoddsapi.com (500 req/เดือน) แล้วพิมพ์ key:")
            inp = input("  Odds API Key (Enter ข้าม): ").strip()
            if inp:
                odds_key = inp
                cfg["odds_api_key"] = odds_key
                save_cfg(cfg)
                print("  ✓ บันทึก Odds key แล้ว\n")
            else:
                print("  – ข้ามราคาต่อรอง\n")
        else:
            # รันผ่าน Claude Code / pipe → ไม่ถาม แสดงวิธีเพิ่ม key แทน
            print(f"  → เพิ่ม key ใน {CFG_FILE}")
            print('     เปิดไฟล์แล้วเพิ่ม: "odds_api_key": "YOUR_KEY_HERE"')
            print("     (สมัครฟรีที่ theoddsapi.com)\n")
    else:
        print(f"  ✓ Odds API key พร้อม\n")

    # ── Step 1: Fixtures ───────────────────────────────────────────────────
    print("  [1/5] ตรวจสอบ API header style & ดึง fixtures...")
    global _hdrs
    _hdrs, first_page = detect_headers(api_key)
    if not _hdrs:
        print("  ✗ API ใช้ไม่ได้ — ตรวจสอบ key และเน็ต")
        cfg.pop("api_key", None)
        save_cfg(cfg)
        return

    all_fixtures = fetch_fixtures(first_page)

    tmr_data = af_get("fixtures", f"date={TOMORROW}", f"fixtures_all_{TOMORROW}")
    if tmr_data and not tmr_data.get("errors"):
        tmr_items = tmr_data.get("response", [])
        print(f"    พรุ่งนี้ ({TOMORROW}) — {len(tmr_items)} fixtures")
        all_fixtures.extend(tmr_items)

    print(f"  รวม fixtures (วันนี้+พรุ่งนี้): {len(all_fixtures)}\n")

    # ── Auto-discovery: filter ด้วย timestamp window 12:00→12:00 (UTC+7) ──────
    # ไม่ใช้ date string เพราะ UTC date ≠ Thai date
    # เอาเฉพาะแมตช์ที่ยังไม่เตะ AND อยู่ใน window ที่กำหนด
    all_ns = [f for f in all_fixtures
              if f.get("fixture", {}).get("status", {}).get("short", "") in ("NS", "TBD")
              and WINDOW_START_TS
                 <= f.get("fixture", {}).get("timestamp", 0)
                 <= WINDOW_END_TS]

    def _sort_key(f):
        return (0 if f["league"]["id"] in TIER1 else 1,
                f["fixture"].get("timestamp", 9e9))
    all_ns.sort(key=_sort_key)

    # สรุปลีกที่พบในช่วงเวลานี้
    leagues_today = {}
    for f in all_ns:
        lid = f["league"]["id"]
        leagues_today[lid] = f'{f["league"].get("name","?")} ({f["league"].get("country","?")})'

    print(f"  ช่วงเวลา: {WINDOW_LABEL}")
    print(f"  พบ {len(all_ns)} แมตช์จาก {len(leagues_today)} ลีก/รายการ")

    # Build league→season map จาก fixture data (API บอกเองว่า season อะไร)
    league_season_map = {}
    for f in all_ns:
        lid = f["league"]["id"]
        s   = f["league"].get("season")
        if s and lid not in league_season_map:
            league_season_map[lid] = int(s)

    # Pre-select candidates (วันนี้ก่อน, จำกัด unique leagues ให้ไม่เกิน budget)
    # 1 call ต่อลีก (standings) + 2 calls ต่อแมตช์ (H2H + injuries)
    # เผื่อ fixtures + odds = 5 calls
    pre_candidates = all_ns[:DEEP_CANDIDATES]
    unique_leagues_pre = list({f["league"]["id"] for f in pre_candidates})

    # ── Step 2: Standings (auto-season) ───────────────────────────────────
    print(f"\n  [2/6] ดึง standings ({len(unique_leagues_pre)} ลีก)...")
    all_standings = {}
    for lid in unique_leagues_pre:
        hint_s = league_season_map.get(lid)
        lname  = leagues_today.get(lid, str(lid))
        print(f"    {lname[:42]:<42} s={hint_s}...", end=" ", flush=True)
        st = fetch_standings(lid, hint_season=hint_s)
        all_standings.update(st)
        print(f"✓ {len(st)} ทีม" if st else "– ไม่มีข้อมูล")

    # ── คำนวณ candidates จาก API calls ที่เหลือจริงๆ ─────────────────────
    calls_used      = _call_count
    calls_for_match = 2          # H2H + injuries (1 each)
    calls_buffer    = 3          # เผื่อ odds / misc
    max_matches     = max(5, (MAX_API_CALLS - calls_used - calls_buffer) // calls_for_match)
    candidates      = all_ns[:max_matches]
    unique_leagues  = list({f["league"]["id"] for f in candidates})

    print(f"\n  Budget: {calls_used} calls used → เหลือ {MAX_API_CALLS-calls_used} → วิเคราะห์ได้ {max_matches} แมตช์")
    print()

    # ── Step 3: Betting Odds ───────────────────────────────────────────────
    print(f"  [3/6] ดึงราคาต่อรอง (The Odds API)...")
    all_odds_map = {}
    if odds_key:
        all_odds_map = fetch_odds_for_leagues(unique_leagues, odds_key)
        if not all_odds_map and _SW_AVAIL:
            print("    → ลอง BetExplorer fallback...")
            all_odds_map = betexplorer_today_odds()
        print(f"    รวม {len(all_odds_map)} คู่มีราคา")
    else:
        print("    – ข้าม (ไม่มี key)")
    print()

    # ── Step 4: Soccerway team URL lookup ─────────────────────────────────
    print(f"  [4/6] Soccerway team search{'  (ข้าม — ไม่มี BeautifulSoup)' if not _SW_AVAIL else ''}...")
    sw_paths = {}
    if _SW_AVAIL:
        team_names = set()
        for fx in candidates:
            tm = fx.get("teams", {})
            team_names.add(tm.get("home", {}).get("name", ""))
            team_names.add(tm.get("away", {}).get("name", ""))
        team_names.discard("")
        for tname in sorted(team_names):
            path = sw_search_team(tname)
            sw_paths[tname] = path
            status = f"✓ {path}" if path else "–"
            print(f"    SW: {tname[:30]:<30} {status}")
    print()

    # ── Step 5: H2H + Injuries + Analysis ─────────────────────
    print(f"  [5/6] ดึง H2H + injuries + วิเคราะห์ ({len(candidates)} แมตช์)...")
    analyses = []
    inj_top  = 12

    for i, fx in enumerate(candidates):
        tm     = fx.get("teams", {})
        fid    = fx.get("fixture", {}).get("id", 0)
        hid    = tm.get("home", {}).get("id", 0)
        aid    = tm.get("away", {}).get("id", 0)
        home_n = tm.get("home", {}).get("name", "?")
        away_n = tm.get("away", {}).get("name", "?")
        lg_id  = fx.get("league", {}).get("id", 0)
        lg_n   = fx.get("league", {}).get("name", "?")
        lg_s   = league_season_map.get(lg_id)
        lg_flag = "\U0001f3c6 " if lg_id == 1 else ""
        print(f"    [{i+1}/{len(candidates)}] {lg_flag}{home_n} vs {away_n}  ({lg_n})")

        h2h_raw  = fetch_h2h(hid, aid, hint_season=lg_s)
        injuries = fetch_injuries(fid) if i < inj_top else []

        sw_h = sw_paths.get(home_n)
        sw_a = sw_paths.get(away_n)

        match_o = find_match_odds(all_odds_map, home_n, away_n)
        if match_o:
            intl_flag = "\U0001f3c6" if lg_id in {1,10,9,5,4} else ""
            print(f"      {intl_flag} ราคา: {match_o.get('h_odds')} | {match_o.get('d_odds')} | {match_o.get('a_odds')}  [{match_o.get('bookmaker','')}]")

        try:
            result = analyze_match(fx, all_standings, h2h_raw, injuries,
                                   sw_home_path=sw_h, sw_away_path=sw_a,
                                   odds=match_o)
            analyses.append(result)
            sig  = result.get("data_signals", 0)
            conf = result.get("combined_conf", 0)
            print(f"      ✓ conf={conf}% signals={sig:.1f}")
        except Exception as e:
            print(f"      ⚠ analysis error: {e}")

    # ── Step 6: Build HTML ────────────────────────────────────────
    print(f"\n  [6/6] สร้าง HTML ({len(analyses)} คู่)...")
    # -- Step 6: Build HTML
    print(f"\n  [6/6] Building HTML ({len(analyses)} matches)...")
    html = build_html(
        matches=analyses,
        total_raw=len(all_ns),
        total_leagues=len(leagues_today),
        call_count=_call_count,
    )
    OUT_FILE.write_text(html, encoding="utf-8")

    # ── Save JSON (for web server / step_server.py) ──────────────────────
    json_out = Path(__file__).parent / "step_result.json"
    try:
        payload = {
            "generated": NOW,
            "window_label": WINDOW_LABEL,
            "window_start_ts": WINDOW_START_TS,
            "window_end_ts": WINDOW_END_TS,
            "total_matches": len(all_ns),
            "total_leagues": len(leagues_today),
            "api_calls": _call_count,
            "api_max": MAX_API_CALLS,
            "analyses": analyses,
        }
        json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        print(f"  OK JSON: {json_out.name}")
    except Exception as _je:
        print(f"  warn: JSON save failed: {_je}")

    no_browser = "--no-browser" in sys.argv
    if not no_browser:
        print("  Opening browser...")
        webbrowser.open(OUT_FILE.as_uri())

    print(f"\n{'='*62}")
    print(f"  Done -- API calls used {_call_count}/{MAX_API_CALLS}")
    print(f"  Result: {OUT_FILE}")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
