import os
import requests
import time
import json
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Tuple, Dict, Any, List, Optional
from supabase import create_client, Client
import numpy as np

# ====================== CONFIG ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mqtfiupwtolrbmojiwgz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

# PIPELINE = logs + Supabase | ENGINE_ONLY = solo tabla 6.1
RUN_MODE = os.getenv("RUN_MODE", "PIPELINE").upper().strip()

# Puntos del torneo
PTS_RESULTADO = float(os.getenv("PTS_RESULTADO", "3"))
PTS_EXACTO = float(os.getenv("PTS_EXACTO", "5"))
PTS_RESULTADO_BONUS = float(os.getenv("PTS_RESULTADO_BONUS", "5"))
PTS_EXACTO_BONUS = float(os.getenv("PTS_EXACTO_BONUS", "10"))

N_SIMS = int(os.getenv("N_SIMS", "10000"))
MAX_GOALS = int(os.getenv("MAX_GOALS", "6"))
RANDOM_SEED = int(os.getenv("RANDOM_SEED", "42"))

# team_xg
XG_WINDOW = int(os.getenv("XG_WINDOW", "6"))
XG_MAX_AGE_HOURS = int(os.getenv("XG_MAX_AGE_HOURS", "36"))

# Overrides opcionales de npxG real (Understat/manual)
NPxG_HOME = os.getenv("NPXG_HOME")
NPxGA_HOME = os.getenv("NPXGA_HOME")
NPxG_AWAY = os.getenv("NPXG_AWAY")
NPxGA_AWAY = os.getenv("NPXGA_AWAY")

if not API_FOOTBALL_KEY:
    raise ValueError("Falta API_FOOTBALL_KEY")

if RUN_MODE == "PIPELINE" and not SUPABASE_KEY:
    raise ValueError("Falta SUPABASE_SERVICE_ROLE_KEY para RUN_MODE=PIPELINE")

supabase: Optional[Client] = None
if SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS_API = {
    "x-apisports-key": API_FOOTBALL_KEY,
    "Content-Type": "application/json",
}

BOGOTA_TZ = ZoneInfo("America/Bogota")
np.random.seed(RANDOM_SEED)

TARGET_HOME = os.getenv("TARGET_HOME", "Independiente Santa Fe")
TARGET_AWAY = os.getenv("TARGET_AWAY", "Tolima")
IS_BONUS = os.getenv("IS_BONUS", "false").lower() in ("1", "true", "yes", "si", "sí")

TEAM_ALIASES = {
    "independiente santa fe": 1139,
    "santa fe": 1139,
    "tolima": 1142,
    "deportes tolima": 1142,
}

EVENTS_CACHE: Dict[int, List[Dict[str, Any]]] = {}


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


# ====================== CALIBRACIÓN POR LIGA ======================
def league_params(league_name: str) -> Dict[str, float]:
    league = normalize(league_name)

    if any(x in league for x in [
        "colombia", "betplay", "dimayor", "primera a",
        "argentina", "liga profesional", "copa argentina",
        "ecuador", "liga pro",
        "mexico", "liga mx",
        "brazil", "brasil", "brasileirao",
    ]):
        return {"volatility": "Alta", "rho": -0.11, "home_adv": 1.05, "league_avg": 1.18}

    if any(x in league for x in ["portugal", "liga portugal", "segunda liga", "turkey", "greece"]):
        return {"volatility": "Media-Alta", "rho": -0.13, "home_adv": 1.07, "league_avg": 1.25}

    if any(x in league for x in ["premier", "la liga", "serie a", "bundesliga", "ligue 1"]):
        return {"volatility": "Baja-Media", "rho": -0.14, "home_adv": 1.09, "league_avg": 1.35}

    return {"volatility": "Baja-Media", "rho": -0.13, "home_adv": 1.07, "league_avg": 1.22}


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
                log(f"⚠️ API Errors: {data['errors']}")
            return data
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return {"response": []}


