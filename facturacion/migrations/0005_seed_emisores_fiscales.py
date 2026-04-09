"""
Data migration: crea los 2 emisores fiscales iniciales (Elian + Ruby) y
asigna todas las SolicitudFactura existentes al emisor de Eventos (Elian),
ya que el flujo histórico del ERP solo cubría eventos hasta este punto.

También crea las UnidadNegocio 'EVENTOS' y 'AIRBNB' si no existen.
"""
from django.db import migrations


# Datos fijos de los emisores — confirmados por el usuario
EMISORES_SEED = [
    {
        "nombre_interno": "Eventos Elian",
        "rfc": "PECE010202IA0",
        "razon_social": "ELIAN DE JESUS PEREIRA CEH",
        "regimen_fiscal": "626",  # RESICO Personas Físicas
        "codigo_postal": "97238",
        "serie_folio": "A",
        "unidad_clave": "EVENTOS",
        "unidad_nombre": "Eventos Quinta Ko'ox Tanil",
    },
    {
        "nombre_interno": "Airbnb Ruby",
        "rfc": "CERU580518QZ5",
        "razon_social": "RUBY ELISABETH CEH",
        "regimen_fiscal": "625",  # Plataformas Tecnológicas
        "codigo_postal": "97238",
        "serie_folio": "H",  # H de hospedaje
        "unidad_clave": "AIRBNB",
        "unidad_nombre": "Airbnb Hospedaje",
    },
]


def seed_emisores(apps, schema_editor):
    UnidadNegocio = apps.get_model("contabilidad", "UnidadNegocio")
    EmisorFiscal = apps.get_model("facturacion", "EmisorFiscal")
    SolicitudFactura = apps.get_model("facturacion", "SolicitudFactura")

    emisor_eventos = None

    for data in EMISORES_SEED:
        unidad, _ = UnidadNegocio.objects.get_or_create(
            clave=data["unidad_clave"],
            defaults={
                "nombre": data["unidad_nombre"],
                "regimen_fiscal": data["regimen_fiscal"],
                "rfc": data["rfc"],
                "razon_social": data["razon_social"],
                "activa": True,
            },
        )

        emisor, created = EmisorFiscal.objects.get_or_create(
            rfc=data["rfc"],
            defaults={
                "nombre_interno": data["nombre_interno"],
                "razon_social": data["razon_social"],
                "regimen_fiscal": data["regimen_fiscal"],
                "codigo_postal": data["codigo_postal"],
                "serie_folio": data["serie_folio"],
                "unidad_negocio": unidad,
                "activo": True,
            },
        )

        if data["unidad_clave"] == "EVENTOS":
            emisor_eventos = emisor

    # Asignar todas las solicitudes existentes al emisor de Eventos
    # (el flujo histórico solo cubría pagos de eventos)
    if emisor_eventos is not None:
        SolicitudFactura.objects.filter(emisor__isnull=True).update(
            emisor=emisor_eventos
        )


def unseed_emisores(apps, schema_editor):
    """
    Reversa: quita la asignación de emisores en las solicitudes y borra
    los 2 emisores seed. NO borra las UnidadNegocio porque pueden estar
    referenciadas por otras entidades.
    """
    EmisorFiscal = apps.get_model("facturacion", "EmisorFiscal")
    SolicitudFactura = apps.get_model("facturacion", "SolicitudFactura")

    rfcs = [d["rfc"] for d in EMISORES_SEED]
    SolicitudFactura.objects.filter(emisor__rfc__in=rfcs).update(emisor=None)
    EmisorFiscal.objects.filter(rfc__in=rfcs).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0004_emisorfiscal_alter_solicitudfactura_options_and_more"),
        ("contabilidad", "0007_rename_contabilida_codigo__a1b2c3_idx_contabilida_codigo__b58027_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_emisores, unseed_emisores),
    ]
