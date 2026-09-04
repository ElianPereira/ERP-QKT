# CLAUDE.md — ERP-QKT

## Contexto del Proyecto
- **Negocio:** Quinta Ko'ox Tanil (QKT), Umán, Yucatán — eventos, pasadía, hospedaje corto (Ka'an, Otoch, Honey Sea House). Unidades de negocio: QUINTA, PASADÍA, AIRBNB.
- **Stack:** Django + PostgreSQL, Railway (`erp.quintakooxtanil.com`, `clientes.quintakooxtanil.com`), Cloudflare Pages (`quintakooxtanil.com`), Cloudinary (en evaluación → DigitalOcean Spaces), Openpay/BBVA, WhatsApp Cloud API.
- **Cuentas bancarias:** BBVA Maestra PYME → QUINTA / BBVA Libretón Básico → AIRBNB (corte día 14).

## Estándares de Código (obligatorio, sin excepción)
- `Decimal` + `ROUND_HALF_UP` en todo cálculo monetario. `float` en ruta de dinero = bug crítico.
- Modelos de auditoría inmutables: soft-deactivation, nunca `DELETE` físico.
- `created_by` / `updated_by` / `created_at` / `updated_at` en toda operación sensible.
- IVA: conversión única sobre el subtotal, nunca por línea (tolerancia SAT PAC ±0.01/concepto).
- Precios visibles al consumidor siempre IVA-incluido (LFPC Art. 7 BIS).

## Reglas de Eficiencia de Tokens
- Prohibido leer la BD completa o volcar archivos gigantes a consola.
- `grep`/`find`/AST antes de abrir archivos completos.
- Revisiones de código: solo `git diff`/`git log --stat` reciente, no el repo entero.

## Zonas Restringidas (nunca editar sin aprobación humana explícita)
- `payments/` — lógica de pagos (Openpay).
- `legal/` — documentos legales, consentimientos, ARCO.
- `accounting/services.py` — cálculo de impuestos y desglose.
- Cualquier migración de schema o dato en producción.

## Flujo de Entrega
- Nunca merge directo a `main`/`master`.
- Todo cambio de código: rama nueva + Pull Request para revisión humana.
- Todo análisis/reporte estratégico: archivo en `/docs/` + Pull Request, nunca implementación directa.

## Watchlist Activa
- [ ] Horario pasadía hardcodeado "10am–7pm" → corregir a "11am–7pm".
- [ ] Precios sin IVA incluido en cualquier vista nueva.
- [ ] Régimen fiscal (RESICO vs. arrendamiento) sin confirmar → no tocar factor de retención 1.1475.
- [ ] Migración Cloudinary → DigitalOcean Spaces (pendiente).
- [ ] Registro PROFECO NOM-174 pendiente.
- [ ] ISH Airbnb sin resolver.
- [ ] Módulo de depósito en garantía ausente.

Ver `docs/agente_instrucciones_erp.md` para el detalle completo de las rutinas de este equipo.
