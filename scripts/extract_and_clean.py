import os
import requests
import time
import json
from datetime import datetime
from supabase import create_client, Client

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
        lambda_val *= 1.07 if volatility == "Alta" else 1.10
    lambda_val += injury_impact
    return round(max(0.55, min(lambda_val, 2.45)), 3)

def estimate_base_xg(team_name: str, league: str, is_home: bool) -> float:
    league = league.lower()
    if "colombia" in league or "betplay" in league: return 1.18 if is_home else 1.05
    if "argentina" in league: return 1.15 if is_home else 1.02
    if "ecuador" in league: return 1.20 if is_home else 1.08
    if "brasil" in league or "brazil" in league: return 1.28 if is_home else 1.12
    return 1.25 if is_home else 1.10

# ====================== BUSCADOR H2H DEFINITIVO ======================
def search_team_id(team_target: str) -> tuple:
    clean_target = team_target.replace("Stade ", "").replace("FC ", "").replace("Club ", "").strip()
    for q in list(dict.fromkeys([team_target, clean_target])):
        print(f"🔍 Consultando API-Football para: '{q}'...")
        res = requests.get(f"https://v3.football.api-sports.io/teams?search={q}", headers=HEADERS_API, timeout=15).json()
        if res.get("response"):
            return res["response"][0]["team"]["id"], res["response"][0]["team"]["name"]
    raise ValueError(f"No se pudo encontrar el equipo '{team_target}'.")

def get_fixture_direct(home_target: str, away_target: str) -> dict:
    home_id, home_real = search_team_id(home_target)
    away_id, away_real = search_team_id(away_target)
    print(f"✅ Localizado: {home_real} (ID: {home_id}) vs {away_real} (ID: {away_id})")
    
    print("🗓️ Ejecutando búsqueda Head-to-Head (H2H) absoluta...")
    h2h_url = f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}"
    matches = requests.get(h2h_url, headers=HEADERS_API, timeout=15).json().get("response", [])
    
    if not matches:
        raise ValueError(f"No hay historial H2H entre {home_real} y {away_real}.")
        
    now = datetime.utcnow().timestamp()
    return min(matches, key=lambda x: abs(x["fixture"]["timestamp"] - now))

def process_single_match(home_target: str, away_target: str):
    try:
        fixture = get_fixture_direct(home_target, away_target)
        
        fix_id = str(fixture["fixture"]["id"])
        home, away = fixture["teams"]["home"]["name"], fixture["teams"]["away"]["name"]
        league, kickoff = fixture["league"]["name"], fixture["fixture"]["date"]
        status_short = fixture["fixture"]["status"]["short"]
        vol = determine_volatility(league)

        print(f"📌 Partido H2H más cercano capturado (ID: {fix_id}) | Estado: {status_short} | Competencia: {league}")

        l_home = calculate_lambda(estimate_base_xg(home, league, True), True, 0.0, vol)
        l_away = calculate_lambda(estimate_base_xg(away, league, False), False, 0.0, vol)

        record = {
            "fixture_id": fix_id, "home_team": home, "away_team": away,
            "league": league, "kickoff": kickoff, "volatility": vol,
            "lambda_home": l_home, "lambda_away": l_away,
            "status": f"quant_processed_{status_short}",
            "updated_at": datetime.utcnow().isoformat()
        }

        print("💾 Guardando métricas en Supabase...")
        
        # Sistema de reintentos para evitar fallos por 504 Timeout
        max_retries = 3
        for attempt in range(max_retries):
            try:
                supabase.table("matches").upsert(record, on_conflict="fixture_id").execute()
                break # Si tiene éxito, rompe el bucle
            except Exception as db_err:
                if attempt < max_retries - 1:
                    print(f"⚠️ Latencia en Supabase (Intento {attempt + 1}/{max_retries}). Reintentando en 5s...")
                    time.sleep(5)
                else:
                    raise Exception(f"Fallo definitivo al guardar en BD: {db_err}")
        
        print("🚀 Desplegando simulación Monte Carlo (10k) en Vercel...")
        v_res = requests.post(VERCEL_API_URL, json={"home_team": home, "away_team": away, "lambda_home": l_home, "lambda_away": l_away}, timeout=25)
        
        if v_res.status_code in [200, 201]:
            print("🏆 ¡Análisis cuantitativo completado con éxito!\n", json.dumps(v_res.json(), indent=2))
        else:
            print(f"⚠️ Error Vercel: {v_res.text}")

    except Exception as e:
        print(f"\n❌ Error en la tubería de datos: {e}")

if __name__ == "__main__":
    print("=" * 65)
    print("PIPELINE QUANT V6.3 - H2H TIMESTAMP MATCHING & RETRIES")
    print("=" * 65)
    process_single_match(TARGET_HOME, TARGET_AWAY)
    print("=" * 65)
