import os
import requests
import time
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Tuple, Dict, Any, List
from supabase import create_client, Client

# ====================== CONFIGURACIÓN ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mqtfiupwtolrbmojiwgz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
VERCEL_API_URL = os.getenv(
    "VERCEL_API_URL",
    "https://football-quant-pipeline.vercel.app/api/predict",
)

if not all([SUPABASE_KEY, API_FOOTBALL_KEY]):
    raise ValueError(
        "Faltan variables de entorno críticas: SUPABASE_SERVICE_ROLE_KEY o API_FOOTBALL_KEY"
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS_API = {
    "x-apisports-key": API_FOOTBALL_KEY,
    "Content-Type": "application/json",
}

BOGOTA_TZ = ZoneInfo("America/Bogota")

# ====================== PARTIDO A PROCESAR ======================
TARGET_HOME = os.getenv("TARGET_HOME", "Independiente Santa Fe")
TARGET_AWAY = os.getenv("TARGET_AWAY", "Tolima")

TEAM_ALIASES = {
    "independiente santa fe": 1139,
    "santa fe": 1139,
    "tolima": 1142,
    "deportes tolima": 1142,
}

# ====================== UTILIDADES ======================
def normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def to_bogota_iso(dt_value) -> str:
    if dt_value is None:
        return datetime.now(BOGOTA_TZ).isoformat()

    if isinstance(dt_value, str):
        raw = dt_value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    else:
        dt = dt_value

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(BOGOTA_TZ).isoformat()


def determine_volatility(league_name: str) -> str:
    league = normalize(league_name)
    high = [
        "colombia", "betplay", "dimayor", "primera a",
        "argentina", "liga profesional",
        "mexico", "liga mx", "ascenso",
        "brazil", "brasil", "brasileirao", "serie a", "serie b",
    ]
    medium = ["portugal", "liga portugal", "segunda liga", "turkey", "greece"]
    if any(x in league for x in high):
        return "Alta"
    if any(x in league for x in medium):
        return "Media-Alta"
    return "Baja-Media"


def estimate_base_xg(team_name: str, league: str, is_home: bool) -> float:
    league = normalize(league)
    if any(x in league for x in ["colombia", "betplay", "dimayor", "primera a"]):
        return 1.18 if is_home else 1.05
    if any(x in league for x in ["argentina", "liga profesional"]):
        return 1.15 if is_home else 1.02
    if any(x in league for x in ["mexico", "liga mx"]):
        return 1.22 if is_home else 1.08
    if any(x in league for x in ["brazil", "brasil", "brasileirao"]):
        return 1.28 if is_home else 1.12
    if any(x in league for x in ["portugal", "liga portugal"]):
        return 1.35 if is_home else 1.15
    if any(x in league for x in ["premier", "la liga", "serie a", "bundesliga", "ligue 1"]):
        return 1.45 if is_home else 1.25
    return 1.25 if is_home else 1.10


def calculate_lambda(
    base_xg: float,
    is_home: bool = True,
    injury_impact: float = 0.0,
    volatility: str = "Alta",
) -> float:
    lambda_val = base_xg
    if is_home:
        lambda_val *= 1.07 if volatility == "Alta" else 1.10
    lambda_val += injury_impact
    return round(max(0.55, min(lambda_val, 2.45)), 3)


def safe_request(url: str, max_retries: int = 3) -> Dict[str, Any]:
    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=HEADERS_API, timeout=20)
            res.raise_for_status()
            data = res.json()
            if data.get("errors"):
                print(f"⚠️ API Errors: {data['errors']}")
            return data
        except Exception:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"⚠️ Request falló (intento {attempt+1}). Reintentando en {wait}s...")
                time.sleep(wait)
            else:
                raise


