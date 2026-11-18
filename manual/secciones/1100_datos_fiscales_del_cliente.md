---
titulo: Datos fiscales del cliente y solicitudes de factura
capitulo: Facturación
orden: 1100
roles: [VENTAS, ENCARGADO, CONTADOR, DIRECCION]
---
Cada pago que se registra genera **solo** una solicitud de factura que llega al contador. Lo único que hay que cuidar es que los datos fiscales del cliente estén completos **antes** de cobrar.

### Qué se necesita del cliente

- **RFC**
- **Razón social** (tal como aparece en su constancia de situación fiscal)
- **Código postal fiscal**
- **Régimen fiscal**
- **Uso del CFDI** (lo que va a hacer con la factura)

Lo más seguro es pedirle su **constancia de situación fiscal**: ahí vienen todos.

### Capturarlos

1. Entra a **Eventos → Clientes** y abre al cliente.
2. En la pestaña **Datos Fiscales**, marca **¿Datos Fiscales?** y captura los cinco datos.
3. Elige el **Tipo de persona**: física si el RFC tiene 13 caracteres, moral si tiene 12. El sistema avisa con un **Revisar** en la lista si no coinciden.
4. Guarda.

:::correcto
Con RFC y razón social capturados, la factura del siguiente pago sale a su nombre. Sin ellos, sale a *Público en general*.
:::

### Qué pasa con cada pago

| Situación | Qué sale |
|---|---|
| El cliente tiene RFC y razón social | Factura a su nombre, con su régimen y uso |
| No los tiene | Factura a *Público en general* |
| Es un reembolso | Nota de crédito (no una factura de ingreso) |
| Es una condonación | No se genera nada: no es un cobro |

:::nohagas
Registrar un pago sin dar tiempo a que el cliente mande sus datos si él dijo que quiere factura. Un pago ya registrado con *Público en general* no se corrige solo desde su ficha.
:::

:::rol CONTADOR, DIRECCION
### Para el contador

Cada solicitud llega al contador por correo y por WhatsApp, con su PDF y la **línea de negocio** (RFC emisor) bajo la que debe timbrar. Si no la factura, el sistema le manda un recordatorio a los 3, 7 y 14 días. Para el caso de datos que llegan tarde, ver *Qué hacer si el cliente pide factura tarde*.
:::
