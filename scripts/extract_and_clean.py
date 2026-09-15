import os
import requests
import time
import json
import re
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Tuple, Dict, Any, List, Optional
from supabase import create_client, Client
import numpy as np

# ====================== CONFIG ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mqtfiupwtolrbmojiwgz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

# PIPELINE = logs + Supabase | ENGINE_ONLY = solo tabla estricta
RUN_MODE = os.getenv("RUN_MODE", "PIPELINE").upper().strip()

# --- TRAMPA DE ERRORES SUPABASE ---
if RUN_MODE == "PIPELINE":
    print(f"🔍 [DIAGNÓSTICO] Verificando URL: {SUPABASE_URL}")
    if not SUPABASE_KEY:
        raise ValueError("❌ LA LLAVE ESTÁ VACÍA: El archivo .yml no está inyectando la variable o el secreto no existe en GitHub.")
    if not SUPABASE_KEY.startswith("eyJ"):
        raise ValueError(f"❌ LLAVE INVÁLIDA: El texto guardado en GitHub ({SUPABASE_KEY[:5]}...) no es un token JWT (debe empezar con eyJ...). Verifica qué copiaste.")
# ----------------------------------

# Puntos del torneo
PTS_RESULTADO = float(os.getenv("PTS_RESULTADO", "3"))
PTS_EXACTO = float(os.getenv("PTS_EXACTO", "5"))
PTS_RESULTADO_BONUS = float(os.getenv("PTS_RESULTADO_BONUS", "5"))
PTS_EXACTO_BONUS = float(os.getenv("PTS_EXACTO_BONUS", "10"))

N_SIMS = int(os.getenv("N_SIMS", "10000"))
MAX_GOALS = int(os.getenv("MAX_GOALS", "6"))
RANDOM_SEED = int(os.getenv("RANDOM_SEED", "42"))

# team_xg
XG_WINDOW = int(os.getenv("XG_WINDOW", "12"))
XG_MAX_AGE_HOURS = int(os.getenv("XG_MAX_AGE_HOURS", "36"))

# Overrides opcionales de npxG real (Understat/manual)
NPxG_HOME = os.getenv("NPXG_HOME")
NPxGA_HOME = os.getenv("NPxGA_HOME")
NPxG_AWAY = os.getenv("NPxG_AWAY")
NPxGA_AWAY = os.getenv("NPxGA_AWAY")

if not API_FOOTBALL_KEY:
    raise ValueError("Falta API_FOOTBALL_KEY")

supabase: Optional[Client] = None
if SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS_API = {
    "x-apisports-key": API_FOOTBALL_KEY,
    "Content-Type": "application/json",
}

BOGOTA_TZ = ZoneInfo("America/Bogota")
np.random.seed(RANDOM_SEED)

TARGET_HOME = os.getenv("TARGET_HOME", "Borussia Dortmund")
TARGET_AWAY = os.getenv("TARGET_AWAY", "SC Paderborn 07")
IS_BONUS = os.getenv("IS_BONUS", "false").lower() in ("1", "true", "yes", "si", "sí")

TEAM_ALIASES = {
    "independiente santa fe": 1139,
    "santa fe": 1139,
    "tolima": 1142,
    "deportes tolima": 1142,
    "borussia dortmund": 165,
    "dortmund": 165,
    "paderborn": 178,
    "sc paderborn 07": 178,
    "sc paderborn": 178,
}

EVENTS_CACHE: Dict[int, List[Dict[str, Any]]] = {}

# ====================== UNDERSTAT SUPPORT + DICCIONARIO ======================
UNDERSTAT_LEAGUES = {
    "premier league": "EPL", "premier": "EPL",
    "la liga": "La_Liga", "laliga": "La_Liga",
    "serie a": "Serie_A",
    "bundesliga": "Bundesliga",
    "ligue 1": "Ligue_1", "ligue1": "Ligue_1",
    "russian premier league": "RFPL", "rfpl": "RFPL",
}

