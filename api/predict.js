const { spawnSync } = require('child_process');
const path = require('path');

module.exports = (req, res) => {
  // Permitir solo peticiones POST
  if (req.method !== 'POST') {
    res.setHeader('Allow', ['POST']);
    return res.status(405).json({ error: `Método ${req.method} no permitido. Utilice POST.` });
  }

  try {
    // Capturar los datos enviados en el cuerpo de la petición (JSON)
    const inputData = JSON.stringify(req.body || {});
    
    // Ruta absoluta hacia el script de Python en la carpeta scripts
    const pythonScriptPath = path.join(process.cwd(), 'scripts', 'extract_and_clean.py');
    
    // Ejecutar el script de Python pasando el JSON por la entrada estándar (stdin)
    const result = spawnSync('python3', [pythonScriptPath], {
      input: inputData,
      encoding: 'utf-8'
    });

    if (result.error) {
      throw new Error(`Error al ejecutar el proceso de Python: ${result.error.message}`);
    }

    if (result.stderr && result.stderr.trim() !== '') {
      console.warn("Advertencia desde Python:", result.stderr);
    }

    // Parsear la respuesta JSON generada por Python
    const parsedOutput = JSON.parse(result.stdout);
    
    return res.status(200).json({
      status: "success",
      timestamp: new Date().toISOString(),
      prediction: parsedOutput
    });

  } catch (error) {
    return res.status(500).json({
      status: "error",
      message: "Fallo en la ejecución del motor cuantitativo",
      details: error.message
    });
  }
};