def score_team_name(target: str, candidate_name: str) -> int:
    t = normalize(target)
    n = normalize(candidate_name)
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
    if "union" in n and "independiente santa fe" in t:
        score -= 40
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
        print(f"⚡ Probando alias para '{team_target}' (ID: {team_id})")
        data = safe_request(f"https://v3.football.api-sports.io/teams?id={team_id}")
        if data.get("response"):
            team = data["response"][0]["team"]
            alias_score = score_team_name(team_target, team["name"])
            print(f"   Alias resolvió: {team['name']} | score={alias_score}")
            if alias_score >= 50:
                return team["id"], team["name"]
            print("   ⚠️ Alias no confiable, se busca por texto...")

    candidates_map: Dict[int, Dict[str, Any]] = {}
    for q in search_queries_for(team_target):
        print(f"🔍 Buscando: '{q}'...")
        data = safe_request(f"https://v3.football.api-sports.io/teams?search={q}")
        for item in data.get("response", []):
            team = item["team"]
            tid = team["id"]
            name = team["name"]
            country = team.get("country", "Unknown")
            sc = score_team_name(team_target, name)
            prev = candidates_map.get(tid)
            if (prev is None) or (sc > prev["score"]):
                candidates_map[tid] = {
                    "id": tid,
                    "name": name,
                    "country": country,
                    "score": sc,
                }

    candidates = sorted(candidates_map.values(), key=lambda x: x["score"], reverse=True)
    if not candidates:
        raise ValueError(f"No se encontró ningún equipo para '{team_target}'")

    print(f"\n📋 Top candidatos para '{team_target}':")
    for i, c in enumerate(candidates[:10], 1):
        print(f"   {i}. {c['name']} (ID: {c['id']}) | {c['country']} | Score: {c['score']}")

    best = candidates[0]
    if best["score"] < 40:
        raise ValueError(
            f"Match poco confiable para '{team_target}'. Mejor candidato: {best['name']} (score={best['score']})"
        )

    print(f"\n✅ Seleccionado: {best['name']} (ID: {best['id']}) | País: {best['country']}")
    return best["id"], best["name"]


def get_fixture_direct(home_target: str, away_target: str) -> Dict[str, Any]:
    home_id, home_real = search_team_id(home_target)
    away_id, away_real = search_team_id(away_target)

    print(f"\n✅ Localizado: {home_real} (ID: {home_id}) vs {away_real} (ID: {away_id})")
    print("🗓️ Buscando historial Head-to-Head...")
    h2h_url = f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}"
    matches = safe_request(h2h_url).get("response", [])

    if matches:
        now = datetime.now(timezone.utc).timestamp()
        closest = min(matches, key=lambda x: abs(x["fixture"]["timestamp"] - now))
        print(f"📌 Partido H2H más cercano (ID: {closest['fixture']['id']})")
        return closest

    print("⚠️ Sin H2H. Buscando próximo enfrentamiento entre ambos...")
    for team_id in [home_id, away_id]:
        fixtures_url = f"https://v3.football.api-sports.io/fixtures?team={team_id}&next=30"
        fixtures = safe_request(fixtures_url).get("response", [])
        for fix in fixtures:
            teams = fix["teams"]
            ids = {teams["home"]["id"], teams["away"]["id"]}
            if home_id in ids and away_id in ids:
                print(f"📌 Fixture próximo encontrado (ID: {fix['fixture']['id']})")
                return fix

    raise ValueError(
        f"No hay historial H2H ni próximo partido programado entre {home_real} y {away_real}."
    )


def upsert_partido(record_es: Dict[str, Any], record_en: Dict[str, Any]) -> None:
    last_err = None
    for table_name, payload in (
        ("matches", record_en),
        ("partidos", record_es),
        ("Partidos", record_es),
    ):
        try:
            supabase.table(table_name).upsert(payload, on_conflict="fixture_id").execute()
            print(f"✅ Guardado en tabla '{table_name}'")
            return
        except Exception as e:
            last_err = e
            continue
    raise Exception(f"Fallo al guardar partido: {last_err}")


def insert_prediccion(record_pred: Dict[str, Any]) -> None:
    """Nombre real confirmado por Supabase: public.predictions"""
    last_err = None
    for table_name in ("predictions", "predicciones", "Predicciones"):
        try:
            supabase.table(table_name).insert(record_pred).execute()
            print(f"✅ Guardado en tabla '{table_name}'")
            return
        except Exception as e:
            last_err = e
            continue
    raise Exception(f"Fallo al guardar en predictions: {last_err}")