UNDERSTAT_TEAM_NAMES = {
    # Premier League
    "arsenal": "Arsenal", "aston villa": "Aston_Villa", "bournemouth": "Bournemouth",
    "brentford": "Brentford", "brighton": "Brighton", "brighton & hove albion": "Brighton",
    "chelsea": "Chelsea", "crystal palace": "Crystal_Palace", "everton": "Everton",
    "fulham": "Fulham", "ipswich": "Ipswich", "ipswich town": "Ipswich",
    "leicester": "Leicester", "leicester city": "Leicester", "liverpool": "Liverpool",
    "manchester city": "Manchester_City", "man city": "Manchester_City",
    "manchester united": "Manchester_United", "man united": "Manchester_United", "man utd": "Manchester_United",
    "newcastle": "Newcastle_United", "newcastle united": "Newcastle_United",
    "nottingham forest": "Nottingham_Forest", "forest": "Nottingham_Forest",
    "southampton": "Southampton", "tottenham": "Tottenham", "tottenham hotspur": "Tottenham", "spurs": "Tottenham",
    "west ham": "West_Ham", "west ham united": "West_Ham",
    "wolves": "Wolverhampton_Wanderers", "wolverhampton": "Wolverhampton_Wanderers", "wolverhampton wanderers": "Wolverhampton_Wanderers",

    # La Liga
    "alaves": "Alaves", "deportivo alaves": "Alaves", "athletic club": "Athletic_Club", "athletic bilbao": "Athletic_Club",
    "atletico madrid": "Atletico_Madrid", "atlético madrid": "Atletico_Madrid",
    "barcelona": "Barcelona", "fc barcelona": "Barcelona",
    "celta": "Celta_Vigo", "celta vigo": "Celta_Vigo", "getafe": "Getafe", "girona": "Girona",
    "las palmas": "Las_Palmas", "mallorca": "Mallorca", "osasuna": "Osasuna",
    "rayo vallecano": "Rayo_Vallecano", "real betis": "Real_Betis", "betis": "Real_Betis",
    "real madrid": "Real_Madrid", "real sociedad": "Real_Sociedad",
    "sevilla": "Sevilla", "valencia": "Valencia", "villarreal": "Villarreal",
    "leganes": "Leganes", "espanyol": "Espanyol", "real valladolid": "Valladolid", "valladolid": "Valladolid",

    # Serie A
    "ac milan": "Milan", "milan": "Milan", "inter": "Inter", "inter milan": "Inter", "internazionale": "Inter",
    "juventus": "Juventus", "napoli": "Napoli", "roma": "Roma", "as roma": "Roma", "lazio": "Lazio",
    "atalanta": "Atalanta", "fiorentina": "Fiorentina", "bologna": "Bologna", "torino": "Torino",
    "genoa": "Genoa", "monza": "Monza", "cagliari": "Cagliari", "udinese": "Udinese",
    "empoli": "Empoli", "verona": "Verona", "hellas verona": "Verona", "lecce": "Lecce",
    "sassuolo": "Sassuolo", "parma": "Parma", "como": "Como", "venezia": "Venezia",

    # Bundesliga
    "bayern munich": "Bayern_Munich", "bayern": "Bayern_Munich", "bayern münchen": "Bayern_Munich",
    "borussia dortmund": "Borussia_Dortmund", "dortmund": "Borussia_Dortmund", "bvb": "Borussia_Dortmund",
    "rb leipzig": "RasenBallsport_Leipzig", "leipzig": "RasenBallsport_Leipzig",
    "bayer leverkusen": "Bayer_Leverkusen", "leverkusen": "Bayer_Leverkusen",
    "eintracht frankfurt": "Eintracht_Frankfurt", "frankfurt": "Eintracht_Frankfurt",
    "wolfsburg": "Wolfsburg", "borussia monchengladbach": "Borussia_M.Gladbach",
    "gladbach": "Borussia_M.Gladbach", "monchengladbach": "Borussia_M.Gladbach",
    "freiburg": "Freiburg", "hoffenheim": "Hoffenheim", "mainz": "Mainz_05", "mainz 05": "Mainz_05",
    "augsburg": "Augsburg", "werder bremen": "Werder_Bremen", "bremen": "Werder_Bremen",
    "stuttgart": "Stuttgart", "vfb stuttgart": "Stuttgart", "union berlin": "Union_Berlin",
    "heidenheim": "Heidenheim", "st pauli": "St_Pauli", "holstein kiel": "Holstein_Kiel", "bochum": "Bochum",

    # Ligue 1
    "psg": "Paris_Saint_Germain", "paris saint germain": "Paris_Saint_Germain", "paris sg": "Paris_Saint_Germain",
    "marseille": "Marseille", "olympique marsella": "Marseille", "monaco": "Monaco", "as monaco": "Monaco",
    "lille": "Lille", "lyon": "Lyon", "olympique lyon": "Lyon", "nice": "Nice", "rennes": "Rennes",
    "lens": "Lens", "strasbourg": "Strasbourg", "nantes": "Nantes", "montpellier": "Montpellier",
    "reims": "Reims", "toulouse": "Toulouse", "brest": "Brest", "auxerre": "Auxerre",
    "le havre": "Le_Havre", "saint etienne": "Saint-Etienne", "angers": "Angers",

    # RFPL
    "zenit": "Zenit", "zenit st petersburg": "Zenit",
    "cska moscow": "CSKA_Moscow", "cska": "CSKA_Moscow",
    "spartak moscow": "Spartak_Moscow", "spartak": "Spartak_Moscow",
    "lokomotiv moscow": "Lokomotiv_Moscow", "lokomotiv": "Lokomotiv_Moscow",
    "dynamo moscow": "Dynamo_Moscow", "krasnodar": "Krasnodar",
    "rostov": "Rostov", "rubin kazan": "Rubin_Kazan", "sochi": "Sochi",
}

