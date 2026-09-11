import os
import requests
import time
from datetime import datetime, timedelta
from supabase import create_client, Client
from bs4 import BeautifulSoup
import json

# ====================== CONFIGURACIÓN ======================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # Importante: usar service_role
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")       # Opcional

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ====================== FUNCIONES DE FUENTES ÉLITE ======================

def get_understat_npxg(team_name: str, league: str = "EPL"):
    """
    Intento de extracción de npxG desde Understat.
    Nota: Understat tiene protección, por lo que en producción se recomienda
    usar una capa intermedia o datos pre-cargados.
    """
    # Placeholder de alta calidad (en producción se reemplaza por scraping real o API)
    # Aquí devolvemos estructura lista para ser reemplazada
    return {
        "npxg": None,
        "npxga": None,
        "source": "understat"
    }


def determine_volatility(league_name: str) -> str:
    league = league_name.lower()
    if any(x in league for x in ["colombia", "argentina", "ecuador", "betplay", "liga profesional", "dimayor"]):
        return "Alta"
    if any(x in league for x in ["brasil", "brazil", "brasileirão", "serie a"]):
        return "Media-Alta"
    return "Baja-Media"


def calculate_lambda(npxg: float, is_home: bool = True, injury_impact: float = 0.0) -> float:
    base = npxg if npxg else 1.20
    if is_home:
        base *= 1.08   # Ajuste localía conservador
    base += injury_impact
    return round(max(min(base, 2.8), 0.45), 3)


def get_fixtures_from_api_football(date: str = None):
    """Fuente base de fixtures (API-Football)"""
    if not API_FOOTBALL_KEY:
        print("No hay API_FOOTBALL_KEY configurada")
        return []

    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")

    url = f"https://v3.football.api-sports.io/fixtures?date={date}"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=15)
        data = response.json()
        return data.get("response", [])
    except Exception as e:
        print(f"Error API-Football: {e}")
        return []


def process_fixture(fixture: dict):
    try:
        fixture_id = str(fixture["fixture"]["id"])
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        league = fixture["league"]["name"]
        kickoff = fixture["fixture"]["date"]

        volatility = determine_volatility(league)

        # === AQUÍ SE INTEGRAN LAS FUENTES ÉLITE ===
        # Por ahora usamos valores base realistas + estructura lista
        # para conectar Understat / FBref más adelante

        npxg_home = 1.32
        npxga_home = 1.18
        npxg_away = 1.15
        npxga_away = 1.38

        # Impacto de lesiones (se puede enriquecer con Transfermarkt)
        injuries_impact = {}

        lambda_home = calculate_lambda(npxg_home, is_home=True)
        lambda_away = calculate_lambda(npxg_away, is_home=False)

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

        # Upsert en Supabase
        supabase.table("matches").upsert(record, on_conflict="fixture_id").execute()
        print(f"✓ {home} vs {away} | λ: {lambda_home} - {lambda_away} | {volatility}")

    except Exception as e:
        print(f"Error procesando fixture {fixture.get('fixture', {}).get('id')}: {e}")


def main():
    print("=" * 60)
    print(f"Iniciando extracción élite - {datetime.utcnow()}")
    print("=" * 60)

    fixtures = get_fixtures_from_api_football()
    print(f"Partidos encontrados: {len(fixtures)}")

    for i, fixture in enumerate(fixtures, 1):
        print(f"[{i}/{len(fixtures)}] ", end="")
        process_fixture(fixture)
        time.sleep(1.1)  # Rate limit respetuoso

    print("=" * 60)
    print("Extracción finalizada")
    print("=" * 60)


if __name__ == "__main__":
    main()
