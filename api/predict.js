import { createClient } from '@supabase/supabase-js';

function getSupabase() {
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseUrl || !supabaseKey) {
    throw new Error('Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en Vercel');
  }

  return createClient(supabaseUrl, supabaseKey);
}

function runMonteCarlo(lambdaHome, lambdaAway, sims = 10000) {
  // Poisson simple + Monte Carlo
  const samplePoisson = (lambda) => {
    const L = Math.exp(-lambda);
    let k = 0;
    let p = 1;
    do {
      k += 1;
      p *= Math.random();
    } while (p > L);
    return k - 1;
  };

  let homeWins = 0;
  let draws = 0;
  let awayWins = 0;
  let btts = 0;
  let over25 = 0;
  let homeGoals = 0;
  let awayGoals = 0;

  for (let i = 0; i < sims; i++) {
    const hg = samplePoisson(lambdaHome);
    const ag = samplePoisson(lambdaAway);
    homeGoals += hg;
    awayGoals += ag;

    if (hg > ag) homeWins += 1;
    else if (hg === ag) draws += 1;
    else awayWins += 1;

    if (hg > 0 && ag > 0) btts += 1;
    if (hg + ag >= 3) over25 += 1;
  }

  return {
    simulations: sims,
    prob_home: +(homeWins / sims).toFixed(4),
    prob_draw: +(draws / sims).toFixed(4),
    prob_away: +(awayWins / sims).toFixed(4),
    prob_btts: +(btts / sims).toFixed(4),
    prob_over_2_5: +(over25 / sims).toFixed(4),
    avg_home_goals: +(homeGoals / sims).toFixed(3),
    avg_away_goals: +(awayGoals / sims).toFixed(3),
  };
}

export default async function handler(req, res) {
  try {
    // CORS básico
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    if (req.method === 'OPTIONS') return res.status(200).end();

    const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
    const query = req.query || {};

    // Acepta POST (pipeline) y GET (manual)
    const fixtureId = body.fixture_id || query.fixture_id || null;
    let homeTeam = body.home_team || query.home_team || null;
    let awayTeam = body.away_team || query.away_team || null;
    let lambdaHome = body.lambda_home ?? query.lambda_home;
    let lambdaAway = body.lambda_away ?? query.lambda_away;
    let league = body.league || query.league || null;
    let volatility = body.volatility || query.volatility || null;

    const supabase = getSupabase();

    // Si viene fixture_id, intenta completar datos desde Supabase
    if (fixtureId) {
      const { data: match, error } = await supabase
        .from('matches')
        .select('*')
        .eq('fixture_id', String(fixtureId))
        .maybeSingle();

      if (error) {
        return res.status(500).json({ error: 'Error consultando Supabase', details: error.message });
      }

      if (match) {
        homeTeam = homeTeam || match.home_team;
        awayTeam = awayTeam || match.away_team;
        league = league || match.league;
        volatility = volatility || match.volatility;
        lambdaHome = lambdaHome ?? match.lambda_home;
        lambdaAway = lambdaAway ?? match.lambda_away;
      }
    }

    lambdaHome = Number(lambdaHome);
    lambdaAway = Number(lambdaAway);

    if (!Number.isFinite(lambdaHome) || !Number.isFinite(lambdaAway)) {
      return res.status(400).json({
        error: 'Faltan lambda_home/lambda_away válidos (o fixture_id con esos campos en BD)',
        received: { fixtureId, homeTeam, awayTeam, lambdaHome, lambdaAway }
      });
    }

    const mc = runMonteCarlo(lambdaHome, lambdaAway, 10000);

    // Guarda resultado de predicción (best effort)
    try {
      if (fixtureId) {
        await supabase.from('matches').upsert({
          fixture_id: String(fixtureId),
          home_team: homeTeam,
          away_team: awayTeam,
          league,
          volatility,
          lambda_home: lambdaHome,
          lambda_away: lambdaAway,
          status: 'quant_predicted',
          prediction: mc,
          updated_at: new Date().toISOString()
        }, { onConflict: 'fixture_id' });
      }
    } catch (e) {
      console.error('No se pudo persistir prediction:', e.message);
    }

    return res.status(200).json({
      status: 'ok',
      fixture_id: fixtureId,
      home_team: homeTeam,
      away_team: awayTeam,
      league,
      volatility,
      lambda_home: lambdaHome,
      lambda_away: lambdaAway,
      prediction: mc
    });
  } catch (err) {
    console.error('Error en /api/predict:', err);
    return res.status(500).json({
      error: 'Error interno del servidor',
      message: err.message
    });
  }
}
