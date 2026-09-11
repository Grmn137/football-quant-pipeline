import sys
import json
import numpy as np
from scipy.stats import poisson

def calcular_matriz_poisson(lambda_local, lambda_visita, max_goles=6):
    """
    Calcula la matriz de probabilidades de goles usando la distribución de Poisson independiente.
    """
    matriz = np.outer(
        [poisson.pmf(i, lambda_local) for i in range(max_goles + 1)],
        [poisson.pmf(j, lambda_visita) for j in range(max_goles + 1)]
    )
    return matriz

def procesar_prediccion(data):
    """
    Motor cuantitativo principal con saneamiento Anti-GIGO.
    """
    # 1. Extracción de variables base
    npxg_local = float(data.get('npxg_local', 1.3))
    npxg_visita = float(data.get('npxg_visita', 1.1))
    
    # 2. Ajuste por bajas o factores externos (Fase 0)
    penalizacion_local = float(data.get('penal_local', 0.0))
    penalizacion_visita = float(data.get('penal_visita', 0.0))
    
    lambda_l = max(0.2, npxg_local - penalizacion_local)
    lambda_v = max(0.2, npxg_visita - penalizacion_visita)
    
    # 3. Cálculo de Matriz de Probabilidades
    matriz = calcular_matriz_poisson(lambda_l, lambda_v)
    
    prob_local = np.sum(np.tril(matriz, -1)) # Gana local
    prob_empate = np.sum(np.diag(matriz))    # Empate
    prob_visita = np.sum(np.triu(matriz, 1)) # Gana visita
    
    # 4. Cálculo de Marcadores Exactos más probables
    marcadores = []
    for i in range(7):
        for j in range(7):
            marcadores.append({
                "goles_local": i,
                "goles_visita": j,
                "probabilidad": float(matriz[i, j])
            })
    
    marcadores = sorted(marcadores, key=lambda x: x['probabilidad'], reverse=True)
    
    resultado = {
        "lambda_local_ajustado": round(lambda_l, 3),
        "lambda_visita_ajustado": round(lambda_v, 3),
        "probabilidades_1x2": {
            "local": round(prob_local * 100, 2),
            "empate": round(prob_empate * 100, 2),
            "visita": round(prob_visita * 100, 2)
        },
        "top_marcadores_exactos": marcadores[:5]
    }
    
    return resultado

if __name__ == "__main__":
    # Prueba local o ejecución por la API serverless
    try:
        entrada_json = sys.stdin.read()
        if entrada_json.strip():
            datos_partido = json.loads(entrada_json)
        else:
            # Datos de prueba por defecto si se ejecuta sin argumentos
            datos_partido = {
                "npxg_local": 1.55,
                "npxg_visita": 1.10,
                "penal_local": 0.0,
                "penal_visita": 0.15
            }
        
        resultado_final = procesar_prediccion(datos_partido)
        print(json.dumps(resultado_final, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
