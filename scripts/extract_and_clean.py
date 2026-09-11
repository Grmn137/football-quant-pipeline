import os
import requests
import time
import json
from datetime import datetime, timezone
from typing import Tuple, Optional, Dict, Any
from supabase import create_client, Client

# ====================== CONFIGURACIÓN ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mqtfiupwtolrbmojiwgz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
VERCEL_API_URL = os.getenv("VERCEL_API_URL", "https://football-quant-pipeline.vercel.app/api/predict")

if not all([SUPABASE_KEY, API_FOOTBALL_KEY]):
    raise ValueError("Faltan variables de entorno críticas: SUPABASE_SERVICE_ROLE_KEY o API_FOOTBALL_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS_API = {
    "x-apisports-key": API_FOOTBALL_KEY,
    "Content-Type": "application/json"
}

# ====================== INGRESO DE EQUIPOS ======================
TARGET_HOME = "Independiente Santa Fe"
TARGET_AWAY = "Tolima"

# IDs CORRECTOS (verificados) + aliases robustos
TEAM_ALIASES = {
    # Independiente Santa Fe (Colombia) - ID real más común en API-Football
    "independiente santa fe": 1137,
    "santa fe": 1137,
    "ind. santa fe": 1137,
    "independiente santafe": 1137,
    
    # Deportes Tolima
    "tolima": 1142,
    "deportes tolima": 1142,
    "deportes de tolima": 1142,
}

COLOMBIA_LEAGUE_ID = 239  # Primera A / Liga BetPlay (ajusta si usas otra temporada)

# ====================== FUNCIONES DE UTILIDAD ======================
def determine_volatility(league_name: str) -> str:
    league = league_name.lower()
    if any(x in league for x in ["colombia", "betplay", "dimayor", "argentina", "liga profesional", "lpfe", "ecuador", "liga pro"]):
        return "Alta"
    if any(x in league for x in ["brasil", "brazil", "brasileirão", "serie a brazil", "serie a betano"]):
        return "Media-Alta"
    return "Baja-Media"

def calculate_lambda(base_xg: float, is_home: bool = True, injury_impact: float = 0.0, volatility: str = "Alta") -> float:
    lambda_val = base_xg
    if is_home:
        lambda_val *= 1.07 if volatility == "Alta" else 1.10
    lambda_val += injury_impact
    return round(max(0.55, min(lambda_val, 2.45)), 3)

def estimate_base_xg(team_name: str, league: str, is_home: bool) -> float:
    league = league.lower()
    if "colombia" in league or "betplay" in league:
        return 1.18 if is_home else 1.05
    if "argentina" in league:
        return 1.15 if is_home else 1.02
    if "ecuador" in league:
        return 1.20 if is_home else 1.08
    if "brasil" in league or "brazil" in league:
        return 1.28 if is_home else 1.12
    return 1.25 if is_home else 1.10

def safe_request(url: str, max_retries: int = 3) -> Dict[str, Any]:
    """Request con reintentos y manejo limpio de errores de API-Football"""
    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=HEADERS_API, timeout=20)
            res.raise_for_status()
            data = res.json()
            if data.get("errors"):
                print(f"⚠️ API Errors: {data['errors']}")
            return data
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"⚠️ Request falló (intento {attempt+1}). Reintentando en {wait}s... → {e}")
                time.sleep(wait)
            else:
                raise

# ====================== BUSCADOR DE EQUIPOS DEFINITIVO ======================
def search_team_id(team_target: str, preferred_country: str = "Colombia") -> Tuple[int, str]:
    target_lower = team_target.lower().strip()
    
    # 1. Alias directo (prioridad máxima)
    if target_lower in TEAM_ALIASES:
        team_id = TEAM_ALIASES[target_lower]
        print(f"⚡ Usando alias directo para '{team_target}' (ID: {team_id})")
        data = safe_request(f"https://v3.football.api-sports.io/teams?id={team_id}")
        if data.get("response"):
            team = data["response"][0]["team"]
            return team["id"], team["name"]
    
    # 2. Búsqueda textual inteligente
    queries = list(dict.fromkeys([
        team_target,
        team_target.replace("Independiente ", "").replace("Deportes ", "").strip(),
        " ".join(team_target.split()[-2:]) if len(team_target.split()) > 2 else team_target
    ]))
    
    candidates = []
    for q in queries:
        print(f"🔍 Buscando: '{q}'...")
        data = safe_request(f"https://v3.football.api-sports.io/teams?search={q}")
        for item in data.get("response", []):
            team = item["team"]
            country = item.get("team", {}).get("country") or item.get("country", {}).get("name", "")
            candidates.append({
                "id": team["id"],
                "name": team["name"],
                "country": country,
                "score": 0
            })
    
    if not candidates:
        raise ValueError(f"No se encontró ningún equipo para '{team_target}'")
    
    # Scoring inteligente: prioriza Colombia + nombre más parecido
    for c in candidates:
        score = 0
        name_lower = c["name"].lower()
        if preferred_country.lower() in c["country"].lower():
            score += 100
        if target_lower in name_lower or name_lower in target_lower:
            score += 50
        if "santa fe" in name_lower and "independiente" in name_lower:
            score += 30
        if "tolima" in name_lower:
            score += 30
        c["score"] = score
    
    best = max(candidates, key=lambda x: x["score"])
    print(f"✅ Mejor match: {best['name']} (ID: {best['id']}) | País: {best['country']} | Score: {best['score']}")
    return best["id"], best["name"]

