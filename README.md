# Albion Cargo Terminal

Terminal de escritorio para llevar el control de rutas de "black market" en
Albion Online: manifiestos de carga, inversión en órdenes de compra, precios
de venta en Caerleon, precios de esencias por tier y notas de inteligencia
por hub. Genera un reporte en PDF con todo el detalle de cada corrida
(incluyendo ítems cancelados) y el resumen financiero completo.

## Requisitos

- Python 3.10 o superior
- Windows, macOS o Linux (es una app de escritorio, **no corre en
  celular/iPhone/Android** — para eso haría falta reescribirla como app web
  o app nativa, que es un proyecto aparte)

## Instalación

```bash
git clone https://github.com/TU_USUARIO/albion-cargo-terminal.git
cd albion-cargo-terminal
python -m venv .venv

# Activar el entorno virtual
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

## Uso

```bash
python albion_cargo_app.py
```

Al abrir la app por primera vez, escribe un nombre de personaje, elige tu
servidor y una clave — eso crea tu usuario local. La próxima vez que abras la
app, esos mismos datos quedan precargados.

Todos los datos (inventario, esencias, notas) se guardan en un archivo local
`albion_cargo.db` (SQLite) que se crea automáticamente junto al script. Ese
archivo **no se sube a git** (ver `.gitignore`), así que cada persona que
clone el repo empieza con su propia base de datos vacía.

## Generar un ejecutable (.exe / binario) sin necesidad de Python

Si quieres compartir la app con alguien que no tiene Python instalado:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name AlbionCargoTerminal --collect-all customtkinter albion_cargo_app.py
```

El ejecutable queda en la carpeta `dist/`. Este repo también incluye un
workflow de GitHub Actions (`.github/workflows/build-release.yml`) que hace
esto automáticamente para Windows, macOS y Linux cada vez que publicas un
tag `v*` (por ejemplo `v1.0.0`), y sube los tres ejecutables como assets de
un Release — así cualquiera puede descargar el que le sirva sin clonar nada.

## Roadmap

- [ ] Versión web (Flask) para poder usarla desde el navegador en celular
- [ ] Exportar historial completo por usuario, no solo por hub activo

## Licencia

MIT — ver [LICENSE](LICENSE).