def process_single_match(home_target: str, away_target: str):
    try:
        fixture = get_fixture_direct(home_target, away_target)

        fix_id = str(fixture["fixture"]["id"])
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        league = fixture["league"]["name"]
        kickoff_raw = fixture["fixture"]["date"]
        status_short = fixture["fixture"]["status"]["short"]
        vol = determine_volatility(league)

        kickoff_bogota = to_bogota_iso(kickoff_raw)
        consulta_bogota = to_bogota_iso(datetime.now(timezone.utc))

        print(f"\n📌 Partido capturado → ID: {fix_id}")
        print(f"   {home} vs {away}")
        print(f"   Estado: {status_short} | Liga: {league}")
        print(f"   Kickoff Bogotá: {kickoff_bogota}")
        print(f"   Consulta Bogotá: {consulta_bogota}")

        l_home = calculate_lambda(estimate_base_xg(home, league, True), True, 0.0, vol)
        l_away = calculate_lambda(estimate_base_xg(away, league, False), False, 0.0, vol)

        record_es = {
            "fixture_id": fix_id,
            "home_team": home,
            "away_team": away,
            "Liga": league,
            "inicio": kickoff_bogota,
            "volatilidad": vol,
            "lambda_home": l_home,
            "lambda_away": l_away,
            "estado": f"quant_processed_{status_short}",
            "updated_at": consulta_bogota,
        }
        record_en = {
            "fixture_id": fix_id,
            "home_team": home,
            "away_team": away,
            "league": league,
            "kickoff": kickoff_bogota,
            "volatility": vol,
            "lambda_home": l_home,
            "lambda_away": l_away,
            "status": f"quant_processed_{status_short}",
            "updated_at": consulta_bogota,
        }

        print("\n💾 Guardando métricas en Partidos/matches...")
        for attempt in range(3):
            try:
                upsert_partido(record_es, record_en)
                break
            except Exception as db_err:
                if attempt < 2:
                    print(f"⚠️ Latencia Supabase (intento {attempt+1}). Reintentando...")
                    time.sleep(4)
                else:
                    raise Exception(f"Fallo definitivo al guardar partido: {db_err}")

        print("🚀 Lanzando simulación Monte Carlo (10k) en Vercel...")
        payload = {
            "fixture_id": fix_id,
            "home_team": home,
            "away_team": away,
            "league": league,
            "volatility": vol,
            "lambda_home": l_home,
            "lambda_away": l_away,
        }

        pred = None
        try:
            v_res = requests.post(VERCEL_API_URL, json=payload, timeout=35)
            if v_res.status_code in (200, 201):
                body = v_res.json()
                print("🏆 ¡Análisis cuantitativo completado!\n")
                print(json.dumps(body, indent=2))
                pred = body.get("prediction") or body
            else:
                print(f"⚠️ Error Vercel ({v_res.status_code}): {v_res.text[:600]}")
        except Exception as ve:
            print(f"⚠️ Error llamando a Vercel: {ve}")

        if not pred:
            raise Exception("No se obtuvo predicción desde Vercel")

        prob_local = float(pred.get("prob_home", 0)) * 100
        prob_empate = float(pred.get("prob_draw", 0)) * 100
        prob_visita = float(pred.get("prob_away", 0)) * 100
        avg_h = float(pred.get("avg_home_goals", l_home))
        avg_a = float(pred.get("avg_away_goals", l_away))
        top_marcador = f"{round(avg_h)}-{round(avg_a)}"

        # Columnas según tu tabla visible en Supabase
        record_pred = {
            "created_at": consulta_bogota,
            "npxg_local": l_home,
            "npxg_visita": l_away,
            "prob_local": round(prob_local, 2),
            "prob_empate": round(prob_empate, 2),
            "prob_visita": round(prob_visita, 2),
            "top_marcador": top_marcador,
        }

        print("💾 Guardando resultado en predictions...")
        for attempt in range(3):
            try:
                insert_prediccion(record_pred)
                break
            except Exception as db_err:
                if attempt < 2:
                    print(f"⚠️ Latencia Supabase predictions (intento {attempt+1})...")
                    time.sleep(4)
                else:
                    raise Exception(f"Fallo al guardar en predictions: {db_err}")

        print("✅ Predicción guardada en Supabase (tabla predictions)")
        print(f"   Horario partido (Bogotá): {kickoff_bogota}")
        print(f"   Horario consulta (Bogotá): {consulta_bogota}")

    except Exception as e:
        print(f"\n❌ Error en la tubería de datos: {e}")
        raise


if __name__ == "__main__":
    print("=" * 75)
    print("PIPELINE QUANT V6.10 – predictions + hora Bogotá")
    print("=" * 75)
    process_single_match(TARGET_HOME, TARGET_AWAY)
    print("=" * 75)
