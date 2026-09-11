import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!supabaseUrl || !supabaseKey) {
  console.error("Faltan variables de entorno de Supabase");
}

const supabase = createClient(supabaseUrl, supabaseKey);

export default async function handler(req, res) {
  try {
    const { fixture_id } = req.query;

    if (!fixture_id) {
      return res.status(400).json({ 
        error: "Falta el parámetro fixture_id" 
      });
    }

    const { data: match, error } = await supabase
      .from("matches")
      .select("*")
      .eq("fixture_id", fixture_id)
      .eq("status", "cleaned")
      .single();

    if (error || !match) {
      return res.status(404).json({
        error: "Partido no encontrado o aún no está en estado 'cleaned'",
        details: error?.message || null
      });
    }

    // Prompt 6.1 listo para inyectar
    const prompt = `PROMPT ULTRA HIPER MEGA ELITE – VERSIÓN 6.1

Datos ya saneados (Fase 0 completada):
- λ_local = ${match.lambda_home}
- λ_visitante = ${match.lambda_away}
- Liga = ${match.league}
- Volatilidad = ${match.volatility}
- Es BONUS = ${match.is_bonus}
- Impacto de lesiones = ${JSON.stringify(match.injuries_impact || {})}

Ejecuta el motor matemático completo (Poisson Bivariado + Dixon-Coles + Monte Carlo + Expected Points) 
y devuelve ÚNICAMENTE la tabla final en el formato obligatorio.`;

    return res.status(200).json({
      status: "ready",
      fixture_id: match.fixture_id,
      home_team: match.home_team,
      away_team: match.away_team,
      league: match.league,
      volatility: match.volatility,
      is_bonus: match.is_bonus,
      lambda_home: match.lambda_home,
      lambda_away: match.lambda_away,
      prompt: prompt
    });

  } catch (err) {
    console.error("Error en /api/predict:", err);
    return res.status(500).json({ 
      error: "Error interno del servidor",
      message: err.message 
    });
  }
}