# ====================== TEAMS / FIXTURES ======================
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
    if "nacional" in n and "santa fe" in t:
        score -= 80
    if "leones negros" in n and "santa fe" in t:
        score -= 80
    return score


def search_queries_for(team_target: str) -> List[str]:
    t = team_target.strip()
    variants = [
        t,
        t.replace("Independiente ", "").replace("Deportes ", "").strip(),
        " ".join(t.split()[-2:]) if len(t.split()) >= 2 else t,
        t.split()[0] if t.split() else t,
    ]
    out = []
    for v in variants:
        if v and v not in out and len(v) >= 3:
            out.append(v)
    return out


def search_team_id(team_target: str) -> Tuple[int, str]:
    target_lower = normalize(team_target)
    if target_lower in TEAM_ALIASES:
        team_id = TEAM_ALIASES[target_lower]
        log(f"⚡ Alias '{team_target}' → {team_id}")
        data = safe_request(f"https://v3.football.api-sports.io/teams?id={team_id}")
        if data.get("response"):
            team = data["response"][0]["team"]
            if score_team_name(team_target, team["name"]) >= 50:
                return team["id"], team["name"]

    candidates_map: Dict[int, Dict[str, Any]] = {}
    for q in search_queries_for(team_target):
        log(f"🔍 Buscando: '{q}'...")
        data = safe_request(f"https://v3.football.api-sports.io/teams?search={q}")
        for item in data.get("response", []):
            team = item["team"]
            tid = team["id"]
            sc = score_team_name(team_target, team["name"])
            prev = candidates_map.get(tid)
            if prev is None or sc > prev["score"]:
                candidates_map[tid] = {
                    "id": tid,
                    "name": team["name"],
                    "country": team.get("country", "Unknown"),
                    "score": sc,
                }

    candidates = sorted(candidates_map.values(), key=lambda x: x["score"], reverse=True)
    if not candidates:
        raise ValueError(f"No se encontró equipo para '{team_target}'")

    best = candidates[0]
    if best["score"] < 40:
        raise ValueError(f"Match poco confiable: {best['name']} score={best['score']}")
    log(f"✅ Seleccionado: {best['name']} (ID:{best['id']})")
    return best["id"], best["name"]


def get_fixture_direct(home_target: str, away_target: str) -> Dict[str, Any]:
    home_id, home_real = search_team_id(home_target)
    away_id, away_real = search_team_id(away_target)
    log(f"✅ {home_real} ({home_id}) vs {away_real} ({away_id})")

    h2h = safe_request(
        f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}"
    ).get("response", [])
    if h2h:
        now = datetime.now(timezone.utc).timestamp()
        closest = min(h2h, key=lambda x: abs(x["fixture"]["timestamp"] - now))
        log(f"📌 H2H ID={closest['fixture']['id']}")
        return closest

    for team_id in [home_id, away_id]:
        fixtures = safe_request(
            f"https://v3.football.api-sports.io/fixtures?team={team_id}&next=30"
        ).get("response", [])
        for fix in fixtures:
            ids = {fix["teams"]["home"]["id"], fix["teams"]["away"]["id"]}
            if home_id in ids and away_id in ids:
                log(f"📌 Próximo fixture ID={fix['fixture']['id']}")
                return fix

    raise ValueError(f"Sin H2H/próximo entre {home_real} y {away_real}")


# ====================== EVENTS + ATÍPICOS ======================
def get_fixture_events(fixture_id: int) -> List[Dict[str, Any]]:
    if fixture_id in EVENTS_CACHE:
        return EVENTS_CACHE[fixture_id]
    data = safe_request(f"https://v3.football.api-sports.io/fixtures/events?fixture={fixture_id}")
    events = data.get("response", []) or []
    EVENTS_CACHE[fixture_id] = events
    time.sleep(0.35)
    return events


