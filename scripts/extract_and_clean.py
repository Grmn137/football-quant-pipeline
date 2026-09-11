import os
import requests
import time
import json
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, List
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

# Solo aliases 100% verificados (puedes ir agregando los que uses más)
TEAM_ALIASES = {
    # Colombia
    "tolima": 1142,
    "deportes tolima": 1142,
    
    # Puedes ir agregando aquí los que uses frecuentemente:
    # "real madrid": 541,
    # "barcelona": 529,
    # "manchester city": 50,
    # "boca juniors": 451,
    # "river plate": 435,
    # "flamengo": 127,
    # "america": 2287,          # América México
    # etc.
}

# ====================== UTILIDADES ======================
def determine_volatility(league_name: str) -> str:
    league = (league_name or "").lower()
    
    high_volatility = [
        "colombia", "betplay", "dimayor",
        "argentina", "liga profesional", "primera división",
        "mexico", "liga mx", "ascenso",
        "brazil", "brasil", "brasileirão", "serie a", "serie b"
    ]
    
    medium_high = [
        "portugal", "liga portugal", "segunda liga",
        "turkey", "süper lig", "greece", "super league"
    ]
    
    if any(x in league for x in high_volatility):
        return "Alta"
    if any(x in league for x in medium_high):
        return "Media-Alta"
    return "Baja-Media"

def calculate_lambda(base_xg: float, is_home: bool = True, injury_impact: float = 0.0, volatility: str = "Alta") -> float:
    lambda_val = base_xg
    if is_home:
        lambda_val *= 1.07 if volatility == "Alta" else 1.10
    lambda_val += injury_impact
    return round(max(0.55, min(lambda_val, 2.45)), 3)

def estimate_base_xg(team_name: str, league: str, is_home: bool) -> float:
    league = (league or "").lower()
    
    if any(x in league for x in ["colombia", "betplay", "dimayor"]):
        return 1.18 if is_home else 1.05
    if any(x in league for x in ["argentina", "liga profesional"]):
        return 1.15 if is_home else 1.02
    if any(x in league for x in ["mexico", "liga mx"]):
        return 1.22 if is_home else 1.08
    if any(x in league for x in ["brazil", "brasil", "brasileirão"]):
        return 1.28 if is_home else 1.12
    if any(x in league for x in ["portugal", "liga portugal"]):
        return 1.35 if is_home else 1.15
    if any(x in league for x in ["premier", "la liga", "serie a", "bundesliga"]):
        return 1.45 if is_home else 1.25
    
    # Default neutral
    return 1.25 if is_home else 1.10

def safe_request(url: str, max_retries: int = 3) -> Dict[str, Any]:
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

# ====================== BUSCADOR UNIVERSAL (SIN SESGO) ======================
def search_team_id(team_target: str) -> Tuple[int, str]:
    target_lower = team_target.lower().strip()
    
    # 1. Alias verificado
    if target_lower in TEAM_ALIASES:
        team_id = TEAM_ALIASES[target_lower]
        print(f"⚡ Usando alias verificado para '{team_target}' (ID: {team_id})")
        data = safe_request(f"https://v3.football.api-sports.io/teams?id={team_id}")
        if data.get("response"):
            team = data["response"][0]["team"]
            return team["id"], team["name"]
    
    # 2. Búsqueda textual limpia
    print(f"🔍 Buscando: '{team_target}'...")
    data = safe_request(f"https://v3.football.api-sports.io/teams?search={team_target}")
    
    candidates: List[Dict] = []
    
    for item in data.get("response", []):
        team = item["team"]
        name = team["name"]
        country = team.get("country", "Unknown")
        name_lower = name.lower()
        
        score = 0
        
        # Matching exacto / parcial
        if target_lower == name_lower:
            score += 100
        elif target_lower in name_lower:
            score += 60
        elif name_lower in target_lower:
            score += 40
        
        # Bonus por palabras clave importantes
        keywords = target_lower.split()
        matched_keywords = sum(1 for k in keywords if k in name_lower)
        score += matched_keywords * 15
        
        candidates.append({
            "id": team["id"],
            "name": name,
            "country": country,
            "score": score
        })
    
    if not candidates:
        raise ValueError(f"No se encontró ningún equipo para '{team_target}'")
    
    # Ordenar por score descendente
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    print(f"\n📋 Top candidatos para '{team_target}':")
    for i, c in enumerate(candidates[:8], 1):
        print(f"   {i}. {c['name']} (ID: {c['id']}) | {c['country']} | Score: {c['score']}")
    
    best = candidates[0]
    
    if best["score"] < 50:
        print(f"\n⚠️ Score bajo ({best['score']}). Revisa los candidatos arriba.")
    
    print(f"\n✅ Seleccionado: {best['name']} (ID: {best['id']}) | País: {best['country']}")
    return best["id"], best["name"]

# ====================== FIXTURE / H2H UNIVERSAL ======================
def get_fixture_direct(home_target: str, away_target: str) -> Dict[str, Any]:
    home_id, home_real = search_team_id(home_target)
    away_id, away_real = search_team_id(away_target)
    
    print(f"\n✅ Localizado: {home_real} (ID: {home_id}) vs {away_real} (ID: {away_id})")
    
    # 1. Intentar H2H
    print("🗓️ Buscando historial Head-to-Head...")
    h2h_url = f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}"
    matches = safe_request(h2h_url).get("response", [])
    
    if matches:
        now = datetime.now(timezone.utc).timestamp()
        closest = min(matches, key=lambda x: abs(x["fixture"]["timestamp"] - now))
        print(f"📌 Partido H2H más cercano encontrado (ID: {closest['fixture']['id']})")
        return closest
    
    # 2. Fallback: próximos partidos de cualquiera de los dos equipos
    print("⚠️ Sin H2H. Buscando próximo enfrentamiento entre ambos...")
    
    for team_id in [home_id, away_id]:
        fixtures_url = f"https://v3.football.api-sports.io/fixtures?team={team_id}&next=30"
        fixtures = safe_request(fixtures_url).get("response", [])
        
        for fix in fixtures:
            teams = fix["teams"]
            if (teams["home"]["id"] == home_id and teams["away"]["id"] == away_id) or \
               (teams["home"]["id"] == away_id and teams["away"]["id"] == home_id):
                print(f"📌 Fixture próximo encontrado (ID: {fix['fixture']['id']})")
               
