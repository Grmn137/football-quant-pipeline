// Función para calcular factoriales necesarios en la distribución de Poisson
function factorial(n) {
  if (n === 0 || n === 1) return 1;
  let acc = 1;
  for (let i = 2; i <= n; i++) acc *= i;
  return acc;
}

function poissonProbability(k, lambda) {
  return (Math.pow(lambda, k) * Math.exp(-lambda)) / factorial(k);
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', ['POST']);
    return res.status(405).json({ error: `Método ${req.method} no permitido. Utilice POST.` });
  }

  try {
    const { npxg_local, npxg_visita, penal_local, penal_visita } = req.body || {};
    
    const lambdaLocal = Math.max(0.1, (npxg_local || 1.5) - (penal_local || 0));
    const lambdaVisita = Math.max(0.1, (npxg_visita || 1.2) - (penal_visita || 0));

    let probLocalWin = 0;
    let probDraw = 0;
    let probAwayWin = 0;
    
    const exactScores = [];

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

    exactScores.sort((a, b) => b.probability - a.probability);
    const topScores = exactScores.slice(0, 5).map(item => ({
      marcador: item.score,
      probabilidad_porcentaje: Number((item.probability * 100).toFixed(2))
    }));

    const pLocalPct = Number((probLocalWin * 100).toFixed(2));
    const pDrawPct = Number((probDraw * 100).toFixed(2));
    const pAwayPct = Number((probAwayWin * 100).toFixed(2));
    const topMarcadorStr = topScores.length > 0 ? topScores[0].marcador : "0-0";

    // Guardar en Supabase usando fetch nativo si las credenciales existen
    const supabaseUrl = process.env.SUPABASE_URL;
    const supabaseKey = process.env.SUPABASE_ANON_KEY;

    if (supabaseUrl && supabaseKey) {
      await fetch(`${supabaseUrl}/rest/v1/predictions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'apikey': supabaseKey,
          'Authorization': `Bearer ${supabaseKey}`,
          'Prefer': 'return=minimal'
        },
        body: JSON.stringify({
          npxg_local: lambdaLocal,
          npxg_visita: lambdaVisita,
          prob_local: pLocalPct,
          prob_empate: pDrawPct,
          prob_visita: pAwayPct,
          top_marcador: topMarcadorStr
        })
      });
    }

    return res.status(200).json({
      status: "success",
      engine: "Native Node.js Poisson + Supabase DB",
      expected_goals_ajustados: {
        local: Number(lambdaLocal.toFixed(2)),
        visita: Number(lambdaVisita.toFixed(2))
      },
      probabilidades_1x2: {
        local_porcentaje: pLocalPct,
        empate_porcentaje: pDrawPct,
        visita_porcentaje: pAwayPct
      },
      top_marcadores_exactos: topScores
    });

  } catch (error) {
    return res.status(500).json({
      status: "error",
      message: "Fallo en el cálculo o guardado en base de datos",
      details: error.message
    });
  }
};
