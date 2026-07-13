import os
import sqlite3
import time
import textwrap
import shutil
import csv
import re
import json
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox
import customtkinter as ctk
try:
    from PIL import Image, ImageTk
    PIL_DISPONIBLE = True
except ImportError:
    # En algunas distros de Linux (Mint, Ubuntu, Debian) 'Pillow' se instala
    # con apt sin el conector a Tkinter -- ese conector es un paquete aparte
    # (python3-pil.imagetk). Si falta, la app sigue funcionando normal, solo
    # que sin ícono personalizado en la ventana.
    PIL_DISPONIBLE = False
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

DB_NAME = "albion_cargo.db"

# Ícono de la app: se descarga UNA sola vez de esta URL y se guarda en disco
# como caché local (ICON_CACHE_FILE). Las siguientes veces que abras la app,
# usa la copia guardada y no necesita internet.
APP_ICON_URL = "https://i.imgur.com/Hi3Qz9r.png"
ICON_CACHE_FILE = "app_icon_cache.png"

# Listas de candidatos por estilo, en orden de preferencia. Si la PC de otro
# jugador no tiene "Oxanium"/"Rajdhani"/"JetBrains Mono" instaladas, la app
# cae automáticamente en la mejor alternativa disponible en su sistema en
# vez de mostrar la fuente genérica fea de tkinter por defecto.
FONT_DISPLAY_CANDIDATES = ["Oxanium", "Orbitron", "Audiowide", "Segoe UI Semibold", "Ubuntu", "Noto Sans", "DejaVu Sans"]
FONT_BODY_CANDIDATES = ["Rajdhani", "Ubuntu", "Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"]
FONT_MONO_CANDIDATES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Ubuntu Mono", "DejaVu Sans Mono", "Courier New"]

# Colores según el estado de la fila (pedido: proceso=naranja, recibido=verde, cancelado=rojo)
STATUS_COLORS = {
    "✓ Recibido": "#3ddc84",
    "⚙ En Proceso": "#e3a53c",
    "✕ Cancelado": "#ef5350",
}

# Paleta extra "HUD Mercado Negro" (idea nueva: más color e identidad visual).
ACCENT_PURPLE = "#9b5de5"
ACCENT_PINK = "#f15bb5"
ACCENT_GOLD = "#ffd166"
ACCENT_TEAL = "#2ec4b6"
ACCENT_LIME = "#c5f04a"
ACCENT_ICE = "#5aa9e6"
ANIM_COLORS = ["#e3a53c", "#3ddc84", "#48c9dc", "#9b5de5", "#f15bb5", "#ef5350", "#2ec4b6", "#c5f04a"]

# --------------------------------------------------------------------- #
# REQUISITOS DE ENCANTADO: cuántas Runas/Almas/Reliquias pide Albion POR
# CADA nivel de encantamiento (.1, .2, .3), según el tipo de pieza. Es
# información fija del juego (no cambia por ciudad ni por servidor), así
# que se muestra como tabla de referencia + calculadora rápida.
# (nombre de la categoría, costo por nivel, color de acento)
# --------------------------------------------------------------------- #
ENCANTE_COSTOS = [
    ("Pies, Cascos, Armas Secundarias y Capas", 96, ACCENT_TEAL),
    ("Pecho y Bolsa", 192, ACCENT_ICE),
    ("Armas de Una Mano", 288, ACCENT_PURPLE),
    ("Armas de Dos Manos", 384, ACCENT_PINK),
]

# Destinos posibles de la carga
DESTINO_MERCADO = "Mercado Negro (Caerleon)"
DESTINO_HIDEOUT = "Hideout (Uso Personal)"
DESTINOS = [DESTINO_MERCADO, DESTINO_HIDEOUT]

# Orden de columnas navegables con el teclado (igual que celdas de Excel)
TABLE_COLS = ["nombre", "cantidad", "tier", "valor_oc", "precio_mn"]
ESSENCE_COLS = ["r", "a", "re"]

# Ventana de expiración de una Orden de Compra en Albion (pedido: aviso de 24h)
OC_EXPIRA_HORAS = 24

# ------------------------------------------------------------------------ #
# INTEGRACIÓN CON LA API DE PRECIOS (Albion Online Data Project - AODP)
# ------------------------------------------------------------------------ #
# AODP tiene un servidor de datos separado por región. Usamos la misma región
# que el jugador ya eligió en el login, así no hay que configurar nada aparte.
REGION_TO_SERVER = {
    "Albion West (América)": "west",
    "Albion East (Asia)": "east",
    "Albion Europe (Europa)": "europe",
}

# Nombres de ciudad tal como los espera la API (coinciden con los hubs de la app,
# salvo que la API es sensible a mayúsculas exactas).
CIUDADES_API = ["Fort Sterling", "Lymhurst", "Bridgewatch", "Martlock", "Thetford", "Caerleon"]

# --------------------------------------------------------------------- #
# DICCIONARIO DE ÍTEMS: nombre en español (normalizado, sin tildes) -> código
# interno de Albion. Esto es lo que traduce lo que escribís en "Nombre del
# Ítem" al ID que entiende la API. Albion tiene miles de ítems con códigos
# exactos; acá va una lista curada de los más comunes para rutas de
# transporte/Mercado Negro. SI UN ÍTEM NO FUNCIONA, es porque no está en
# esta lista todavía -- podés agregarlo vos mismo siguiendo el patrón de
# abajo (ver la guía "CÓMO AGREGAR MÁS ÍTEMS" en la explicación del chat).
#
# El valor es el código BASE (sin el prefijo "T{tier}_"). La app arma el
# ID final combinando esto con el tier que pusiste en la fila, por ejemplo:
# nombre="Bolsa", tier="T6" -> "T6_BAG"
ITEM_ALIASES = {
    # --- Materiales crudos ---
    "madera": "WOOD",
    "fibra": "FIBER",
    "piedra": "ROCK",
    "mineral": "ORE",
    "mena": "ORE",
    "cuero crudo": "HIDE",
    "piel": "HIDE",
    # --- Materiales refinados ---
    "tabla": "PLANKS",
    "tablones": "PLANKS",
    "tela": "CLOTH",
    "bloque de piedra": "STONEBLOCK",
    "piedra labrada": "STONEBLOCK",
    "lingote": "METALBAR",
    "cuero": "LEATHER",
    # --- Carga / accesorios ---
    "bolsa": "BAG",
    "capa": "CAPEITEM_FW",
    # --- Pociones y comida (genéricas T4+) ---
    "pocion de vida": "POTIONHEAL",
    "pocion de energia": "POTIONENERGY",
    "estofado": "MEAL_SOUP",
    "guiso": "MEAL_SOUP",
    "omelette": "MEAL_OMELETTE",
    # --- Armas comunes (una mano / dos manos, set genérico) ---
    "espada": "MAIN_SWORD",
    "espadon": "2H_CLAYMORE",
    "daga": "MAIN_DAGGER",
    "lanza": "MAIN_SPEAR",
    "hacha": "MAIN_AXE",
    "martillo": "MAIN_HAMMER",
    "arco": "2H_LONGBOW",
    "ballesta": "2H_CROSSBOWLARGE",
    "vara de fuego": "MAIN_FIRESTAFF",
    "vara de frost": "MAIN_FROSTSTAFF",
    "vara de arcano": "MAIN_ARCANESTAFF",
    "vara sagrada": "MAIN_HOLYSTAFF",
    "vara amaldecida": "MAIN_CURSEDSTAFF",
    "vara de naturaleza": "MAIN_NATURESTAFF",
    "guanteletes": "MAIN_KNUCKLES",
    "escudo": "OFF_SHIELD",
}


def pick_available_font(candidates, fallback="TkDefaultFont"):
    try:
        installed = set(tkfont.families())
    except Exception:
        installed = set()
    for name in candidates:
        if name in installed:
            return name
    return fallback


def cargar_icono_app():
    """
    Descarga el ícono de la app desde APP_ICON_URL la primera vez y lo guarda
    como ICON_CACHE_FILE junto a la base de datos. Si ya existe el archivo
    cacheado, lo usa directo (no vuelve a pedir internet). Si algo falla
    (sin conexión, URL caída, Pillow/ImageTk no disponible, etc.) devuelve
    None y la app simplemente arranca sin ícono personalizado -- nunca se
    rompe por esto.
    """
    if not PIL_DISPONIBLE:
        print("PIL/ImageTk no disponible (falta 'python3-pil.imagetk' en Linux) -- se sigue sin icono.")
        return None
    try:
        if not os.path.exists(ICON_CACHE_FILE):
            req = urllib.request.Request(APP_ICON_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = resp.read()
            with open(ICON_CACHE_FILE, "wb") as f:
                f.write(data)
        return Image.open(ICON_CACHE_FILE)
    except Exception as ex:
        print(f"No se pudo cargar el icono de la app (se sigue sin icono): {ex}")
        return None


def normalizar_texto(texto):
    """
    Pasa un texto a minúsculas y le saca los acentos, para poder comparar
    "Poción" con "pocion" sin que la tilde arruine el match contra
    ITEM_ALIASES. Ej: "Espadón" -> "espadon".
    """
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto


def resolver_item_id(nombre, tier_texto):
    """
    Intenta traducir el nombre libre que el jugador escribió en la tabla
    (ej. "Bolsa", tier "T6") al código interno que usa la API de Albion
    (ej. "T6_BAG"). Devuelve None si el nombre no está en ITEM_ALIASES o si
    no se pudo leer el número de tier -- en ese caso simplemente no se
    puede consultar precio para esa fila (no rompe nada, solo se salta).
    """
    nombre_norm = normalizar_texto(nombre)
    if nombre_norm not in ITEM_ALIASES:
        return None

    match_tier = re.search(r"(\d+)", str(tier_texto))
    if not match_tier:
        return None
    tier_num = int(match_tier.group(1))
    if tier_num < 1 or tier_num > 8:
        return None

    codigo_base = ITEM_ALIASES[nombre_norm]

    # Si el campo tier trae un enchant tipo "T6.1" o "T6@1", lo agregamos con
    # el formato "@N" que usa la API SOLO para equipo (armas/armaduras).
    # Los materiales (madera, tela, etc.) no usan ese sufijo.
    match_encant = re.search(r"[.@](\d)", str(tier_texto))
    es_material = codigo_base in (
        "WOOD", "FIBER", "ROCK", "ORE", "HIDE", "PLANKS", "CLOTH",
        "STONEBLOCK", "METALBAR", "LEATHER", "BAG",
    )
    if match_encant and not es_material:
        return f"T{tier_num}_{codigo_base}@{match_encant.group(1)}"
    return f"T{tier_num}_{codigo_base}"


def consultar_precios_api(item_ids, ciudad, servidor, timeout=8):
    """
    Llama al endpoint de precios de Albion Online Data Project para una lista
    de IDs de ítem en una ciudad puntual. Devuelve una lista de dicts (uno
    por resultado) o lanza una excepción si falla la conexión -- el que la
    llama se encarga de mostrar el error al usuario, esta función no atrapa
    nada para que el error real llegue completo.
    """
    if not item_ids:
        return []
    ids_str = ",".join(item_ids)
    ids_encoded = urllib.parse.quote(ids_str)
    ciudad_encoded = urllib.parse.quote(ciudad)
    url = (
        f"https://{servidor}.albion-online-data.com/api/v2/stats/prices/"
        f"{ids_encoded}.json?locations={ciudad_encoded}&qualities=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # --- Tabla usuarios ---
    # El nombre de personaje ahora es único POR REGIÓN (pedido #11): dos jugadores
    # en regiones distintas pueden compartir nombre, pero dentro de la misma región no.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            region TEXT NOT NULL,
            password TEXT NOT NULL,
            premium INTEGER DEFAULT 0,
            UNIQUE(username, region)
        )
    ''')
    # Migración automática: si la base de datos viene de una versión anterior
    # (username único global, sin columna "premium"), la recreamos preservando
    # todos los datos existentes para que nada se pierda.
    cursor.execute("PRAGMA table_info(usuarios)")
    cols_usuarios = [c[1] for c in cursor.fetchall()]
    if "premium" not in cols_usuarios:
        cursor.execute("ALTER TABLE usuarios RENAME TO usuarios_old")
        cursor.execute('''
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                region TEXT NOT NULL,
                password TEXT NOT NULL,
                premium INTEGER DEFAULT 0,
                UNIQUE(username, region)
            )
        ''')
        cursor.execute('''
            INSERT INTO usuarios (id, username, region, password, premium)
            SELECT id, username, region, password, 0 FROM usuarios_old
        ''')
        cursor.execute("DROP TABLE usuarios_old")

    # --- Tabla inventario (sin "calidad": las calidades salen aleatorias al comprar,
    # no aporta nada llevar registro de eso, pedido #8) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            hub TEXT NOT NULL,
            status TEXT NOT NULL,
            nombre TEXT,
            cantidad INTEGER,
            tier TEXT,
            valor_oc REAL,
            precio_mn REAL,
            favorito INTEGER DEFAULT 0,
            oc_timestamp TEXT,
            FOREIGN KEY(user_id) REFERENCES usuarios(id)
        )
    ''')
    cursor.execute("PRAGMA table_info(inventario)")
    cols_inv = [c[1] for c in cursor.fetchall()]
    if "calidad" in cols_inv:
        try:
            cursor.execute("ALTER TABLE inventario DROP COLUMN calidad")
        except sqlite3.OperationalError:
            pass  # SQLite viejo sin soporte DROP COLUMN: queda la columna pero ya no se usa
    # Migraciones aditivas para bases de datos ya existentes (no destructivas)
    if "favorito" not in cols_inv:
        cursor.execute("ALTER TABLE inventario ADD COLUMN favorito INTEGER DEFAULT 0")
    if "oc_timestamp" not in cols_inv:
        cursor.execute("ALTER TABLE inventario ADD COLUMN oc_timestamp TEXT")
    # Idea nueva: "cargas" paralelas dentro del mismo hub (ej. "Carga 1" y
    # "Carga 2" comprando al mismo tiempo en Fort Sterling sin mezclarse).
    if "carga_nombre" not in cols_inv:
        cursor.execute("ALTER TABLE inventario ADD COLUMN carga_nombre TEXT DEFAULT 'Carga 1'")
        cursor.execute("UPDATE inventario SET carga_nombre = 'Carga 1' WHERE carga_nombre IS NULL")

    # Catálogo de nombres de "carga" por hub (permite crear una carga vacía
    # antes de meterle ítems, y saber qué mostrar en el selector).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cargas_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            hub TEXT,
            carga_nombre TEXT,
            UNIQUE(user_id, hub, carga_nombre)
        )
    ''')

    # Snapshot por ítem de cada carga archivada (idea nueva: ranking de
    # ítems más rentables históricamente).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historial_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            historial_id INTEGER,
            user_id INTEGER,
            nombre TEXT,
            cantidad INTEGER,
            valor_oc REAL,
            precio_mn REAL,
            profit_unidad REAL,
            FOREIGN KEY(historial_id) REFERENCES historial_cargas(id)
        )
    ''')

    # Checklist pre-viaje (idea nueva): se guarda marcado/desmarcado por
    # usuario, no por hub, porque son hábitos generales antes de salir.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS checklist (
            user_id INTEGER,
            item_key TEXT,
            checked INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, item_key)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS esencias (
            user_id INTEGER,
            hub TEXT,
            tier INTEGER,
            runas INTEGER DEFAULT 0,
            almas INTEGER DEFAULT 0,
            reliquias INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, hub, tier)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notas (
            user_id INTEGER,
            hub TEXT,
            texto TEXT,
            PRIMARY KEY(user_id, hub)
        )
    ''')
    # Config de ruta/destino por hub: se guarda para que NO se borre nada al
    # cambiar de ciudad, cerrar la app o reiniciarla (pedido #12).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config_hub (
            user_id INTEGER,
            hub TEXT,
            destino TEXT DEFAULT 'Mercado Negro (Caerleon)',
            ruta_tipo TEXT DEFAULT '',
            costo_transporte REAL DEFAULT 0,
            PRIMARY KEY(user_id, hub)
        )
    ''')
    # Config general por usuario (no por hub): guarda cosas que deben
    # persistir entre reinicios de la app sin importar en qué ciudad estés,
    # como la hora de "Inicio Carga/Compra" (idea nueva: solo se reinicia
    # manualmente al exportar PDF o CSV, nunca por cerrar/abrir la app).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config_general (
            user_id INTEGER PRIMARY KEY,
            inicio_carga TEXT
        )
    ''')
    # Historial de cargas cerradas: cada vez que el jugador exporta/archiva una
    # carga queda una foto fija de la rentabilidad para poder graficar en el tiempo.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historial_cargas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            hub TEXT,
            destino TEXT,
            fecha TEXT,
            inversion REAL,
            venta_neta REAL,
            profit_final REAL,
            FOREIGN KEY(user_id) REFERENCES usuarios(id)
        )
    ''')
    conn.commit()
    conn.close()


init_db()


class AlbionCargoApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Albion Online - Black Market Cargo Terminal")
        self.geometry("1420x960")
        self.minsize(1050, 680)

        # Ícono de la app (ventana + barra de tareas). Blindado: si falla,
        # la app sigue funcionando normal, solo que sin ícono personalizado.
        icono_img = cargar_icono_app()
        if icono_img is not None:
            try:
                self._icon_photo = ImageTk.PhotoImage(icono_img)
                self.iconphoto(True, self._icon_photo)
            except Exception as ex:
                print(f"No se pudo aplicar el icono a la ventana: {ex}")

        # Resolvemos las fuentes reales disponibles en ESTE sistema (necesita
        # que la ventana ya exista). Así la app se ve bien en cualquier PC,
        # no solo en la que tiene Oxanium/Rajdhani/JetBrains Mono instaladas.
        self.F_DISPLAY = pick_available_font(FONT_DISPLAY_CANDIDATES)
        self.F_BODY = pick_available_font(FONT_BODY_CANDIDATES)
        self.F_MONO = pick_available_font(FONT_MONO_CANDIDATES)

        self.current_user_id = None
        self.current_username = ""
        self.current_region = ""
        self.current_hub = "Fort Sterling"
        self.current_destino = DESTINO_MERCADO
        self.current_ruta_tipo = ""
        self.current_carga = "Carga 1"
        self.is_premium = False
        self.fase_venta_activa = False
        self.modo_presentacion = False
        self._titulo_real_txt = ""

        self.row_inputs = []
        self.essence_inputs = {}
        self.last_metrics = {
            "inversion": 0.0, "mochila": 0.0, "venta_bruta": 0.0, "impuesto": 0.0,
            "ajuste": 0.0, "venta_neta": 0.0, "profit_est": 0.0, "profit_final": 0.0,
            "fase_venta_activa": False, "destino": DESTINO_MERCADO, "ruta_tipo": "",
            "costo_transporte": 0.0, "premium": False, "tax_rate": 0.08,
        }

        # Click global para desenfocar las cajas de texto (Actúa como Esc)
        self.bind_all("<Button-1>", self.quitar_foco_clic)
        self.bind("<Escape>", lambda e: self.focus_set())

        # Ctrl+A global: selecciona todo el texto de la caja/entrada que
        # tenga el foco en ese momento (pedido nuevo: funciona en cualquier
        # campo de texto de toda la app, no hay que bindearlo campo por campo).
        self.bind_all("<Control-a>", self.global_select_all)
        self.bind_all("<Control-A>", self.global_select_all)

        self._anim_tick = 0

        self.show_auth_screen()

    def quitar_foco_clic(self, event):
        # Soltar foco solo si hacemos clic fuera de una entrada de texto
        try:
            widget = event.widget
            if not isinstance(widget, (tk.Entry, tk.Text, tk.Listbox)):
                self.focus_set()
        except Exception:
            pass

    def global_select_all(self, event):
        """
        Ctrl+A universal: si el foco está en una caja de texto de una sola
        línea (Entry, incluye las de adentro de un CTkEntry) selecciona todo
        su contenido; si está en un cuadro de texto multilínea (CTkTextbox,
        como las Notas) selecciona todo el texto ahí. No hace nada raro en
        otro tipo de widget (botones, menús, etc.).
        """
        widget = self.focus_get()
        try:
            if isinstance(widget, tk.Text):
                widget.tag_add("sel", "1.0", "end-1c")
                return "break"
            if isinstance(widget, tk.Entry):
                widget.select_range(0, "end")
                widget.icursor("end")
                return "break"
        except Exception:
            pass
        return None

    def bind_mousewheel_recursive(self, widget, scrollable_frame, orient="y"):
        """
        Hace que la rueda del mouse funcione para scrollear aunque el
        puntero esté encima de cualquier widget hijo (filas de ítems,
        labels, botones, etc.) y no solo sobre el borde del contenedor.
        Tkinter no propaga la rueda del mouse hacia arriba automáticamente,
        así que hay que bindearla a mano en cada widget de este árbol.
        """
        canvas = getattr(scrollable_frame, "_parent_canvas", None)
        if canvas is None:
            return

        def _on_wheel(event):
            if orient == "y":
                if getattr(event, "delta", 0):
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                elif getattr(event, "num", None) == 4:
                    canvas.yview_scroll(-1, "units")
                elif getattr(event, "num", None) == 5:
                    canvas.yview_scroll(1, "units")
            else:
                if getattr(event, "delta", 0):
                    canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
                elif getattr(event, "num", None) == 4:
                    canvas.xview_scroll(-1, "units")
                elif getattr(event, "num", None) == 5:
                    canvas.xview_scroll(1, "units")

        try:
            widget.bind("<MouseWheel>", _on_wheel, add="+")
            widget.bind("<Button-4>", _on_wheel, add="+")
            widget.bind("<Button-5>", _on_wheel, add="+")
        except Exception:
            pass

        for child in widget.winfo_children():
            self.bind_mousewheel_recursive(child, scrollable_frame, orient=orient)

    def get_float(self, value):
        # Función a prueba de balas para convertir textos a números sin crashear
        try:
            val = str(value).replace(',', '.')
            if val.strip() == "":
                return 0.0
            return float(val)
        except (ValueError, TypeError, AttributeError):
            return 0.0

    def set_display_text(self, widget, text, color=None):
        """
        Actualiza un CTkEntry de 'solo lectura' (usado para mostrar datos calculados).
        El widget queda en estado 'readonly': se puede seleccionar y copiar su
        contenido con el mouse o Ctrl+C, pero no se puede reescribir ni borrar.
        """
        widget.configure(state="normal")
        widget.delete(0, "end")
        widget.insert(0, text)
        if color:
            widget.configure(text_color=color)
        widget.configure(state="readonly")

    # ------------------------------------------------------------------ #
    # AUTENTICACIÓN
    # ------------------------------------------------------------------ #
    def show_auth_screen(self):
        self.auth_frame = ctk.CTkFrame(self, fg_color="#12151f", border_color="#e3a53c", border_width=2, corner_radius=18, width=460, height=610)
        self.auth_frame.place(relx=0.5, rely=0.5, anchor="center")
        self.auth_frame.pack_propagate(False)

        title_label = ctk.CTkLabel(self.auth_frame, text="TRANSPORTES", font=(self.F_DISPLAY, 28, "bold"), text_color="#e3a53c")
        title_label.pack(pady=(40, 5))
        sub_label = ctk.CTkLabel(self.auth_frame, text="Control de transporte", font=(self.F_BODY, 14), text_color="#97a2bd")
        sub_label.pack(pady=(0, 30))

        lbl_user = ctk.CTkLabel(self.auth_frame, text="Nombre del Personaje:", font=(self.F_BODY, 15, "bold"), text_color="#eef1f8")
        lbl_user.pack(anchor="w", padx=45, pady=(10, 2))
        self.ent_username = ctk.CTkEntry(self.auth_frame, placeholder_text="Ej: XitSsoTox", fg_color="#0b0e17", font=(self.F_BODY, 16), border_color="#2a3142", text_color="#eef1f8")
        self.ent_username.pack(fill="x", padx=45, pady=5)

        lbl_region = ctk.CTkLabel(self.auth_frame, text="Region del servidor", font=(self.F_BODY, 15, "bold"), text_color="#eef1f8")
        lbl_region.pack(anchor="w", padx=45, pady=(10, 2))

        self.sel_region = ctk.CTkOptionMenu(
            self.auth_frame, values=["Albion West (América)", "Albion East (Asia)", "Albion Europe (Europa)"],
            fg_color="#1c2130", button_color="#232838", button_hover_color="#333c52",
            dropdown_fg_color="#12151f", dropdown_hover_color="#e3a53c", dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 16))
        self.sel_region.pack(fill="x", padx=45, pady=5)

        lbl_note_region = ctk.CTkLabel(self.auth_frame, text="El nombre debe ser único dentro de tu región.", font=(self.F_BODY, 11), text_color="#97a2bd")
        lbl_note_region.pack(anchor="w", padx=45, pady=(0, 5))

        lbl_pass = ctk.CTkLabel(self.auth_frame, text="Contraseña:", font=(self.F_BODY, 15, "bold"), text_color="#eef1f8")
        lbl_pass.pack(anchor="w", padx=45, pady=(10, 2))
        self.ent_password = ctk.CTkEntry(self.auth_frame, placeholder_text="••••••••", show="*", fg_color="#0b0e17", font=(self.F_BODY, 16), border_color="#2a3142", text_color="#eef1f8")
        self.ent_password.pack(fill="x", padx=45, pady=5)

        # Carga automática del último usuario registrado
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT username, region FROM usuarios ORDER BY id DESC LIMIT 1")
        last_user = c.fetchone()
        conn.close()
        if last_user:
            self.ent_username.insert(0, last_user[0])
            self.sel_region.set(last_user[1])
            self.ent_password.focus_set()

        self.ent_username.bind("<Return>", lambda e: self.handle_auth())
        self.ent_password.bind("<Return>", lambda e: self.handle_auth())

        btn_login = ctk.CTkButton(self.auth_frame, text="INICIAR SESIÓN / REGISTRAR", font=(self.F_DISPLAY, 14, "bold"), fg_color="#e3a53c", text_color="#12141c", hover_color="#eef1f8", command=self.handle_auth)
        btn_login.pack(fill="x", padx=45, pady=(35, 10))

    def handle_auth(self):
        username = self.ent_username.get().strip()
        region = self.sel_region.get()
        password = self.ent_password.get().strip()

        if not username or not password:
            messagebox.showerror("Error", "¡Campos vacíos!")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        # Único por (username, region): el mismo nombre puede existir en otra región
        # como una cuenta totalmente distinta (pedido #11).
        cursor.execute("SELECT id, password, premium FROM usuarios WHERE username = ? AND region = ?", (username, region))
        row = cursor.fetchone()

        if row:
            user_id, db_password, premium = row
            if db_password == password:
                self.current_user_id = user_id
                self.current_username = username
                self.current_region = region
                self.is_premium = bool(premium)
                self.auth_frame.destroy()
                self.iniciar_hud_seguro()
            else:
                messagebox.showerror("Error", "Clave incorrecta para ese personaje en esta región.")
        else:
            try:
                cursor.execute("INSERT INTO usuarios (username, region, password, premium) VALUES (?, ?, ?, 0)", (username, region, password))
                conn.commit()
            except sqlite3.IntegrityError:
                messagebox.showerror("Error", "Ese nombre ya está en uso en esta región.")
                conn.close()
                return
            self.current_user_id = cursor.lastrowid
            self.current_username = username
            self.current_region = region
            self.is_premium = False
            self.auth_frame.destroy()
            self.iniciar_hud_seguro()
        conn.close()

    def iniciar_hud_seguro(self):
        """
        Envuelve show_main_hud() en un try/except: si algo falla al armar la
        pantalla principal, en vez de quedar la ventana "pegada" en silencio,
        te muestra el error exacto en un popup para poder diagnosticarlo.
        """
        try:
            self.show_main_hud()
        except Exception as ex:
            import traceback
            detalle = traceback.format_exc()
            print(detalle)
            messagebox.showerror(
                "Error al abrir el panel principal",
                f"Ocurrió un error construyendo la pantalla:\n\n{ex}\n\n"
                "Mirá la consola/terminal para el detalle completo."
            )

    # ------------------------------------------------------------------ #
    # HUD PRINCIPAL
    # ------------------------------------------------------------------ #
    def show_main_hud(self):
        # Header Principal
        self.header_frame = ctk.CTkFrame(self, fg_color="#161a26", border_color="#e3a53c", border_width=1, corner_radius=14, height=90)
        self.header_frame.pack(fill="x", padx=25, pady=(20, 0))
        self.header_frame.pack_propagate(False)

        title_txt = f"Nombre: {self.current_username.upper()} • {self.current_region.upper()}"
        self.ent_title = ctk.CTkEntry(self.header_frame, width=340, font=(self.F_DISPLAY, 18, "bold"), text_color="#eef1f8",
                                      fg_color="transparent", border_width=0, justify="left")
        self.ent_title.pack(side="left", padx=25, pady=30)
        self.set_display_text(self.ent_title, title_txt)

        # Indicador "en vivo" parpadeante (idea nueva: le da vida al HUD,
        # como una lucecita de grabación activa junto al reloj).
        self.lbl_live_dot = ctk.CTkLabel(self.header_frame, text="●", font=(self.F_MONO, 14, "bold"), text_color="#3ddc84")
        self.lbl_live_dot.pack(side="left", padx=(10, 0), pady=30)

        self.lbl_live_clock = ctk.CTkLabel(self.header_frame, text="", font=(self.F_MONO, 14, "bold"), text_color="#48c9dc")
        self.lbl_live_clock.pack(side="left", padx=(6, 50), pady=30)
        self.update_live_clock()

        # NOTA: el botón de tema claro/oscuro que había acá se sacó a propósito.
        # Toda la app usa colores de fondo fijos pensados para verse oscura
        # (estética "Mercado Negro"); un modo claro real necesitaría rediseñar
        # cada color de la app por separado. En vez de dejar un botón que rompe
        # la legibilidad, se fuerza el modo oscuro siempre (ver
        # ctk.set_appearance_mode("Dark") al principio del archivo).

        # Botón de backup manual de la base de datos (idea nueva: calidad de vida)
        self.btn_backup = ctk.CTkButton(self.header_frame, text="💾 Backup DB", width=110, fg_color="#1e2536",
                                         hover_color=ACCENT_PURPLE, font=(self.F_BODY, 13, "bold"),
                                         command=self.backup_database)
        self.btn_backup.pack(side="right", padx=5, pady=30)

        # Comparar el profit acumulado de todos tus personajes registrados
        # en esta base de datos (idea nueva: multi-cuenta).
        self.btn_cuentas = ctk.CTkButton(self.header_frame, text="👥 Cuentas", width=110, fg_color="#1e2536",
                                          hover_color=ACCENT_TEAL, font=(self.F_BODY, 13, "bold"),
                                          command=self.comparar_cuentas)
        self.btn_cuentas.pack(side="right", padx=5, pady=30)

        # Modo presentación (idea nueva): oculta el nombre de personaje y
        # bloquea toda la tabla para compartir pantalla sin exponer datos.
        self.btn_presentacion = ctk.CTkButton(self.header_frame, text="🕶 Modo Presentación", width=170, fg_color="#1e2536",
                                               hover_color=ACCENT_PINK, font=(self.F_BODY, 13, "bold"),
                                               command=self.toggle_modo_presentacion)
        self.btn_presentacion.pack(side="right", padx=5, pady=30)

        self.hub_selector = ctk.CTkOptionMenu(
            self.header_frame, values=["Fort Sterling", "Lymhurst", "Bridgewatch", "Martlock", "Thetford", "Caerleon"],
            command=self.change_hub, fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color="#e3a53c", dropdown_text_color="#eef1f8",
            font=(self.F_DISPLAY, 14, "bold"), text_color="#e3a53c", width=170)
        self.hub_selector.set(self.current_hub)
        self.hub_selector.pack(side="right", padx=25, pady=30)

        # Barra animada de "flujo de datos" del Mercado Negro (idea nueva:
        # detalle estético único). Es un Canvas angosto justo debajo del
        # header que hace correr un bloque de color de un lado a otro,
        # cambiando de color: le da un toque HUD/cyberpunk a la app sin
        # afectar ninguna función.
        self.scan_canvas = tk.Canvas(self, height=4, bg="#0d111c", highlightthickness=0, bd=0)
        self.scan_canvas.pack(fill="x", padx=25, pady=(0, 10))
        self._scan_pos = 0
        self._scan_dir = 1
        self._scan_color_idx = 0
        self.animate_scan_bar()

        # --- Barra superior: tiempos, destino, ruta, premium y mochila ---
        self.top_bar = ctk.CTkFrame(self, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        self.top_bar.pack(fill="x", padx=25, pady=10)

        row1 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row1.pack(fill="x")
        row2 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row2.pack(fill="x")
        row3 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row3.pack(fill="x")
        row4 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row4.pack(fill="x")

        lbl_t1 = ctk.CTkLabel(row1, text="Inicio Carga/Compra:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_t1.pack(side="left", padx=(20, 10), pady=15)
        self.ent_start_time = ctk.CTkEntry(row1, placeholder_text="Ej: 2026-07-11 12:00", width=160, fg_color="#0d111c", text_color="#eef1f8")
        # CAMBIO PEDIDO #5: la hora de inicio ahora se carga desde la base de
        # datos (persiste entre reinicios de la app) y solo vuelve a "ahora"
        # cuando exportás un PDF o un CSV (ver reiniciar_inicio_carga()).
        self.ent_start_time.insert(0, self.cargar_inicio_carga())
        self.ent_start_time.pack(side="left", padx=5, pady=15)
        self.ent_start_time.bind("<KeyRelease>", lambda e: (self.guardar_inicio_carga(), self.update_oc_expira_label()))

        lbl_destino = ctk.CTkLabel(row1, text="Destino de la Carga:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_destino.pack(side="left", padx=(30, 10), pady=15)
        self.sel_destino = ctk.CTkOptionMenu(
            row1, values=DESTINOS, command=self.change_destino,
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color="#48c9dc", dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", width=220)
        self.sel_destino.set(self.current_destino)
        self.sel_destino.pack(side="left", padx=5, pady=15)

        self.lbl_ruta = ctk.CTkLabel(row1, text="Punto de Entrada:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        self.sel_ruta = ctk.CTkOptionMenu(
            row1, values=[self.current_hub], command=self.change_ruta,
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color="#48c9dc", dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", width=220)

        # Aviso de expiración de OC (idea nueva: logística/riesgo). Se calcula sobre
        # ent_start_time + 24h, en texto, sin depender de que la app siga abierta.
        self.lbl_oc_expira = ctk.CTkLabel(row1, text="", font=(self.F_MONO, 13, "bold"), text_color="#e3a53c")
        self.lbl_oc_expira.pack(side="left", padx=(20, 10), pady=15)

        # Premium (pedido #5): baja el impuesto de venta del 8% al 4%
        self.switch_premium = ctk.CTkSwitch(
            row2, text="Cuenta Premium (Impuesto Mercado Negro 8% → 4%)",
            font=(self.F_BODY, 13, "bold"), progress_color="#3ddc84",
            command=self.toggle_premium)
        self.switch_premium.pack(side="left", padx=(20, 30), pady=15)

        # Costo de transporte, solo relevante y visible si el destino es Hideout
        self.lbl_costo_transporte = ctk.CTkLabel(row2, text="Costo de Transporte a Hideout:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        self.ent_costo_transporte = ctk.CTkEntry(row2, placeholder_text="0", width=140, fg_color="#0d111c", text_color="#48c9dc", font=(self.F_MONO, 14, "bold"))
        self.ent_costo_transporte.insert(0, "0")
        self.ent_costo_transporte.bind("<KeyRelease>", lambda e: self.save_costo_transporte())

        # Mochila Manual, Global e Independiente
        lbl_mochila_section = ctk.CTkLabel(row2, text="Valor De La Mochila (Inventario):", font=(self.F_DISPLAY, 14, "bold"), text_color="#48c9dc")
        lbl_mochila_section.pack(side="right", padx=(10, 20), pady=15)

        self.ent_mochila_global = ctk.CTkEntry(row2, placeholder_text="0", fg_color="#0d111c", font=(self.F_MONO, 14, "bold"), text_color="#48c9dc", width=180)
        self.ent_mochila_global.insert(0, "0")
        self.ent_mochila_global.pack(side="right", padx=5, pady=15)
        self.ent_mochila_global.bind("<KeyRelease>", lambda e: self.calculate_metrics())

        # Peso de carga vs. capacidad de montura (idea nueva: logística/riesgo)
        lbl_peso = ctk.CTkLabel(row3, text="Peso Carga (kg):", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_peso.pack(side="left", padx=(20, 10), pady=15)
        self.ent_peso_carga = ctk.CTkEntry(row3, placeholder_text="0", width=100, fg_color="#0d111c", text_color="#eef1f8", font=(self.F_MONO, 14, "bold"))
        self.ent_peso_carga.insert(0, "0")
        self.ent_peso_carga.pack(side="left", padx=5, pady=15)
        self.ent_peso_carga.bind("<KeyRelease>", lambda e: self.update_peso_ui())

        lbl_capacidad = ctk.CTkLabel(row3, text="Capacidad Montura (kg):", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_capacidad.pack(side="left", padx=(20, 10), pady=15)
        self.sel_montura = ctk.CTkOptionMenu(
            row3, values=["Mula (700)", "Caballo T5 (409)", "Buey Acorazado T4 (1650)", "Camello (940)", "Personalizado"],
            command=lambda v: self.update_peso_ui(),
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color="#48c9dc", dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", width=210)
        self.sel_montura.pack(side="left", padx=5, pady=15)

        self.ent_capacidad_custom = ctk.CTkEntry(row3, placeholder_text="kg", width=90, fg_color="#0d111c", text_color="#eef1f8", font=(self.F_MONO, 14, "bold"))
        self.ent_capacidad_custom.bind("<KeyRelease>", lambda e: self.update_peso_ui())

        self.lbl_peso_status = ctk.CTkLabel(row3, text="", font=(self.F_MONO, 13, "bold"), text_color="#3ddc84")
        self.lbl_peso_status.pack(side="left", padx=(15, 10), pady=15)

        # Etiqueta de riesgo por ruta (idea nueva: logística/riesgo)
        lbl_riesgo = ctk.CTkLabel(row3, text="Riesgo de Ruta:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_riesgo.pack(side="right", padx=(10, 20), pady=15)
        self.sel_riesgo = ctk.CTkOptionMenu(
            row3, values=["🟢 Zona Segura", "🟡 Zona Amarilla", "🔴 Zona Roja", "⚫ Zona Negra"],
            command=lambda v: self.update_riesgo_ui(),
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color="#ef5350", dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", width=180)
        self.sel_riesgo.pack(side="right", padx=5, pady=15)
        self.lbl_riesgo_mult = ctk.CTkLabel(row3, text="", font=(self.F_MONO, 13, "bold"), text_color="#ef5350")
        self.lbl_riesgo_mult.pack(side="right", padx=(10, 5), pady=15)

        # Alerta de margen mínimo (idea nueva): si el % de ganancia de una
        # fila cae por debajo de este umbral, su columna "Margen %" se pinta
        # de rojo como advertencia -- sin necesitar ninguna API externa,
        # solo comparando lo que VOS ya escribiste en Valor O/C y Precio MN.
        lbl_margen_min = ctk.CTkLabel(row4, text="⚠ Alerta Margen Mínimo (%):", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_margen_min.pack(side="left", padx=(20, 10), pady=15)
        self.ent_margen_minimo = ctk.CTkEntry(row4, placeholder_text="15", width=80, fg_color="#0d111c", text_color=ACCENT_GOLD, font=(self.F_MONO, 14, "bold"))
        self.ent_margen_minimo.insert(0, "15")
        self.ent_margen_minimo.pack(side="left", padx=5, pady=15)
        self.ent_margen_minimo.bind("<KeyRelease>", lambda e: self.calculate_metrics())

        # Checklist pre-viaje (idea nueva): hábitos rápidos antes de salir.
        self.btn_checklist = ctk.CTkButton(row4, text="✅ Checklist Pre-Viaje", fg_color="#1e2536", hover_color=ACCENT_LIME,
                                            text_color="#eef1f8", font=(self.F_BODY, 13, "bold"), width=190,
                                            command=self.abrir_checklist)
        self.btn_checklist.pack(side="left", padx=(20, 10), pady=15)

        # Eficiencia: silver ganado por hora transcurrida desde que arrancó
        # la logística (idea nueva, "bonus" propio): te dice qué tan bien
        # estás aprovechando el tiempo, no solo el profit en bruto.
        self.lbl_eficiencia = ctk.CTkLabel(row4, text="", font=(self.F_MONO, 13, "bold"), text_color=ACCENT_TEAL)
        self.lbl_eficiencia.pack(side="right", padx=(10, 20), pady=15)

        # Cuadros de Contadores Elitizados
        self.counters_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.counters_frame.pack(fill="x", padx=25, pady=(15, 0))

        _, self.card_budget = self.create_counter_card(self.counters_frame, "INVERSIÓN ÓRDENES DE COMPRA (+2.5% Setup)", "#e3a53c")
        _, self.card_bag_value = self.create_counter_card(self.counters_frame, "VALOR DE LA MOCHILA", "#48c9dc")
        _, self.card_status = self.create_counter_card(self.counters_frame, "PROFIT ESTIMADO (MOCHILA - INVERSIÓN)", "#eef1f8")
        self.card_pt_label, self.card_pt = self.create_counter_card(self.counters_frame, "PROFIT FINAL MERCADO NEGRO (-10.5% Imp.)", "#3ddc84")

        self.lbl_desglose = ctk.CTkLabel(self, text="", font=(self.F_BODY, 12), text_color="#97a2bd", anchor="w", justify="left")
        self.lbl_desglose.pack(fill="x", padx=33, pady=(6, 0))

        self.workspace = ctk.CTkFrame(self, fg_color="transparent")
        self.workspace.pack(fill="both", expand=True, padx=25, pady=(10, 25))

        self.left_panel = ctk.CTkFrame(self.workspace, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        self.left_panel.pack(side="left", fill="both", expand=True, padx=(0, 15))

        table_actions = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        table_actions.pack(fill="x", padx=20, pady=15)

        self.lbl_manifest = ctk.CTkEntry(table_actions, width=280, font=(self.F_DISPLAY, 18, "bold"), text_color="#eef1f8",
                                         fg_color="transparent", border_width=0, justify="left")
        self.lbl_manifest.pack(side="left")

        # Selector de "Carga" (idea nueva: cargas paralelas dentro del mismo
        # hub, ej. "Carga 1" comprando para Mercado Negro y "Carga 2" para
        # Hideout al mismo tiempo, sin que los ítems se mezclen en la tabla).
        self.sel_carga = ctk.CTkOptionMenu(
            table_actions, values=["Carga 1"], command=self.change_carga,
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color=ACCENT_PURPLE, dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 13, "bold"), text_color=ACCENT_PURPLE, width=150)
        self.sel_carga.pack(side="left", padx=(15, 5))

        btn_nueva_carga = ctk.CTkButton(table_actions, text="+ Carga", width=70, fg_color="#1e2536",
                                         hover_color=ACCENT_PURPLE, font=(self.F_BODY, 12, "bold"),
                                         command=self.crear_nueva_carga)
        btn_nueva_carga.pack(side="left", padx=(0, 10))

        self.btn_fase = ctk.CTkButton(table_actions, text="✓ LISTO: PASAR A VENTA M/N", fg_color="#e3a53c", text_color="#12141c", font=(self.F_DISPLAY, 12, "bold"), width=210, command=self.activar_fase_venta)
        self.btn_fase.pack(side="right", padx=5)

        self.btn_regresar = ctk.CTkButton(table_actions, text="↩ REGRESAR A FASE COMPRA", fg_color="#ef5350", text_color="#eef1f8", font=(self.F_DISPLAY, 12, "bold"), width=210, command=self.regresar_fase_compra)

        btn_add = ctk.CTkButton(table_actions, text="+ Meter Ítem", fg_color="#48c9dc", text_color="#12141c", font=(self.F_DISPLAY, 13, "bold"), width=110, command=self.add_item_row)
        btn_add.pack(side="right", padx=5)

        btn_pdf = ctk.CTkButton(table_actions, text="Generar PDF", fg_color="#3ddc84", text_color="#12141c", font=(self.F_DISPLAY, 13, "bold"), width=170, command=self.export_to_pdf)
        btn_pdf.pack(side="right", padx=5)

        # Exportar a CSV/Excel (idea nueva: calidad de vida)
        btn_csv = ctk.CTkButton(table_actions, text="Exportar CSV", fg_color="#97a2bd", text_color="#12141c", font=(self.F_DISPLAY, 13, "bold"), width=140, command=self.export_to_csv)
        btn_csv.pack(side="right", padx=5)

        # Exportar una vista de solo lectura para compartir con el gremio
        # (idea nueva): un HTML autocontenido, sin contraseña ni datos de
        # cuenta editables, solo la ruta y los números de esta carga.
        btn_vista_gremio = ctk.CTkButton(table_actions, text="📤 Vista Gremio", fg_color=ACCENT_TEAL, text_color="#12141c", font=(self.F_DISPLAY, 12, "bold"), width=140, command=self.exportar_vista_gremio)
        btn_vista_gremio.pack(side="right", padx=5)

        # Archivar carga al historial (idea nueva: historial de rentabilidad)
        btn_archivar = ctk.CTkButton(table_actions, text="📌 Archivar al Historial", fg_color="#e3a53c", text_color="#12141c", font=(self.F_DISPLAY, 12, "bold"), width=190, command=self.archivar_carga)
        btn_archivar.pack(side="right", padx=5)

        # Consultar precios reales en vivo (idea nueva: integración con Albion
        # Online Data Project). Necesita internet; si falla, avisa con un
        # popup y no rompe nada más de la app.
        btn_precios_api = ctk.CTkButton(table_actions, text="🔄 Precios API", fg_color="#48c9dc", text_color="#12141c", font=(self.F_DISPLAY, 12, "bold"), width=140, command=self.actualizar_precios_api)
        btn_precios_api.pack(side="right", padx=5)

        # Comparar el mejor precio de venta entre TODAS las ciudades para los
        # ítems de la tabla actual (idea propia: comparador entre hubs).
        btn_comparar = ctk.CTkButton(table_actions, text="🌍 Comparar Ciudades", fg_color="#97a2bd", text_color="#12141c", font=(self.F_DISPLAY, 12, "bold"), width=170, command=self.comparar_precios_ciudades)
        btn_comparar.pack(side="right", padx=5)

        # Idea nueva: buscador rápido + contador de ítems por estado. Todo en
        # una segunda fila de herramientas, debajo de los botones de acción.
        table_toolbar2 = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        table_toolbar2.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(table_toolbar2, text="🔎", font=(self.F_BODY, 14), text_color="#97a2bd").pack(side="left", padx=(0, 4))
        self.ent_buscar_item = ctk.CTkEntry(table_toolbar2, placeholder_text="Buscar ítem por nombre...", width=260,
                                             fg_color="#0d111c", text_color="#eef1f8")
        self.ent_buscar_item.pack(side="left")
        self.ent_buscar_item.bind("<KeyRelease>", lambda e: self.filtrar_tabla())

        self.lbl_contador_estados = ctk.CTkLabel(table_toolbar2, text="", font=(self.F_MONO, 13, "bold"), text_color="#97a2bd")
        self.lbl_contador_estados.pack(side="right", padx=5)

        self.table_scroll = ctk.CTkScrollableFrame(self.left_panel, fg_color="#0d111c", corner_radius=10)
        self.table_scroll.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        headers_frame = ctk.CTkFrame(self.table_scroll, fg_color="transparent")
        headers_frame.pack(fill="x", pady=(5, 10))
        headers = ["★", "Estado", "Nombre del Ítem", "Cant.", "Tier", "Valor Compra O/C", "Precio de Venta", "Margen %", "OC"]
        widths = [30, 130, 260, 70, 80, 150, 150, 90, 90]
        for h, w in zip(headers, widths):
            lbl = ctk.CTkLabel(headers_frame, text=h, font=(self.F_BODY, 14, "bold"), text_color="#97a2bd", width=w, anchor="w" if h not in ("Estado", "★") else "center")
            lbl.pack(side="left", padx=4)

        # Rueda del mouse para scrollear la tabla de ítems (pedido nuevo):
        # se bindea de forma recursiva a todo lo que ya existe en el
        # contenedor (encabezados incluidos); las filas nuevas se bindean
        # solas al crearse en create_row_ui().
        self.bind_mousewheel_recursive(self.table_scroll, self.table_scroll, orient="y")

        # Panel Derecho (Utilidades Auxiliares)
        self.right_panel = ctk.CTkFrame(self.workspace, fg_color="#161a26", width=500, border_color="#2a3142", border_width=1, corner_radius=14)
        self.right_panel.pack(side="right", fill="y")
        self.right_panel.pack_propagate(False)

        # Pestañas del panel derecho: separamos Esencias/Notas de las cosas nuevas
        # (refinado, roundtrip, historial) para no amontonar todo en una sola columna.
        # OJO: se hacen "a mano" con botones + frames en vez de usar CTkTabview,
        # porque ese widget no existe en versiones viejas de customtkinter y
        # rompía la app entera al arrancar (quedaba "pegada").
        tabs_btn_bar = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        tabs_btn_bar.pack(fill="x", padx=10, pady=(10, 0))

        tabs_container = ctk.CTkFrame(self.right_panel, fg_color="#161a26")
        tabs_container.pack(fill="both", expand=True, padx=10, pady=10)

        tab_general = ctk.CTkFrame(tabs_container, fg_color="transparent")
        tab_refino = ctk.CTkFrame(tabs_container, fg_color="transparent")
        tab_encante = ctk.CTkFrame(tabs_container, fg_color="transparent")
        tab_round = ctk.CTkFrame(tabs_container, fg_color="transparent")
        tab_hist = ctk.CTkFrame(tabs_container, fg_color="transparent")
        self._tab_frames = {"General": tab_general, "Refino": tab_refino, "Encantar": tab_encante, "Roundtrip": tab_round, "Historial": tab_hist}
        self._tab_buttons = {}

        def mostrar_tab(nombre):
            for n, frame in self._tab_frames.items():
                frame.pack_forget()
            self._tab_frames[nombre].pack(fill="both", expand=True)
            for n, btn in self._tab_buttons.items():
                btn.configure(fg_color="#e3a53c" if n == nombre else "#1c2130",
                               text_color="#12141c" if n == nombre else "#eef1f8")

        self._mostrar_tab_fn = mostrar_tab

        for nombre in ("General", "Refino", "Encantar", "Roundtrip", "Historial"):
            b = ctk.CTkButton(tabs_btn_bar, text=nombre, width=64, font=(self.F_BODY, 11, "bold"),
                               fg_color="#1c2130", text_color="#eef1f8", hover_color=ACCENT_PURPLE,
                               command=lambda n=nombre: mostrar_tab(n))
            b.pack(side="left", padx=2)
            self._tab_buttons[nombre] = b

        mostrar_tab("General")

        # Atajos Ctrl+1..5 (idea nueva): saltar entre pestañas sin mouse.
        orden_tabs = ["General", "Refino", "Encantar", "Roundtrip", "Historial"]
        for i, nombre in enumerate(orden_tabs, start=1):
            self.bind_all(f"<Control-Key-{i}>", lambda e, n=nombre: mostrar_tab(n))

        # --- Tab General: esencias + notas (igual que antes) ---
        lbl_esencias = ctk.CTkLabel(tab_general, text="Runas, Almas y Reliquias", font=(self.F_DISPLAY, 14, "bold"), text_color="#e3a53c")
        lbl_esencias.pack(pady=(10, 2), padx=5, anchor="w")

        # CTkScrollableFrame con scroll HORIZONTAL: es la red de seguridad para
        # que la columna "Reliquias" nunca vuelva a quedar cortada/invisible.
        # Panel más ancho (500px) + columnas más angostas + rueda del mouse
        # (con Shift) para deslizar sin depender solo de la barrita de abajo.
        self.essence_scroll = ctk.CTkScrollableFrame(
            tab_general, fg_color="#0d111c", corner_radius=10,
            orientation="horizontal", height=235
        )
        self.essence_scroll.pack(fill="x", padx=5, pady=5)
        self.render_essence_inputs()
        self.bind_mousewheel_recursive(self.essence_scroll, self.essence_scroll, orient="x")

        lbl_notas = ctk.CTkLabel(tab_general, text="Inteligencia de Zona / Notas Extra", font=(self.F_DISPLAY, 14, "bold"), text_color="#eef1f8")
        lbl_notas.pack(pady=(15, 2), padx=5, anchor="w")

        self.txt_notes = ctk.CTkTextbox(tab_general, fg_color="#0d111c", font=(self.F_BODY, 15), border_color="#2a3142", border_width=1, height=160)
        self.txt_notes.pack(fill="both", expand=True, padx=5, pady=(0, 10))
        self.txt_notes.bind("<KeyRelease>", lambda e: self.save_notes())

        # --- Tab Refino: calculadora de ratio materia prima -> refinado ---
        self.build_tab_refino(tab_refino)

        # --- Tab Encantar: requisitos de Runas/Almas/Reliquias por tipo de pieza ---
        self.build_tab_encantar(tab_encante)

        # --- Tab Roundtrip: comprar en NPC y vender directo en Mercado Negro ---
        self.build_tab_roundtrip(tab_round)

        # --- Tab Historial: lista simple + "gráfico" ascii de rentabilidad ---
        self.build_tab_historial(tab_hist)

        if self.is_premium:
            self.switch_premium.select()

        self.refrescar_selector_cargas()
        self.load_hub_data()
        self.update_peso_ui()
        self.update_riesgo_ui()

    def update_live_clock(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        self.lbl_live_clock.configure(text=f"HORA LOCAL: {current_time}")
        # Parpadeo simple del puntito "en vivo" (idea estética nueva)
        if hasattr(self, "lbl_live_dot"):
            actual = self.lbl_live_dot.cget("text_color")
            nuevo_color = "#1c2130" if actual == "#3ddc84" else "#3ddc84"
            self.lbl_live_dot.configure(text_color=nuevo_color)
        self.update_oc_expira_label()
        self.actualizar_oc_por_fila()
        self.after(1000, self.update_live_clock)

    def animate_scan_bar(self):
        """
        Anima una barrita de "flujo de datos" debajo del header: un bloque
        de color que rebota de izquierda a derecha y va cambiando de color
        entre la paleta de acento de la app. Puramente decorativo (idea #6/7/8
        pedida por el usuario), no afecta ningún cálculo ni dato guardado.
        """
        if not hasattr(self, "scan_canvas") or not self.scan_canvas.winfo_exists():
            return
        try:
            width = self.scan_canvas.winfo_width()
            if width <= 1:
                width = 1370
            block_w = 140

            self._scan_pos += 6 * self._scan_dir
            if self._scan_pos + block_w >= width:
                self._scan_dir = -1
                self._scan_color_idx = (self._scan_color_idx + 1) % len(ANIM_COLORS)
            elif self._scan_pos <= 0:
                self._scan_dir = 1
                self._scan_color_idx = (self._scan_color_idx + 1) % len(ANIM_COLORS)

            self.scan_canvas.delete("all")
            color = ANIM_COLORS[self._scan_color_idx]
            self.scan_canvas.create_rectangle(
                self._scan_pos, 0, self._scan_pos + block_w, 4,
                fill=color, outline=""
            )
        except Exception:
            pass
        self.after(35, self.animate_scan_bar)

    def create_counter_card(self, parent, label_text, color):
        card = ctk.CTkFrame(parent, fg_color="#161a26", border_color=color, border_width=1, corner_radius=16)
        card.pack(side="left", fill="x", expand=True, padx=8)

        # Franja de acento arriba de la tarjeta (idea estética nueva: le da un
        # borde "neón" a cada contador, look HUD de nave/terminal).
        strip = ctk.CTkFrame(card, fg_color=color, height=3, corner_radius=0)
        strip.pack(fill="x", side="top")

        lbl = ctk.CTkLabel(card, text=label_text, font=(self.F_BODY, 12, "bold"), text_color="#97a2bd")
        lbl.pack(pady=(12, 4), padx=18, anchor="w")
        val = ctk.CTkEntry(card, font=(self.F_MONO, 18, "bold"), text_color=color,
                            fg_color="transparent", border_width=0, justify="left")
        val.pack(pady=(0, 12), padx=18, anchor="w", fill="x")
        val.insert(0, "---")
        val.configure(state="readonly")
        return lbl, val

    # ------------------------------------------------------------------ #
    # CARGA / GUARDADO DE DATOS POR HUB
    # ------------------------------------------------------------------ #
    def refresh_manifest_label(self):
        ruta_txt = f" ({self.current_ruta_tipo})" if self.current_ruta_tipo and self.current_hub != "Caerleon" else ""
        self.set_display_text(self.lbl_manifest, f"Ruta: {self.current_hub}{ruta_txt} → {self.current_destino}")

    def load_hub_data(self):
        for row in self.row_inputs:
            row["frame"].destroy()
        self.row_inputs.clear()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, oc_timestamp "
            "FROM inventario WHERE user_id = ? AND hub = ? AND carga_nombre = ?",
            (self.current_user_id, self.current_hub, self.current_carga)
        )
        items = cursor.fetchall()
        for item in items:
            self.create_row_ui(item)

        # Limpieza obligatoria visual de esencias antes de cargar las nuevas (Evita cruce de precios entre ciudades)
        for t in range(4, 9):
            self.essence_inputs[f"r_t{t}"].delete(0, "end")
            self.essence_inputs[f"r_t{t}"].insert(0, "0")
            self.essence_inputs[f"a_t{t}"].delete(0, "end")
            self.essence_inputs[f"a_t{t}"].insert(0, "0")
            self.essence_inputs[f"re_t{t}"].delete(0, "end")
            self.essence_inputs[f"re_t{t}"].insert(0, "0")

        for t in range(4, 9):
            cursor.execute("SELECT runas, almas, reliquias FROM esencias WHERE user_id = ? AND hub = ? AND tier = ?", (self.current_user_id, self.current_hub, t))
            esc = cursor.fetchone()
            if esc:
                r, a, re = esc
                self.essence_inputs[f"r_t{t}"].delete(0, "end")
                self.essence_inputs[f"r_t{t}"].insert(0, str(r))
                self.essence_inputs[f"a_t{t}"].delete(0, "end")
                self.essence_inputs[f"a_t{t}"].insert(0, str(a))
                self.essence_inputs[f"re_t{t}"].delete(0, "end")
                self.essence_inputs[f"re_t{t}"].insert(0, str(re))

        # --- Config de destino / ruta / transporte de ESTE hub (persiste siempre, pedido #12) ---
        cursor.execute("SELECT destino, ruta_tipo, costo_transporte FROM config_hub WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
        cfg = cursor.fetchone()
        if cfg:
            destino_guardado, ruta_guardada, costo_guardado = cfg
        else:
            destino_guardado, ruta_guardada, costo_guardado = DESTINO_MERCADO, "", 0.0

        self.current_destino = destino_guardado or DESTINO_MERCADO
        self.sel_destino.set(self.current_destino)

        if self.current_hub == "Caerleon":
            # Caerleon ya ES el Mercado Negro: no aplica el sub-selector de portal/ciudad.
            self.lbl_ruta.pack_forget()
            self.sel_ruta.pack_forget()
            self.current_ruta_tipo = ""
        else:
            opciones_ruta = [self.current_hub, f"{self.current_hub} Portal"]
            self.sel_ruta.configure(values=opciones_ruta)
            self.current_ruta_tipo = ruta_guardada if ruta_guardada in opciones_ruta else self.current_hub
            self.sel_ruta.set(self.current_ruta_tipo)
            self.lbl_ruta.pack(side="left", padx=(20, 10), pady=15)
            self.sel_ruta.pack(side="left", padx=5, pady=15)

        self.ent_costo_transporte.delete(0, "end")
        self.ent_costo_transporte.insert(0, str(int(costo_guardado)))
        self.update_destino_ui()
        self.refresh_manifest_label()

        cursor.execute("SELECT texto FROM notas WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
        nota = cursor.fetchone()
        self.txt_notes.delete("1.0", "end")
        if nota:
            self.txt_notes.insert("1.0", nota[0])
        conn.close()
        self.calculate_metrics()
        self.refresh_historial_tab()

    def create_row_ui(self, db_row=None):
        # CAMBIO PEDIDO #1: una fila nueva ahora arranca en "⚙ En Proceso" en vez
        # de "✓ Recibido" (antes se le pasaba "checked" como default acá).
        if db_row:
            db_id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, oc_timestamp = db_row
        else:
            db_id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, oc_timestamp = (
                None, "processing", "", 1, "", 0.0, 0.0, 0, datetime.now().strftime("%Y-%m-%d %H:%M")
            )

        row_bg = "#1c2130" if len(self.row_inputs) % 2 == 0 else "#181d29"
        row_frame = ctk.CTkFrame(self.table_scroll, fg_color=row_bg, corner_radius=8)
        row_frame.pack(fill="x", pady=5, padx=2)

        # Favorito / tag de ítem rentable (idea nueva: calidad de vida). Es solo
        # un toggle visual + guardado en DB, no afecta ningún cálculo.
        fav_var = {"on": bool(favorito)}
        btn_fav = ctk.CTkButton(row_frame, text=("★" if fav_var["on"] else "☆"), width=30, corner_radius=8,
                                 fg_color="transparent", hover_color="#e3a53c",
                                 text_color=("#e3a53c" if fav_var["on"] else "#97a2bd"),
                                 font=(self.F_BODY, 15, "bold"),
                                 command=lambda: self.toggle_favorito(fav_var, btn_fav))
        btn_fav.pack(side="left", padx=4)

        status_sel = ctk.CTkOptionMenu(row_frame, values=list(STATUS_COLORS.keys()), width=130, font=(self.F_BODY, 13, "bold"), text_color="#12141c")
        if status == "canceled":
            status_sel.set("✕ Cancelado")
        elif status == "processing":
            status_sel.set("⚙ En Proceso")
        else:
            status_sel.set("✓ Recibido")
        status_sel.configure(fg_color=STATUS_COLORS.get(status_sel.get(), "#232838"))

        def on_status_change(value, sel=status_sel):
            sel.configure(fg_color=STATUS_COLORS.get(value, "#232838"))
            # CAMBIO PEDIDO #3: el habilitado/deshabilitado del precio MN depende
            # del estado de ESTA fila (solo Recibido se puede tocar en venta).
            self.aplicar_bloqueo_precio_mn(self.row_inputs_lookup(sel))
            self.sync_and_calc()

        status_sel.configure(command=on_status_change)
        status_sel.pack(side="left", padx=4)

        ent_nombre = ctk.CTkEntry(row_frame, width=300, placeholder_text="Nombre Ítem...", fg_color="#0d111c", text_color="#eef1f8")
        ent_nombre.insert(0, nombre)
        ent_nombre.pack(side="left", padx=4)
        ent_nombre.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_cant = ctk.CTkEntry(row_frame, width=70, fg_color="#0d111c", justify="center", text_color="#eef1f8")
        ent_cant.insert(0, str(cantidad))
        ent_cant.pack(side="left", padx=4)
        ent_cant.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_tier = ctk.CTkEntry(row_frame, width=80, placeholder_text="T6.1", fg_color="#0d111c", justify="center", text_color="#eef1f8")
        ent_tier.insert(0, tier)
        ent_tier.pack(side="left", padx=4)
        ent_tier.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_oc = ctk.CTkEntry(row_frame, width=160, fg_color="#0d111c", text_color="#48c9dc")
        ent_oc.insert(0, str(int(valor_oc)))
        ent_oc.pack(side="left", padx=4)
        ent_oc.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        # El estado inicial del campo MN se resuelve más abajo con
        # aplicar_bloqueo_precio_mn, una vez que la fila ya está en row_inputs.
        # CAMBIO PEDIDO #4: arranca en "readonly" en vez de "disabled" para que
        # su contenido siga siendo seleccionable/copiable con el mouse aunque
        # esté bloqueado para edición.
        ent_mn = ctk.CTkEntry(row_frame, width=160, fg_color="#232838", text_color="#3ddc84", state="readonly")
        ent_mn.configure(state="normal")
        ent_mn.insert(0, str(int(precio_mn)))
        ent_mn.configure(state="readonly")
        ent_mn.pack(side="left", padx=4)
        ent_mn.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        # Margen % por fila (idea nueva): ganancia real de ESE ítem puntual,
        # no solo el total de la carga. Se recalcula solo, no se edita a mano.
        lbl_margen = ctk.CTkLabel(row_frame, text="---", width=90, font=(self.F_MONO, 13, "bold"), text_color="#97a2bd", anchor="center")
        lbl_margen.pack(side="left", padx=4)

        # Indicador de expiración de la Orden de Compra DE ESTA FILA puntual
        # (idea nueva): cada ítem guarda su propio oc_timestamp, así que si
        # compraste cosas en momentos distintos, cada una avisa por separado.
        lbl_oc_fila = ctk.CTkLabel(row_frame, text="", width=90, font=(self.F_MONO, 11, "bold"), text_color="#97a2bd", anchor="center")
        lbl_oc_fila.pack(side="left", padx=4)

        btn_del = ctk.CTkButton(row_frame, text="✕", width=35, corner_radius=8, fg_color="transparent", hover_color="#ef5350", text_color="#97a2bd", font=(self.F_BODY, 14, "bold"), command=lambda: self.delete_row(db_id, row_frame))
        btn_del.pack(side="left", padx=6)

        row_data = {
            "db_id": db_id, "status": status_sel, "nombre": ent_nombre,
            "cantidad": ent_cant, "tier": ent_tier, "valor_oc": ent_oc, "precio_mn": ent_mn,
            "frame": row_frame, "favorito": fav_var, "btn_fav": btn_fav,
            "lbl_margen": lbl_margen, "lbl_oc_fila": lbl_oc_fila, "oc_timestamp": oc_timestamp,
        }
        self.row_inputs.append(row_data)

        # Ahora que la fila ya está registrada, aplicamos el bloqueo correcto
        # según fase de venta + estado de la fila (pedido #3).
        self.aplicar_bloqueo_precio_mn(row_data)

        # Navegación tipo Excel con las flechas del teclado (pedido #6)
        for key, widget in (("nombre", ent_nombre), ("cantidad", ent_cant), ("tier", ent_tier), ("valor_oc", ent_oc), ("precio_mn", ent_mn)):
            widget.bind("<Up>", lambda e, w=widget: self.move_focus_table(w, -1, 0))
            widget.bind("<Down>", lambda e, w=widget: self.move_focus_table(w, 1, 0))
            widget.bind("<Left>", lambda e, w=widget: self.move_focus_table(w, 0, -1))
            widget.bind("<Right>", lambda e, w=widget: self.move_focus_table(w, 0, 1))

        # Rueda del mouse sobre esta fila también scrollea la tabla (pedido nuevo)
        self.bind_mousewheel_recursive(row_frame, self.table_scroll, orient="y")

    def row_inputs_lookup(self, status_widget):
        # Encuentra el dict de row_inputs correspondiente a un OptionMenu de estado.
        for row in self.row_inputs:
            if row["status"] is status_widget:
                return row
        return None

    def aplicar_bloqueo_precio_mn(self, row):
        """
        CAMBIO PEDIDO #3: decide si el campo 'Precio de Venta (MN)' de una fila
        se puede editar. Regla: solo es editable si (a) la fase de venta está
        activa Y (b) el estado de ESA fila puntual es "✓ Recibido". Si la fila
        está "⚙ En Proceso" o "✕ Cancelado", el campo queda bloqueado sin
        importar la fase — y su estado NO se toca ni se fuerza a otra cosa.
        CAMBIO PEDIDO #4: usamos "readonly" en vez de "disabled" para que el
        campo bloqueado siga siendo seleccionable/copiable con el mouse.
        """
        if row is None:
            return
        estado = row["status"].get()
        editable = self.fase_venta_activa and estado == "✓ Recibido"
        if editable:
            row["precio_mn"].configure(state="normal", fg_color="#0d111c")
        else:
            row["precio_mn"].configure(state="readonly", fg_color="#232838")

    def toggle_favorito(self, fav_var, btn_fav):
        fav_var["on"] = not fav_var["on"]
        btn_fav.configure(text=("★" if fav_var["on"] else "☆"),
                           text_color=("#e3a53c" if fav_var["on"] else "#97a2bd"))
        self.sync_and_calc()

    def move_focus_table(self, widget, drow, dcol):
        row_idx, col_idx = None, None
        for i, row in enumerate(self.row_inputs):
            for j, key in enumerate(TABLE_COLS):
                if row.get(key) is widget:
                    row_idx, col_idx = i, j
                    break
            if row_idx is not None:
                break
        if row_idx is None:
            return "break"
        new_row = row_idx + drow
        new_col = col_idx + dcol
        if 0 <= new_row < len(self.row_inputs) and 0 <= new_col < len(TABLE_COLS):
            target = self.row_inputs[new_row][TABLE_COLS[new_col]]
            try:
                target.focus_set()
                target.select_range(0, "end")
            except Exception:
                pass
        return "break"

    def move_focus_essence(self, widget, drow, dcol):
        current_tier, current_col = None, None
        for key, w in self.essence_inputs.items():
            if w is widget:
                col_prefix, trest = key.split("_t")
                current_col = col_prefix
                current_tier = int(trest)
                break
        if current_tier is None:
            return "break"
        col_idx = ESSENCE_COLS.index(current_col)
        new_tier = current_tier + drow
        new_col_idx = col_idx + dcol
        if 4 <= new_tier <= 8 and 0 <= new_col_idx < len(ESSENCE_COLS):
            target_key = f"{ESSENCE_COLS[new_col_idx]}_t{new_tier}"
            target = self.essence_inputs[target_key]
            try:
                target.focus_set()
                target.select_range(0, "end")
            except Exception:
                pass
        return "break"

    def activar_fase_venta(self):
        # CAMBIO PEDIDO #3: al pasar a fase de venta, los estados de TODAS las
        # filas se quedan exactamente como estaban (cancelado sigue cancelado,
        # en proceso sigue en proceso, recibido sigue recibido). Ya NO se
        # fuerza "En Proceso" -> "Recibido" automáticamente como antes.
        if not self.row_inputs:
            messagebox.showwarning("Carga Vacía", "No tienes ningún ítem registrado.")
            return

        self.fase_venta_activa = True
        for row in self.row_inputs:
            # Solo aplicamos el bloqueo/desbloqueo del campo MN según el estado
            # real de cada fila; no tocamos el estado en sí.
            self.aplicar_bloqueo_precio_mn(row)

        self.btn_fase.configure(text="FASE DE VENTA ACTIVA ✓", fg_color="#3ddc84")
        self.btn_regresar.pack(side="right", padx=5)
        self.sync_and_calc()

    def regresar_fase_compra(self):
        self.fase_venta_activa = False
        for row in self.row_inputs:
            self.aplicar_bloqueo_precio_mn(row)

        self.btn_fase.configure(text="✓ LISTO: PASAR A VENTA M/N", fg_color="#e3a53c")
        self.btn_regresar.pack_forget()
        self.sync_and_calc()

    def add_item_row(self):
        self.create_row_ui()
        self.sync_and_calc()
        self.actualizar_contador_estados()

    def render_essence_inputs(self):
        h_frame = ctk.CTkFrame(self.essence_scroll, fg_color="transparent")
        h_frame.pack(fill="x", pady=6)
        ctk.CTkLabel(h_frame, text="T", width=24, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=3)
        ctk.CTkLabel(h_frame, text="Runas", width=66, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=3)
        ctk.CTkLabel(h_frame, text="Almas", width=66, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=3)
        ctk.CTkLabel(h_frame, text="Reliquias", width=80, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=3)

        for t in range(4, 9):
            row = ctk.CTkFrame(self.essence_scroll, fg_color="transparent")
            row.pack(fill="x", pady=4)

            ctk.CTkLabel(row, text=f"T{t}", width=24, font=(self.F_DISPLAY, 14, "bold"), text_color="#e3a53c").pack(side="left", padx=3)

            r_in = ctk.CTkEntry(row, width=66, fg_color="#0d111c", justify="center", text_color="#eef1f8")
            r_in.insert(0, "0")
            r_in.pack(side="left", padx=3)
            r_in.bind("<KeyRelease>", lambda e: self.save_esencias_and_calc())

            a_in = ctk.CTkEntry(row, width=66, fg_color="#0d111c", justify="center", text_color="#eef1f8")
            a_in.insert(0, "0")
            a_in.pack(side="left", padx=3)
            a_in.bind("<KeyRelease>", lambda e: self.save_esencias_and_calc())

            re_in = ctk.CTkEntry(row, width=80, fg_color="#0d111c", justify="center", text_color="#eef1f8")
            re_in.insert(0, "0")
            re_in.pack(side="left", padx=3)
            re_in.bind("<KeyRelease>", lambda e: self.save_esencias_and_calc())

            self.essence_inputs[f"r_t{t}"] = r_in
            self.essence_inputs[f"a_t{t}"] = a_in
            self.essence_inputs[f"re_t{t}"] = re_in

            r_in.bind("<Up>", lambda e, w=r_in: self.move_focus_essence(w, -1, 0))
            r_in.bind("<Down>", lambda e, w=r_in: self.move_focus_essence(w, 1, 0))
            r_in.bind("<Left>", lambda e, w=r_in: self.move_focus_essence(w, 0, -1))
            r_in.bind("<Right>", lambda e, w=r_in: self.move_focus_essence(w, 0, 1))

            a_in.bind("<Up>", lambda e, w=a_in: self.move_focus_essence(w, -1, 0))
            a_in.bind("<Down>", lambda e, w=a_in: self.move_focus_essence(w, 1, 0))
            a_in.bind("<Left>", lambda e, w=a_in: self.move_focus_essence(w, 0, -1))
            a_in.bind("<Right>", lambda e, w=a_in: self.move_focus_essence(w, 0, 1))

            re_in.bind("<Up>", lambda e, w=re_in: self.move_focus_essence(w, -1, 0))
            re_in.bind("<Down>", lambda e, w=re_in: self.move_focus_essence(w, 1, 0))
            re_in.bind("<Left>", lambda e, w=re_in: self.move_focus_essence(w, 0, -1))
            re_in.bind("<Right>", lambda e, w=re_in: self.move_focus_essence(w, 0, 1))

    def sync_and_calc(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        for row in self.row_inputs:
            txt_status = row["status"].get()
            if txt_status == "✕ Cancelado":
                status = "canceled"
            elif txt_status == "⚙ En Proceso":
                status = "processing"
            else:
                status = "checked"

            nombre = row["nombre"].get()
            cantidad = int(self.get_float(row["cantidad"].get()))
            tier = row["tier"].get()
            valor_oc = self.get_float(row["valor_oc"].get())
            precio_mn = self.get_float(row["precio_mn"].get())
            favorito = 1 if row["favorito"]["on"] else 0

            if row["db_id"] is None:
                cursor.execute('''
                    INSERT INTO inventario (user_id, hub, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, oc_timestamp, carga_nombre)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (self.current_user_id, self.current_hub, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, row.get("oc_timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M"), self.current_carga))
                row["db_id"] = cursor.lastrowid
            else:
                cursor.execute('''
                    UPDATE inventario SET status=?, nombre=?, cantidad=?, tier=?, valor_oc=?, precio_mn=?, favorito=?
                    WHERE id=?
                ''', (status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, row["db_id"]))
        conn.commit()
        conn.close()
        self.calculate_metrics()
        self.actualizar_contador_estados()

    def actualizar_contador_estados(self):
        """
        Idea nueva: cuenta cuántas filas hay en cada estado (Recibido / En
        Proceso / Cancelado) y lo muestra como un resumen rápido arriba de
        la tabla, para ver de un vistazo cómo va la carga sin tener que
        contar fila por fila.
        """
        if not hasattr(self, "lbl_contador_estados"):
            return
        recibidos = sum(1 for r in self.row_inputs if r["status"].get() == "✓ Recibido")
        procesos = sum(1 for r in self.row_inputs if r["status"].get() == "⚙ En Proceso")
        cancelados = sum(1 for r in self.row_inputs if r["status"].get() == "✕ Cancelado")
        self.lbl_contador_estados.configure(
            text=f"✓ {recibidos} Recibidos   ⚙ {procesos} En Proceso   ✕ {cancelados} Cancelados"
        )

    def filtrar_tabla(self):
        """
        Idea nueva: buscador rápido. Mientras escribís en el campo de
        búsqueda, oculta (sin borrar nada de la base de datos) las filas
        cuyo nombre no contenga el texto buscado. Vaciar el campo vuelve a
        mostrar todo.
        """
        texto = normalizar_texto(self.ent_buscar_item.get())
        for row in self.row_inputs:
            nombre_norm = normalizar_texto(row["nombre"].get())
            if texto == "" or texto in nombre_norm:
                row["frame"].pack(fill="x", pady=5, padx=2)
            else:
                row["frame"].pack_forget()

    def save_esencias_and_calc(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        for t in range(4, 9):
            r = int(self.get_float(self.essence_inputs[f"r_t{t}"].get()))
            a = int(self.get_float(self.essence_inputs[f"a_t{t}"].get()))
            re = int(self.get_float(self.essence_inputs[f"re_t{t}"].get()))
            cursor.execute('''
                INSERT OR REPLACE INTO esencias (user_id, hub, tier, runas, almas, reliquias)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (self.current_user_id, self.current_hub, t, r, a, re))
        conn.commit()
        conn.close()

    def save_notes(self):
        texto = self.txt_notes.get("1.0", "end-1c")
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO notas (user_id, hub, texto) VALUES (?, ?, ?)", (self.current_user_id, self.current_hub, texto))
        conn.commit()
        conn.close()

    def delete_row(self, db_id, frame_widget):
        # Idea nueva: confirmación antes de borrar -- evita perder una fila
        # por un click de más. Buscamos el nombre para que el mensaje sea
        # concreto en vez de un genérico "¿estás seguro?".
        nombre_item = ""
        for row in self.row_inputs:
            if row["db_id"] == db_id and row["frame"] is frame_widget:
                nombre_item = row["nombre"].get().strip()
                break
        etiqueta = f'"{nombre_item}"' if nombre_item else "este ítem"
        if not messagebox.askyesno("Confirmar Borrado", f"¿Seguro que querés borrar {etiqueta}? Esto no se puede deshacer."):
            return

        if db_id:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM inventario WHERE id = ?", (db_id,))
            conn.commit()
            conn.close()
        frame_widget.destroy()
        self.row_inputs = [r for r in self.row_inputs if r["db_id"] != db_id]
        self.calculate_metrics()
        self.actualizar_contador_estados()

    def change_hub(self, chosen_hub):
        self.current_hub = chosen_hub
        self.current_carga = "Carga 1"
        self.fase_venta_activa = False
        self.btn_fase.configure(text="✓ LISTO: PASAR A VENTA M/N", fg_color="#e3a53c")
        self.btn_regresar.pack_forget()
        self.refrescar_selector_cargas()
        self.load_hub_data()

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: CARGAS PARALELAS DENTRO DEL MISMO HUB
    # ------------------------------------------------------------------ #
    def refrescar_selector_cargas(self):
        """
        Trae del catálogo cargas_meta todas las "cargas" nombradas que
        existen para el hub actual. Si no hay ninguna (primera vez en ese
        hub), crea "Carga 1" automáticamente para que el selector nunca
        quede vacío.
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT carga_nombre FROM cargas_meta WHERE user_id = ? AND hub = ? ORDER BY id", (self.current_user_id, self.current_hub))
        nombres = [r[0] for r in c.fetchall()]
        if not nombres:
            c.execute("INSERT OR IGNORE INTO cargas_meta (user_id, hub, carga_nombre) VALUES (?, ?, ?)",
                      (self.current_user_id, self.current_hub, "Carga 1"))
            conn.commit()
            nombres = ["Carga 1"]
        conn.close()
        if self.current_carga not in nombres:
            self.current_carga = nombres[0]
        if hasattr(self, "sel_carga"):
            self.sel_carga.configure(values=nombres)
            self.sel_carga.set(self.current_carga)

    def change_carga(self, nombre):
        self.current_carga = nombre
        self.fase_venta_activa = False
        self.btn_fase.configure(text="✓ LISTO: PASAR A VENTA M/N", fg_color="#e3a53c")
        self.btn_regresar.pack_forget()
        self.load_hub_data()

    def crear_nueva_carga(self):
        """
        Pide un nombre nuevo de carga (ej. "Carga 2") y la agrega al
        catálogo de este hub, dejando la tabla vacía lista para meter
        ítems sin tocar los de la carga anterior.
        """
        import tkinter.simpledialog as simpledialog
        nombre = simpledialog.askstring("Nueva Carga", "Nombre para la nueva carga (ej. 'Carga 2'):", parent=self)
        if not nombre:
            return
        nombre = nombre.strip()
        if not nombre:
            return
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO cargas_meta (user_id, hub, carga_nombre) VALUES (?, ?, ?)",
                      (self.current_user_id, self.current_hub, nombre))
            conn.commit()
        except sqlite3.IntegrityError:
            messagebox.showwarning("Ya existe", f"Ya hay una carga llamada '{nombre}' en este hub.")
            conn.close()
            return
        conn.close()
        self.current_carga = nombre
        self.fase_venta_activa = False
        self.btn_fase.configure(text="✓ LISTO: PASAR A VENTA M/N", fg_color="#e3a53c")
        self.btn_regresar.pack_forget()
        self.refrescar_selector_cargas()
        self.load_hub_data()

    def actualizar_oc_por_fila(self):
        """
        Actualiza el mini-indicador de expiración de OC (24h) de cada fila
        de la tabla, usando el oc_timestamp propio de esa fila (no el
        "Inicio Carga/Compra" global). Se llama junto con el reloj en vivo.
        """
        for row in self.row_inputs:
            lbl = row.get("lbl_oc_fila")
            ts = row.get("oc_timestamp")
            if lbl is None or not lbl.winfo_exists():
                continue
            try:
                inicio = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            except Exception:
                lbl.configure(text="")
                continue
            restante = (inicio + timedelta(hours=OC_EXPIRA_HORAS)) - datetime.now()
            if restante.total_seconds() <= 0:
                lbl.configure(text="⚠ Expiró", text_color="#ef5350")
            else:
                horas = int(restante.total_seconds() // 3600)
                minutos = int((restante.total_seconds() % 3600) // 60)
                color = "#e3a53c" if restante.total_seconds() > 3600 else "#ef5350"
                lbl.configure(text=f"{horas}h {minutos}m", text_color=color)

    # ------------------------------------------------------------------ #
    # INICIO CARGA/COMPRA (persiste entre reinicios, pedido nuevo #5)
    # ------------------------------------------------------------------ #
    def cargar_inicio_carga(self):
        """
        Devuelve la hora de "Inicio Carga/Compra" guardada para este usuario
        en config_general. Si nunca se guardó nada (primera vez), usa la
        hora actual como punto de partida.
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT inicio_carga FROM config_general WHERE user_id = ?", (self.current_user_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def guardar_inicio_carga(self, valor=None):
        if valor is None:
            valor = self.ent_start_time.get().strip()
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            INSERT INTO config_general (user_id, inicio_carga) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET inicio_carga=excluded.inicio_carga
        ''', (self.current_user_id, valor))
        conn.commit()
        conn.close()

    def reiniciar_inicio_carga(self):
        """
        Reinicia el reloj de "Inicio Carga/Compra" a la hora actual. Se llama
        SOLO después de exportar un PDF o un CSV con éxito (pedido nuevo #5):
        cerrar/abrir la app, cambiar de ciudad, etc. nunca lo tocan.
        """
        nuevo = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.ent_start_time.delete(0, "end")
        self.ent_start_time.insert(0, nuevo)
        self.guardar_inicio_carga(nuevo)
        self.update_oc_expira_label()

    # ------------------------------------------------------------------ #
    # DESTINO / RUTA / PREMIUM (persisten en config_hub y usuarios)
    # ------------------------------------------------------------------ #
    def update_destino_ui(self):
        if self.current_destino == DESTINO_HIDEOUT:
            self.lbl_costo_transporte.pack(side="left", padx=(20, 10), pady=15)
            self.ent_costo_transporte.pack(side="left", padx=5, pady=15)
        else:
            self.lbl_costo_transporte.pack_forget()
            self.ent_costo_transporte.pack_forget()
        self.refresh_manifest_label()
        self.calculate_metrics()

    def _guardar_config_hub(self, destino=None, ruta_tipo=None, costo_transporte=None):
        if destino is None:
            destino = self.current_destino
        if ruta_tipo is None:
            ruta_tipo = self.current_ruta_tipo
        if costo_transporte is None:
            costo_transporte = self.get_float(self.ent_costo_transporte.get()) if hasattr(self, "ent_costo_transporte") else 0.0
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO config_hub (user_id, hub, destino, ruta_tipo, costo_transporte)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, hub) DO UPDATE SET destino=excluded.destino, ruta_tipo=excluded.ruta_tipo, costo_transporte=excluded.costo_transporte
        ''', (self.current_user_id, self.current_hub, destino, ruta_tipo, costo_transporte))
        conn.commit()
        conn.close()

    def change_destino(self, value):
        self.current_destino = value
        self._guardar_config_hub(destino=value)
        self.update_destino_ui()

    def change_ruta(self, value):
        self.current_ruta_tipo = value
        self._guardar_config_hub(ruta_tipo=value)
        self.refresh_manifest_label()

    def save_costo_transporte(self):
        self._guardar_config_hub(costo_transporte=self.get_float(self.ent_costo_transporte.get()))
        self.calculate_metrics()

    def toggle_premium(self):
        self.is_premium = bool(self.switch_premium.get())
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE usuarios SET premium = ? WHERE id = ?", (1 if self.is_premium else 0, self.current_user_id))
        conn.commit()
        conn.close()
        self.calculate_metrics()

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: BACKUP DE DB (Calidad de vida)
    # ------------------------------------------------------------------ #
    def backup_database(self):
        try:
            sugerido = f"albion_cargo_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            destino = filedialog.asksaveasfilename(
                title="Guardar Backup de la Base de Datos",
                defaultextension=".db",
                initialfile=sugerido,
                filetypes=[("Base de datos SQLite", "*.db")]
            )
            if not destino:
                return
            shutil.copyfile(DB_NAME, destino)
            messagebox.showinfo("Backup Completo", f"Copia de seguridad guardada en:\n{destino}")
        except Exception as ex:
            messagebox.showerror("Error de Backup", f"No se pudo copiar la base de datos:\n{ex}")

    def restaurar_backup(self):
        try:
            origen = filedialog.askopenfilename(
                title="Restaurar Backup de la Base de Datos",
                filetypes=[("Base de datos SQLite", "*.db")]
            )
            if not origen:
                return
            confirmar = messagebox.askyesno(
                "Confirmar Restauración",
                "Esto reemplaza TODA tu base de datos actual por la del backup elegido.\n"
                "¿Seguro que querés continuar? Se recomienda cerrar y reabrir la app después."
            )
            if not confirmar:
                return
            shutil.copyfile(origen, DB_NAME)
            messagebox.showinfo("Restauración Completa", "Backup restaurado. Cerrá y volvé a abrir la app para verlo reflejado.")
        except Exception as ex:
            messagebox.showerror("Error de Restauración", f"No se pudo restaurar el backup:\n{ex}")

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: EXPORTAR A CSV/EXCEL
    # ------------------------------------------------------------------ #
    def export_to_csv(self):
        if not self.row_inputs:
            messagebox.showwarning("CSV Vacío", "No hay datos en la tabla para exportar.")
            return
        self.sync_and_calc()

        filename = filedialog.asksaveasfilename(
            title="Guardar Carga como CSV",
            defaultextension=".csv",
            initialfile=f"Carga{self.current_hub.replace(' ', '_')}.csv",
            filetypes=[("Archivo CSV (Excel)", "*.csv")]
        )
        if not filename:
            return

        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Favorito", "Estado", "Nombre", "Cantidad", "Tier", "Valor Compra O/C", "Precio Venta MN"])
                for row in self.row_inputs:
                    writer.writerow([
                        "Si" if row["favorito"]["on"] else "No",
                        row["status"].get(),
                        row["nombre"].get(),
                        row["cantidad"].get(),
                        row["tier"].get(),
                        row["valor_oc"].get(),
                        row["precio_mn"].get(),
                    ])
                writer.writerow([])
                writer.writerow(["Resumen", "Valor"])
                m = self.last_metrics
                writer.writerow(["Inversion OC", int(m["inversion"])])
                writer.writerow(["Valor Mochila", int(m["mochila"])])
                writer.writerow(["Profit Estimado", int(m["profit_est"])])
                writer.writerow(["Profit Final", int(m["profit_final"])])
            messagebox.showinfo("CSV Exportado", f"Archivo guardado exitosamente en:\n{filename}")
            # CAMBIO PEDIDO #5: exportar CSV reinicia la hora de Inicio Carga/Compra.
            self.reiniciar_inicio_carga()
        except Exception as ex:
            messagebox.showerror("Error CSV", f"No se pudo exportar:\n{ex}")

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: PESO DE CARGA VS CAPACIDAD DE MONTURA
    # ------------------------------------------------------------------ #
    def update_peso_ui(self):
        montura = self.sel_montura.get()
        if montura == "Personalizado":
            self.ent_capacidad_custom.pack(side="left", padx=5, pady=15)
            capacidad = self.get_float(self.ent_capacidad_custom.get())
        else:
            self.ent_capacidad_custom.pack_forget()
            try:
                capacidad = float(montura.split("(")[1].replace(")", ""))
            except Exception:
                capacidad = 0.0

        peso = self.get_float(self.ent_peso_carga.get())

        if capacidad <= 0:
            self.lbl_peso_status.configure(text="Definí una capacidad", text_color="#97a2bd")
            return

        pct = (peso / capacidad) * 100
        if pct <= 100:
            color = "#3ddc84"
            texto = f"{pct:.0f}% de carga ✓"
        elif pct <= 140:
            color = "#e3a53c"
            texto = f"{pct:.0f}% ¡SOBRECARGA! (más lento)"
        else:
            color = "#ef5350"
            texto = f"{pct:.0f}% ¡NO PUEDE MOVERSE!"
        self.lbl_peso_status.configure(text=texto, text_color=color)

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: ETIQUETA DE RIESGO POR RUTA
    # ------------------------------------------------------------------ #
    def update_riesgo_ui(self):
        riesgo = self.sel_riesgo.get()
        multiplicadores = {
            "🟢 Zona Segura": "Riesgo x1 (sin PvP)",
            "🟡 Zona Amarilla": "Riesgo x2 (flag PvP opcional)",
            "🔴 Zona Roja": "Riesgo x4 (PvP total, sin fama al morir)",
            "⚫ Zona Negra": "Riesgo x8 (PvP total + gremios hostiles)",
        }
        self.lbl_riesgo_mult.configure(text=multiplicadores.get(riesgo, ""))

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: AVISO DE EXPIRACIÓN DE ÓRDENES DE COMPRA (24H)
    # ------------------------------------------------------------------ #
    def update_oc_expira_label(self):
        # Blindaje: el reloj en vivo llama a esta función cada segundo, y arranca
        # a los pocos milisegundos de abrir el HUD -- antes de que existan estos
        # widgets (se crean más abajo en show_main_hud). Si todavía no están
        # listos, simplemente no hacemos nada esta vez.
        if not hasattr(self, "ent_start_time") or not hasattr(self, "lbl_oc_expira"):
            return
        try:
            inicio = datetime.strptime(self.ent_start_time.get().strip(), "%Y-%m-%d %H:%M")
        except Exception:
            self.lbl_oc_expira.configure(text="")
            return
        expira = inicio + timedelta(hours=OC_EXPIRA_HORAS)
        restante = expira - datetime.now()
        if restante.total_seconds() <= 0:
            self.lbl_oc_expira.configure(text="⚠ ÓRDENES DE COMPRA EXPIRADAS", text_color="#ef5350")
        else:
            horas = int(restante.total_seconds() // 3600)
            minutos = int((restante.total_seconds() % 3600) // 60)
            color = "#e3a53c" if restante.total_seconds() > 3600 else "#ef5350"
            self.lbl_oc_expira.configure(text=f"⏳ OC expira en {horas}h {minutos}m", text_color=color)

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: CALCULADORA DE REFINADO
    # ------------------------------------------------------------------ #
    def build_tab_refino(self, parent):
        ctk.CTkLabel(parent, text="Calculadora de Refinado", font=(self.F_DISPLAY, 14, "bold"), text_color="#e3a53c").pack(pady=(10, 4), padx=5, anchor="w")
        ctk.CTkLabel(parent, text="Ratio base: 5 materia prima → 1 refinado (T4+).\nEl bonus de ciudad/foco reduce cuánta materia prima\nse gasta realmente por unidad refinada.",
                     font=(self.F_BODY, 12), text_color="#97a2bd", justify="left").pack(padx=5, pady=(0, 10), anchor="w")

        frame_in = ctk.CTkFrame(parent, fg_color="#0d111c", corner_radius=10)
        frame_in.pack(fill="x", padx=5, pady=5)

        ctk.CTkLabel(frame_in, text="Cantidad Refinado Deseado:", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_refino_cantidad = ctk.CTkEntry(frame_in, placeholder_text="Ej: 100", fg_color="#161a26", text_color="#eef1f8")
        self.ent_refino_cantidad.insert(0, "100")
        self.ent_refino_cantidad.pack(fill="x", padx=10, pady=4)
        self.ent_refino_cantidad.bind("<KeyRelease>", lambda e: self.calcular_refino())

        ctk.CTkLabel(frame_in, text="Bonus de Ciudad (%):", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_refino_bonus_ciudad = ctk.CTkEntry(frame_in, placeholder_text="Ej: 44 (capital de ese recurso)", fg_color="#161a26", text_color="#eef1f8")
        self.ent_refino_bonus_ciudad.insert(0, "0")
        self.ent_refino_bonus_ciudad.pack(fill="x", padx=10, pady=4)
        self.ent_refino_bonus_ciudad.bind("<KeyRelease>", lambda e: self.calcular_refino())

        ctk.CTkLabel(frame_in, text="Bonus de Foco/Especialización (%):", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_refino_bonus_foco = ctk.CTkEntry(frame_in, placeholder_text="Ej: 25 (foco 100% activo)", fg_color="#161a26", text_color="#eef1f8")
        self.ent_refino_bonus_foco.insert(0, "0")
        self.ent_refino_bonus_foco.pack(fill="x", padx=10, pady=(4, 10))
        self.ent_refino_bonus_foco.bind("<KeyRelease>", lambda e: self.calcular_refino())

        self.lbl_refino_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 14, "bold"), text_color="#48c9dc", justify="left")
        self.lbl_refino_resultado.pack(padx=5, pady=15, anchor="w")

        self.calcular_refino()

    def calcular_refino(self):
        cantidad = self.get_float(self.ent_refino_cantidad.get())
        bonus_ciudad = self.get_float(self.ent_refino_bonus_ciudad.get()) / 100
        bonus_foco = self.get_float(self.ent_refino_bonus_foco.get()) / 100

        ratio_base = 5.0
        # El bonus de ciudad reduce el costo efectivo de materia prima; el foco
        # además devuelve una parte de esa materia prima gastada.
        materia_por_unidad_ciudad = ratio_base * (1 - bonus_ciudad)
        materia_total_ciudad = cantidad * materia_por_unidad_ciudad

        materia_por_unidad_total = ratio_base * (1 - bonus_ciudad) * (1 - bonus_foco)
        materia_total_con_foco = cantidad * materia_por_unidad_total

        ahorro = materia_total_ciudad - materia_total_con_foco

        self.lbl_refino_resultado.configure(
            text=(f"Materia prima con solo bonus ciudad: {materia_total_ciudad:.1f}\n"
                  f"Materia prima con ciudad + foco: {materia_total_con_foco:.1f}\n"
                  f"Ahorro extra por Foco: {ahorro:.1f} unidades")
        )

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: SIMULADOR DE ROUNDTRIP (NPC -> Mercado Negro directo)
    # ------------------------------------------------------------------ #
    def build_tab_roundtrip(self, parent):
        ctk.CTkLabel(parent, text="Simulador de Roundtrip", font=(self.F_DISPLAY, 14, "bold"), text_color="#e3a53c").pack(pady=(10, 4), padx=5, anchor="w")
        ctk.CTkLabel(parent, text="Comprar directo al NPC vendor (sin esperar\nOrden de Compra) y vender directo en el\nMercado Negro. Sin fee de setup del 2.5%.",
                     font=(self.F_BODY, 12), text_color="#97a2bd", justify="left").pack(padx=5, pady=(0, 10), anchor="w")

        frame_in = ctk.CTkFrame(parent, fg_color="#0d111c", corner_radius=10)
        frame_in.pack(fill="x", padx=5, pady=5)

        ctk.CTkLabel(frame_in, text="Precio Compra NPC (por unidad):", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_rt_compra = ctk.CTkEntry(frame_in, placeholder_text="0", fg_color="#161a26", text_color="#eef1f8")
        self.ent_rt_compra.insert(0, "0")
        self.ent_rt_compra.pack(fill="x", padx=10, pady=4)
        self.ent_rt_compra.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        ctk.CTkLabel(frame_in, text="Precio Venta Mercado Negro (por unidad):", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_rt_venta = ctk.CTkEntry(frame_in, placeholder_text="0", fg_color="#161a26", text_color="#eef1f8")
        self.ent_rt_venta.insert(0, "0")
        self.ent_rt_venta.pack(fill="x", padx=10, pady=4)
        self.ent_rt_venta.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        ctk.CTkLabel(frame_in, text="Cantidad de Unidades:", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_rt_cantidad = ctk.CTkEntry(frame_in, placeholder_text="1", fg_color="#161a26", text_color="#eef1f8")
        self.ent_rt_cantidad.insert(0, "1")
        self.ent_rt_cantidad.pack(fill="x", padx=10, pady=(4, 10))
        self.ent_rt_cantidad.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        self.switch_rt_premium = ctk.CTkSwitch(parent, text="Usar Premium en este cálculo (4% en vez de 8%)",
                                                font=(self.F_BODY, 12, "bold"), progress_color="#3ddc84",
                                                command=self.calcular_roundtrip)
        self.switch_rt_premium.pack(padx=5, pady=(5, 10), anchor="w")

        self.lbl_rt_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 14, "bold"), text_color="#3ddc84", justify="left")
        self.lbl_rt_resultado.pack(padx=5, pady=10, anchor="w")

        self.calcular_roundtrip()

    def calcular_roundtrip(self):
        compra = self.get_float(self.ent_rt_compra.get())
        venta = self.get_float(self.ent_rt_venta.get())
        cantidad = self.get_float(self.ent_rt_cantidad.get())
        tax_rate = 0.04 if self.switch_rt_premium.get() else 0.08
        ajuste_rate = 0.025

        inversion_total = compra * cantidad  # Sin fee de setup: es compra directa al NPC, no Orden de Compra
        venta_bruta = venta * cantidad
        impuesto = venta_bruta * tax_rate
        ajuste = venta_bruta * ajuste_rate
        venta_neta = venta_bruta - impuesto - ajuste
        profit = venta_neta - inversion_total

        color = "#3ddc84" if profit >= 0 else "#ef5350"
        self.lbl_rt_resultado.configure(
            text=(f"Inversión total (NPC): {int(inversion_total):,} silver\n"
                  f"Venta neta (M. Negro): {int(venta_neta):,} silver\n"
                  f"Profit Roundtrip: {int(profit):,} silver"),
            text_color=color
        )

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: INTEGRACIÓN CON LA API DE PRECIOS (Albion Online Data Project)
    # ------------------------------------------------------------------ #
    def servidor_api_actual(self):
        # Usa la región que el jugador eligió al loguearse; si por algo no
        # está mapeada, cae en "west" por defecto en vez de romper.
        return REGION_TO_SERVER.get(self.current_region, "west")

    def actualizar_precios_api(self):
        """
        Recorre las filas de la tabla actual, intenta resolver el ID de
        Albion de cada ítem (nombre + tier) y le pregunta a la API el precio
        de venta más reciente reportado en la ciudad actual. Si lo consigue,
        actualiza el campo 'Precio de Venta' SOLO si ese campo es editable
        en este momento (fase de venta activa + estado Recibido) -- si no,
        deja el dato en un resumen para que lo veas sin tocar nada bloqueado.
        """
        if not self.row_inputs:
            messagebox.showwarning("Tabla Vacía", "No hay ítems cargados para consultar precio.")
            return

        # Paso 1: resolver qué filas tienen un ítem reconocible.
        filas_resueltas = []  # (row, item_id)
        filas_sin_reconocer = []
        for row in self.row_inputs:
            if row["status"].get() == "✕ Cancelado":
                continue
            item_id = resolver_item_id(row["nombre"].get(), row["tier"].get())
            if item_id:
                filas_resueltas.append((row, item_id))
            else:
                nombre_mostrado = row["nombre"].get().strip() or "(sin nombre)"
                if nombre_mostrado not in filas_sin_reconocer:
                    filas_sin_reconocer.append(nombre_mostrado)

        if not filas_resueltas:
            messagebox.showinfo(
                "Sin ítems reconocidos",
                "Ninguno de los nombres de la tabla está en el diccionario de ítems conocidos.\n\n"
                "Esta versión reconoce nombres como 'Bolsa', 'Espada', 'Madera', 'Tela', 'Lingote', etc. "
                "combinados con un tier tipo 'T6'.\n\n"
                "Si tu ítem no matchea, se puede agregar al diccionario ITEM_ALIASES en el código."
            )
            return

        # Paso 2: llamar a la API (una sola llamada con todos los IDs juntos).
        servidor = self.servidor_api_actual()
        ids_unicos = list({item_id for _, item_id in filas_resueltas})
        try:
            resultados = consultar_precios_api(ids_unicos, self.current_hub, servidor)
        except urllib.error.URLError:
            messagebox.showerror(
                "Sin Conexión",
                "No se pudo conectar con la API de precios (Albion Online Data Project).\n"
                "Revisá tu conexión a internet e intentá de nuevo."
            )
            return
        except Exception as ex:
            messagebox.showerror("Error de API", f"No se pudo consultar la API de precios:\n{ex}")
            return

        # Paso 3: mapear resultados por item_id (nos quedamos con el más alto
        # si hay varias entradas de la misma ciudad/calidad).
        precios_por_id = {}
        for r in resultados:
            precio = r.get("sell_price_min", 0)
            if precio and precio > 0:
                item_id = r.get("item_id")
                if item_id not in precios_por_id or precio > precios_por_id[item_id]:
                    precios_por_id[item_id] = precio

        # Paso 4: aplicar a las filas que se puedan editar, y armar un resumen
        # de todo lo demás para que el jugador lo revise a mano.
        actualizados = []
        solo_informativos = []
        for row, item_id in filas_resueltas:
            precio_api = precios_por_id.get(item_id)
            if not precio_api:
                continue
            estado = row["status"].get()
            editable = self.fase_venta_activa and estado == "✓ Recibido"
            if editable:
                row["precio_mn"].configure(state="normal")
                row["precio_mn"].delete(0, "end")
                row["precio_mn"].insert(0, str(int(precio_api)))
                actualizados.append(f"{row['nombre'].get()}: {int(precio_api):,} silver (aplicado)")
            else:
                solo_informativos.append(f"{row['nombre'].get()}: {int(precio_api):,} silver (sin aplicar, campo bloqueado)")

        self.sync_and_calc()

        resumen = ""
        if actualizados:
            resumen += "PRECIOS APLICADOS:\n" + "\n".join(actualizados) + "\n\n"
        if solo_informativos:
            resumen += "SOLO INFORMATIVO (activá fase de venta y marcá Recibido para aplicar):\n" + "\n".join(solo_informativos) + "\n\n"
        if filas_sin_reconocer:
            resumen += "NO RECONOCIDOS (agregalos a ITEM_ALIASES si querés):\n" + ", ".join(filas_sin_reconocer)
        if not resumen:
            resumen = "La API respondió pero no tenía precios recientes reportados para esta ciudad."

        messagebox.showinfo("Precios de Mercado Actualizados", resumen)

    def comparar_precios_ciudades(self):
        """
        Idea propia (comparador entre hubs): para los ítems reconocidos de la
        tabla actual, consulta el precio de venta en TODAS las ciudades y te
        dice cuál conviene más para vender, en vez de asumir que la ciudad
        en la que estás parado es la mejor opción.
        """
        if not self.row_inputs:
            messagebox.showwarning("Tabla Vacía", "No hay ítems cargados para comparar.")
            return

        ids_a_nombre = {}
        for row in self.row_inputs:
            if row["status"].get() == "✕ Cancelado":
                continue
            item_id = resolver_item_id(row["nombre"].get(), row["tier"].get())
            if item_id:
                ids_a_nombre[item_id] = row["nombre"].get()

        if not ids_a_nombre:
            messagebox.showinfo("Sin ítems reconocidos", "Ninguno de los ítems de la tabla está en el diccionario conocido.")
            return

        servidor = self.servidor_api_actual()
        try:
            resultados = consultar_precios_api(list(ids_a_nombre.keys()), ",".join(CIUDADES_API), servidor)
        except urllib.error.URLError:
            messagebox.showerror("Sin Conexión", "No se pudo conectar con la API de precios. Revisá tu internet.")
            return
        except Exception as ex:
            messagebox.showerror("Error de API", f"No se pudo consultar la API de precios:\n{ex}")
            return

        # Por cada ítem, nos quedamos con la ciudad de mayor sell_price_min.
        mejor_por_item = {}
        for r in resultados:
            precio = r.get("sell_price_min", 0)
            if not precio or precio <= 0:
                continue
            item_id = r.get("item_id")
            ciudad = r.get("city")
            actual = mejor_por_item.get(item_id)
            if actual is None or precio > actual[1]:
                mejor_por_item[item_id] = (ciudad, precio)

        if not mejor_por_item:
            messagebox.showinfo("Sin Datos", "La API no tiene precios recientes reportados para estos ítems en ninguna ciudad.")
            return

        lineas = []
        for item_id, nombre in ids_a_nombre.items():
            if item_id in mejor_por_item:
                ciudad, precio = mejor_por_item[item_id]
                lineas.append(f"{nombre}: mejor en {ciudad} ({int(precio):,} silver)")
        messagebox.showinfo("Mejor Ciudad Para Vender", "\n".join(lineas) if lineas else "Sin datos suficientes.")

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: HISTORIAL DE CARGAS CON "GRÁFICO" DE RENTABILIDAD
    # ------------------------------------------------------------------ #
    def build_tab_historial(self, parent):
        ctk.CTkLabel(parent, text="Historial de Cargas Archivadas", font=(self.F_DISPLAY, 14, "bold"), text_color="#e3a53c").pack(pady=(10, 4), padx=5, anchor="w")
        ctk.CTkLabel(parent, text="Usá el botón '📌 Archivar al Historial' arriba\nde la tabla para guardar una foto de esta\ncarga y compararla con las anteriores.",
                     font=(self.F_BODY, 12), text_color="#97a2bd", justify="left").pack(padx=5, pady=(0, 10), anchor="w")

        # Resumen rápido de la semana/mes actual (idea nueva).
        self.lbl_resumen_periodo = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 12, "bold"), text_color=ACCENT_ICE, justify="left")
        self.lbl_resumen_periodo.pack(padx=5, pady=(0, 8), anchor="w")

        botones_hist = ctk.CTkFrame(parent, fg_color="transparent")
        botones_hist.pack(fill="x", padx=5, pady=(0, 8))
        btn_ranking = ctk.CTkButton(botones_hist, text="🏆 Ranking Ítems", width=140, fg_color=ACCENT_GOLD, text_color="#12141c",
                                     font=(self.F_BODY, 12, "bold"), command=self.mostrar_ranking_items)
        btn_ranking.pack(side="left", padx=(0, 5))
        btn_exp_hist = ctk.CTkButton(botones_hist, text="📊 CSV Historial", width=140, fg_color="#97a2bd", text_color="#12141c",
                                      font=(self.F_BODY, 12, "bold"), command=self.exportar_historial_csv)
        btn_exp_hist.pack(side="left", padx=5)

        self.historial_scroll = ctk.CTkScrollableFrame(parent, fg_color="#0d111c", corner_radius=10, height=340)
        self.historial_scroll.pack(fill="both", expand=True, padx=5, pady=5)
        self.bind_mousewheel_recursive(self.historial_scroll, self.historial_scroll, orient="y")

    def _guardar_snapshot_items(self, cursor, historial_id):
        """
        Guarda una copia de cada ítem (no cancelado) de la carga actual,
        vinculada al historial_id recién archivado. Es la base para el
        Ranking de Ítems más rentables (idea nueva).
        """
        tax_rate = self.last_metrics.get("tax_rate", 0.08)
        ajuste_rate = 0.025
        setup_rate = 0.025
        for row in self.row_inputs:
            if row["status"].get() == "✕ Cancelado":
                continue
            nombre = row["nombre"].get().strip() or "(sin nombre)"
            valor_oc = self.get_float(row["valor_oc"].get())
            precio_mn = self.get_float(row["precio_mn"].get())
            cantidad = int(self.get_float(row["cantidad"].get())) or 1
            costo_unit = valor_oc * (1 + setup_rate)
            venta_neta_unit = precio_mn * (1 - tax_rate - ajuste_rate)
            profit_unidad = venta_neta_unit - costo_unit
            cursor.execute('''
                INSERT INTO historial_items (historial_id, user_id, nombre, cantidad, valor_oc, precio_mn, profit_unidad)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (historial_id, self.current_user_id, nombre, cantidad, valor_oc, precio_mn, profit_unidad))

    def archivar_carga(self):
        if not self.row_inputs:
            messagebox.showwarning("Nada que archivar", "No hay ítems cargados para archivar.")
            return
        self.sync_and_calc()
        m = self.last_metrics
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO historial_cargas (user_id, hub, destino, fecha, inversion, venta_neta, profit_final)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (self.current_user_id, self.current_hub, self.current_destino,
              datetime.now().strftime("%Y-%m-%d %H:%M"), m["inversion"], m["venta_neta"], m["profit_final"]))
        self._guardar_snapshot_items(cursor, cursor.lastrowid)
        conn.commit()
        conn.close()
        messagebox.showinfo("Carga Archivada", "Se guardó una foto de esta carga en el Historial.")
        self.refresh_historial_tab()

    def refresh_historial_tab(self):
        if not hasattr(self, "historial_scroll"):
            return
        for w in self.historial_scroll.winfo_children():
            w.destroy()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT hub, destino, fecha, inversion, venta_neta, profit_final
            FROM historial_cargas WHERE user_id = ? ORDER BY id DESC LIMIT 30
        ''', (self.current_user_id,))
        registros = cursor.fetchall()
        conn.close()

        # Resumen semanal/mensual (idea nueva): suma el profit_final de todas
        # las cargas archivadas cuya fecha caiga en la semana o el mes actual.
        if hasattr(self, "lbl_resumen_periodo"):
            ahora = datetime.now()
            inicio_semana = ahora - timedelta(days=ahora.weekday())
            inicio_semana = inicio_semana.replace(hour=0, minute=0, second=0, microsecond=0)
            profit_semana, cargas_semana = 0.0, 0
            profit_mes, cargas_mes = 0.0, 0
            for hub, destino, fecha, inversion, venta_neta, profit in registros:
                try:
                    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d %H:%M")
                except Exception:
                    continue
                if fecha_dt >= inicio_semana:
                    profit_semana += profit
                    cargas_semana += 1
                if fecha_dt.year == ahora.year and fecha_dt.month == ahora.month:
                    profit_mes += profit
                    cargas_mes += 1
            self.lbl_resumen_periodo.configure(
                text=(f"📅 Esta semana: {int(profit_semana):,} silver ({cargas_semana} cargas)   |   "
                      f"📆 Este mes: {int(profit_mes):,} silver ({cargas_mes} cargas)")
            )

        if not registros:
            ctk.CTkLabel(self.historial_scroll, text="(Sin cargas archivadas todavía)", font=(self.F_BODY, 12), text_color="#97a2bd").pack(pady=10)
            return

        # "Gráfico" simple con barras de texto: cada carga es una fila con una
        # barra proporcional al profit (verde si es positivo, roja si es negativo).
        max_abs_profit = max(abs(r[5]) for r in registros) or 1

        for hub, destino, fecha, inversion, venta_neta, profit in registros:
            item_frame = ctk.CTkFrame(self.historial_scroll, fg_color="#1c2130", corner_radius=8)
            item_frame.pack(fill="x", pady=4, padx=2)

            top_line = ctk.CTkLabel(item_frame, text=f"{fecha}  •  {hub} → {destino.split('(')[0].strip()}",
                                     font=(self.F_BODY, 12, "bold"), text_color="#eef1f8", anchor="w")
            top_line.pack(fill="x", padx=10, pady=(8, 0))

            barra_len = int((abs(profit) / max_abs_profit) * 20)
            barra = "█" * max(barra_len, 1)
            color_barra = "#3ddc84" if profit >= 0 else "#ef5350"
            bar_line = ctk.CTkLabel(item_frame, text=f"{barra}  {int(profit):,} silver",
                                     font=(self.F_MONO, 13, "bold"), text_color=color_barra, anchor="w")
            bar_line.pack(fill="x", padx=10, pady=(2, 8))
            self.bind_mousewheel_recursive(item_frame, self.historial_scroll, orient="y")

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: RANKING DE ÍTEMS MÁS RENTABLES (histórico)
    # ------------------------------------------------------------------ #
    def mostrar_ranking_items(self):
        """
        Agrupa todos los snapshots de historial_items por nombre de ítem y
        muestra los 10 más rentables en promedio por unidad, junto con
        cuántas veces aparecieron y el profit total acumulado. Te dice en
        qué ítems conviene enfocarte a futuro, en vez de adivinar.
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            SELECT nombre, AVG(profit_unidad) as promedio, SUM(profit_unidad * cantidad) as total, COUNT(*) as veces
            FROM historial_items WHERE user_id = ?
            GROUP BY nombre ORDER BY promedio DESC LIMIT 10
        ''', (self.current_user_id,))
        filas = c.fetchall()
        conn.close()

        if not filas:
            messagebox.showinfo("Sin Datos", "Todavía no archivaste ninguna carga con ítems para poder rankear.")
            return

        lineas = []
        for i, (nombre, promedio, total, veces) in enumerate(filas, start=1):
            lineas.append(f"{i}. {nombre} — {int(promedio):,} silver/unidad prom. (x{veces}, total {int(total):,} silver)")
        messagebox.showinfo("🏆 Ranking de Ítems Más Rentables", "\n".join(lineas))

    def exportar_historial_csv(self):
        """
        Saca TODO el historial de cargas archivadas (no solo la carga
        actual) a un CSV, para analizarlo en Excel/Sheets si querés.
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            SELECT fecha, hub, destino, inversion, venta_neta, profit_final
            FROM historial_cargas WHERE user_id = ? ORDER BY id DESC
        ''', (self.current_user_id,))
        filas = c.fetchall()
        conn.close()

        if not filas:
            messagebox.showwarning("Sin Datos", "No hay cargas archivadas todavía para exportar.")
            return

        filename = filedialog.asksaveasfilename(
            title="Exportar Historial Completo a CSV",
            defaultextension=".csv",
            initialfile=f"Historial_{self.current_username}.csv",
            filetypes=[("Archivo CSV (Excel)", "*.csv")]
        )
        if not filename:
            return
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Fecha", "Hub", "Destino", "Inversion", "Venta Neta", "Profit Final"])
                for fecha, hub, destino, inversion, venta_neta, profit_final in filas:
                    writer.writerow([fecha, hub, destino, int(inversion), int(venta_neta), int(profit_final)])
            messagebox.showinfo("Historial Exportado", f"Archivo guardado exitosamente en:\n{filename}")
        except Exception as ex:
            messagebox.showerror("Error CSV", f"No se pudo exportar el historial:\n{ex}")

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: COMPARAR CUENTAS (multi-personaje)
    # ------------------------------------------------------------------ #
    def comparar_cuentas(self):
        """
        Suma el profit_final de historial_cargas agrupado por cada
        personaje/región que hayas registrado en esta app y los muestra
        ordenados de mayor a menor, para ver qué cuenta rinde más.
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            SELECT u.username, u.region, COALESCE(SUM(h.profit_final), 0) as total, COUNT(h.id) as cargas
            FROM usuarios u
            LEFT JOIN historial_cargas h ON h.user_id = u.id
            GROUP BY u.id
            ORDER BY total DESC
        ''')
        filas = c.fetchall()
        conn.close()

        if not filas:
            messagebox.showinfo("Sin Cuentas", "No hay personajes registrados todavía.")
            return

        lineas = []
        for i, (username, region, total, cargas) in enumerate(filas, start=1):
            marca = " ← Actual" if username == self.current_username and region == self.current_region else ""
            lineas.append(f"{i}. {username} ({region}): {int(total):,} silver en {cargas} cargas archivadas{marca}")
        messagebox.showinfo("👥 Comparación de Cuentas", "\n".join(lineas))

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: EXPORTAR VISTA DE SOLO LECTURA PARA EL GREMIO
    # ------------------------------------------------------------------ #
    def exportar_vista_gremio(self):
        """
        Genera un HTML autocontenido y de solo lectura con la ruta y los
        números actuales de la carga, para compartir con tu gremio sin
        exponer la contraseña ni dejarles editar nada -- es solo un
        archivo estático que cualquiera puede abrir en el navegador.
        """
        if not self.row_inputs:
            messagebox.showwarning("Nada que exportar", "No hay ítems cargados para compartir.")
            return
        self.sync_and_calc()
        m = self.last_metrics

        filas_html = ""
        for row in self.row_inputs:
            estado = row["status"].get()
            nombre = row["nombre"].get() or "(sin nombre)"
            cantidad = row["cantidad"].get()
            tier = row["tier"].get()
            fav = "★ " if row["favorito"]["on"] else ""
            filas_html += (
                f"<tr><td>{estado}</td><td>{fav}{nombre}</td><td>{cantidad}</td><td>{tier}</td></tr>\n"
            )

        html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Ruta {self.current_hub} - {self.current_carga}</title>
<style>
body {{ background:#0d111c; color:#eef1f8; font-family: Arial, sans-serif; padding:30px; }}
h1 {{ color:#e3a53c; }}
table {{ width:100%; border-collapse: collapse; margin-top:20px; }}
th, td {{ border-bottom:1px solid #2a3142; padding:8px; text-align:left; }}
th {{ color:#97a2bd; }}
.card {{ display:inline-block; background:#161a26; border:1px solid #2a3142; border-radius:12px; padding:16px; margin:8px; min-width:200px; }}
.verde {{ color:#3ddc84; }} .rojo {{ color:#ef5350; }} .oro {{ color:#e3a53c; }}
</style></head>
<body>
<h1>Ruta: {self.current_hub} → {self.current_destino} ({self.current_carga})</h1>
<p>Solo lectura — generado el {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
<div class="card"><b>Inversión O/C</b><br><span class="oro">{int(m['inversion']):,} silver</span></div>
<div class="card"><b>Venta Neta</b><br><span class="verde">{int(m['venta_neta']):,} silver</span></div>
<div class="card"><b>Profit Final</b><br><span class="{'verde' if m['profit_final'] >= 0 else 'rojo'}">{int(m['profit_final']):,} silver</span></div>
<table>
<tr><th>Estado</th><th>Ítem</th><th>Cant.</th><th>Tier</th></tr>
{filas_html}
</table>
</body></html>"""

        filename = filedialog.asksaveasfilename(
            title="Exportar Vista para el Gremio",
            defaultextension=".html",
            initialfile=f"Vista_{self.current_hub.replace(' ', '_')}.html",
            filetypes=[("Página HTML", "*.html")]
        )
        if not filename:
            return
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html)
            messagebox.showinfo("Vista Exportada", f"Archivo guardado exitosamente en:\n{filename}\n\nSe puede abrir con cualquier navegador, sin necesidad de la app ni tu contraseña.")
        except Exception as ex:
            messagebox.showerror("Error", f"No se pudo exportar la vista:\n{ex}")

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: CHECKLIST PRE-VIAJE
    # ------------------------------------------------------------------ #
    CHECKLIST_ITEMS = [
        ("seguro_montura", "Seguro de Montura activo"),
        ("aviso_gremio", "Aviso enviado al gremio (ruta y hora)"),
        ("silver_reserva", "Silver de reserva aparte (no todo invertido)"),
        ("ruta_escape", "Ruta de escape / portal de retorno planeada"),
        ("pociones", "Pociones de vida/energía cargadas"),
    ]

    def abrir_checklist(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT item_key, checked FROM checklist WHERE user_id = ?", (self.current_user_id,))
        estado_guardado = dict(c.fetchall())
        conn.close()

        win = ctk.CTkToplevel(self)
        win.title("Checklist Pre-Viaje")
        win.geometry("420x360")
        win.configure(fg_color="#12151f")
        win.attributes("-topmost", True)

        ctk.CTkLabel(win, text="✅ Antes de salir a Mercado Negro", font=(self.F_DISPLAY, 16, "bold"), text_color=ACCENT_LIME).pack(pady=(20, 10), padx=20, anchor="w")

        vars_check = {}
        for key, texto in self.CHECKLIST_ITEMS:
            var = tk.BooleanVar(value=bool(estado_guardado.get(key, 0)))
            chk = ctk.CTkCheckBox(win, text=texto, variable=var, font=(self.F_BODY, 13),
                                   text_color="#eef1f8", fg_color=ACCENT_LIME, hover_color=ACCENT_TEAL,
                                   command=lambda k=key, v=var: self.guardar_checklist_item(k, v.get()))
            chk.pack(anchor="w", padx=30, pady=8)
            vars_check[key] = var

        btn_cerrar = ctk.CTkButton(win, text="Cerrar", fg_color=ACCENT_LIME, text_color="#12141c",
                                    font=(self.F_BODY, 13, "bold"), command=win.destroy)
        btn_cerrar.pack(pady=20)

    def guardar_checklist_item(self, item_key, checked):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            INSERT INTO checklist (user_id, item_key, checked) VALUES (?, ?, ?)
            ON CONFLICT(user_id, item_key) DO UPDATE SET checked=excluded.checked
        ''', (self.current_user_id, item_key, 1 if checked else 0))
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: MODO PRESENTACIÓN
    # ------------------------------------------------------------------ #
    def toggle_modo_presentacion(self):
        """
        Oculta el nombre de personaje/región y bloquea toda la tabla de
        ítems para compartir pantalla (ej. en un stream o con el gremio)
        sin exponer datos que preferís mantener privados. No borra ni
        cambia ningún valor, solo lo enmascara visualmente.
        """
        self.modo_presentacion = not self.modo_presentacion
        if self.modo_presentacion:
            self._titulo_real_txt = self.ent_title.get()
            self.set_display_text(self.ent_title, "•••••• MODO PRESENTACIÓN ••••••")
            self.btn_presentacion.configure(text="🕶 Salir de Presentación", fg_color=ACCENT_PINK)
            for row in self.row_inputs:
                for campo in ("nombre", "cantidad", "tier", "valor_oc"):
                    row[campo].configure(state="readonly")
        else:
            self.set_display_text(self.ent_title, self._titulo_real_txt)
            self.btn_presentacion.configure(text="🕶 Modo Presentación", fg_color="#1e2536")
            for row in self.row_inputs:
                for campo in ("nombre", "cantidad", "tier", "valor_oc"):
                    row[campo].configure(state="normal")
                self.aplicar_bloqueo_precio_mn(row)

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: REQUISITOS DE ENCANTADO (Runas/Almas/Reliquias)
    # ------------------------------------------------------------------ #
    def build_tab_encantar(self, parent):
        ctk.CTkLabel(parent, text="Requisitos de Encantado", font=(self.F_DISPLAY, 14, "bold"), text_color=ACCENT_GOLD).pack(pady=(10, 4), padx=5, anchor="w")
        ctk.CTkLabel(parent, text="Costo de esencias (Runas + Almas + Reliquias)\nPOR CADA nivel de encantamiento (.1, .2, .3),\nsegún el tipo de pieza de equipo.",
                     font=(self.F_BODY, 12), text_color="#97a2bd", justify="left").pack(padx=5, pady=(0, 10), anchor="w")

        for nombre_cat, costo, color in ENCANTE_COSTOS:
            card = ctk.CTkFrame(parent, fg_color="#0d111c", border_color=color, border_width=1, corner_radius=10)
            card.pack(fill="x", padx=5, pady=5)
            strip = ctk.CTkFrame(card, fg_color=color, height=3, corner_radius=0)
            strip.pack(fill="x", side="top")
            ctk.CTkLabel(card, text=nombre_cat, font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", wraplength=380, justify="left").pack(anchor="w", padx=12, pady=(8, 2))
            ctk.CTkLabel(card, text=f"{costo:,} Runas  +  {costo:,} Almas  +  {costo:,} Reliquias  (por nivel)",
                         font=(self.F_MONO, 13, "bold"), text_color=color).pack(anchor="w", padx=12, pady=(0, 10))

        # Calculadora rápida: cuántas esencias totales necesitás según
        # cuántos niveles querés subir y cuántas piezas vas a encantar.
        frame_calc = ctk.CTkFrame(parent, fg_color="#0d111c", corner_radius=10)
        frame_calc.pack(fill="x", padx=5, pady=(15, 5))
        ctk.CTkLabel(frame_calc, text="Calculadora Rápida", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=10, pady=(10, 4))

        ctk.CTkLabel(frame_calc, text="Tipo de Equipo:", font=(self.F_BODY, 12, "bold"), text_color="#97a2bd").pack(anchor="w", padx=10, pady=(4, 2))
        self.sel_encante_cat = ctk.CTkOptionMenu(
            frame_calc, values=[c[0] for c in ENCANTE_COSTOS], command=lambda v: self.calcular_encantado(),
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color=ACCENT_GOLD, dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 12, "bold"))
        self.sel_encante_cat.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(frame_calc, text="Niveles a subir (ej: 2 = de .0 a .2):", font=(self.F_BODY, 12, "bold"), text_color="#97a2bd").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_encante_niveles = ctk.CTkEntry(frame_calc, fg_color="#161a26", text_color="#eef1f8")
        self.ent_encante_niveles.insert(0, "1")
        self.ent_encante_niveles.pack(fill="x", padx=10, pady=4)
        self.ent_encante_niveles.bind("<KeyRelease>", lambda e: self.calcular_encantado())

        ctk.CTkLabel(frame_calc, text="Cantidad de Piezas:", font=(self.F_BODY, 12, "bold"), text_color="#97a2bd").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_encante_piezas = ctk.CTkEntry(frame_calc, fg_color="#161a26", text_color="#eef1f8")
        self.ent_encante_piezas.insert(0, "1")
        self.ent_encante_piezas.pack(fill="x", padx=10, pady=(4, 10))
        self.ent_encante_piezas.bind("<KeyRelease>", lambda e: self.calcular_encantado())

        self.lbl_encante_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 14, "bold"), text_color=ACCENT_GOLD, justify="left")
        self.lbl_encante_resultado.pack(padx=5, pady=15, anchor="w")

        self.calcular_encantado()

    def calcular_encantado(self):
        cat = self.sel_encante_cat.get()
        costo_base = next((costo for nombre, costo, color in ENCANTE_COSTOS if nombre == cat), 0)
        niveles = int(self.get_float(self.ent_encante_niveles.get()))
        piezas = int(self.get_float(self.ent_encante_piezas.get()))
        total = costo_base * max(niveles, 0) * max(piezas, 0)
        self.lbl_encante_resultado.configure(
            text=(f"Total necesario para {piezas} pieza(s), {niveles} nivel(es):\n"
                  f"{total:,} Runas\n{total:,} Almas\n{total:,} Reliquias")
        )

    # ------------------------------------------------------------------ #
    # CÁLCULOS
    # ------------------------------------------------------------------ #
    def calculate_metrics(self):
        tax_rate = 0.04 if self.is_premium else 0.08
        ajuste_rate = 0.025   # Ajuste de mercado al vender en el Mercado Negro
        setup_rate = 0.025    # Setup fee al comprar con Órdenes de Compra

        total_inversion_oc = 0.0
        total_venta_bruta = 0.0

        val_mochila_manual = self.get_float(self.ent_mochila_global.get())
        margen_minimo = self.get_float(self.ent_margen_minimo.get()) if hasattr(self, "ent_margen_minimo") else 15.0

        for row in self.row_inputs:
            status = row["status"].get()

            # Margen % por fila (idea nueva): se calcula para TODAS las filas
            # (incluidas canceladas, en gris) para que sea un vistazo rápido
            # de qué tan rentable es cada ítem puntual.
            valor_oc_fila = self.get_float(row["valor_oc"].get())
            precio_mn_fila = self.get_float(row["precio_mn"].get())
            lbl_margen = row.get("lbl_margen")
            if lbl_margen is not None and lbl_margen.winfo_exists():
                if status == "✕ Cancelado":
                    lbl_margen.configure(text="---", text_color="#97a2bd")
                elif valor_oc_fila <= 0:
                    lbl_margen.configure(text="---", text_color="#97a2bd")
                else:
                    costo_unit = valor_oc_fila * (1 + setup_rate)
                    venta_neta_unit = precio_mn_fila * (1 - tax_rate - ajuste_rate)
                    margen_pct = ((venta_neta_unit - costo_unit) / costo_unit) * 100
                    color_margen = "#3ddc84" if margen_pct >= margen_minimo else ("#e3a53c" if margen_pct >= 0 else "#ef5350")
                    lbl_margen.configure(text=f"{margen_pct:.0f}%", text_color=color_margen)

            if status == "✕ Cancelado":
                continue

            cantidad = int(self.get_float(row["cantidad"].get()))
            if cantidad == 0:
                cantidad = 1

            valor_oc = self.get_float(row["valor_oc"].get())
            precio_mn = self.get_float(row["precio_mn"].get())

            total_inversion_oc += (cantidad * valor_oc * (1 + setup_rate))
            total_venta_bruta += (cantidad * precio_mn)

        total_impuesto = total_venta_bruta * tax_rate
        total_ajuste = total_venta_bruta * ajuste_rate
        total_venta_neta = total_venta_bruta - total_impuesto - total_ajuste

        self.set_display_text(self.card_budget, f"{int(total_inversion_oc):,} silver")
        self.set_display_text(self.card_bag_value, f"{int(val_mochila_manual):,} silver")

        profit_estimado_mochila = val_mochila_manual - total_inversion_oc

        if self.fase_venta_activa:
            self.set_display_text(self.card_status, f"{int(profit_estimado_mochila):,} silver", "#3ddc84" if profit_estimado_mochila >= 0 else "#ef5350")
        else:
            self.set_display_text(self.card_status, "---", "#eef1f8")

        costo_transporte = self.get_float(self.ent_costo_transporte.get()) if hasattr(self, "ent_costo_transporte") else 0.0

        if self.current_destino == DESTINO_HIDEOUT:
            self.card_pt_label.configure(text="COSTO TOTAL DE TRANSPORTE (Uso Personal a Hideout)")
            self.set_display_text(self.card_pt, f"{int(costo_transporte):,} silver", "#48c9dc")
            self.lbl_desglose.configure(
                text=("Carga con destino Hideout: no se vende en el Mercado Negro. "
                      f"Costo de transporte registrado: {int(costo_transporte):,} silver.")
            )
            profit_final_real = -costo_transporte
        else:
            tax_pct = int(round(tax_rate * 100))
            self.card_pt_label.configure(text=f"PROFIT FINAL MERCADO NEGRO (-{tax_pct}% Imp. -2.5% Ajuste)")
            profit_final_real = total_venta_neta - total_inversion_oc

            # Punto de equilibrio (idea nueva): cuánta Venta Bruta necesitás
            # para no perder plata, dado el impuesto + ajuste actuales. Es
            # pura matemática con tus propios números, sin depender de nada
            # externo.
            factor_neto = (1 - tax_rate - ajuste_rate)
            venta_equilibrio = (total_inversion_oc / factor_neto) if factor_neto > 0 else 0.0

            if self.fase_venta_activa:
                self.set_display_text(self.card_pt, f"{int(profit_final_real):,} silver", "#3ddc84" if profit_final_real >= 0 else "#ef5350")
                self.lbl_desglose.configure(
                    text=(f"Venta Bruta: {int(total_venta_bruta):,}  |  "
                          f"-{tax_pct}% Impuesto: -{int(total_impuesto):,}  |  "
                          f"-2.5% Ajuste: -{int(total_ajuste):,}  |  "
                          f"Neto: {int(total_venta_neta):,} silver  |  "
                          f"⚖ Punto de Equilibrio (Venta Bruta mínima): {int(venta_equilibrio):,} silver")
                )
            else:
                self.set_display_text(self.card_pt, "---", "#3ddc84")
                self.lbl_desglose.configure(
                    text=(f"Activa la fase de venta para ver el desglose de impuestos y ajuste.  |  "
                          f"⚖ Punto de Equilibrio estimado (Venta Bruta mínima): {int(venta_equilibrio):,} silver")
                )

        # Eficiencia: silver por hora desde que arrancó "Inicio Carga/Compra"
        # (idea nueva, bonus propio). Usa el profit que corresponda según el
        # destino de la carga (Mercado Negro o costo de transporte a Hideout).
        if hasattr(self, "lbl_eficiencia") and hasattr(self, "ent_start_time"):
            try:
                inicio_dt = datetime.strptime(self.ent_start_time.get().strip(), "%Y-%m-%d %H:%M")
                horas_transcurridas = max((datetime.now() - inicio_dt).total_seconds() / 3600, 0.1)
                eficiencia = profit_final_real / horas_transcurridas
                color_efi = "#3ddc84" if eficiencia >= 0 else "#ef5350"
                self.lbl_eficiencia.configure(text=f"⚡ Eficiencia: {int(eficiencia):,} silver/hora", text_color=color_efi)
            except Exception:
                self.lbl_eficiencia.configure(text="")

        self.last_metrics = {
            "inversion": total_inversion_oc,
            "mochila": val_mochila_manual,
            "venta_bruta": total_venta_bruta,
            "impuesto": total_impuesto,
            "ajuste": total_ajuste,
            "venta_neta": total_venta_neta,
            "profit_est": profit_estimado_mochila,
            "profit_final": profit_final_real,
            "fase_venta_activa": self.fase_venta_activa,
            "destino": self.current_destino,
            "ruta_tipo": self.current_ruta_tipo,
            "costo_transporte": costo_transporte,
            "premium": self.is_premium,
            "tax_rate": tax_rate,
        }

    # ------------------------------------------------------------------ #
    # EXPORTAR PDF
    # ------------------------------------------------------------------ #
    def export_to_pdf(self):
        if not self.row_inputs:
            messagebox.showwarning("PDF Vacío", "No hay datos en la tabla para exportar.")
            return

        self.sync_and_calc()

        filename = filedialog.asksaveasfilename(
            title="Guardar Carga PDF",
            defaultextension=".pdf",
            initialfile=f"Carga{self.current_hub.replace(' ', '_')}.pdf",
            filetypes=[("Archivos PDF", "*.pdf")]
        )

        if not filename:
            return

        start_str = self.ent_start_time.get().strip()
        generado_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        def to_utc_str(local_str):
            try:
                dt = datetime.strptime(local_str, "%Y-%m-%d %H:%M")
                utc_timestamp = time.mktime(dt.timetuple()) + (6 * 3600)
                dt_utc = datetime.utcfromtimestamp(utc_timestamp)
                return dt_utc.strftime("%Y-%m-%d %H:%M") + " UTC"
            except Exception:
                return "N/D"

        start_utc = to_utc_str(start_str)

        c = canvas.Canvas(filename, pagesize=letter)
        w, h = letter
        BOTTOM_MARGIN = 60

        def draw_page_background():
            c.setFillColorRGB(0.04, 0.05, 0.07)
            c.rect(0, 0, w, h, fill=1)

        def ensure_space(y, needed=20):
            if y - needed < BOTTOM_MARGIN:
                c.showPage()
                draw_page_background()
                new_y = h - 50
                return new_y
            return y

        def draw_section_title(y, text):
            y = ensure_space(y, 40)
            c.setFillColorRGB(1, 0.66, 0)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, y, text)
            y -= 8
            c.setStrokeColorRGB(0.2, 0.2, 0.3)
            c.line(40, y, w - 40, y)
            return y - 18

        draw_page_background()

        c.setFillColorRGB(1, 0.66, 0)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, h - 50, f"REPORTE DE CARGA: {self.current_hub.upper()} -> {self.current_destino.upper()}")

        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.6, 0.7, 0.8)
        c.drawString(40, h - 75, f"Nombre: {self.current_username}   |   Servidor: {self.current_region}")

        c.setFillColorRGB(1, 1, 1)
        c.drawString(40, h - 100, f"REGISTRO INICIO LOGÍSTICA: {start_str} (Local)  /  {start_utc}")
        c.drawString(40, h - 115, f"REPORTE GENERADO: {generado_str} (Local)")
        estado_fase = "VENTA MERCADO NEGRO ACTIVA" if self.last_metrics.get("fase_venta_activa") else "FASE DE COMPRA (Venta aún no iniciada)"
        c.drawString(40, h - 130, f"ESTADO DE LA CARGA: {estado_fase}")

        y = h - 160

        y = draw_section_title(y, "RESUMEN FINANCIERO GENERAL")
        m = self.last_metrics
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(1, 1, 1)

        ruta_txt = f"{self.current_hub}"
        if m['ruta_tipo'] and self.current_hub != "Caerleon":
            ruta_txt += f" ({m['ruta_tipo']})"

        resumen_lines = [
            f"Cuenta Premium: {'Sí (Impuesto Mercado Negro 4%)' if m['premium'] else 'No (Impuesto Mercado Negro 8%)'}",
            f"Ruta: {ruta_txt}  ->  {m['destino']}",
            f"Inversión Órdenes de Compra (+2.5% Setup): {int(m['inversion']):,} silver",
            f"Valor Declarado Mochila: {int(m['mochila']):,} silver",
            f"Profit Estimado (Mochila - Inversión): {int(m['profit_est']):,} silver",
        ]

        if m['destino'] == DESTINO_HIDEOUT:
            resumen_lines += [
                "",
                "Esta carga tiene destino Hideout (uso personal): no se vende en el Mercado Negro.",
                f"Costo de Transporte Registrado: {int(m['costo_transporte']):,} silver",
            ]
        else:
            tax_pct = int(round(m['tax_rate'] * 100))
            resumen_lines += [
                f"Venta Bruta Proyectada en Mercado Negro: {int(m['venta_bruta']):,} silver",
                f"  -{tax_pct}% Impuesto Mercado Negro: -{int(m['impuesto']):,} silver",
                f"  -2.5% Ajuste de Mercado: -{int(m['ajuste']):,} silver",
                f"Venta Neta: {int(m['venta_neta']):,} silver",
                f"Profit Final Mercado Negro (Venta Neta - Inversión): {int(m['profit_final']):,} silver",
            ]

        for line in resumen_lines:
            y = ensure_space(y, 16)
            c.drawString(40, y, line)
            y -= 16

        y -= 10

        y = draw_section_title(y, "CARGA DE ÍTEMS (incluye cancelados)")

        def draw_table_headers(y):
            c.setFillColorRGB(1, 0.66, 0)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(40, y, "Estado")
            c.drawString(115, y, "Ítem")
            c.drawString(300, y, "Cant.")
            c.drawString(335, y, "Tier")
            c.drawString(375, y, "Costo Compra O/C")
            c.drawString(475, y, "Precio de Venta")
            y -= 8
            c.setStrokeColorRGB(0.2, 0.2, 0.3)
            c.line(40, y, w - 40, y)
            return y - 14

        y = draw_table_headers(y)
        c.setFont("Helvetica", 8.5)

        for row in self.row_inputs:
            y = ensure_space(y, 18)
            status = row["status"].get()
            cancelado = (status == "✕ Cancelado")

            valor_oc_raw = self.get_float(row["valor_oc"].get())
            precio_mn_raw = self.get_float(row["precio_mn"].get())
            costo_oc_total = valor_oc_raw * 1.025
            venta_mn_total = precio_mn_raw * (1 - 0.105) if not self.is_premium else precio_mn_raw * (1 - 0.065)

            fav_marca = "★ " if row["favorito"]["on"] else ""

            c.setFillColorRGB(1, 0.3, 0.3) if cancelado else c.setFillColorRGB(1, 1, 1)
            c.drawString(40, y, status)
            c.drawString(115, y, (fav_marca + (row["nombre"].get() or "(sin nombre)"))[:32])
            c.drawString(300, y, row["cantidad"].get())
            c.drawString(335, y, row["tier"].get())

            if cancelado:
                c.drawString(375, y, f"{int(costo_oc_total):,} (Cancelado, no incluido)")
                c.drawString(475, y, f"{int(venta_mn_total):,} (Cancelado, no incluido)")
            else:
                c.drawString(375, y, f"{int(costo_oc_total):,} silver")
                c.drawString(475, y, f"{int(venta_mn_total):,} silver")

            y -= 16

        c.setFillColorRGB(1, 1, 1)
        y -= 10

        y = draw_section_title(y, "REFINO / PRECIOS DE ESENCIAS")
        c.setFont("Helvetica-Bold", 9)
        c.setFillColorRGB(1, 0.66, 0)
        c.drawString(40, y, "Tier")
        c.drawString(120, y, "Runas")
        c.drawString(220, y, "Almas")
        c.drawString(320, y, "Reliquias")
        y -= 16
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(1, 1, 1)
        for t in range(4, 9):
            y = ensure_space(y, 16)
            r = self.essence_inputs[f"r_t{t}"].get()
            a = self.essence_inputs[f"a_t{t}"].get()
            re = self.essence_inputs[f"re_t{t}"].get()
            c.drawString(40, y, f"T{t}")
            c.drawString(120, y, str(r))
            c.drawString(220, y, str(a))
            c.drawString(320, y, str(re))
            y -= 16

        y -= 10

        y = draw_section_title(y, "NOTAS Extras")
        nota_texto = self.txt_notes.get("1.0", "end-1c").strip()
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(1, 1, 1)
        if nota_texto:
            for parrafo in nota_texto.split("\n"):
                wrapped = textwrap.wrap(parrafo, width=100) or [""]
                for linea in wrapped:
                    y = ensure_space(y, 14)
                    c.drawString(40, y, linea)
                    y -= 14
        else:
            y = ensure_space(y, 14)
            c.drawString(40, y, "(Sin notas registradas para este hub)")
            y -= 14

        c.save()

        # Al exportar el PDF también archivamos automáticamente la carga al
        # historial, así el historial se llena solo con cada reporte generado.
        try:
            self.archivar_carga_silenciosa()
        except Exception:
            pass

        messagebox.showinfo("Carga Exportada", f"Archivo guardado exitosamente en:\n{filename}")
        # CAMBIO PEDIDO #5: exportar PDF reinicia la hora de Inicio Carga/Compra.
        self.reiniciar_inicio_carga()

    def archivar_carga_silenciosa(self):
        """Igual que archivar_carga() pero sin popup, usada al exportar PDF."""
        m = self.last_metrics
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO historial_cargas (user_id, hub, destino, fecha, inversion, venta_neta, profit_final)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (self.current_user_id, self.current_hub, self.current_destino,
              datetime.now().strftime("%Y-%m-%d %H:%M"), m["inversion"], m["venta_neta"], m["profit_final"]))
        self._guardar_snapshot_items(cursor, cursor.lastrowid)
        conn.commit()
        conn.close()
        self.refresh_historial_tab()


if __name__ == "__main__":
    app = AlbionCargoApp()
    app.mainloop()
