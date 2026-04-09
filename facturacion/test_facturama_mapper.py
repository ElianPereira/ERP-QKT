"""
Tests del mapper SolicitudFactura → payload Facturama.

No golpea la red: prueba la lógica pura de transformación.
"""
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from facturacion.services import facturama_mapper
from facturacion.services.facturama_mapper import (
    solicitud_a_payload_facturama,
    _aplica_retencion_isr_resico,
    _es_persona_moral,
    _product_code_para_emisor,
    PRODUCT_CODE_EVENTOS,
    PRODUCT_CODE_HOSPEDAJE,
    PRODUCT_CODE_DEFAULT,
)


def _make_emisor(
    *,
    rfc="PECE010202IA0",
    regimen_fiscal="626",
    codigo_postal="97238",
    lugar_expedicion="",
    serie_folio="A",
    razon_social="ELIAN DE JESUS PEREIRA CEH",
    nombre_interno="Eventos Elian",
    activo=True,
    unidad_clave="EVENTOS",
):
    unidad = SimpleNamespace(clave=unidad_clave) if unidad_clave else None
    return SimpleNamespace(
        rfc=rfc,
        regimen_fiscal=regimen_fiscal,
        codigo_postal=codigo_postal,
        lugar_expedicion=lugar_expedicion,
        lugar_expedicion_efectivo=lugar_expedicion or codigo_postal,
        serie_folio=serie_folio,
        razon_social=razon_social,
        nombre_interno=nombre_interno,
        activo=activo,
        unidad_negocio=unidad,
    )


_SENTINEL = object()


def _make_solicitud(
    *,
    pk=42,
    emisor=_SENTINEL,
    rfc="CACX7605101P8",  # 13 chars = Persona Física
    razon_social="XOCHITL CASAS HERNANDEZ",
    codigo_postal="06600",
    regimen_fiscal="612",
    uso_cfdi="G03",
    forma_pago="03",
    metodo_pago="PUE",
    subtotal=Decimal("1000.00"),
    iva=Decimal("160.00"),
    retencion_isr=Decimal("0.00"),
    retencion_iva=Decimal("0.00"),
    concepto="Servicio de evento",
):
    if emisor is _SENTINEL:
        emisor = _make_emisor()
    return SimpleNamespace(
        pk=pk,
        emisor=emisor,
        rfc=rfc,
        razon_social=razon_social,
        codigo_postal=codigo_postal,
        regimen_fiscal=regimen_fiscal,
        uso_cfdi=uso_cfdi,
        forma_pago=forma_pago,
        metodo_pago=metodo_pago,
        subtotal=subtotal,
        iva=iva,
        retencion_isr=retencion_isr,
        retencion_iva=retencion_iva,
        concepto=concepto,
    )


class PersonaMoralDetectionTests(SimpleTestCase):
    def test_rfc_pm_tiene_12_chars(self):
        self.assertTrue(_es_persona_moral("ABC123456XYZ"))  # 12

    def test_rfc_pf_tiene_13_chars(self):
        self.assertFalse(_es_persona_moral("ABCD123456XYZ"))  # 13

    def test_rfc_vacio_no_es_pm(self):
        self.assertFalse(_es_persona_moral(""))
        self.assertFalse(_es_persona_moral(None))


class RetencionISRRuleTests(SimpleTestCase):
    def test_resico_pf_a_persona_moral_aplica(self):
        self.assertTrue(
            _aplica_retencion_isr_resico("626", "ABC123456XYZ")
        )

    def test_resico_pf_a_persona_fisica_no_aplica(self):
        self.assertFalse(
            _aplica_retencion_isr_resico("626", "ABCD123456XYZ")
        )

    def test_resico_pf_a_publico_en_general_no_aplica(self):
        self.assertFalse(
            _aplica_retencion_isr_resico("626", "XAXX010101000")
        )

    def test_emisor_no_resico_no_aplica(self):
        self.assertFalse(
            _aplica_retencion_isr_resico("625", "ABC123456XYZ")
        )
        self.assertFalse(
            _aplica_retencion_isr_resico("612", "ABC123456XYZ")
        )


class ProductCodeTests(SimpleTestCase):
    def test_eventos(self):
        emisor = _make_emisor(unidad_clave="EVENTOS")
        self.assertEqual(_product_code_para_emisor(emisor), PRODUCT_CODE_EVENTOS)

    def test_airbnb(self):
        emisor = _make_emisor(unidad_clave="AIRBNB")
        self.assertEqual(_product_code_para_emisor(emisor), PRODUCT_CODE_HOSPEDAJE)

    def test_sin_unidad(self):
        emisor = _make_emisor(unidad_clave=None)
        self.assertEqual(_product_code_para_emisor(emisor), PRODUCT_CODE_DEFAULT)