def analyze_match_anomalies(events: List[Dict[str, Any]], team_id: int) -> Dict[str, Any]:
    early_red = False
    penalties_for = 0
    penalties_against = 0

    for ev in events:
        try:
            etype = (ev.get("type") or "").lower()
            detail = (ev.get("detail") or "").lower()
            elapsed = ev.get("time", {}).get("elapsed")
            ev_team = ev.get("team", {}).get("id")

            if (
                ev_team == team_id
                and etype == "card"
                and elapsed is not None
                and int(elapsed) < 60
                and ("red" in detail or "second yellow" in detail)
            ):
                early_red = True

            is_pen_goal = etype == "goal" and "penalty" in detail
            if is_pen_goal:
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
        "multi_penalty_game": (penalties_for + penalties_against) >= 3,
    }


def red_penalty_factor(elapsed_min: Optional[int] = None) -> float:
    """Determinista: <30 → 1.18 | 30-59 → 1.15"""
    if elapsed_min is None:
        return 1.15
    if elapsed_min < 30:
        return 1.18
    return 1.15


def earliest_red_minute(events: List[Dict[str, Any]], team_id: int) -> Optional[int]:
    mins = []
    for ev in events:
        try:
            if ev.get("team", {}).get("id") != team_id:
                continue
            etype = (ev.get("type") or "").lower()
            detail = (ev.get("detail") or "").lower()
            elapsed = ev.get("time", {}).get("elapsed")
            if elapsed is None:
                continue
            if etype == "card" and ("red" in detail or "second yellow" in detail) and int(elapsed) < 60:
                mins.append(int(elapsed))
        except Exception:
            continue
    return min(mins) if mins else None


def fetch_recent_team_metrics(team_id: int, last_n: int = 6) -> Dict[str, Any]:
    """Proxy honestamente etiquetado (goles saneados + rojas + atípicos)."""
    fixtures = safe_request(
        f"https://v3.football.api-sports.io/fixtures?team={team_id}&last={last_n}"
    ).get("response", [])

    gf_list, ga_list = [], []
    early_reds = 0
    multi_pen_games = 0
    used = 0

    for fx in fixtures:
        try:
            fid = int(fx["fixture"]["id"])
            home = fx["teams"]["home"]
            away = fx["teams"]["away"]
            gh, ga_ = fx["goals"]["home"], fx["goals"]["away"]
            if gh is None or ga_ is None:
                continue

            if home["id"] == team_id:
                g_for, g_against = float(gh), float(ga_)
            else:
                g_for, g_against = float(ga_), float(gh)

            events = get_fixture_events(fid)
            anomalies = analyze_match_anomalies(events, team_id)

            if anomalies["early_red"]:
                early_reds += 1
                minute = earliest_red_minute(events, team_id)
                g_against *= red_penalty_factor(minute)

            weight = 1.0
            if anomalies["multi_penalty_game"]:
                multi_pen_games += 1
                weight *= 0.40
            elif (anomalies["penalties_for"] + anomalies["penalties_against"]) >= 2:
                weight *= 0.70

            total = g_for + g_against
            if total >= 7:
                weight *= 0.50
            elif total >= 6:
                weight *= 0.75

            gf_list.append(g_for * weight)
            ga_list.append(g_against * weight)
            used += 1
        except Exception:
            continue

    if used == 0:
        return {
            "gf": 1.15,
            "ga": 1.15,
            "n": 0,
            "early_reds": 0,
            "multi_pen_games": 0,
            "source": "proxy_goals",
        }

    return {
        "gf": float(np.mean(gf_list)),
        "ga": float(np.mean(ga_list)),
        "n": used,
        "early_reds": early_reds,
        "multi_pen_games": multi_pen_games,
        "source": "proxy_goals",
    }