try:
    from understatapi import UnderstatClient
    UNDERSTAT_AVAILABLE = True
except ImportError:
    UNDERSTAT_AVAILABLE = False

def is_understat_league(league_name: str) -> bool:
    league = normalize(league_name)
    return any(k in league for k in UNDERSTAT_LEAGUES.keys())

def get_understat_league_code(league_name: str) -> Optional[str]:
    league = normalize(league_name)
    for key, code in UNDERSTAT_LEAGUES.items():
        if key in league:
            return code
    return None

def get_understat_team_name(team_name: str) -> Optional[str]:
    key = normalize(team_name)
    if key in UNDERSTAT_TEAM_NAMES:
        return UNDERSTAT_TEAM_NAMES[key]
    for k, v in UNDERSTAT_TEAM_NAMES.items():
        if k in key or key in k:
            return v
    return None

# ====================== LOG ======================
def log(msg: str = "") -> None:
    if RUN_MODE != "ENGINE_ONLY":
        print(msg)

def normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text)

def to_bogota_iso(dt_value) -> str:
    if dt_value is None:
        return datetime.now(BOGOTA_TZ).isoformat()
    if isinstance(dt_value, str):
        dt = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
    else:
        dt = dt_value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BOGOTA_TZ).isoformat()

# ====================== CALIBRACIÓN POR LIGA (Prompt 5.9) ======================
def league_params(league_name: str) -> Dict[str, Any]:
    league = normalize(league_name)
    if any(x in league for x in [
        "colombia", "betplay", "dimayor", "primera a",
        "argentina", "liga profesional", "copa argentina",
        "ecuador", "liga pro", "mexico", "liga mx",
    ]):
        return {
            "volatility": "Alta",
            "rho": -0.11,
            "home_adv": 1.05,
            "league_avg": 1.18,
            "max_fav_cap": 0.48,
            "late_goal_weight": 0.23
        }
    if any(x in league for x in ["brazil", "brasil", "brasileirao", "portugal", "liga portugal"]):
        return {
            "volatility": "Media-Alta",
            "rho": -0.13,
            "home_adv": 1.07,
            "league_avg": 1.25,
            "max_fav_cap": 0.51,
            "late_goal_weight": 0.20
        }
    if any(x in league for x in ["premier", "la liga", "serie a", "bundesliga", "ligue 1"]):
        return {
            "volatility": "Baja-Media",
            "rho": -0.14,
            "home_adv": 1.09,
            "league_avg": 1.35,
            "max_fav_cap": 0.54,
            "late_goal_weight": 0.18
        }
    return {
        "volatility": "Baja-Media",
        "rho": -0.13,
        "home_adv": 1.07,
        "league_avg": 1.22,
        "max_fav_cap": 0.52,
        "late_goal_weight": 0.19
    }

def safe_request(url: str, max_retries: int = 3) -> Dict[str, Any]:
    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=HEADERS_API, timeout=25)
            if res.status_code == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            res.raise_for_status()
            data = res.json()
            if data.get("errors"):
                log(f"⚠️ API Errors/Warnings: {data['errors']}")
            return data
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return {"response": []}

