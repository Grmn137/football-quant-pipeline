import os
import requests
import time
from datetime import datetime
from supabase import create_client, Client
from typing import Dict, Optional

# ====================== CONFIGURACIÓN ======================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS_API = {
    "x-apisports-key": API_FOOTBALL_KEY
}

# ====================== FUNCIONES DE UTILIDAD ======================

def determine_volatility(league_name: str) -> str:
    """Clasificación de volatilidad optimizada para el torneo"""
    league = league_name.lower()
    
    if any(x in league for x in [
        "colombia", "betplay", "dimayor",
        "argentina", "liga profesional", "lpfe",
        "ecuador", "liga pro", "serie a ecuador"
    ]):
        return "Alta"
    
    if any(x in league for x in ["brasil", "brazil", "brasileirão", "serie a brazil", "serie a betano"]):
        return "Media-Alta"
    
    return "Baja-Media"


def calculate_lambda(
    base_xg: float,
    is_home: bool = True,
    injury_impact: float = 0.0,
    volatility: str = "Alta"
) -> float:
    """
    Cálculo de λ optimizado para ligas sudamericanas.
    Más conservador en ligas de alta volatilidad.
    """
    lambda_val = base_xg

    # Ajuste de localía (más moderado en Sudamérica)
    if is_home:
        if volatility == "Alta":
            lambda_val *= 1.07
        else:
            lambda_val *= 1.10

    # Impacto de lesiones
    lambda_val += injury_impact

    # Límites de seguridad (evitar valores extremos)
    lambda_val = max(0.55, min(lambda_val, 2.45))

    return round(lambda_val, 3)


def get_fixtures_today() -> list:
    """Obtiene los fixtures del día desde API-Football"""
    if not API_FOOTBALL_KEY:
        print("ERROR: No hay API_FOOTBALL_KEY configurada")
        return []

    today = datetime.utcnow().strftime("%Y-%m-%d")
    url = f"https://v3.football.api-sports.io/fixtures?date={today}"

    try:
        response = requests.get(url, headers=HEADERS_API, timeout=20)
        data = response.json()
        return data.get("response", [])
    except Exception as e:
        print(f"Error obteniendo fixtures: {e}")
        return []


def estimate_base_xg(team_name: str, league: str, is_home: bool) -> float:
    """
    Estimación base de xG cuando no hay datos de Understat/FBref.
    Valores calibrados para ligas sudamericanas (más bajos que Europa).
    """
    # Valores promedio realistas por tipo de liga
    if "colombia" in league.lower() or "betplay" in league.lower():
        base = 1.18 if is_home else 1.05
    elif "argentina" in league.lower():
        base = 1.15 if is_home else 1.02
    elif "ecuador" in league.lower():
        base = 1.20 if is_home else 1.08
    elif "brasil" in league.lower() or "brazil" in league.lower():
        base = 1.28 if is_home else 1.12
    else:
        base = 1.25 if is_home else 1.10

    return base


def process_fixture(fixture: dict):
    try:
        fixture_id = str(fixture["fixture"]["id"])
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        league = fixture["league"]["name"]
        kickoff = fixture["fixture"]["date"]

        volatility = determine_volatility(league)

        # === ESTIMACIÓN DE npxG (optimizada para LATAM) ===
        # En el futuro aquí se puede conectar FBref o Sofascore
        npxg_home = estimate_base_xg(home, league, is_home=True)
        npxga_home = estimate_base_xg(away, league, is_home=False)  # aproximación
        npxg_away = estimate_base_xg(away, league, is_home=False)
        npxga_away = estimate_base_xg(home, league, is_home=True)

        # Impacto de lesiones (se puede enriquecer después)
        injuries_impact = {}

        lambda_home = calculate_lambda(
            base_xg=npxg_home,
            is_home=True,
            injury_impact=0.0,
            volatility=volatility
        )
        lambda_away = calculate_lambda(
            base_xg=npxg_away,
            is_home=False,
            injury_impact=0.0,
            volatility=volatility
        )

        record = {
            "fixture_id": fixture_id,
            "home_team": home,
            "away_team": away,
            "league": league,
            "kickoff": kickoff,
            "volatility": volatility,
            "npxg_home": npxg_home,
            "npxga_home": npxga_home,
            "npxg_away": npxg_away,
            "npxga_away": npxga_away,
            "lambda_home": lambda_home,
            "lambda_away": lambda_away,
            "injuries_impact": injuries_impact,
            "status": "cleaned",
            "updated_at": datetime.utcnow().isoformat()
        }

        # Guardar en Supabase
        supabase.table("matches").upsert(record, on_conflict="fixture_id").execute()
        
        print(f"✓ {home} vs {away}")
        print(f"   Liga: {league} | Volatilidad: {volatility}")
        print(f"   λ → {lambda_home} - {lambda_away}")

    except Exception as e:
        print(f"Error procesando fixture: {e}")


def main():
    print("=" * 65)
    print(f"EXTRACCIÓN LATAM OPTIMIZADA - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 65)

    fixtures = get_fixtures_today()
    print(f"Partidos encontrados hoy: {len(fixtures)}\n")

    processed = 0
    for fixture in fixtures:
        process_fixture(fixture)
        processed += 1
        time.sleep(1.15)  # Respetar rate limit

    print("\n" + "=" * 65)
    print(f"Proceso finalizado. Partidos procesados: {processed}")
    print("=" * 65)


if __name__ == "__main__":
    main()
