"""
Servicios del Módulo de Contabilidad
====================================
Lógica de negocio para reportes contables y regularización.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone


class BalanzaComprobacionService:
    """Genera la balanza de comprobación para un período."""

    @classmethod
    def generar(
        cls,
        fecha_inicio: date,
        fecha_fin: date,
        unidad_negocio=None,
        nivel_detalle: int = 3
    ) -> List[Dict]:
        from .models import CuentaContable, MovimientoContable

        cuentas = CuentaContable.objects.filter(
            activa=True,
            nivel__lte=nivel_detalle
        ).order_by('codigo_sat')

        filtros_mov = Q(poliza__estado='APLICADA')
        if unidad_negocio:
            filtros_mov &= Q(poliza__unidad_negocio=unidad_negocio)

        resultado = []

        for cuenta in cuentas:
            saldo_inicial_data = MovimientoContable.objects.filter(
                filtros_mov,
                cuenta=cuenta,
                poliza__fecha__lt=fecha_inicio
            ).aggregate(debe=Sum('debe'), haber=Sum('haber'))

            debe_inicial = saldo_inicial_data['debe'] or Decimal('0.00')
            haber_inicial = saldo_inicial_data['haber'] or Decimal('0.00')

            if cuenta.naturaleza == 'D':
                saldo_inicial = debe_inicial - haber_inicial
            else:
                saldo_inicial = haber_inicial - debe_inicial

            movimientos_periodo = MovimientoContable.objects.filter(
                filtros_mov,
                cuenta=cuenta,
                poliza__fecha__gte=fecha_inicio,
                poliza__fecha__lte=fecha_fin
            ).aggregate(debe=Sum('debe'), haber=Sum('haber'))

            cargos = movimientos_periodo['debe'] or Decimal('0.00')
            abonos = movimientos_periodo['haber'] or Decimal('0.00')

            if cuenta.naturaleza == 'D':
                saldo_final = saldo_inicial + cargos - abonos
            else:
                saldo_final = saldo_inicial - cargos + abonos

            if saldo_inicial != 0 or cargos != 0 or abonos != 0 or saldo_final != 0:
                resultado.append({
                    'codigo': cuenta.codigo_sat,
                    'nombre': cuenta.nombre,
                    'tipo': cuenta.tipo,
                    'naturaleza': cuenta.naturaleza,
                    'nivel': cuenta.nivel,
                    'saldo_inicial_debe': saldo_inicial if cuenta.naturaleza == 'D' and saldo_inicial > 0 else Decimal('0.00'),
                    'saldo_inicial_haber': abs(saldo_inicial) if cuenta.naturaleza == 'A' or saldo_inicial < 0 else Decimal('0.00'),
                    'cargos': cargos,
                    'abonos': abonos,
                    'saldo_final_debe': saldo_final if cuenta.naturaleza == 'D' and saldo_final > 0 else Decimal('0.00'),
                    'saldo_final_haber': abs(saldo_final) if cuenta.naturaleza == 'A' or saldo_final < 0 else Decimal('0.00'),
                })

        return resultado


# ==========================================
# REGULARIZACIÓN: SALDO DE APERTURA
# ==========================================

def aplicar_saldo_apertura(saldo_apertura, usuario=None):
    """
    Genera la póliza de apertura para una cuenta a su fecha de corte,
    comparando el saldo actual calculado en el sistema contra el saldo
    certificado por el contador. La diferencia se registra contra la
    cuenta de ajuste de apertura (AJUSTE_APERTURA), nunca se fuerza
    el saldo del sistema directamente.
    """
    from .models import MovimientoContable, Poliza
    from .signals import get_cuenta, get_unidad_negocio, get_usuario_sistema

    if saldo_apertura.aplicado:
        raise ValueError(f"El saldo de apertura #{saldo_apertura.pk} ya fue aplicado.")

    cuenta_bancaria = saldo_apertura.cuenta_bancaria
    if not cuenta_bancaria.cuenta_contable:
        raise ValueError(f"La cuenta {cuenta_bancaria} no tiene cuenta_contable ligada.")

    cuenta_ajuste = get_cuenta('AJUSTE_APERTURA')
    if not cuenta_ajuste:
        raise ValueError("Falta configurar AJUSTE_APERTURA en ConfiguracionContable.")

    unidad = get_unidad_negocio('QUINTA')
    usuario = usuario or get_usuario_sistema()

    # A la fecha de corte, no a hoy: `saldo_actual` incluye todo lo posterior
    # al corte, así que la póliza salía por un importe que no correspondía a la
    # fecha en la que se asienta y dejaba el saldo del corte sin regularizar.
    saldo_sistema = cuenta_bancaria.saldo_a_fecha(saldo_apertura.fecha_corte)
    diferencia = saldo_apertura.saldo_certificado - saldo_sistema

    with transaction.atomic():
        content_type = ContentType.objects.get_for_model(saldo_apertura)
        poliza = Poliza.objects.create(
            tipo='D',
            folio=Poliza.siguiente_folio('D', saldo_apertura.fecha_corte),
            fecha=saldo_apertura.fecha_corte,
            concepto=f"Apertura: {cuenta_bancaria.nombre} @ {saldo_apertura.fecha_corte}",
            unidad_negocio=unidad,
            estado='APLICADA',
            origen='APERTURA',
            content_type=content_type,
            object_id=saldo_apertura.pk,
            created_by=usuario,
        )

        if diferencia > 0:
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_bancaria.cuenta_contable,
                debe=diferencia, haber=Decimal('0.00'),
                concepto="Ajuste de apertura (saldo real mayor al del sistema)",
            )
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_ajuste,
                debe=Decimal('0.00'), haber=diferencia,
                concepto="Contrapartida ajuste de apertura",
            )
        elif diferencia < 0:
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_ajuste,
                debe=abs(diferencia), haber=Decimal('0.00'),
                concepto="Contrapartida ajuste de apertura",
            )
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_bancaria.cuenta_contable,
                debe=Decimal('0.00'), haber=abs(diferencia),
                concepto="Ajuste de apertura (saldo real menor al del sistema)",
            )
        # Si diferencia == 0, no se generan movimientos, pero la póliza
        # queda como constancia de que la cuenta cuadró en el corte.

        saldo_apertura.aplicado = True
        saldo_apertura.poliza = poliza
        saldo_apertura.save(update_fields=['aplicado', 'poliza'])

    return poliza


# ==========================================
# REGULARIZACIÓN: DIFERENCIA ARRASTRADA
# ==========================================

def proponer_regularizacion_arrastre(conciliacion, usuario):
    """
    Propone —en BORRADOR— la póliza que cancela la diferencia arrastrada de una
    conciliación, para que Dirección la autorice.

    La diferencia arrastrada es lo que ya no cuadraba entre libros y banco
    ANTES del primer movimiento del estado de cuenta: pólizas anteriores al
    primer estado de cuenta cargado, o asientos sobre la cuenta de bancos que
    nunca pasaron por ese banco. No se origina en el periodo conciliado y no se
    arregla tocándolo.

    La póliza se fecha el **día anterior al primer movimiento del estado de
    cuenta**, y eso no es negociable para que el mecanismo funcione: la
    diferencia arrastrada se mide como `saldo_libros(inicio − 1 día) −
    saldo_inicial_estado`, así que solo un asiento con esa fecha o anterior la
    cancela. Fecharla dentro del periodo la dejaría intacta y además descuadraría
    el periodo por el mismo importe.

    El importe no se inventa: es la distancia contra el saldo inicial que
    imprime el propio banco en el estado de cuenta, que es el documento que
    certifica cuál era el saldo real. La contrapartida va a AJUSTE_APERTURA
    (resultados de ejercicios anteriores), nunca se fuerza el saldo de bancos
    contra nada.

    Es idempotente: si ya hay una propuesta en BORRADOR para esta conciliación,
    la reescribe con las cifras actuales en vez de acumular duplicados.
    """
    from .models import ConciliacionBancaria, MovimientoContable, Poliza
    from .signals import get_cuenta, get_unidad_negocio

    if abs(conciliacion.diferencia_arrastrada) < Decimal('0.01'):
        raise ValueError(
            f"La conciliación {conciliacion} no tiene diferencia arrastrada: "
            "no hay nada que regularizar."
        )
    if not conciliacion.fecha_inicio_periodo:
        raise ValueError(
            f"La conciliación {conciliacion} no tiene fecha de inicio de periodo — "
            "regenérala desde su estado de cuenta."
        )
    cuenta_bancaria = conciliacion.cuenta_bancaria
    if not cuenta_bancaria.cuenta_contable:
        raise ValueError(f"La cuenta {cuenta_bancaria} no tiene cuenta_contable ligada.")

    cuenta_ajuste = get_cuenta('AJUSTE_APERTURA')
    if not cuenta_ajuste:
        raise ValueError("Falta configurar AJUSTE_APERTURA en ConfiguracionContable.")

    fecha = conciliacion.fecha_inicio_periodo - timedelta(days=1)
    importe = abs(conciliacion.diferencia_arrastrada)
    # Diferencia positiva = los libros traían MÁS dinero del que el banco
    # reconocía, así que hay que bajarlos: abono a bancos.
    libros_sobran = conciliacion.diferencia_arrastrada > 0

    content_type = ContentType.objects.get_for_model(ConciliacionBancaria)
    concepto = (
        f"Regularización de diferencia arrastrada — {cuenta_bancaria.nombre} "
        f"al {fecha:%d/%m/%Y} (conciliación {conciliacion.mes:02d}/{conciliacion.anio})"
    )

    with transaction.atomic():
        poliza = Poliza.objects.filter(
            content_type=content_type,
            object_id=conciliacion.pk,
            origen='APERTURA',
            estado='BORRADOR',
        ).first()

        if poliza:
            poliza.fecha = fecha
            poliza.concepto = concepto
            poliza.save(update_fields=['fecha', 'concepto'])
            poliza.movimientos.all().delete()
        else:
            poliza = Poliza.objects.create(
                tipo='D',
                folio=Poliza.siguiente_folio('D', fecha),
                fecha=fecha,
                concepto=concepto,
                unidad_negocio=cuenta_bancaria.unidad_negocio or get_unidad_negocio('QUINTA'),
                estado='BORRADOR',
                origen='APERTURA',
                content_type=content_type,
                object_id=conciliacion.pk,
                created_by=usuario,
            )

        cuenta_banco = cuenta_bancaria.cuenta_contable
        detalle = "Ajuste al saldo que certifica el estado de cuenta del banco"

        if libros_sobran:
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_ajuste,
                debe=importe, haber=Decimal('0.00'),
                concepto="Contrapartida de regularización de bancos",
            )
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_banco,
                debe=Decimal('0.00'), haber=importe,
                concepto=detalle + " (los libros traían saldo de más)",
            )
        else:
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_banco,
                debe=importe, haber=Decimal('0.00'),
                concepto=detalle + " (los libros traían saldo de menos)",
            )
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_ajuste,
                debe=Decimal('0.00'), haber=importe,
                concepto="Contrapartida de regularización de bancos",
            )

    return poliza


def aprobar_regularizacion_arrastre(poliza, usuario):
    """
    Autoriza una propuesta de regularización: la pasa de BORRADOR a APLICADA
    dejando asentado quién la autorizó y cuándo.

    El control de quién puede hacerlo vive en el admin (es donde está el
    `request.user`); aquí se comprueba lo que sí es invariante del modelo: que
    la póliza sea efectivamente una propuesta de regularización en borrador y
    que cuadre.
    """
    from .models import Poliza

    if not isinstance(poliza, Poliza):
        raise ValueError("Se esperaba una póliza.")
    if not poliza.requiere_autorizacion_direccion:
        raise ValueError(
            f"La póliza {poliza.tipo}-{poliza.folio} no es una regularización de saldos."
        )
    if poliza.estado != 'BORRADOR':
        raise ValueError(
            f"La póliza {poliza.tipo}-{poliza.folio} ya no está en borrador "
            f"(está {poliza.get_estado_display().lower()})."
        )
    poliza.aplicar(usuario)
    return poliza


def generar_compra_retroactiva(poliza):
    """
    Genera el registro Compra que debió existir detrás de una póliza de
    egreso capturada a mano (ej. "FACEBOOK ADS", "SUSCRIPCIÓN RAILWAY") —
    gastos reales sin factura/CFDI que se registraron directo como póliza
    en vez de pasar por Compra, y por eso no aparecen en los reportes/KPIs
    que dependen de Compra.

    No genera una nueva póliza: la Compra se crea con las señales de
    contabilidad desactivadas (para no duplicar el asiento ya existente) y
    luego se re-vincula la póliza recibida a esa Compra vía content_type/
    object_id, dejando origen='COMPRA'. Se marca es_deducible=False porque,
    por definición, estas pólizas no tienen CFDI detrás.

    Lanza ValueError si la póliza no es elegible (no es egreso, ya está
    vinculada a un documento origen, o no tiene movimientos).
    """
    from django.conf import settings
    from django.contrib.contenttypes.models import ContentType

    from comercial.models import Compra

    if poliza.tipo != 'E':
        raise ValueError(f"{poliza} no es una póliza de egreso.")
    if poliza.content_type_id:
        raise ValueError(f"{poliza} ya está vinculada a un documento origen.")

    total = poliza.total_debe
    if total <= 0:
        raise ValueError(f"{poliza} no tiene movimientos.")

    with transaction.atomic():
        signals_habilitados_antes = getattr(settings, 'CONTABILIDAD_SIGNALS_ENABLED', True)
        settings.CONTABILIDAD_SIGNALS_ENABLED = False
        try:
            compra = Compra.objects.create(
                proveedor_nombre=poliza.concepto[:200],
                fecha_emision=poliza.fecha,
                subtotal=total,
                total=total,
                unidad_negocio=poliza.unidad_negocio,
                es_deducible=False,
            )
        finally:
            settings.CONTABILIDAD_SIGNALS_ENABLED = signals_habilitados_antes

        poliza.content_type = ContentType.objects.get_for_model(Compra)
        poliza.object_id = compra.pk
        poliza.origen = 'COMPRA'
        poliza.save(update_fields=['content_type', 'object_id', 'origen'])

    return compra


def completar_poliza_compra(poliza):
    """
    Completa una póliza de Compra que quedó en BORRADOR sin la contrapartida
    de banco (crear_poliza_compra no la crea si a la Compra le faltaba
    unidad_negocio y/o cuenta_pago en el momento de guardarla — ver
    contabilidad.signals.crear_poliza_compra). Se usa después de editar la
    Compra para completar esos datos: agrega el movimiento HABER que falta
    y sincroniza unidad_negocio, dejando la póliza lista para aplicarse
    (no la aplica — eso sigue siendo la acción "Aplicar pólizas" existente).

    Lanza ValueError si la póliza no es elegible: no está en BORRADOR, no
    viene de una Compra, ya está balanceada, o a la Compra le sigue
    faltando la cuenta de pago.
    """
    from comercial.models import Compra

    from .models import MovimientoContable

    if poliza.estado != 'BORRADOR':
        raise ValueError(f"{poliza} no está en BORRADOR.")
    if poliza.origen != 'COMPRA' or not poliza.content_type_id:
        raise ValueError(f"{poliza} no viene de una Compra.")
    if poliza.esta_cuadrada:
        raise ValueError(f"{poliza} ya está balanceada — no le falta nada.")

    try:
        compra = Compra.objects.get(pk=poliza.object_id)
    except Compra.DoesNotExist:
        raise ValueError(f"{poliza} apunta a una Compra que ya no existe.")

    if not compra.cuenta_pago or not compra.cuenta_pago.cuenta_contable:
        raise ValueError(f"La Compra de {poliza} sigue sin cuenta_pago — complétala primero.")

    with transaction.atomic():
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=compra.cuenta_pago.cuenta_contable,
            debe=Decimal('0.00'),
            haber=poliza.total_debe,
            concepto="Pago a proveedor",
            referencia=compra.uuid[:20] if compra.uuid else '',
        )
        if compra.unidad_negocio and compra.unidad_negocio_id != poliza.unidad_negocio_id:
            poliza.unidad_negocio = compra.unidad_negocio
            poliza.save(update_fields=['unidad_negocio'])

    return poliza


# ==========================================
# CIERRE DEL HISTÓRICO CONTABLE
# ==========================================

MOTIVO_CIERRE_HISTORICO = (
    "Cierre de histórico: la contabilidad anterior a {fecha:%d/%m/%Y} la cerró "
    "el contador fuera del ERP. Esta póliza queda fuera de saldos y reportes; "
    "sus movimientos se conservan para auditoría."
)


def cerrar_historico_contable(fecha_corte, usuario, aplicar=False):
    """
    Deja fuera de la contabilidad del ERP todas las pólizas hasta `fecha_corte`,
    para arrancar los libros del sistema en una fecha concreta.

    **Cancela, no borra.** Una póliza CANCELADA conserva sus movimientos y su
    auditoría, pero queda fuera de todo saldo y todo reporte, porque en el ERP
    entero solo suma `estado='APLICADA'`. Borrarlas sí sería destructivo: los
    movimientos desaparecerían y las pólizas están ligadas por content_type a
    pagos, compras, recibos de nómina y pagos de Airbnb que sí siguen vivos.

    Para qué existe: cuando los periodos anteriores ya los cerró el contador
    fuera del ERP, las pólizas previas del sistema no son los libros — son
    captura parcial que solo arrastra descuadres a la conciliación bancaria.

    Toca también los BORRADOR anteriores al corte: si se quedaran vivos,
    cualquiera podría aplicarlos después y volver a meter movimiento en un
    periodo ya cerrado.

    Devuelve siempre un informe; solo escribe si `aplicar=True`.
    """
    from .models import CuentaBancaria, Poliza

    if isinstance(fecha_corte, str):
        fecha_corte = date.fromisoformat(fecha_corte)
    if fecha_corte > date.today():
        raise ValueError(
            f"La fecha de corte ({fecha_corte:%d/%m/%Y}) está en el futuro. "
            "Se cerraría contabilidad que todavía no existe."
        )

    afectadas = Poliza.objects.filter(
        fecha__lte=fecha_corte,
        estado__in=('APLICADA', 'BORRADOR'),
    )

    por_periodo = {}
    por_origen = {}
    total = 0
    for poliza in afectadas.only('fecha', 'origen'):
        total += 1
        clave = f"{poliza.fecha.year}-{poliza.fecha.month:02d}"
        por_periodo[clave] = por_periodo.get(clave, 0) + 1
        etiqueta = poliza.get_origen_display()
        por_origen[etiqueta] = por_origen.get(etiqueta, 0) + 1

    cuentas = []
    for cuenta in CuentaBancaria.objects.filter(activa=True).select_related('cuenta_contable'):
        cuentas.append({
            'cuenta': cuenta,
            'saldo_antes': cuenta.saldo_a_fecha(fecha_corte),
            # Cancelado el histórico, al corte solo queda el saldo_inicial del
            # alta de la cuenta: es el punto de partida sobre el que hay que
            # capturar el saldo de apertura certificado.
            'saldo_despues': cuenta.saldo_inicial,
        })

    informe = {
        'fecha_corte': fecha_corte,
        'total': total,
        'por_periodo': dict(sorted(por_periodo.items())),
        'por_origen': dict(sorted(por_origen.items(), key=lambda kv: -kv[1])),
        'cuentas': cuentas,
        'aplicado': False,
        'canceladas': 0,
    }

    if not aplicar or not total:
        return informe

    motivo = MOTIVO_CIERRE_HISTORICO.format(fecha=fecha_corte)
    with transaction.atomic():
        informe['canceladas'] = afectadas.update(
            estado='CANCELADA',
            cancelada_por=usuario,
            fecha_cancelacion=timezone.now(),
            motivo_cancelacion=motivo,
        )
    informe['aplicado'] = True
    for fila in informe['cuentas']:
        fila['saldo_despues'] = fila['cuenta'].saldo_a_fecha(fecha_corte)
    return informe
