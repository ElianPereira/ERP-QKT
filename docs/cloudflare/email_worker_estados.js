/**
 * Email Worker: estado de cuenta BBVA → ERP (Issue #401).
 *
 * Cómo instalarlo (guía completa en docs/estado_cuenta_correo.md):
 *   1. Cloudflare → Workers & Pages → Create → Worker → pega este archivo y despliega.
 *   2. Worker → Settings → Variables and Secrets → agrega, como *Secret*:
 *        ERP_ESTADOS_CORREO_SECRET  el mismo valor que en Railway (openssl rand -hex 32)
 *        ERP_URL                    https://erp.quintakooxtanil.com
 *        BBVA_REMITENTES            remitentes de BBVA separados por coma, p. ej.
 *                                   estados@bbva.mx,otro@bbva.mx (el que traiga el correo real)
 *      Opcional, solo para depurar el primer correo real: BBVA_AUTENTICACION = no (apaga la
 *      verificación DKIM/DMARC de abajo; vuelve a quitarla en cuanto funcione).
 *   3. Email → Email Routing → Routes → estados@quintakooxtanil.com →
 *      acción «Send to a Worker» → este Worker.
 *
 * Qué hace: si el From (el real del mensaje, no el del sobre de Gmail) no está en
 * BBVA_REMITENTES, o si el correo no pasó DKIM/DMARC alineado con el dominio de ese From,
 * lo descarta (la cabecera From sola se falsifica con facilidad). Si sí, hace POST del correo crudo al ERP firmado
 * con HMAC-SHA256 sobre "<timestamp>.<cuerpo>". El ERP vuelve a validar la firma,
 * el número de cuenta y que el archivo sea un estado de cuenta.
 */
const RUTA = '/api/contabilidad/estados-cuenta/correo/';
const MAX_BYTES = 15 * 1024 * 1024;

// Gmail anota el resultado de su verificación del remitente original en Authentication-Results.
function autenticado(headers, from) {
  const dominio = from.split('@')[1] || '';
  if (!dominio) return false;
  const d = dominio.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const resultados = (headers.get('authentication-results') || '').toLowerCase();
  return new RegExp(`dkim=pass[^;]*header\\.(?:d|i)=[^;\\s]*${d}`).test(resultados)
    || new RegExp(`dmarc=pass[^;]*header\\.from=${d}`).test(resultados);
}

function direccion(texto) {
  const m = /<([^>]+)>/.exec(texto || '');
  return (m ? m[1] : (texto || '')).trim().toLowerCase();
}

async function firmar(secreto, timestamp, cuerpo) {
  const enc = new TextEncoder();
  const clave = await crypto.subtle.importKey(
    'raw', enc.encode(secreto), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const prefijo = enc.encode(`${timestamp}.`);
  const mensaje = new Uint8Array(prefijo.length + cuerpo.length);
  mensaje.set(prefijo, 0);
  mensaje.set(cuerpo, prefijo.length);
  const firma = await crypto.subtle.sign('HMAC', clave, mensaje);
  return [...new Uint8Array(firma)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export default {
  async email(message, env) {
    const permitidos = (env.BBVA_REMITENTES || '').split(',').map((r) => r.trim().toLowerCase()).filter(Boolean);
    // Gmail reenvía con su propio sobre: el remitente de BBVA está en la cabecera From.
    const from = direccion(message.headers.get('from'));
    if (!permitidos.length || !permitidos.includes(from)) {
      return; // no es de BBVA: se descarta sin avisar
    }
    if ((env.BBVA_AUTENTICACION || '').toLowerCase() !== 'no' && !autenticado(message.headers, from)) {
      return; // From de BBVA pero sin DKIM/DMARC válido: probable suplantación
    }
    if (message.rawSize > MAX_BYTES) {
      throw new Error('Correo demasiado grande'); // Cloudflare rebota el mensaje
    }
    const cuerpo = new Uint8Array(await new Response(message.raw).arrayBuffer());
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const respuesta = await fetch(env.ERP_URL.replace(/\/$/, '') + RUTA, {
      method: 'POST',
      headers: {
        'Content-Type': 'message/rfc822',
        'X-QKT-Timestamp': timestamp,
        'X-QKT-Firma': await firmar(env.ERP_ESTADOS_CORREO_SECRET, timestamp, cuerpo),
      },
      body: cuerpo,
    });
    if (!respuesta.ok) {
      // Un error del ERP hace que Cloudflare rebote el correo y quede visible en su registro.
      throw new Error(`El ERP respondió ${respuesta.status}`);
    }
  },
};
