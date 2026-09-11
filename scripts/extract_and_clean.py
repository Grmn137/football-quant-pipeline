import os
import requests
import json

def main():
    # Endpoint de tu backend en Vercel
    vercel_api_url = os.getenv("VERCEL_API_URL", "https://football-quant-pipeline.vercel.app/api/predict")
    
    # Datos del partido a analizar: Union Berlin vs Schalke 04
    match_data = {
        "home_team": "Union Berlin",
        "away_team": "Schalke 04",
        "lambda_home": 1.45,
        "lambda_away": 0.85
    }

    print(f"Iniciando análisis cuantitativo para: {match_data['home_team']} vs {match_data['away_team']}")
    print(f"Conectando con el motor en Vercel...")

    try:
        # Petición POST enviando los datos del partido
        response = requests.post(vercel_api_url, json=match_data, timeout=20)

        print(f"Código de estado HTTP: {response.status_code}")
        
        if response.status_code == 200:
            result_json = response.json()
            print("¡Pronóstico generado con éxito!")
            print(json.dumps(result_json, indent=2))
        else:
            print(f"Error en la respuesta del servidor: {response.text}")
            exit(1)

    except requests.exceptions.RequestException as e:
        print(f"Error de conexión con la API: {e}")
        exit(1)

if __name__ == "__main__":
    main()
