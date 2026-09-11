import os
import requests
import time
import difflib
import json
from datetime import datetime
from supabase import create_client, Client
from typing import Dict, Optional

# ====================== CONFIGURACIÓN ======================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
VERCEL_API_URL = os.getenv("VERCEL_API_URL", "https://football-quant-pipeline.vercel.app/api/predict")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS_API = {
    "x-apisports-key": API_FOOTBALL_KEY,
    "Content-Type": "application/json"
}

# ====================== INGRESO DE EQUIPOS ======================
TARGET_HOME = "Rennes"
TARGET_AWAY = "Marseille"


# ====================== FUNCIONES DE UTILIDAD ======================

def determine_volatility(league_name: str) -> str:
    league = league_name.lower()
    if any(x in league for x in ["colombia", "betplay", "dimayor", "argentina", "liga profesional", "lpfe", "ecuador", "liga pro", "serie a ecuador"]):
        return "Alta"
    if any(x in league for x in ["brasil", "brazil", "brasileirão", "serie a brazil", "serie a betano"]):
        return "Media-Alta"
    return "Baja-Media"

def calculate_lambda(base_xg: float, is_home: bool = True, injury_impact: float = 0.0, volatility: str = "Alta") -> float:
    lambda_val = base_xg
    if is_home:
        if volatility == "Alta":
            lambda_val *= 1.07
        else:
            lambda_val *= 1.10
    lambda_val += injury_impact
    lambda_val = max(0.55, min(lambda_val, 2.45))
    return round(lambda_val, 3)

def estimate_base_xg(team_name: str, league: str, is_home: bool) -> float:
    if "colombia" in league.lower() or "betplay" in league.lower():
        return 1.18 if is_home else 1.05
    elif "argentina" in league.lower():
        return 1.15 if is_home else 1.02
    elif "ecuador" in league.lower():
        return 1.20 if is_home else 1.08
    elif "brasil" in league.lower() or "brazil" in league.lower():
        return 1.28 if is_home else 1.12
    else:
        return 1.25 if is_home else 1.10


# ====================== MOTOR DE BÚSQUEDA ELITE (HÍBRIDO) ======================

def search_team_id(team_target: str) -> tuple:
    clean_target = team_target.replace("Stade ", "").replace("FC ", "").replace("Club ", "").strip()
    queries = list(dict.fromkeys([team_target, clean_target]))
    
    for q in queries:
        print(f"🔍 Consultando API-Football para: '{q}'...")
        team_url = f"https://v3.football.api-sports.io/teams?search={q}"
        team_res = requests.get(team_url, headers=HEADERS_API, timeout=15).json()
        
        if team_res.get("response"):
            team_data = team_res["response"][0]["team"]
            return team_data["id"], team_data["name"]
            
    raise ValueError(f"No se pudo encontrar el equipo '{team_target}' en la API.")

def fuzzy_match(target: str, name: str) -> bool:
    t_clean = target.lower().strip()
    n_clean = name.lower().strip()
    if t_clean in n_clean or n_clean in t_clean:
        return True
    return difflib.SequenceMatcher(None, t_clean, n_clean).ratio() > 0.45

def get_fixture_by_names(home_target: str, away_target: str) -> dict:
    if not API_FOOTBALL_KEY:
        raise ValueError("ERROR: API_FOOTBALL_KEY no configurada.")

    home_id, home_real = search_team_id(home_target)
    away_id, away_real = search_team_id(away_target)
    print(f"✅ Localizado: {home_real} (ID: {home_id}) vs {away_real} (ID: {away_id})")
    
    # ESTRATEGIA HÍBRIDA: Buscamos tanto en partidos en vivo/hoy como en los próximos del calendario
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    endpoints = [
        f"https://v3.football.api-sports.io/fixtures?team={home_id}&date={today_str}", # Partidos de hoy (en vivo o por jugar)
        f"https://v3.football.api-sports.io/fixtures?team={home_id}&next=25"          # Próximos partidos
    ]
    
    for fix_url in endpoints:
        print(f"🗓️ Consultando calendario (Endpoint: {'hoy' if 'date' in fix_url else 'próximos'})...")
        fix_res = requests.get(fix_url, headers=HEADERS_API, timeout=15).json()
        
        for match in fix_res.get("response", []):
            m_home_id = match["teams"]["home"]["id"]
            m_away_id = match["teams"]["away"]["id"]
            
            if (m_home_id == home_id and m_away_id == away_id) or \
               (fuzzy_match(away_target, match["teams"]["home"]["name"]) or fuzzy_match(away_target, match["teams"]["away"]["name"])):
                return match
                
    raise ValueError(f"No se encontró un partido activo o próximo programado entre {home_real} y {away_real}.")

def process_single_match(home_target: str, away_target: str):
    try:
        fixture = get_fixture_by_names(home_target, away_target)
        
        fixture_id = str(fixture["fixture"]["id"])
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        league = fixture["league"]["name"]
        kickoff = fixture["fixture"]["date"]
        status_short = fixture["fixture"]["status"]["short"]
        volatility = determine_volatility(league)

        print(f"📌 Estado actual del partido en la API: {status_short}")

        npxg_home = estimate_base_xg(home, league, is_home=True)
        npxg_away = estimate_base_xg(away, league, is_home=False)
        lambda_home = calculate_lambda(npxg_home, True, 0.0, volatility)
        lambda_away = calculate_lambda(npxg_away, False, 0.0, volatility)

        record = {
            "fixture_id": fixture_id,
            "home_team": home,
            "away_team": away,
            "league": league,
            "kickoff": kickoff,
            "volatility": volatility,
            "lambda_home": lambda_home,
            "lambda_away": lambda_away,
            "status": f"quant_processed_{status_short}",
            "updated_at": datetime.utcnow().isoformat()
        }

        print(f"💾 Guardando métricas en Supabase...")
        supabase.table("matches").upsert(record, on_conflict="fixture_id").execute()
        print(f"   ✓ Partido: {home} vs {away} | Liga: {league} | λ: {lambda_home} - {lambda_away}")

        print(f"🚀 Desplegando simulación Monte Carlo (10k) en Vercel...")
        vercel_payload = {
            "home_team": home,
            "away_team": away,
            "lambda_home": lambda_home,
            "lambda_away": lambda_away
        }
        v_res = requests.post(VERCEL_API_URL, json=vercel_payload, timeout=25)
        
        if v_res.status_code in [200, 201]:
            print("🏆 ¡Análisis cuantitativo completado con éxito!")
            print(json.dumps(v_res.json(), indent=2))
        else:
            print(f"⚠️ Error en motor Vercel: {v_res.text}")

    except Exception as e:
        print(f"\n❌ Error en la tubería de datos: {e}")

def main():
    print("=" * 65)
    print(f"PIPELINE QUANT V6.1 - BUSCADOR HÍBRIDO (EN VIVO / PRE-PARTIDO)")
    print("=" * 65)
    process_single_match(TARGET_HOME, TARGET_AWAY)
    print("=" * 65)

if __name__ == "__main__":
    main()
