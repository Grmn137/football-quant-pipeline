# Pipeline de Fútbol Cuantitativo ⚽📊

Infraestructura de análisis cuantitativo y modelado predictivo para torneos de fútbol, basada en la distribución de **Poisson Bivariada** y correcciones estadísticas (Dixon-Coles / Anti-GIGO).

## Estructura del Repositorio
- `api/`: Funciones serverless (Node.js) para manejar las solicitudes de predicción.
- `scripts/`: Motores de procesamiento de datos y algoritmos estadísticos (Python).
- `requirements.txt`: Dependencias científicas (`numpy`, `scipy`, `pandas`).

## Características del Motor
* **Saneamiento de Datos (Anti-GIGO):** Aislamiento de métricas de goles esperados sin penales (npxG) para evitar distorsiones por eventos de alta varianza.
* **Ajuste Estructural:** Inyección de variables de penalización por ausencias críticas de jugadores en el once inicial.
* **Optimización de Puntos (EV):** Cálculo de probabilidades exactas y retorno de valor esperado por encuentro.
