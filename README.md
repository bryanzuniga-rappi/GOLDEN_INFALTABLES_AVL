Supply Command Center — Streamlit

Migración del dashboard de Apps Script a una aplicación de Streamlit. Conserva la lógica original de disponibilidad, quiebres, críticos, rankings, mezcla de estatus, AVL por tienda y visibilidad de inventario en CEDIS 444, 831, 811 y 834.

Estructura

streamlit_supply_pro/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    ├── config.toml
    └── secrets.toml.example

Configuración rápida

Sube todos estos archivos a tu repositorio de GitHub, conservando la carpeta .streamlit.

Confirma que el Google Sheet tenga acceso Cualquier persona con el enlace → Lector.

En Streamlit Community Cloud, crea una app desde el repositorio y usa app.py como archivo de entrada.

En Advanced settings → Secrets, agrega:

SHEET_URL = "https://docs.google.com/spreadsheets/d/TU_ID/edit"
SHEET_NAME = "BASE"
HEADER_ROW = 2
MAX_DATA_ROWS = 15000

Aunque el Sheet sea público, configurar la URL de esta forma permite cambiar el origen sin modificar ni volver a publicar el código. Streamlit documenta el flujo de despliegue y la carga de Secrets en su guía oficial de Community Cloud.

Ejecución local

Requiere Python 3.11 o superior.

python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run app.py

Antes de iniciar, reemplaza REEMPLAZA_CON_TU_ID dentro de .streamlit/secrets.toml.

También puedes definir SHEET_URL, SHEET_NAME, HEADER_ROW y MAX_DATA_ROWS como variables de entorno. Si no existe SHEET_URL, la app muestra un campo temporal para pegar la URL al abrirla.

Supuestos heredados del dashboard original

Pestaña de datos: BASE.

Encabezados: fila 2.

Máximo de lectura: 15,000 filas.

Quiebre físico: STOCK TIENDA <= 0 e INCOMING <= 0.

Registro sano: AVL > 0 o el registro no está en quiebre físico.

Crítico: COMMENT contiene CRÍTICO/CRITICO o STATUS ACTUAL contiene LINKS.

El detalle muestra como máximo los primeros 100 registros filtrados.

La lectura se mantiene en caché durante 5 minutos. El botón Actualizar datos fuerza una nueva consulta.

Encabezados obligatorios

PRODUCT_ID, PRODUCT_NAME, WAREHOUSE_NAME, CITY, IGA, STOCK TIENDA, INCOMING, AVL y STATUS ACTUAL.

COMMENT, 444, 831, 811 y 834 son opcionales; si no existen, el dashboard los interpreta como vacíos o con stock 0.