# ====================== BÚSQUEDA DE EQUIPOS ======================
def score_team_name(target: str, candidate_name: str) -> int:
    t, n = normalize(target), normalize(candidate_name)
    score = 0
    if t == n:
        score += 120
    elif t in n or n in t:
        score += 70
    t_tokens = [x for x in t.split() if len(x) > 2]
    n_tokens = set(n.split())
    score += sum(18 for tok in t_tokens if tok in n_tokens)
    return score

def search_queries_for(team_target: str) -> List[str]:
    t = team_target.strip()
    variants = [t, t.replace("Independiente ", "").replace("Deportes ", "").replace("SC ", "").strip()]
    if len(t.split()) >= 2:
        variants.append(" ".join(t.split()[-2:]))
    variants.append(t.split()[0] if t.split() else t)
    return [v for v in variants if v and len(v) >= 3]

def search_team_id(team_target: str) -> Tuple[int, str]:
    target_lower = normalize(team_target)
    if target_lower in TEAM_ALIASES:
        team_id = TEAM_ALIASES[target_lower]
        data = safe_request(f"https://v3.football.api-sports.io/teams?id={team_id}")
        if data.get("response"):
            team = data["response"][0]["team"]
            return team["id"], team["name"]

    candidates_map = {}
    for q in search_queries_for(team_target):
        data = safe_request(f"https://v3.football.api-sports.io/teams?search={q}")
        for item in data.get("response", []):
            team = item["team"]
            tid = team["id"]
            sc = score_team_name(team_target, team["name"])
            if tid not in candidates_map or sc > candidates_map[tid]["score"]:
                candidates_map[tid] = {"id": tid, "name": team["name"], "score": sc}

    candidates = sorted(candidates_map.values(), key=lambda x: x["score"], reverse=True)
    if not candidates or candidates[0]["score"] < 30:
        raise ValueError(f"No se encontró equipo confiable para '{team_target}'")
    return candidates[0]["id"], candidates[0]["name"]

def get_fixture_direct(home_target: str, away_target: str) -> Dict[str, Any]:
    home_id, home_real = search_team_id(home_target)
    away_id, away_real = search_team_id(away_target)

    h2h = safe_request(f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}").get("response", [])
    if h2h:
        now = datetime.now(timezone.utc).timestamp()
        closest = min(h2h, key=lambda x: abs(x["fixture"]["timestamp"] - now))
        return closest

    for team_id in [home_id, away_id]:
        fixtures = safe_request(f"https://v3.football.api-sports.io/fixtures?team={team_id}&next=30").get("response", [])
        for fix in fixtures:
            ids = {fix["teams"]["home"]["id"], fix["teams"]["away"]["id"]}
            if home_id in ids and away_id in ids:
                return fix
    raise ValueError(f"Sin fixture entre {home_real} y {away_real}")

# ====================== EVENTOS Y ANOMALÍAS ======================
def get_fixture_events(fixture_id: int) -> List[Dict[str, Any]]:
    if fixture_id in EVENTS_CACHE:
        return EVENTS_CACHE[fixture_id]
    data = safe_request(f"https://v3.football.api-sports.io/fixtures/events?fixture={fixture_id}")
    events = data.get("response", []) or []
    EVENTS_CACHE[fixture_id] = events
    time.sleep(0.30)
    return events

def analyze_match_anomalies(events: List[Dict[str, Any]], team_id: int) -> Dict[str, Any]:
    early_red = False
    penalties_for = penalties_against = 0
    for ev in events:
        try:
            etype = (ev.get("type") or "").lower()
            detail = (ev.get("detail") or "").lower()
            elapsed = ev.get("time", {}).get("elapsed")
            ev_team = ev.get("team", {}).get("id")
            if ev_team == team_id and etype == "card" and elapsed and int(elapsed) < 60 and ("red" in detail or "second yellow" in detail):
                early_red = True
            if etype == "goal" and "penalty" in detail:
                if ev_team == team_id:
                    penalties_for += 1
                else:
                    penalties_against += 1
        except Exception:
            continue
    return {
        "early_red": early_red,
        "penalties_for": penalties_for,
        "penalties_against": penalties_against,
        "multi_penalty_game": (penalties_for + penalties_against) >= 3
    }

