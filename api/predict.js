// Funciones auxiliares matemáticas
function factorial(n) {
  if (n === 0 || n === 1) return 1;
  let acc = 1;
  for (let i = 2; i <= n; i++) acc *= i;
  return acc;
}

// Generador de números aleatorios para Poisson (Método de Knuth)
function samplePoisson(lambda) {
  let L = Math.exp(-lambda);
  let k = 0;
  let p = 1;
  do {
    k++;
    p *= Math.random();
  } while (p > L);
  return k - 1;
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', ['POST']);
    return res.status(405).json({ error: `Método ${req.method} no permitido. Utilice POST.` });
  }

  try {
    const { 
      npxg_local, 
      npxg_visita, 
      penal_local = 0, 
      penal_visita = 0,
      red_card_home = false,
      red_card_away = false,
      injuries_impact_home = 0, // ej: -0.15
      injuries_impact_away = 0,
      volatility = 'Media-Alta', // 'Alta', 'Media-Alta', 'Baja-Media'
      is_bonus = false
    } = req.body || {};
    
    // --- FASE 0: SANEAMIENTO Y AJUSTES ---
    let lambdaLocal = Math.max(0.1, (npxg_local || 1.5) - penal_local + injuries_impact_home);
    let lambdaVisita = Math.max(0.1, (npxg_visita || 1.2) - penal_visita + injuries_impact_away);

    // Ajuste por tarjeta roja antes del min 60 (-15% al npxGA rival aprox, reflejado en tasas)
    if (red_card_home) lambdaVisita *= 1.12; 
    if (red_card_away) lambdaLocal *= 1.12;

    // --- MOTOR MATEMÁTICO: MONTE CARLO (10,000 iteraciones) + DIXON-COLES ---
    const iterations = 10000;
    const scoreCounts = {};
    let homeWins = 0;
    let draws = 0;
    let awayWins = 0;

    // Parámetro Dixon-Coles (rho)
    const rho = -0.12;

    for (let i = 0; i < iterations; i++) {
      let h = samplePoisson(lambdaLocal);
      let a = samplePoisson(lambdaVisita);

      // Aplicar corrección Dixon-Coles para marcadores bajos (0-0, 1-0, 0-1, 1-1)
      if (h === 0 && a === 0) {
        if (Math.random() < Math.abs(rho) * lambdaLocal * lambdaVisita) { h = 1; a = 1; }
      } else if (h === 1 && a === 0) {
        if (Math.random() < Math.abs(rho)) h = 0;
      } else if (h === 0 && a === 1) {
        if (Math.random() < Math.abs(rho)) a = 0;
      } else if (h === 1 && a === 1) {
        if (Math.random() < Math.abs(rho)) { h = 0; a = 0; }
      }

      const scoreKey = `${h}-${a}`;
      scoreCounts[scoreKey] = (scoreCounts[scoreKey] || 0) + 1;

      if (h > a) homeWins++;
      else if (h === a) draws++;
      else awayWins++;
    }

    const probLocalWin = homeWins / iterations;
    const probDraw = draws / iterations;
    const probAwayWin = awayWins / iterations;

    // --- CÁLCULO DE EXPECTED POINTS (EV) ---
    // Puntos base: Resultado (3 si acierta 1X2, 0 si no) | Exacto (5 si acierta marcador exacto)
    // Si es BONUS: Resultado = 5, Exacto = 10
    const pointsResult = is_bonus ? 5 : 3;
    const pointsExact = is_bonus ? 10 : 5;

    const evaluatedScores = [];

    for (const [score, count] of Object.entries(scoreCounts)) {
      const probExact = count / iterations;
      const [hStr, aStr] = score.split('-');
      const h = parseInt(hStr);
      const a = parseInt(aStr);

      // Determinar si este marcador acierta el 1X2 asociado
      let matchesOutcome = false;
      if (h > a && probLocalWin > probDraw && probLocalWin > probAwayWin) matchesOutcome = true;
      else if (h === a && probDraw > probLocalWin && probDraw > probAwayWin) matchesOutcome = true;
      else if (h < a && probAwayWin > probLocalWin && probAwayWin > probDraw) matchesOutcome = true;

      // Fórmula de Expected Points
      let ev = (probExact * pointsExact) + (matchesOutcome ? (probExact * pointsResult) : 0);

      // Penalización por Alta Volatilidad (si total goles >= 3.5 y liga es volátil)
      if ((volatility === 'Alta') && (h + a >= 4)) {
        ev *= 0.85; // Penalización del 15%
      }

      evaluatedScores.push({
        marcador: score,
        probabilidad_porcentaje: Number((probExact * 100).toFixed(2)),
        expected_points: Number(ev.toFixed(4))
      });
    }

    // Ordenar por Expected Points (EV) descendente
    evaluatedScores.sort((a, b) => b.expected_points - a.expected_points);

    const topScores = evaluatedScores.slice(0, 5);
    const top1 = topScores[0] ? topScores[0].marcador : "0-0";
    const top2 = topScores[1] ? topScores[1].marcador : "1-0";

    const pLocalPct = Number((probLocalWin * 100).toFixed(2));
    const pDrawPct = Number((probDraw * 100).toFixed(2));
    const pAwayPct = Number((probAwayWin * 100).toFixed(2));

    // --- PERSISTENCIA EN SUPABASE ---
    const supabaseUrl = process.env.SUPABASE_URL;
    const supabaseKey = process.env.SUPABASE_ANON_KEY;

    let dbResponseStatus = "No intentado";
    let dbErrorDetails = null;

    if (supabaseUrl && supabaseKey) {
      const cleanUrl = supabaseUrl.replace(/\/$/, "");
      
      const dbResponse = await fetch(`${cleanUrl}/rest/v1/predictions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'apikey': supabaseKey,
          'Authorization': `Bearer ${supabaseKey}`,
          'Prefer': 'return=representation'
        },
        body: JSON.stringify({
          npxg_local: lambdaLocal,
          npxg_visita: lambdaVisita,
          prob_local: pLocalPct,
          prob_empate: pDrawPct,
          prob_visita: pAwayPct,
          top_marcador: top1
        })
      });

      dbResponseStatus = dbResponse.status;
      if (!dbResponse.ok) {
        dbErrorDetails = await dbResponse.text();
      }
    }

    return res.status(200).json({
      status: "success",
      engine: "Versión 6.1 - Monte Carlo (10,000 iteraciones) + Dixon-Coles + EV",
      fase_0_lambda: {
        lambda_local: Number(lambdaLocal.toFixed(3)),
        lambda_visitante: Number(lambdaVisita.toFixed(3)),
        volatility,
        is_bonus
      },
      db_debug: {
        http_status: dbResponseStatus,
        error: dbErrorDetails
      },
      probabilidades_1x2: {
        local_porcentaje: pLocalPct,
        empate_porcentaje: pDrawPct,
        visita_porcentaje: pAwayPct
      },
      top_marcadores_por_ev: topScores
    });

  } catch (error) {
    return res.status(500).json({
      status: "error",
      message: "Fallo general en la ejecución del motor 6.1",
      details: error.message
    });
  }
};