# ====================== FIXTURE / H2H ======================
def get_fixture_direct(home_target: str, away_target: str) -> Dict[str, Any]:
    home_id, home_real = search_team_id(home_target)
    away_id, away_real = search_team_id(away_target)
    print(f"✅ Localizado: {home_real} (ID: {home_id}) vs {away_real} (ID: {away_id})")
    
    # 1. Intentar H2H
    print("🗓️ Buscando historial Head-to-Head...")
    h2h_url = f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}"
    matches = safe_request(h2h_url).get("response", [])
    
    if matches:
        now = datetime.now(timezone.utc).timestamp()
        closest = min(matches, key=lambda x: abs(x["fixture"]["timestamp"] - now))
        print(f"📌 Partido H2H más cercano encontrado (ID: {closest['fixture']['id']})")
        return closest
    
    # 2. Fallback profesional: próximo partido entre estos equipos en la liga colombiana
    print("⚠️ Sin H2H. Buscando próximo fixture en Liga BetPlay...")
    fixtures_url = (
        f"https://v3.football.api-sports.io/fixtures"
        f"?team={home_id}&next=15&league={COLOMBIA_LEAGUE_ID}"
    )
    fixtures = safe_request(fixtures_url).get("response", [])
    
    for fix in fixtures:
        teams = fix["teams"]
        if (teams["home"]["id"] == home_id and teams["away"]["id"] == away_id) or \
           (teams["home"]["id"] == away_id and teams["away"]["id"] == home_id):
            print(f"📌 Fixture próximo encontrado (ID: {fix['fixture']['id']})")
            return fix
    
    raise ValueError(
        f"No hay historial H2H ni próximo partido programado entre {home_real} y {away_real}."
    )

def process_single_match(home_target: str, away_target: str):
    try:
        fixture = get_fixture_direct(home_target, away_target)
        
        fix_id = str(fixture["fixture"]["id"])
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        league = fixture["league"]["name"]
        kickoff = fixture["fixture"]["date"]
        status_short = fixture["fixture"]["status"]["short"]
        vol = determine_volatility(league)

        print(f"📌 Partido capturado → ID: {fix_id} | {home} vs {away} | Estado: {status_short} | Liga: {league}")

        l_home = calculate_lambda(estimate_base_xg(home, league, True), True, 0.0, vol)
        l_away = calculate_lambda(estimate_base_xg(away, league, False), False, 0.0, vol)

        record = {
            "fixture_id": fix_id,
            "home_team": home,
            "away_team": away,
            "league": league,
            "kickoff": kickoff,
            "volatility": vol,
            "lambda_home": l_home,
            "lambda_away": l_away,
            "status": f"quant_processed_{status_short}",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        print("💾 Guardando en Supabase...")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                supabase.table("matches").upsert(record, on_conflict="fixture_id").execute()
                break
            except Exception as db_err:
                if attempt < max_retries - 1:
                    print(f"⚠️ Supabase latency (intento {attempt+1}). Reintentando...")
                    time.sleep(4)
                else:
                    raise Exception(f"Fallo definitivo en BD: {db_err}")

        print("🚀 Lanzando Monte Carlo (10k) en Vercel...")
        v_res = requests.post(
            VERCEL_API_URL,
            json={
                "home_team": home,
                "away_team": away,
                "lambda_home": l_home,
                "lambda_away": l_away
            },
            timeout=30
        )
        
        if v_res.status_code in (200, 201):
            print("🏆 ¡Análisis cuantitativo completado!\n", json.dumps(v_res.json(), indent=2))
        else:
            print(f"⚠️ Error Vercel ({v_res.status_code}): {v_res.text}")

    except Exception as e:
        print(f"\n❌ Error en la tubería de datos: {e}")
        raise

if __name__ == "__main__":
    print("=" * 70)
    print("PIPELINE QUANT V6.4 – CORRECT IDs + SMART SEARCH + FALLBACK")
    print("=" * 70)
    process_single_match(TARGET_HOME, TARGET_AWAY)
    print("=" * 70)