# ====================== CAPA B: EXPECTED GOALS FRAMEWORK ======================
def fetch_recent_team_metrics_decay(team_id: int, max_n: int = 12) -> Dict[str, Any]:
    """Proxy robusto con ponderación 50/30/20 del Prompt 5.9"""
    current_year = datetime.now(timezone.utc).year
    data = safe_request(f"https://v3.football.api-sports.io/fixtures?team={team_id}&season={current_year}")
    fixtures = [fx for fx in data.get("response", []) if fx.get("fixture", {}).get("status", {}).get("short") in ["FT", "AET", "PEN"]]
    fixtures.sort(key=lambda x: x["fixture"]["timestamp"], reverse=True)
    selected = fixtures[:max_n]

    if not selected:
        return {"gf": 1.15, "ga": 1.15, "n": 0, "source": "fallback"}

    weights = []
    for idx in range(len(selected)):
        if idx < 4:
            weights.append(0.50 / min(4, len(selected)))
        elif idx < 8:
            weights.append(0.30 / min(4, max(1, len(selected) - 4)))
        else:
            weights.append(0.20 / min(4, max(1, len(selected) - 8)))
    norm = sum(weights)

    weighted_gf = weighted_ga = 0.0
    for idx, fx in enumerate(selected):
        try:
            fid = int(fx["fixture"]["id"])
            home = fx["teams"]["home"]
            gh, ga_ = fx["goals"]["home"], fx["goals"]["away"]
            if gh is None or ga_ is None:
                continue
            if home["id"] == team_id:
                g_for, g_against = float(gh), float(ga_)
            else:
                g_for, g_against = float(ga_), float(gh)

            events = get_fixture_events(fid)
            anomalies = analyze_match_anomalies(events, team_id)
            mult = 1.0
            if anomalies["early_red"]:
                g_against *= 1.15
            if anomalies["multi_penalty_game"]:
                mult *= 0.40

            w = weights[idx] / norm
            weighted_gf += g_for * mult * w
            weighted_ga += g_against * mult * w
        except Exception:
            continue

    return {
        "gf": float(weighted_gf),
        "ga": float(weighted_ga),
        "n": len(selected),
        "source": "xg_proxy_50_30_20"
    }

def fetch_understat_npxg(team_name: str, league_name: str) -> Optional[Dict[str, float]]:
    if not UNDERSTAT_AVAILABLE:
        return None
    understat_name = get_understat_team_name(team_name)
    if not understat_name:
        return None
    code = get_understat_league_code(league_name)
    if not code:
        return None
    try:
        with UnderstatClient() as client:
            season = str(datetime.now().year - 1)
            data = client.team(team=understat_name).get_match_data(season=season)
            if not data:
                return None
            xg_for_list, xg_against_list = [], []
            for match in data[-12:]:
                if match.get("xG") is not None:
                    xg_for_list.append(float(match["xG"]))
                if match.get("xGA") is not None:
                    xg_against_list.append(float(match["xGA"]))
            if not xg_for_list:
                return None
            return {
                "npxg_for": sum(xg_for_list) / len(xg_for_list),
                "npxga": sum(xg_against_list) / len(xg_against_list) if xg_against_list else 1.2,
                "n": len(xg_for_list)
            }
    except Exception as e:
        log(f"⚠️ Understat error ({understat_name}): {e}")
        return None

def ensure_team_xg(team_id: int, team_name: str, league: str, window_n: int = XG_WINDOW) -> Dict[str, Any]:
    if is_understat_league(league):
        real = fetch_understat_npxg(team_name, league)
        if real is not None:
            log(f"✅ xG real Understat → {team_name}")
            return {
                "team_id": str(team_id),
                "team_name": team_name,
                "league": league,
                "npxg_for": round(real["npxg_for"], 3),
                "npxga": round(real["npxga"], 3),
                "sample_size": real["n"],
                "source": "understat_real"
            }
    metrics = fetch_recent_team_metrics_decay(team_id, max_n=window_n)
    log(f"📉 Proxy 50/30/20 → {team_name}")
    return {
        "team_id": str(team_id),
        "team_name": team_name,
        "league": league,
        "npxg_for": round(float(metrics["gf"]), 3),
        "npxga": round(float(metrics["ga"]), 3),
        "sample_size": int(metrics.get("n", 0)),
        "source": metrics.get("source", "xg_proxy_50_30_20")
    }

