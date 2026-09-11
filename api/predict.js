// Función para calcular factoriales necesarios en la distribución de Poisson
function factorial(n) {
  if (n === 0 || n === 1) return 1;
  let acc = 1;
  for (let i = 2; i <= n; i++) acc *= i;
  return acc;
}

// Cálculo de la probabilidad de Poisson (modelo Dixon-Coles simplificado)
function poissonProbability(k, lambda) {
  return (Math.pow(lambda, k) * Math.exp(-lambda)) / factorial(k);
}

module.exports = (req, res) => {
  // Validar método HTTP POST
  if (req.method !== 'POST') {
    res.setHeader('Allow', ['POST']);
    return res.status(405).json({ error: `Método ${req.method} no permitido. Utilice POST.` });
  }

  try {
    const { npxg_local, npxg_visita, penal_local, penal_visita } = req.body || {};
    
    // Aplicar protocolo Anti-GIGO y deducciones por ausencias
    const lambdaLocal = Math.max(0.1, (npxg_local || 1.5) - (penal_local || 0));
    const lambdaVisita = Math.max(0.1, (npxg_visita || 1.2) - (penal_visita || 0));

    let probLocalWin = 0;
    let probDraw = 0;
    let probAwayWin = 0;
    
    const exactScores = [];

    // Generar matriz de probabilidades de goles (hasta 6 goles por equipo)
    for (let h = 0; h <= 6; h++) {
      for (let a = 0; a <= 6; a++) {
        const pHome = poissonProbability(h, lambdaLocal);
        const pAway = poissonProbability(a, lambdaVisita);
        const pScore = pHome * pAway;

        if (h > a) probLocalWin += pScore;
        else if (h === a) probDraw += pScore;
        else probAwayWin += pScore;

        exactScores.push({ score: `${h}-${a}`, probability: pScore });
      }
    }

    // Ordenar y extraer el Top 5 de marcadores más probables
    exactScores.sort((a, b) => b.probability - a.probability);
    const topScores = exactScores.slice(0, 5).map(item => ({
      marcador: item.score,
      probabilidad_porcentaje: Number((item.probability * 100).toFixed(2))
    }));

    return res.status(200).json({
      status: "success",
      engine: "Native Node.js Poisson",
      expected_goals_ajustados: {
        local: Number(lambdaLocal.toFixed(2)),
        visita: Number(lambdaVisita.toFixed(2))
      },
      probabilidades_1x2: {
        local_porcentaje: Number((probLocalWin * 100).toFixed(2)),
        empate_porcentaje: Number((probDraw * 100).toFixed(2)),
        visita_porcentaje: Number((probAwayWin * 100).toFixed(2))
      },
      top_marcadores_exactos: topScores
    });

  } catch (error) {
    return res.status(500).json({
      status: "error",
      message: "Fallo en el cálculo matemático nativo",
      details: error.message
    });
  }
};