# ====================== PUNTO 2: team_xg ======================
def get_team_xg_from_db(team_id: int, window_n: int = XG_WINDOW) -> Optional[Dict[str, Any]]:
    if supabase is None:
        return None
    try:
        res = (
            supabase.table("team_xg")
            .select("*")
            .eq("team_id", str(team_id))
            .eq("window_n", window_n)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None

        row = rows[0]
        updated = row.get("updated_at")
        if updated:
            dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
            if age > timedelta(hours=XG_MAX_AGE_HOURS):
                return None
        return row
    except Exception as e:
        log(f"⚠️ No se pudo leer team_xg ({team_id}): {e}")
        return None


def upsert_team_xg(row: Dict[str, Any]) -> None:
    if supabase is None:
        return
    try:
        supabase.table("team_xg").upsert(row, on_conflict="team_id,window_n").execute()
        log(f"✅ team_xg upsert: {row.get('team_name')} ({row.get('source')})")
    except Exception as e:
        log(f"⚠️ Fallo upsert team_xg: {e}")


def build_proxy_team_xg(
    team_id: int, team_name: str, league: str, window_n: int = XG_WINDOW
) -> Dict[str, Any]:
    metrics = fetch_recent_team_metrics(team_id, last_n=window_n)
    return {
        "team_id": str(team_id),
        "team_name": team_name,
        "league": league,
        "window_n": window_n,
        "npxg_for": round(float(metrics["gf"]), 3),
        "npxga": round(float(metrics["ga"]), 3),
        "sample_size": int(metrics.get("n", 0)),
        "source": "proxy_goals",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def ensure_team_xg(
    team_id: int, team_name: str, league: str, window_n: int = XG_WINDOW
) -> Dict[str, Any]:
    cached = get_team_xg_from_db(team_id, window_n)
    if cached:
        log(f"📥 XG cache hit: {team_name} | source={cached.get('source')}")
        return cached

    row = build_proxy_team_xg(team_id, team_name, league, window_n)
    upsert_team_xg(row)
    log(f"🧮 XG proxy: {team_name} | for={row['npxg_for']} against={row['npxga']}")
    return row


# ====================== PUNTO 3: FASE 0 ======================
def phase0_build_lambdas(
    home_id: int,
    away_id: int,
    home_name: str,
    away_name: str,
    league: str,
    injury_impact_home: float = 0.0,
    injury_impact_away: float = 0.0,
) -> Dict[str, Any]:
    params = league_params(league)
    vol = params["volatility"]
    home_adv = params["home_adv"]
    league_avg = params["league_avg"]
    rho = params["rho"]

    if all(v is not None for v in [NPxG_HOME, NPxGA_HOME, NPxG_AWAY, NPxGA_AWAY]):
        home_x = {
            "npxg_for": float(NPxG_HOME),
            "npxga": float(NPxGA_HOME),
            "source": "manual",
            "sample_size": -1,
        }
        away_x = {
            "npxg_for": float(NPxG_AWAY),
            "npxga": float(NPxGA_AWAY),
            "source": "manual",
            "sample_size": -1,
        }
        source = "REAL_NPXG_OVERRIDE"
    else:
        home_x = ensure_team_xg(home_id, home_name, league)
        away_x = ensure_team_xg(away_id, away_name, league)
        source = f"{home_x.get('source')}+{away_x.get('source')}"

    att_home = float(home_x["npxg_for"]) / league_avg
    def_home = float(home_x["npxga"]) / league_avg
    att_away = float(away_x["npxg_for"]) / league_avg
    def_away = float(away_x["npxga"]) / league_avg

    base_home = att_home * def_away * league_avg
    base_away = att_away * def_home * league_avg

    lambda_home = float(np.clip(base_home * home_adv + injury_impact_home, 0.55, 2.60))
    lambda_away = float(np.clip(base_away + injury_impact_away, 0.45, 2.40))

    log("\n===== FASE 0 (team_xg primero) =====")
    log(f"data_source: {source}")
    log(f"Liga: {league} | Vol: {vol} | rho={rho} | home_adv={home_adv}")
    log(f"Home XG: for={home_x['npxg_for']} against={home_x['npxga']} | src={home_x.get('source')}")
    log(f"Away XG: for={away_x['npxg_for']} against={away_x['npxga']} | src={away_x.get('source')}")
    log(f"Impacto bajas L/V: {injury_impact_home}/{injury_impact_away}")
    log(f"λ_local={lambda_home:.3f} | λ_visitante={lambda_away:.3f}")
    log("===================================\n")

    return {
        "lambda_home": round(lambda_home, 3),
        "lambda_away": round(lambda_away, 3),
        "volatility": vol,
        "rho": rho,
        "home_adv": home_adv,
        "data_source": source,
        "home_xg": home_x,
        "away_xg": away_x,
        "is_bonus": IS_BONUS,
        "injury_impact_home": injury_impact_home,
        "injury_impact_away": injury_impact_away,
    }


# ====================== MOTOR 6.1 ======================
def poisson_pmf(k: int, lam: float) -> float:
    return float(np.exp(-lam) * (lam ** k) / np.math.factorial(k))


def dixon_coles_tau(x: int, y: int, lam_h: float, lam_a: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1.0 - lam_h * lam_a * rho
    if x == 0 and y == 1:
        return 1.0 + lam_h * rho
    if x == 1 and y == 0:
        return 1.0 + lam_a * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def build_score_matrix(lam_h: float, lam_a: float, rho: float, max_goals: int) -> np.ndarray:
    mat = np.zeros((max_goals + 1, max_goals + 1), dtype=float)
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_pmf(i, lam_h) * poisson_pmf(j, lam_a)
            p *= dixon_coles_tau(i, j, lam_h, lam_a, rho)
            mat[i, j] = max(p, 0.0)
    s = mat.sum()
    if s <= 0:
        raise RuntimeError(
            "ERROR: Datos insuficientes o no saneados. No se puede ejecutar el motor matemático."
        )
    return mat / s


def monte_carlo_from_matrix(mat: np.ndarray, n_sims: int) -> Dict[str, Any]:
    flat = mat.ravel()
    draws = np.random.choice(np.arange(flat.size), size=n_sims, p=flat)
    max_g = mat.shape[0]
    hs, aws = draws // max_g, draws % max_g
    return {
        "prob_home": float(np.mean(hs > aws)),
        "prob_draw": float(np.mean(hs == aws)),
        "prob_away": float(np.mean(hs < aws)),
        "prob_btts": float(np.mean((hs > 0) & (aws > 0))),
        "prob_over_2_5": float(np.mean((hs + aws) >= 3)),
        "avg_home_goals": float(hs.mean()),
        "avg_away_goals": float(aws.mean()),
    }


def compute_expected_points(mat: np.ndarray, volatility: str, is_bonus: bool) -> List[Dict[str, Any]]:
    pts_res = PTS_RESULTADO_BONUS if is_bonus else PTS_RESULTADO
    pts_ex = PTS_EXACTO_BONUS if is_bonus else PTS_EXACTO

    p_home = float(sum(mat[i, j] for i in range(mat.shape[0]) for j in range(mat.shape[1]) if i > j))
    p_draw = float(sum(mat[i, i] for i in range(mat.shape[0])))
    p_away = float(sum(mat[i, j] for i in range(mat.shape[0]) for j in range(mat.shape[1]) if i < j))

    rows = []
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            p_exact = float(mat[i, j])
            p_1x2 = p_home if i > j else p_draw if i == j else p_away
            ev = p_exact * pts_ex + p_1x2 * pts_res
            if volatility == "Alta" and (i + j) >= 4:
                ev *= 0.85
            rows.append({"score": f"{i}-{j}", "p_exact": p_exact, "p_1x2": p_1x2, "ev": ev})
    rows.sort(key=lambda x: x["ev"], reverse=True)
    return rows


def run_engine_6_1(
    lam_h: float, lam_a: float, volatility: str, is_bonus: bool, rho: float
) -> Dict[str, Any]:
    log("🧮 Motor 6.1 reproducible (seed fija)")
    mat = build_score_matrix(lam_h, lam_a, rho, MAX_GOALS)
    mc = monte_carlo_from_matrix(mat, N_SIMS)
    ranking = compute_expected_points(mat, volatility, is_bonus)
    top1, top2 = ranking[0], ranking[1]
    return {
        "simulations": N_SIMS,
        "seed": RANDOM_SEED,
        "rho": rho,
        "prob_home": round(mc["prob_home"], 4),
        "prob_draw": round(mc["prob_draw"], 4),
        "prob_away": round(mc["prob_away"], 4),
        "prob_btts": round(mc["prob_btts"], 4),
        "prob_over_2_5": round(mc["prob_over_2_5"], 4),
        "avg_home_goals": round(mc["avg_home_goals"], 3),
        "avg_away_goals": round(mc["avg_away_goals"], 3),
        "top1_score": top1["score"],
        "top1_ev": round(top1["ev"], 4),
        "top2_score": top2["score"],
        "top2_ev": round(top2["ev"], 4),
        "top_marcador": top1["score"],
        "ranking_top5": [
            {"score": r["score"], "ev": round(r["ev"], 4), "p_exact": round(r["p_exact"], 4)}
            for r in ranking[:5]
        ],
    }


# ====================== SUPABASE ======================
def upsert_partido(record_en: Dict[str, Any]) -> None:
    if supabase is None:
        return
    last_err = None
    for table_name in ("matches", "partidos", "Partidos"):
        try:
            supabase.table(table_name).upsert(record_en, on_conflict="fixture_id").execute()
            log(f"✅ Partido en '{table_name}'")
            return
        except Exception as e:
            last_err = e
    raise Exception(f"Fallo partido: {last_err}")


def insert_prediccion(record_pred: Dict[str, Any]) -> None:
    if supabase is None:
        return
    last_err = None
    for table_name in ("predictions", "predicciones", "Predicciones"):
        try:
            supabase.table(table_name).insert(record_pred).execute()
            log(f"✅ Prediction en '{table_name}'")
            return
        except Exception as e:
            last_err = e
    raise Exception(f"Fallo prediction: {last_err}")


# ====================== PUNTO 4: MAIN ======================
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

    inj_h = float(os.getenv("INJURY_IMPACT_HOME", "0") or 0)
    inj_a = float(os.getenv("INJURY_IMPACT_AWAY", "0") or 0)

    # 1) team_xg primero (DB → proxy)
    # 2) λ limpios
    phase0 = phase0_build_lambdas(
        home_id=home_id,
        away_id=away_id,
        home_name=home,
        away_name=away,
        league=league,
        injury_impact_home=inj_h,
        injury_impact_away=inj_a,
    )

    # 3) Motor 6.1
    engine = run_engine_6_1(
        phase0["lambda_home"],
        phase0["lambda_away"],
        phase0["volatility"],
        IS_BONUS,
        phase0["rho"],
    )

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
            "status": f"quant_6_1_{status_short}",
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
        log(json.dumps({"phase0": phase0, "engine": engine}, indent=2, default=str))

    bonus_tag = " (BONUS)" if IS_BONUS else ""
    if RUN_MODE == "ENGINE_ONLY":
        print("Partido,1° más probable,2° más probable")
        print(f"{home} vs {away}{bonus_tag},{engine['top1_score']},{engine['top2_score']}")
    else:
        print("\nPartido,1° más probable,2° más probable")
        print(f"{home} vs {away}{bonus_tag},{engine['top1_score']},{engine['top2_score']}")


if __name__ == "__main__":
    if RUN_MODE != "ENGINE_ONLY":
        print("=" * 75)
        print("PIPELINE QUANT V7.3 – team_xg + motor 6.1 auditado")
        print("=" * 75)
    try:
        process_single_match(TARGET_HOME, TARGET_AWAY)
    except Exception as e:
        if RUN_MODE == "ENGINE_ONLY":
            print("ERROR: Datos insuficientes o no saneados. No se puede ejecutar el motor matemático.")
        else:
            print(f"\n❌ Error en la tubería de datos: {e}")
            raise
    if RUN_MODE != "ENGINE_ONLY":
        print("=" * 75)