# ====================== TRAMOS DE GOL (76-90+) ======================
def get_league_tramos(league: str) -> Dict[str, float]:
    params = league_params(league)
    late = params.get("late_goal_weight", 0.19)
    return {
        "0-15": 0.12, "16-30": 0.14, "31-45": 0.15,
        "46-60": 0.16, "61-75": 0.17, "76-90+": late
    }

def apply_late_goal_adjustment(mat: np.ndarray, league: str) -> np.ndarray:
    late_weight = get_league_tramos(league)["76-90+"]
    adjusted = mat.copy()
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            total = i + j
            if total >= 2 and abs(i - j) <= 1:
                adjusted[i, j] *= (1.0 + late_weight * 0.35)
            if i > j and (i - j) == 1:
                adjusted[i, j] *= (1.0 + late_weight * 0.25)
    s = adjusted.sum()
    return adjusted / s if s > 0 else mat

# ====================== H2H + LESIONES ======================
def analyze_h2h_last4(home_id: int, away_id: int) -> Dict[str, Any]:
    data = safe_request(f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}&last=4")
    matches = data.get("response", [])
    low_scoring = sum(1 for m in matches if (m["goals"]["home"] or 0) + (m["goals"]["away"] or 0) <= 1)
    return {
        "matches": len(matches),
        "low_scoring_count": low_scoring,
        "force_low_scoring": low_scoring >= 3
    }

def get_injury_impact(team_id: int, team_name: str) -> float:
    return float(os.getenv(f"INJURY_IMPACT_{'HOME' if 'home' in team_name.lower() else 'AWAY'}", "0") or 0)

# ====================== FASE 0 ======================
def phase0_build_lambdas(home_id, away_id, home_name, away_name, league,
                         injury_impact_home=0.0, injury_impact_away=0.0) -> Dict[str, Any]:
    params = league_params(league)

    if all(v is not None for v in [NPxG_HOME, NPxGA_HOME, NPxG_AWAY, NPxGA_AWAY]):
        home_x = {"npxg_for": float(NPxG_HOME), "npxga": float(NPxGA_HOME), "source": "manual"}
        away_x = {"npxg_for": float(NPxG_AWAY), "npxga": float(NPxGA_AWAY), "source": "manual"}
        source = "REAL_NPXG_OVERRIDE"
    else:
        home_x = ensure_team_xg(home_id, home_name, league)
        away_x = ensure_team_xg(away_id, away_name, league)
        source = f"{home_x.get('source')}+{away_x.get('source')}"

    xgd_home = float(home_x["npxg_for"]) - float(home_x["npxga"])
    xgd_away = float(away_x["npxg_for"]) - float(away_x["npxga"])

    reg_h = 0.05 if xgd_home > 0.35 else (-0.05 if xgd_home < -0.20 else 0.0)
    reg_a = 0.05 if xgd_away > 0.35 else (-0.05 if xgd_away < -0.20 else 0.0)

    att_home = (float(home_x["npxg_for"]) + reg_h) / params["league_avg"]
    def_home = float(home_x["npxga"]) / params["league_avg"]
    att_away = (float(away_x["npxg_for"]) + reg_a) / params["league_avg"]
    def_away = float(away_x["npxga"]) / params["league_avg"]

    lambda_home = float(np.clip(att_home * def_away * params["league_avg"] * params["home_adv"] + injury_impact_home, 0.55, 2.60))
    lambda_away = float(np.clip(att_away * def_home * params["league_avg"] + injury_impact_away, 0.45, 2.40))

    log(f"\n===== FASE 0 – Expected Goals Framework 5.9 =====")
    log(f"Liga: {league} | Vol: {params['volatility']} | Cap: {params['max_fav_cap']*100:.0f}%")
    log(f"Fuente: {source}")
    log(f"xGD Local: {xgd_home:+.2f} | xGD Visita: {xgd_away:+.2f}")
    log(f"λ_local={lambda_home:.3f} | λ_visitante={lambda_away:.3f}")
    log("=================================================\n")

    return {
        "lambda_home": round(lambda_home, 3),
        "lambda_away": round(lambda_away, 3),
        "volatility": params["volatility"],
        "rho": params["rho"],
        "max_fav_cap": params["max_fav_cap"],
        "xg_diff_direct": float(home_x["npxg_for"]) - float(away_x["npxga"]),
        "data_source": source,
        "is_bonus": IS_BONUS
    }

