"""
Servicio de integración con Jibble API
=======================================
Basado en la estructura real de la API verificada en producción.

Endpoints verificados:
- Token:  POST https://identity.prod.jibble.io/connect/token
- People: GET  https://workspace.prod.jibble.io/v1/People
- Report: GET  https://time-attendance.prod.jibble.io/v1/TrackedTimeReport
          Params: from, to, groupBy (date|member), personId (opcional)
          Duración: ISO 8601 (PT6H6M52.753408S)
          Fecha id: "16 March 2026"

Variables de entorno: JIBBLE_CLIENT_ID, JIBBLE_CLIENT_SECRET
"""

import re
import logging
import requests
from datetime import datetime, timedelta
from django.conf import settings

logger = logging.getLogger(__name__)

JIBBLE_TOKEN_URL = 'https://identity.prod.jibble.io/connect/token'
JIBBLE_PEOPLE_URL = 'https://workspace.prod.jibble.io/v1/People'
JIBBLE_REPORT_URL = 'https://time-attendance.prod.jibble.io/v1/TrackedTimeReport'


class JibbleAPIError(Exception):
    pass


class JibbleService:

    def __init__(self):
        self.client_id = getattr(settings, 'JIBBLE_CLIENT_ID', '') or ''
        self.client_secret = getattr(settings, 'JIBBLE_CLIENT_SECRET', '') or ''
        self.access_token = None
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json; charset=UTF-8',
        })

    def esta_configurado(self):
        return bool(self.client_id and self.client_secret)

    def autenticar(self):
        if not self.esta_configurado():
            raise JibbleAPIError(
                "Credenciales Jibble no configuradas. "
                "Agrega JIBBLE_CLIENT_ID y JIBBLE_CLIENT_SECRET en Railway."
            )
        try:
            response = requests.post(
                JIBBLE_TOKEN_URL,
                data={
                    'grant_type': 'client_credentials',
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=15,
            )
            if response.status_code != 200:
                raise JibbleAPIError(
                    f"Auth error (HTTP {response.status_code}): {response.text[:200]}"
                )
            data = response.json()
            self.access_token = data.get('access_token')
            if not self.access_token:
                raise JibbleAPIError("Jibble respondió OK pero sin access_token.")
            self._session.headers.update({'Authorization': f'Bearer {self.access_token}'})
            logger.info("Jibble autenticación exitosa.")
            return True
        except requests.exceptions.RequestException as e:
            raise JibbleAPIError(f"Error de conexión: {e}")

    def _verificar_token(self):
        if not self.access_token:
            self.autenticar()

    # ==========================================
    # PEOPLE — obtener mapa {person_id: nombre}
    # ==========================================
    def obtener_personas(self):
        """
        GET /v1/People → lista de miembros.
        Retorna dict {person_id: fullName} solo de miembros activos.
        """
        self._verificar_token()
        try:
            response = self._session.get(JIBBLE_PEOPLE_URL, timeout=15)
            response.raise_for_status()
            data = response.json()
            items = data.get('value', []) if isinstance(data, dict) else data

            mapa = {}
            for p in items:
                pid = p.get('id', '')
                nombre = p.get('fullName') or p.get('preferredName', 'Desconocido')
                status = p.get('status', '').lower()
                role = p.get('role', '').lower()

                # Excluir owners/admins si no son empleados operativos
                # (puedes ajustar este filtro según necesites)
                if status == 'joined' and pid:
                    mapa[pid] = nombre.upper().strip()

            return mapa
        except requests.exceptions.RequestException as e:
            raise JibbleAPIError(f"Error al obtener personas: {e}")

    # ==========================================
    # TIMESHEETS — desglose diario por persona
    # ==========================================
    def obtener_timesheets_semana(self, fecha_inicio, fecha_fin, person_ids=None):
        """
        Obtiene horas por día por empleado.
        
        Estrategia:
        1. GET People → mapa {id: nombre}
        2. Por cada persona, GET TrackedTimeReport?groupBy=date&personId=X
        3. Parsear duración ISO 8601 y fecha textual
        
        Returns:
            dict {
                'personas': {
                    'NOMBRE': {
                        'person_id': 'xxx',
                        'dias': [{fecha, duracion_segundos, entrada, salida}, ...]
                    }
                },
                'fuente': 'TrackedTimeReport'
            }
        """
        self._verificar_token()

        # 1. Obtener personas
        mapa_personas = self.obtener_personas()
        if not mapa_personas:
            raise JibbleAPIError("No se encontraron personas activas en Jibble.")

        # Filtrar si se proporcionaron IDs específicos
        if person_ids:
            mapa_personas = {k: v for k, v in mapa_personas.items() if k in person_ids}

        logger.info(f"Jibble: {len(mapa_personas)} personas activas encontradas.")

        # 2. Por cada persona, obtener desglose diario
        personas = {}

        for person_id, nombre in mapa_personas.items():
            try:
                params = {
                    'from': f'{fecha_inicio}T00:00:00.000Z',
                    'to': f'{fecha_fin}T23:59:59.000Z',
                    'groupBy': 'date',
                    'personId': person_id,
                }

                response = self._session.get(
                    JIBBLE_REPORT_URL,
                    params=params,
                    timeout=20,
                )

                if response.status_code != 200:
                    logger.warning(
                        f"Jibble report para {nombre} ({person_id}): "
                        f"HTTP {response.status_code} - {response.text[:200]}"
                    )
                    continue

                data = response.json()
                items = data.get('value', [])

                if not items:
                    continue

                dias = []
                for item in items:
                    # Parsear fecha: "16 March 2026" → "2026-03-16"
                    fecha_str = self._parsear_fecha_jibble(item.get('id', ''))
                    if not fecha_str:
                        continue

                    # Parsear duración ISO 8601: "PT6H6M52.753408S" → segundos
                    duracion_seg = self._parsear_iso_duration(
                        item.get('trackedTime') or item.get('time', '')
                    )

                    if duracion_seg > 60:  # Ignorar registros < 1 minuto (check-in accidental)
                        dias.append({
                            'fecha': fecha_str,
                            'duracion_segundos': duracion_seg,
                            'entrada': '-',  # La API de report no da hora exacta
                            'salida': '-',
                        })

                if dias:
                    personas[nombre] = {
                        'person_id': person_id,
                        'dias': sorted(dias, key=lambda d: d['fecha']),
                    }

            except Exception as e:
                logger.warning(f"Error procesando {nombre}: {e}")
                continue

        return {'personas': personas, 'fuente': 'TrackedTimeReport'}

    # ==========================================
    # PARSERS
    # ==========================================
    @staticmethod
    def _parsear_iso_duration(valor):
        """
        Parsea duración ISO 8601 a segundos.
        
        Formatos de Jibble:
            PT6H6M52.753408S    → 6h 6m 52s = 22012s
            P1DT11H40M37.534S  → 1d 11h 40m 37s = 128437s
            PT6.830227S         → 0h 0m 6s = 6s
            PT0S                → 0s
        
        Regex: P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?
        """
        if not valor or not isinstance(valor, str):
            return 0

        try:
            pattern = r'P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?'
            match = re.match(pattern, valor)
            if not match:
                return 0

            days = int(match.group(1) or 0)
            hours = int(match.group(2) or 0)
            minutes = int(match.group(3) or 0)
            seconds = float(match.group(4) or 0)

            total = (days * 86400) + (hours * 3600) + (minutes * 60) + int(seconds)
            return total
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _parsear_fecha_jibble(valor):
        """
        Parsea fecha textual de Jibble a formato YYYY-MM-DD.
        
        Formatos:
            "16 March 2026" → "2026-03-16"
            "2026-03-16"    → "2026-03-16" (pass-through)
        """
        if not valor or not isinstance(valor, str):
            return None

        # Si ya está en formato ISO
        if re.match(r'\d{4}-\d{2}-\d{2}', valor):
            return valor[:10]

        # Parsear formato "16 March 2026"
        try:
            dt = datetime.strptime(valor.strip(), '%d %B %Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

        # Intentar otros formatos
        for fmt in ('%B %d, %Y', '%d %b %Y', '%b %d, %Y'):
            try:
                dt = datetime.strptime(valor.strip(), fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue

        logger.warning(f"No se pudo parsear fecha Jibble: '{valor}'")
        return None

    # ==========================================
    # DIAGNÓSTICO
    # ==========================================
    def diagnostico(self):
        resultado = {
            'configurado': self.esta_configurado(),
            'autenticado': False,
            'personas': {},
            'errores': [],
        }

        if not resultado['configurado']:
            resultado['errores'].append('JIBBLE_CLIENT_ID y/o JIBBLE_CLIENT_SECRET no configurados.')
            return resultado

        try:
            self.autenticar()
            resultado['autenticado'] = True
        except JibbleAPIError as e:
            resultado['errores'].append(f'Auth: {e}')
            return resultado

        try:
            personas = self.obtener_personas()
            resultado['personas'] = personas
        except JibbleAPIError as e:
            resultado['errores'].append(f'People: {e}')

        return resultado