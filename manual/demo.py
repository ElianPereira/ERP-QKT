"""
Datos de demostración del manual (Issue #507). TODO es inventado: nombres,
teléfonos, folios e importes. Las capturas nunca salen de datos reales.

Solo se corre dentro de la base temporal que levanta `generar_capturas_manual`.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone

CLAVE_DEMO = 'demo-manual-no-real'   # contraseña de los usuarios de demostración (base temporal)
USUARIOS = {   # rol -> (usuario, grupo, superusuario)
    'DIRECCION': ('demo_direccion', None, True),
    'ENCARGADO': ('demo_encargado', 'Ventas', False),
    'VENTAS': ('demo_vendedor', 'Vendedor', False),
    'CONTADOR': ('demo_contador', 'Contabilidad', False),
    'TODOS': ('demo_equipo', None, False),
}
NOMBRES = {'DIRECCION': 'Dirección Demo', 'ENCARGADO': 'Encargado Demo', 'VENTAS': 'Vendedora Demo',
           'CONTADOR': 'Contador Demo', 'TODOS': 'Equipo Demo'}


def sembrar_usuarios():
    """Un usuario por rol, con los mismos grupos que usa el equipo real."""
    call_command('crear_grupos_permisos', verbosity=0)
    for rol, (nombre, grupo, superusuario) in USUARIOS.items():
        usuario, _ = User.objects.get_or_create(username=nombre, defaults={
            'first_name': NOMBRES[rol].split()[0], 'last_name': 'Demo',
            'is_staff': True, 'is_superuser': superusuario})
        usuario.set_password(CLAVE_DEMO)
        usuario.save()
        if grupo:
            usuario.groups.add(Group.objects.get_or_create(name=grupo)[0])


def sembrar_datos():
    """Clientes, cotizaciones, pagos, cuenta bancaria, estados de cuenta y correos inventados."""
    from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago
    from contabilidad.models import (
        CorreoEstadoCuenta,
        CuentaBancaria,
        CuentaContable,
        EstadoCuentaBancario,
        UnidadNegocio,
    )

    hoy = timezone.localdate()
    ficticios = [
        ('Cliente de Ejemplo Uno', '9990000001', 'Fiesta de ejemplo', 90, Decimal('18000.00')),
        ('Cliente de Ejemplo Dos', '9990000002', 'Pasadía de ejemplo', 45, Decimal('6000.00')),
        ('Cliente de Ejemplo Tres', '9990000003', 'Evento de prueba', 120, Decimal('25000.00')),
    ]
    for i, (nombre, telefono, evento, dias, precio) in enumerate(ficticios, start=1):
        cliente = Cliente.objects.create(nombre=nombre, tipo_persona='FISICA', telefono=telefono)
        cotizacion = Cotizacion.objects.create(
            cliente=cliente, nombre_evento=evento, fecha_evento=hoy + timedelta(days=dias),
            incluye_refrescos=False)
        ItemCotizacion.objects.create(cotizacion=cotizacion, descripcion='Servicio de ejemplo', cantidad=1,
                                      precio_unitario=precio)
        if i < 3:
            Pago.objects.create(cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
                                monto=(precio / 2).quantize(Decimal('0.01')),
                                metodo='EFECTIVO' if i == 1 else 'TRANSFERENCIA',
                                referencia='' if i == 1 else 'EJEMPLO-0001',
                                fecha_pago=hoy - timedelta(days=3 * i))

    cuenta = CuentaBancaria.objects.create(
        nombre='Banco de ejemplo', banco='BBVA', clabe='000000000000000000',
        cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.01'),
        unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'))
    mes_pasado = (hoy.replace(day=1) - timedelta(days=1))
    estado = EstadoCuentaBancario.objects.create(
        cuenta_bancaria=cuenta, banco='BBVA', periodo_mes=mes_pasado.month, periodo_anio=mes_pasado.year,
        formato='PDF', estado='PROCESADO', origen='CORREO', fecha_corte_real=mes_pasado,
        saldo_inicial_estado=Decimal('10000.00'), saldo_final_estado=Decimal('24000.00'),
        archivo=ContentFile(b'%PDF-1.4 demostracion', name='estado-demo.pdf'))
    for dias, resultado in ((3, 'PROCESADO'), (4, 'ERROR'), (33, 'PROCESADO')):
        CorreoEstadoCuenta.objects.create(
            message_id=f'<demo-{dias}@ejemplo.invalid>', remitente='avisos@banco-ejemplo.invalid',
            asunto='Estado de cuenta de ejemplo', recibido_en=timezone.now() - timedelta(days=dias),
            resultado=resultado, estado_cuenta=estado if resultado == 'PROCESADO' else None,
            detalle='' if resultado == 'PROCESADO' else 'Ejemplo de error de lectura.',
            archivo_eml=ContentFile(b'From: avisos@banco-ejemplo.invalid\n', name='demo.eml'))