# ====================== MOTOR DIXON-COLES + EV ======================
def poisson_pmf(k: int, lam: float) -> float:
    return float(np.exp(-lam) * (lam ** k) / math.factorial(k))

def dixon_coles_tau(x: int, y: int, lam_h: float, lam_a: float, rho: float) -> float:
    if x == 0 and y == 0: return 1.0 - lam_h * lam_a * rho
    if x == 0 and y == 1: return 1.0 + lam_h * rho
    if x == 1 and y == 0: return 1.0 + lam_a * rho
    if x == 1 and y == 1: return 1.0 - rho
    return 1.0

def build_score_matrix(lam_h: float, lam_a: float, rho: float, max_goals: int) -> np.ndarray:
    mat = np.zeros((max_goals + 1, max_goals + 1))
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_pmf(i, lam_h) * poisson_pmf(j, lam_a) * dixon_coles_tau(i, j, lam_h, lam_a, rho)
            mat[i, j] = max(p, 0.0)
    s = mat.sum()
    return mat / s if s > 0 else mat

def compute_expected_points(mat: np.ndarray, volatility: str, is_bonus: bool, xg_diff: float) -> List[Dict]:
    pts_res = PTS_RESULTADO_BONUS if is_bonus else PTS_RESULTADO
    pts_ex = PTS_EXACTO_BONUS if is_bonus else PTS_EXACTO
    p_home = float(np.triu(mat, k=1).sum())
    p_draw = float(np.trace(mat))
    p_away = float(np.tril(mat, k=-1).sum())

    rows = []
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            p_exact = float(mat[i, j])
            p_1x2 = p_home if i > j else (p_draw if i == j else p_away)
            ev = p_exact * pts_ex + p_1x2 * pts_res
            score = f"{i}-{j}"
            if xg_diff > 0.60 and score in ["2-0", "2-1", "3-1"]:
                ev *= 1.15
            if xg_diff < 0.25 and score in ["1-0", "1-1", "0-0", "0-1"]:
                ev *= 1.12
            if volatility == "Alta" and (i + j) >= 3:
                ev *= 1.08
            rows.append({"score": score, "p_exact": p_exact, "ev": ev})
    rows.sort(key=lambda x: x["ev"], reverse=True)
    return rows

def run_engine_5_9(lam_h, lam_a, volatility, is_bonus, rho, max_fav_cap, xg_diff, league) -> Dict[str, Any]:
    if is_bonus:
        max_fav_cap -= 0.05

    mat = build_score_matrix(lam_h, lam_a, rho, MAX_GOALS)
    mat = apply_late_goal_adjustment(mat, league)

    p_home = float(np.triu(mat, k=1).sum())
    p_away = float(np.tril(mat, k=-1).sum())
    if p_home > max_fav_cap or p_away > max_fav_cap:
        mat = build_score_matrix(lam_h * 0.92, lam_a * 0.92, rho, MAX_GOALS)
        mat = apply_late_goal_adjustment(mat, league)
        log(f"⚠️ Techo de volatilidad aplicado ({max_fav_cap*100:.0f}%)")

    ranking = compute_expected_points(mat, volatility, is_bonus, xg_diff)
    return {
        "top1_score": ranking[0]["score"],
        "top2_score": ranking[1]["score"],
        "prob_home": float(np.triu(mat, k=1).sum()),
        "prob_draw": float(np.trace(mat)),
        "prob_away": float(np.tril(mat, k=-1).sum())
    }

# ====================== PERSISTENCIA SUPABASE ======================
def upsert_partido(record: Dict[str, Any]) -> None:
    if supabase is None:
        return
    for table_name in ("matches", "partidos", "Partidos"):
        try:
            supabase.table(table_name).upsert(record, on_conflict="fixture_id").execute()
            log(f"✅ Partido registrado en '{table_name}'")
            return
        except Exception:
            continue