class MapperPayloadTests(SimpleTestCase):

    def test_payload_minimo_eventos(self):
        sol = _make_solicitud()
        payload = solicitud_a_payload_facturama(sol)

        self.assertEqual(payload["CfdiType"], "I")
        self.assertEqual(payload["PaymentForm"], "03")
        self.assertEqual(payload["PaymentMethod"], "PUE")
        self.assertEqual(payload["Currency"], "MXN")
        self.assertEqual(payload["Serie"], "A")
        self.assertEqual(payload["ExpeditionPlace"], "97238")

        self.assertEqual(payload["Issuer"]["Rfc"], "PECE010202IA0")
        self.assertEqual(payload["Issuer"]["FiscalRegime"], "626")
        self.assertEqual(
            payload["Issuer"]["Name"], "ELIAN DE JESUS PEREIRA CEH"
        )

        self.assertEqual(payload["Receiver"]["Rfc"], "CACX7605101P8")
        self.assertEqual(payload["Receiver"]["CfdiUse"], "G03")
        self.assertEqual(payload["Receiver"]["FiscalRegime"], "612")
        self.assertEqual(payload["Receiver"]["TaxZipCode"], "06600")

        self.assertEqual(len(payload["Items"]), 1)
        item = payload["Items"][0]
        self.assertEqual(item["ProductCode"], PRODUCT_CODE_EVENTOS)
        self.assertEqual(item["UnitCode"], "E48")
        self.assertEqual(item["Subtotal"], 1000.0)
        self.assertEqual(item["UnitPrice"], 1000.0)
        self.assertEqual(item["Quantity"], 1)
        self.assertEqual(item["Total"], 1160.0)  # 1000 + IVA 16%
        self.assertEqual(item["TaxObject"], "02")

        # Solo IVA (receptor es PF → no hay retención ISR)
        self.assertEqual(len(item["Taxes"]), 1)
        iva = item["Taxes"][0]
        self.assertEqual(iva["Name"], "IVA")
        self.assertEqual(iva["Rate"], 0.16)
        self.assertFalse(iva["IsRetention"])
        self.assertEqual(iva["Total"], 160.0)

    def test_retencion_isr_se_aplica_a_persona_moral(self):
        sol = _make_solicitud(
            rfc="ABC123456XYZ",  # 12 chars → PM
            razon_social="MI EMPRESA SA DE CV",
            regimen_fiscal="601",
            retencion_isr=Decimal("12.50"),  # 1.25% de 1000
        )
        payload = solicitud_a_payload_facturama(sol)
        item = payload["Items"][0]

        # 2 impuestos: IVA trasladado + ISR retenido
        self.assertEqual(len(item["Taxes"]), 2)
        isr = [t for t in item["Taxes"] if t["Name"] == "ISR"][0]
        self.assertTrue(isr["IsRetention"])
        self.assertEqual(isr["Rate"], 0.0125)
        self.assertEqual(isr["Total"], 12.5)

    def test_retencion_isr_se_calcula_si_viene_vacia_y_regla_aplica(self):
        sol = _make_solicitud(
            rfc="ABC123456XYZ",
            regimen_fiscal="601",
            retencion_isr=Decimal("0.00"),  # no viene calculada
        )
        payload = solicitud_a_payload_facturama(sol)
        item = payload["Items"][0]
        isr = [t for t in item["Taxes"] if t["Name"] == "ISR"][0]
        self.assertEqual(isr["Total"], 12.5)

    def test_publico_en_general_no_lleva_retencion(self):
        sol = _make_solicitud(
            rfc="XAXX010101000",
            razon_social="PUBLICO EN GENERAL",
            regimen_fiscal="616",
            uso_cfdi="S01",
        )
        payload = solicitud_a_payload_facturama(sol)
        item = payload["Items"][0]
        # Solo IVA, nada de retenciones
        self.assertEqual(len(item["Taxes"]), 1)

    def test_emisor_airbnb_usa_product_code_hospedaje(self):
        emisor = _make_emisor(
            rfc="CERU580518QZ5",
            regimen_fiscal="625",
            razon_social="RUBY ELISABETH CEH",
            nombre_interno="Airbnb Ruby",
            serie_folio="H",
            unidad_clave="AIRBNB",
        )
        sol = _make_solicitud(emisor=emisor)
        payload = solicitud_a_payload_facturama(sol)
        self.assertEqual(payload["Serie"], "H")
        self.assertEqual(payload["Items"][0]["ProductCode"], PRODUCT_CODE_HOSPEDAJE)

    def test_lugar_expedicion_override(self):
        emisor = _make_emisor(
            codigo_postal="97238", lugar_expedicion="97000"
        )
        sol = _make_solicitud(emisor=emisor)
        payload = solicitud_a_payload_facturama(sol)
        self.assertEqual(payload["ExpeditionPlace"], "97000")

    def test_falta_emisor_lanza_value_error(self):
        sol = _make_solicitud(emisor=None)
        with self.assertRaises(ValueError):
            solicitud_a_payload_facturama(sol)

    def test_emisor_inactivo_lanza_value_error(self):
        sol = _make_solicitud(emisor=_make_emisor(activo=False))
        with self.assertRaises(ValueError):
            solicitud_a_payload_facturama(sol)

    def test_subtotal_cero_lanza_value_error(self):
        sol = _make_solicitud(subtotal=Decimal("0"), iva=Decimal("0"))
        with self.assertRaises(ValueError):
            solicitud_a_payload_facturama(sol)

    def test_concepto_vacio_usa_fallback(self):
        sol = _make_solicitud(concepto="")
        payload = solicitud_a_payload_facturama(sol)
        self.assertEqual(payload["Items"][0]["Description"], "Servicio")

    def test_razon_social_se_sanitiza(self):
        sol = _make_solicitud(razon_social="  MI  EMPRESA   SA DE CV  ")
        payload = solicitud_a_payload_facturama(sol)
        self.assertEqual(payload["Receiver"]["Name"], "MI EMPRESA SA DE CV")
