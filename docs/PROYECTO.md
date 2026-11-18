# Proyecto: Sistema de Inventario

Referencias para retomar el trabajo en cualquier conversación.

- Documentación del proyecto (fuente única): https://claude.ai/artifact/28juXgBFq7uMuewNM81MzZ
- Guía personal de programación: https://claude.ai/artifact/SoMeTjncNrbhwa34Mt6GWo
- Repositorio de código (privado): https://github.com/ianpatrick-pereira/prueba_inventario
- Bosquejo navegable: https://claude.ai/artifact/S3Fcwhg3nAeMKrqvFWud32 (archivo: /home/user/inventario/bosquejo.html)

## Contexto clave (al 18-11-2026)

- La IA programa; el responsable decide, prueba y aprende (≥ 4 h/semana).
- Factura en papel (nombre, código, precio, cantidad) → foto leída con IA → vista previa → confirmar.
- El sistema "aprende" guardando cada asociación código-proveedor → producto confirmada.
- RUMBO (D-20): este sistema es el INVENTARIO OFICIAL; Eleventa queda solo como CAJA.
  Los ingresos se registran solo aquí. De Eleventa solo se leen los tickets cobrados (RF-13).
  Eleventa descuenta su stock solo al cobrar; si bloquea con stock 0: activar venta sin
  existencia o cargar una sola vez un stock alto (RF-24). No es una copia de Eleventa.
- VISIÓN (D-23): un Eleventa propio pero mejor (inventario, ventas y caja), por etapas: primero inventario (fases 1-4), luego ventas y caja. D-20 es transitoria. Orden confirmado: primero inventario.
- sku = código del producto en Eleventa (llave de sincronización).
- Eleventa trabaja con tickets por vehículo; al cobrar descuenta stock; tickets cobrados se buscan
  por fecha y se pueden renombrar (ej. patente). Cientos de tickets. Las salidas se importan por
  ticket cobrado, usando el número de ticket para no duplicar (D-14). RF-26: consumo por patente.
- Confirmado: los tickets se exportan; Eleventa guarda tickets cobrados y no cobrados (solo se
  importan cobrados); hay devoluciones parciales y cancelaciones totales (movimientos inversos);
  nombre del ticket = "marca modelo patente", la patente es el identificador (D-15).
- Tickets por día: varía según clientes (cientos en total); importación diaria sirve igual.
- Eleventa NO importa productos ni inventario -> resuelto con D-20 (opción C).
- Modelo de datos APROBADO (D-17): tablas Categoría, Producto, Código alternativo (barras/fabricante),
  Producto–Proveedor (un producto, varios proveedores, código por proveedor), Movimiento,
  Documento (factura/ticket/conteo, con foto de factura y patente), Proveedor, Pedido (fase 3).
  Usuarios: tabla incluida en Django.
- Pendiente: archivo de ejemplo de la exportación de tickets para diseñar el importador.
- Stack: Python + Django; SQLite en PC local, PostgreSQL en producción (nube, fase 4).
- Diseño genérico: categorías, unidades y servicios configurables (cualquier negocio con inventario).

## Política de IA (D-16)

Reglas completas en /home/user/inventario/POLITICA_AGENTES.md (y en la pestaña "Política de IA").
Al crear el repositorio de código, copiar ese archivo como CLAUDE.md en su raíz.
Resumen: fase 0 sin delegación; desde fase 1, tareas con contrato (T-F<fase>-<n>);
auditor ≠ ejecutor; riesgo ALTO/CRÍTICO requiere aprobación del responsable;
nunca escribir en la base de datos de Eleventa; clave de API solo en variable de entorno.

## Regla de trabajo (D-06)

El usuario no edita la documentación a mano. Cada cambio que pide al proyecto en la conversación lo refleja Claude en todas las pestañas afectadas, en el mismo momento:
- Función o pantalla → Requisitos, Flujos (si cambia el recorrido), Bitácora
- Campo, tabla o relación → Modelo de datos, Bitácora
- Tecnología o forma de trabajo → Arquitectura, Decisiones, Bitácora
- Alcance u objetivo → Visión y alcance, Decisiones, Plan por fases
- Avance de fase → Plan por fases, Requisitos (estado), Bitácora
- Duda nueva o resuelta → Preguntas abiertas → Decisiones
- Riesgo o prueba → Riesgos / Pruebas
- Término técnico → Glosario
- Bosquejo → bosquejo publicado, Requisitos (estado), Bitácora
- Código → Guía personal de programación

Toda modificación suma una fila en Bitácora; los cambios relevantes suben la versión en Inicio → Control de cambios.

- ESTADO (18-11-2026): fase 0 cerrada (D-26). Fase 1 en curso; el programador trabaja en la nube y T-F1-01 se hace antes de T-F1-08 (D-27).