def insert_prediccion(record: Dict[str, Any]) -> None:
    if supabase is None:
        return
    for table_name in ("predictions", "predicciones", "Predicciones"):
        try:
            supabase.table(table_name).insert(record).execute()
            log(f"✅ Predicción registrada en '{table_name}'")
            return
        except Exception:
            continue

# ====================== PROCESO PRINCIPAL ======================
def process_single_match(home_target: str, away_target: str):
    fixture = get_fixture_direct(home_target, away_target)
    fix_id = str(fixture["fixture"]["id"])
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    home_id = fixture["teams"]["home"]["id"]
    away_id = fixture["teams"]["away"]["id"]
    league = fixture["league"]["name"]
    status_short = fixture["fixture"]["status"]["short"]
    kickoff_bogota = to_bogota_iso(fixture["fixture"]["date"])
    consulta_bogota = to_bogota_iso(datetime.now(timezone.utc))

    log(f"📌 {home} vs {away} | {league} | ID={fix_id}")
    log(f"Kickoff Bogotá: {kickoff_bogota}")
    log(f"Consulta Bogotá: {consulta_bogota}")

    h2h_info = analyze_h2h_last4(home_id, away_id)
    inj_h = get_injury_impact(home_id, home)
    inj_a = get_injury_impact(away_id, away)

    phase0 = phase0_build_lambdas(
        home_id=home_id,
        away_id=away_id,
        home_name=home,
        away_name=away,
        league=league,
        injury_impact_home=inj_h,
        injury_impact_away=inj_a,
    )

    engine = run_engine_5_9(
        lam_h=phase0["lambda_home"],
        lam_a=phase0["lambda_away"],
        volatility=phase0["volatility"],
        is_bonus=IS_BONUS,
        rho=phase0["rho"],
        max_fav_cap=phase0["max_fav_cap"],
        xg_diff=phase0["xg_diff_direct"],
        league=league
    )

    if h2h_info["force_low_scoring"]:
        log("⚠️ Regla 14 activada: H2H de baja intensidad")

    if RUN_MODE == "PIPELINE":
        upsert_partido({
            "fixture_id": fix_id,
            "home_team": home,
            "away_team": away,
            "league": league,
            "kickoff": kickoff_bogota,
            "volatility": phase0["volatility"],
            "lambda_home": phase0["lambda_home"],
            "lambda_away": phase0["lambda_away"],
            "status": f"quant_5_9_{status_short}",
            "updated_at": consulta_bogota,
        })
        insert_prediccion({
            "created_at": consulta_bogota,
            "npxg_local": phase0["lambda_home"],
            "npxg_visita": phase0["lambda_away"],
            "prob_local": round(engine["prob_home"] * 100, 2),
            "prob_empate": round(engine["prob_draw"] * 100, 2),
            "prob_visita": round(engine["prob_away"] * 100, 2),
            "top_marcador": engine["top1_score"],
        })

    bonus_tag = " (BONUS)" if IS_BONUS else ""
    match_title = f"{home} vs {away}{bonus_tag}"

    if RUN_MODE == "ENGINE_ONLY":
        print("| Partido | 1° más probable | 2° más probable |")
        print("| --- | --- | --- |")
        print(f"| {match_title} | {engine['top1_score']} | {engine['top2_score']} |")
    else:
        print("\n" + "="*60)
        print("RESULTADO FINAL – PROMPT 5.9 COMPLETO")
        print("="*60)
        print("| Partido | 1° más probable | 2° más probable |")
        print("| --- | --- | --- |")
        print(f"| {match_title} | {engine['top1_score']} | {engine['top2_score']} |")
        print("="*60 + "\n")

if __name__ == "__main__":
    if RUN_MODE != "ENGINE_ONLY":
        print("=" * 75)
        print("PIPELINE QUANT V9.3 – Prompt 5.9 Full + Understat + Diccionario + Base V7.3")
        print("=" * 75)
    try:
        process_single_match(TARGET_HOME, TARGET_AWAY)
    except Exception as e:
        if RUN_MODE == "ENGINE_ONLY":
            print("| Partido | 1° más probable | 2° más probable |")
            print("| --- | --- | --- |")
            print("| ERROR: Datos insuficientes | N/A | N/A |")
        else:
            print(f"\n❌ Error en la tubería de datos: {e}")
            raise
    if RUN_MODE != "ENGINE_ONLY":
        print("=" * 75)
