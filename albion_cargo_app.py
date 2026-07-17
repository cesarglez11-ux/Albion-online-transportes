import os
import sqlite3
import time
import textwrap
import shutil
import csv
import unicodedata
import urllib.request
from datetime import datetime, timedelta
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox
import customtkinter as ctk
try:
    from PIL import Image, ImageTk
    PIL_DISPONIBLE = True
except ImportError:
    PIL_DISPONIBLE = False
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

DB_NAME = "albion_cargo.db"
APP_ICON_URL = "https://i.imgur.com/Hi3Qz9r.png"
ICON_CACHE_FILE = "app_icon_cache.png"

FONT_DISPLAY_CANDIDATES = ["Oxanium", "Orbitron", "Audiowide", "Segoe UI Semibold", "Ubuntu", "Noto Sans", "DejaVu Sans"]
FONT_BODY_CANDIDATES = ["Rajdhani", "Ubuntu", "Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"]
FONT_MONO_CANDIDATES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Ubuntu Mono", "DejaVu Sans Mono", "Courier New"]

STATUS_COLORS = {
    "✓ Recibido": "#3ddc84",
    "⚙ En Proceso": "#e3a53c",
    "✕ Cancelado": "#ef5350",
}

ACCENT_PURPLE = "#9b5de5"
ACCENT_PINK = "#f15bb5"
ACCENT_GOLD = "#ffd166"
ACCENT_TEAL = "#2ec4b6"
ACCENT_LIME = "#c5f04a"
ACCENT_ICE = "#5aa9e6"
ANIM_COLORS = ["#e3a53c", "#3ddc84", "#48c9dc", "#9b5de5", "#f15bb5", "#ef5350", "#2ec4b6", "#c5f04a"]

ENCANTE_COSTOS = [
    ("Pies, Cascos, Armas Secundarias y Capas", 96, ACCENT_TEAL),
    ("Pecho y Bolsa", 192, ACCENT_ICE),
    ("Armas de Una Mano", 288, ACCENT_PURPLE),
    ("Armas de Dos Manos", 384, ACCENT_PINK),
]

# --------------------------------------------------------------------- #
# PÁGINAS (idea nueva del jugador): cada botón abre una página COMPLETA a
# lo ancho de toda la ventana, una a la vez. Nada de paneles angostos con
# sub-pestañas amontonadas -- eso fue lo que se veía mal y se cortaba.
# "Mi Carga" es la página de arranque (la tabla de ítems).
# --------------------------------------------------------------------- #
PAGINAS = [
    "📦 Mi Carga", "💎 Runas y Notas", "⚗ Refino", "✨ Encantar",
    "🔄 Roundtrip", "🧪 Materiales", "📊 Historial", "🏦 Banco",
    "★ Favoritos", "🚀 Viaje",
]

HORAS_OC_ATASCADA = 72

DESTINO_MERCADO = "Mercado Negro (Caerleon)"
DESTINO_HIDEOUT = "Hideout (Uso Personal)"
DESTINOS = [DESTINO_MERCADO, DESTINO_HIDEOUT]

TABLE_COLS = ["nombre", "cantidad", "tier", "valor_oc", "precio_mn"]
ESSENCE_COLS = ["r", "a", "re"]

OC_EXPIRA_HORAS = 24


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
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

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
            pass
    if "favorito" not in cols_inv:
        cursor.execute("ALTER TABLE inventario ADD COLUMN favorito INTEGER DEFAULT 0")
    if "oc_timestamp" not in cols_inv:
        cursor.execute("ALTER TABLE inventario ADD COLUMN oc_timestamp TEXT")
    if "carga_nombre" not in cols_inv:
        cursor.execute("ALTER TABLE inventario ADD COLUMN carga_nombre TEXT DEFAULT 'Carga 1'")
        cursor.execute("UPDATE inventario SET carga_nombre = 'Carga 1' WHERE carga_nombre IS NULL")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cargas_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            hub TEXT,
            carga_nombre TEXT,
            UNIQUE(user_id, hub, carga_nombre)
        )
    ''')

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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config_hub (
            user_id INTEGER,
            hub TEXT,
            destino TEXT DEFAULT 'Mercado Negro (Caerleon)',
            ruta_tipo TEXT DEFAULT '',
            costo_transporte REAL DEFAULT 0,
            valor_mochila REAL DEFAULT 0,
            PRIMARY KEY(user_id, hub)
        )
    ''')
    cursor.execute("PRAGMA table_info(config_hub)")
    cols_config_hub = [c[1] for c in cursor.fetchall()]
    if "valor_mochila" not in cols_config_hub:
        cursor.execute("ALTER TABLE config_hub ADD COLUMN valor_mochila REAL DEFAULT 0")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config_general (
            user_id INTEGER PRIMARY KEY,
            inicio_carga TEXT
        )
    ''')
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banco_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            hub TEXT,
            carga_nombre TEXT,
            nombre TEXT,
            cantidad INTEGER,
            tier TEXT,
            estado TEXT,
            valor_oc REAL,
            precio_mn REAL,
            profit_unidad REAL,
            profit_total REAL,
            favorito INTEGER DEFAULT 0,
            fecha TEXT,
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

        icono_img = cargar_icono_app()
        if icono_img is not None:
            try:
                self._icon_photo = ImageTk.PhotoImage(icono_img)
                self.iconphoto(True, self._icon_photo)
            except Exception as ex:
                print(f"No se pudo aplicar el icono a la ventana: {ex}")

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
        self.pagina_actual = "📦 Mi Carga"

        self.row_inputs = []
        self.essence_inputs = {}
        self.last_metrics = {
            "inversion": 0.0, "mochila": 0.0, "venta_bruta": 0.0, "impuesto": 0.0,
            "ajuste": 0.0, "venta_neta": 0.0, "profit_est": 0.0, "profit_final": 0.0,
            "fase_venta_activa": False, "destino": DESTINO_MERCADO, "ruta_tipo": "",
            "costo_transporte": 0.0, "premium": False, "tax_rate": 0.08,
        }

        self.bind_all("<Button-1>", self.quitar_foco_clic)
        self.bind("<Escape>", lambda e: self.focus_set())
        self.bind_all("<Control-a>", self.global_select_all)
        self.bind_all("<Control-A>", self.global_select_all)

        self._anim_tick = 0

        self.show_auth_screen()

    def quitar_foco_clic(self, event):
        try:
            widget = event.widget
            if not isinstance(widget, (tk.Entry, tk.Text, tk.Listbox)):
                self.focus_set()
        except Exception:
            pass

    def global_select_all(self, event):
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

    # ------------------------------------------------------------------ #
    # VENTANAS PROPIAS EN VEZ DE LOS CUADROS FEOS DE WINDOWS/LINUX
    # ------------------------------------------------------------------ #
    def _centrar_ventana(self, win, ancho, alto):
        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - (ancho // 2)
            y = self.winfo_y() + (self.winfo_height() // 2) - (alto // 2)
            win.geometry(f"{ancho}x{alto}+{max(x,0)}+{max(y,0)}")
        except Exception:
            pass

    def _ventana_emergente(self, titulo, color_borde):
        win = ctk.CTkToplevel(self)
        win.title(titulo)
        win.configure(fg_color="#12151f")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.transient(self)
        borde = ctk.CTkFrame(win, fg_color="#12151f", border_color=color_borde, border_width=2, corner_radius=14)
        borde.pack(fill="both", expand=True, padx=3, pady=3)
        return win, borde

    def _dialogo(self, titulo, mensaje, icono, color):
        win, borde = self._ventana_emergente(titulo, color)
        ctk.CTkLabel(borde, text=icono, font=(self.F_DISPLAY, 30, "bold"), text_color=color).pack(pady=(22, 6))
        ctk.CTkLabel(borde, text=titulo, font=(self.F_DISPLAY, 15, "bold"), text_color="#eef1f8").pack(pady=(0, 10), padx=20)
        ctk.CTkLabel(borde, text=mensaje, font=(self.F_BODY, 13), text_color="#c7cede",
                     wraplength=380, justify="left").pack(padx=25, pady=(0, 20))
        btn = ctk.CTkButton(borde, text="Listo", fg_color=color, text_color="#12141c",
                             font=(self.F_BODY, 13, "bold"), width=140, command=win.destroy)
        btn.pack(pady=(0, 22))
        self._centrar_ventana(win, 440, 260)
        win.grab_set()

    def dialogo_info(self, titulo, mensaje):
        self._dialogo(titulo, mensaje, "i", ACCENT_ICE)

    def dialogo_warning(self, titulo, mensaje):
        self._dialogo(titulo, mensaje, "!", "#e3a53c")

    def dialogo_error(self, titulo, mensaje):
        self._dialogo(titulo, mensaje, "x", "#ef5350")

    def confirmar_dialogo(self, titulo, mensaje):
        resultado = {"ok": False}
        win, borde = self._ventana_emergente(titulo, "#ef5350")

        def _si():
            resultado["ok"] = True
            win.destroy()

        def _no():
            resultado["ok"] = False
            win.destroy()

        ctk.CTkLabel(borde, text="!", font=(self.F_DISPLAY, 30, "bold"), text_color="#ef5350").pack(pady=(22, 6))
        ctk.CTkLabel(borde, text=titulo, font=(self.F_DISPLAY, 15, "bold"), text_color="#eef1f8").pack(pady=(0, 10), padx=20)
        ctk.CTkLabel(borde, text=mensaje, font=(self.F_BODY, 13), text_color="#c7cede",
                     wraplength=380, justify="left").pack(padx=25, pady=(0, 20))
        fila_btns = ctk.CTkFrame(borde, fg_color="transparent")
        fila_btns.pack(pady=(0, 22))
        ctk.CTkButton(fila_btns, text="Si, dale", fg_color="#ef5350", text_color="#eef1f8",
                      font=(self.F_BODY, 13, "bold"), width=130, command=_si).pack(side="left", padx=8)
        ctk.CTkButton(fila_btns, text="No, cancelar", fg_color="#1e2536", text_color="#eef1f8",
                      font=(self.F_BODY, 13, "bold"), width=130, command=_no).pack(side="left", padx=8)
        self._centrar_ventana(win, 440, 280)
        win.grab_set()
        win.wait_window()
        return resultado["ok"]

    def bind_mousewheel_recursive(self, widget, scrollable_frame, orient="y"):
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
        """
        Convierte lo que escribiste a número, a prueba de balas. Además
        entiende abreviaturas de jugador: "500k" -> 500000, "1.5m" o
        "1.5kk" -> 1500000. Las comas se tratan como separador de miles
        (se ignoran), no como coma decimal.
        """
        try:
            texto = str(value).strip().lower()
            if texto == "":
                return 0.0
            texto = texto.replace(",", "")
            multiplicador = 1.0
            if texto.endswith("kk"):
                multiplicador = 1_000_000.0
                texto = texto[:-2]
            elif texto.endswith("m"):
                multiplicador = 1_000_000.0
                texto = texto[:-1]
            elif texto.endswith("k"):
                multiplicador = 1_000.0
                texto = texto[:-1]
            texto = texto.strip()
            if texto == "":
                return 0.0
            return float(texto) * multiplicador
        except (ValueError, TypeError, AttributeError):
            return 0.0

    def formatear_numero_visual(self, widget):
        """
        Idea nueva: al salir de un campo de plata, si escribiste algo como
        "500k" o "1.5m", lo traduce solo al número completo con comas de
        miles ("500,000"). Así no hace falta tipear el número completo a
        mano. Si el campo está en modo solo-lectura (readonly), lo habilita
        un instante para poder reescribirlo y lo vuelve a dejar como estaba.
        """
        try:
            estado_previo = widget.cget("state")
        except Exception:
            estado_previo = "normal"
        texto_actual = widget.get()
        if texto_actual.strip() == "":
            return
        valor = self.get_float(texto_actual)
        nuevo_texto = f"{int(valor):,}"
        if nuevo_texto == texto_actual:
            return
        if estado_previo == "readonly":
            widget.configure(state="normal")
        widget.delete(0, "end")
        widget.insert(0, nuevo_texto)
        if estado_previo == "readonly":
            widget.configure(state="readonly")

    def set_display_text(self, widget, text, color=None):
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

        lbl_region = ctk.CTkLabel(self.auth_frame, text="Región del servidor", font=(self.F_BODY, 15, "bold"), text_color="#eef1f8")
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
            self.dialogo_error("Error", "Faltan campos por completar.")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
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
                self.dialogo_error("Error", "La contraseña no coincide para ese personaje en esta región.")
        else:
            try:
                cursor.execute("INSERT INTO usuarios (username, region, password, premium) VALUES (?, ?, ?, 0)", (username, region, password))
                conn.commit()
            except sqlite3.IntegrityError:
                self.dialogo_error("Error", "Ese nombre ya está en uso en esta región.")
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
        try:
            self.show_main_hud()
        except Exception as ex:
            import traceback
            detalle = traceback.format_exc()
            print(detalle)
            self.dialogo_error(
                "Se rompió algo al abrir la app",
                f"Pasó esto:\n\n{ex}\n\n"
                "Fijate en la consola/terminal para ver el detalle completo."
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

        self.lbl_live_dot = ctk.CTkLabel(self.header_frame, text="●", font=(self.F_MONO, 14, "bold"), text_color="#3ddc84")
        self.lbl_live_dot.pack(side="left", padx=(10, 0), pady=30)

        self.lbl_live_clock = ctk.CTkLabel(self.header_frame, text="", font=(self.F_MONO, 14, "bold"), text_color="#48c9dc")
        self.lbl_live_clock.pack(side="left", padx=(6, 50), pady=30)
        self.update_live_clock()

        self.btn_backup = ctk.CTkButton(self.header_frame, text="Backup DB", width=110, fg_color="#1e2536",
                                         hover_color=ACCENT_PURPLE, font=(self.F_BODY, 13, "bold"),
                                         command=self.backup_database)
        self.btn_backup.pack(side="right", padx=5, pady=30)

        self.btn_cuentas = ctk.CTkButton(self.header_frame, text="Cuentas", width=110, fg_color="#1e2536",
                                          hover_color=ACCENT_TEAL, font=(self.F_BODY, 13, "bold"),
                                          command=self.comparar_cuentas)
        self.btn_cuentas.pack(side="right", padx=5, pady=30)

        self.btn_presentacion = ctk.CTkButton(self.header_frame, text="Modo Presentación", width=160, fg_color="#1e2536",
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
        ctk.CTkFrame(self.top_bar, fg_color="#232a3c", height=1, corner_radius=0).pack(fill="x", padx=18)
        row2 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row2.pack(fill="x")
        ctk.CTkFrame(self.top_bar, fg_color="#232a3c", height=1, corner_radius=0).pack(fill="x", padx=18)
        row3 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row3.pack(fill="x")
        ctk.CTkFrame(self.top_bar, fg_color="#232a3c", height=1, corner_radius=0).pack(fill="x", padx=18)
        row4 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row4.pack(fill="x")

        lbl_t1 = ctk.CTkLabel(row1, text="Inicio Carga/Compra:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_t1.pack(side="left", padx=(20, 10), pady=15)
        self.ent_start_time = ctk.CTkEntry(row1, placeholder_text="Ej: 2026-07-11 12:00", width=160, fg_color="#0d111c", text_color="#eef1f8")
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

        self.lbl_oc_expira = ctk.CTkLabel(row1, text="", font=(self.F_MONO, 13, "bold"), text_color="#e3a53c")
        self.lbl_oc_expira.pack(side="left", padx=(20, 10), pady=15)

        self.switch_premium = ctk.CTkSwitch(
            row2, text="Cuenta Premium (Impuesto Mercado Negro 8% → 4%)",
            font=(self.F_BODY, 13, "bold"), progress_color="#3ddc84",
            command=self.toggle_premium)
        self.switch_premium.pack(side="left", padx=(20, 30), pady=15)

        self.lbl_costo_transporte = ctk.CTkLabel(row2, text="Costo de Transporte a Hideout:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        self.ent_costo_transporte = ctk.CTkEntry(row2, placeholder_text="0", width=140, fg_color="#0d111c", text_color="#48c9dc", font=(self.F_MONO, 14, "bold"))
        self.ent_costo_transporte.insert(0, "0")
        self.ent_costo_transporte.bind("<KeyRelease>", lambda e: self.save_costo_transporte())
        self.ent_costo_transporte.bind("<FocusOut>", lambda e: self.formatear_numero_visual(self.ent_costo_transporte))

        lbl_mochila_section = ctk.CTkLabel(row2, text="Valor De La Mochila (Inventario):", font=(self.F_DISPLAY, 14, "bold"), text_color="#48c9dc")
        lbl_mochila_section.pack(side="right", padx=(10, 20), pady=15)

        self.ent_mochila_global = ctk.CTkEntry(row2, placeholder_text="0", fg_color="#0d111c", font=(self.F_MONO, 14, "bold"), text_color="#48c9dc", width=180)
        self.ent_mochila_global.insert(0, "0")
        self.ent_mochila_global.pack(side="right", padx=5, pady=15)
        self.ent_mochila_global.bind("<KeyRelease>", lambda e: self.guardar_valor_mochila())
        self.ent_mochila_global.bind("<FocusOut>", lambda e: self.formatear_numero_visual(self.ent_mochila_global))

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

        lbl_riesgo = ctk.CTkLabel(row3, text="Riesgo de Ruta:", font=(self.F_BODY, 14, "bold"), text_color="#97a2bd")
        lbl_riesgo.pack(side="right", padx=(10, 20), pady=15)
        self.sel_riesgo = ctk.CTkOptionMenu(
            row3, values=["Zona Segura", "Zona Amarilla", "Zona Roja", "Zona Negra"],
            command=lambda v: self.update_riesgo_ui(),
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color="#ef5350", dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", width=180)
        self.sel_riesgo.pack(side="right", padx=5, pady=15)
        self.lbl_riesgo_mult = ctk.CTkLabel(row3, text="", font=(self.F_MONO, 13, "bold"), text_color="#ef5350")
        self.lbl_riesgo_mult.pack(side="right", padx=(10, 5), pady=15)

        self.btn_checklist = ctk.CTkButton(row4, text="Checklist Pre-Viaje", fg_color="#1e2536", hover_color=ACCENT_LIME,
                                            text_color="#eef1f8", font=(self.F_BODY, 13, "bold"), width=180,
                                            command=self.abrir_checklist)
        self.btn_checklist.pack(side="left", padx=(20, 10), pady=15)

        self.lbl_eficiencia = ctk.CTkLabel(row4, text="", font=(self.F_MONO, 13, "bold"), text_color=ACCENT_TEAL)
        self.lbl_eficiencia.pack(side="right", padx=(10, 20), pady=15)

        # Cuadros de Contadores
        self.counters_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.counters_frame.pack(fill="x", padx=25, pady=(15, 0))

        _, self.card_budget = self.create_counter_card(self.counters_frame, "INVERSIÓN ÓRDENES DE COMPRA (+2.5% Setup)", "#e3a53c")
        _, self.card_bag_value = self.create_counter_card(self.counters_frame, "VALOR DE LA MOCHILA", "#48c9dc")
        _, self.card_status = self.create_counter_card(self.counters_frame, "PROFIT ESTIMADO (MOCHILA - INVERSIÓN)", "#eef1f8")
        self.card_pt_label, self.card_pt = self.create_counter_card(self.counters_frame, "PROFIT FINAL MERCADO NEGRO (-10.5% Imp.)", "#3ddc84")

        self.lbl_desglose = ctk.CTkLabel(self, text="", font=(self.F_BODY, 12), text_color="#97a2bd", anchor="w", justify="left")
        self.lbl_desglose.pack(fill="x", padx=33, pady=(6, 0))

        # ------------------------------------------------------------------ #
        # NAVEGACIÓN POR PÁGINAS COMPLETAS (idea nueva del jugador): cada
        # botón de acá abre una página entera a lo ancho de la ventana, en
        # vez de amontonar todo en un panel angosto al costado.
        # ------------------------------------------------------------------ #
        self.nav_bar = ctk.CTkFrame(self, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        self.nav_bar.pack(fill="x", padx=25, pady=(15, 10))

        self._pagina_botones = {}
        for nombre in PAGINAS:
            b = ctk.CTkButton(self.nav_bar, text=nombre, font=(self.F_BODY, 12, "bold"), height=34,
                               fg_color="#1c2130", text_color="#eef1f8", hover_color=ACCENT_PURPLE,
                               command=lambda n=nombre: self.mostrar_pagina(n))
            b.pack(side="left", padx=4, pady=8, fill="x", expand=True)
            self._pagina_botones[nombre] = b

        # Atajos Ctrl+1..9 para saltar directo a una página (las primeras 9).
        for i, nombre in enumerate(PAGINAS, start=1):
            if i > 9:
                break
            accion = lambda e, n=nombre: self.mostrar_pagina(n)
            for secuencia in (f"<Control-Key-{i}>", f"<Control-{i}>", f"<Control-KP_{i}>"):
                self.bind_all(secuencia, accion)

        # Contenedor de la página actual, a todo lo ancho de la ventana.
        self.content_area = ctk.CTkFrame(self, fg_color="transparent")
        self.content_area.pack(fill="both", expand=True, padx=25, pady=(0, 25))

        if self.is_premium:
            self.switch_premium.select()

        self.mostrar_pagina("📦 Mi Carga")
        self.update_peso_ui()
        self.update_riesgo_ui()

    def mostrar_pagina(self, nombre):
        """
        Limpia el área de contenido y construye ahí adentro la página
        elegida, a todo lo ancho de la ventana. Es la única forma de
        cambiar de vista en toda la app -- reemplaza al viejo sistema de
        secciones+sub-pestañas apretadas que se veía mal y se cortaba.
        """
        self.pagina_actual = nombre
        # Estos grupos de widgets solo existen mientras su página está
        # abierta; los "olvidamos" acá para no quedarnos con referencias a
        # widgets que ya van a ser destruidos.
        self.row_inputs = []
        self.essence_inputs = {}

        for w in self.content_area.winfo_children():
            w.destroy()

        for n, btn in self._pagina_botones.items():
            activo = (n == nombre)
            btn.configure(fg_color="#e3a53c" if activo else "#1c2130",
                           text_color="#12141c" if activo else "#eef1f8")

        if nombre == "📦 Mi Carga":
            self.build_pagina_mi_carga(self.content_area)
        elif nombre == "💎 Runas y Notas":
            self.build_pagina_runas_notas(self.content_area)
        elif nombre == "⚗ Refino":
            self.build_tab_refino(self.content_area)
        elif nombre == "✨ Encantar":
            self.build_tab_encantar(self.content_area)
        elif nombre == "🔄 Roundtrip":
            self.build_tab_roundtrip(self.content_area)
        elif nombre == "🧪 Materiales":
            self.build_tab_materiales(self.content_area)
        elif nombre == "📊 Historial":
            self.build_tab_historial(self.content_area)
            self.refresh_historial_tab()
        elif nombre == "🏦 Banco":
            self.build_tab_banco(self.content_area)
        elif nombre == "★ Favoritos":
            self.build_tab_favoritos(self.content_area)
        elif nombre == "🚀 Viaje":
            self.build_tab_viaje(self.content_area)

        self.calculate_metrics()
        self.actualizar_contador_estados()

    def update_live_clock(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        self.lbl_live_clock.configure(text=f"HORA LOCAL: {current_time}")
        if hasattr(self, "lbl_live_dot"):
            actual = self.lbl_live_dot.cget("text_color")
            nuevo_color = "#1c2130" if actual == "#3ddc84" else "#3ddc84"
            self.lbl_live_dot.configure(text_color=nuevo_color)
        self.update_oc_expira_label()
        self.actualizar_oc_por_fila()
        self.after(1000, self.update_live_clock)

    def animate_scan_bar(self):
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
    # PÁGINA: MI CARGA (la tabla de ítems, ahora a todo lo ancho)
    # ------------------------------------------------------------------ #
    def build_pagina_mi_carga(self, parent):
        panel = ctk.CTkFrame(parent, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        panel.pack(fill="both", expand=True)

        table_actions = ctk.CTkFrame(panel, fg_color="transparent")
        table_actions.pack(fill="x", padx=20, pady=15)

        self.lbl_manifest = ctk.CTkEntry(table_actions, width=280, font=(self.F_DISPLAY, 18, "bold"), text_color="#eef1f8",
                                         fg_color="transparent", border_width=0, justify="left")
        self.lbl_manifest.pack(side="left")

        self.sel_carga = ctk.CTkOptionMenu(
            table_actions, values=["Carga 1"], command=self.change_carga,
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color=ACCENT_PURPLE, dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 13, "bold"), text_color=ACCENT_PURPLE, width=150)
        self.sel_carga.pack(side="left", padx=(15, 5))

        btn_nueva_carga = ctk.CTkButton(table_actions, text="+ Carga", width=65, fg_color="#1e2536",
                                         hover_color=ACCENT_PURPLE, font=(self.F_BODY, 11, "bold"), height=26,
                                         command=self.crear_nueva_carga)
        btn_nueva_carga.pack(side="left", padx=(0, 4))

        btn_retirar_carga = ctk.CTkButton(table_actions, text="- Retirar", width=70, fg_color="#1e2536",
                                           hover_color="#ef5350", font=(self.F_BODY, 11, "bold"), height=26,
                                           command=self.retirar_carga_actual)
        btn_retirar_carga.pack(side="left", padx=(0, 10))

        self.btn_fase = ctk.CTkButton(table_actions, text="Pasar a Venta", fg_color="#e3a53c", text_color="#12141c", font=(self.F_BODY, 11, "bold"), width=120, height=26, command=self.activar_fase_venta)
        self.btn_fase.pack(side="right", padx=5)

        self.btn_regresar = ctk.CTkButton(table_actions, text="Cancelar Venta", fg_color="#ef5350", text_color="#eef1f8", font=(self.F_BODY, 11, "bold"), width=120, height=26, command=self.regresar_fase_compra)

        btn_add = ctk.CTkButton(table_actions, text="+ Meter Ítem", fg_color="#48c9dc", text_color="#12141c", font=(self.F_DISPLAY, 13, "bold"), width=110, command=self.add_item_row)
        btn_add.pack(side="right", padx=5)

        btn_pdf = ctk.CTkButton(table_actions, text="Generar PDF", fg_color="#3ddc84", text_color="#12141c", font=(self.F_DISPLAY, 13, "bold"), width=140, command=self.export_to_pdf)
        btn_pdf.pack(side="right", padx=5)

        btn_csv = ctk.CTkButton(table_actions, text="Exportar CSV", fg_color="#97a2bd", text_color="#12141c", font=(self.F_DISPLAY, 13, "bold"), width=130, command=self.export_to_csv)
        btn_csv.pack(side="right", padx=5)

        btn_vista_gremio = ctk.CTkButton(table_actions, text="Vista Gremio", fg_color=ACCENT_TEAL, text_color="#12141c", font=(self.F_DISPLAY, 12, "bold"), width=120, command=self.exportar_vista_gremio)
        btn_vista_gremio.pack(side="right", padx=5)

        btn_archivar = ctk.CTkButton(table_actions, text="Archivar al Historial", fg_color="#e3a53c", text_color="#12141c", font=(self.F_DISPLAY, 12, "bold"), width=170, command=self.archivar_carga)
        btn_archivar.pack(side="right", padx=5)

        table_toolbar2 = ctk.CTkFrame(panel, fg_color="transparent")
        table_toolbar2.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(table_toolbar2, text="Buscar:", font=(self.F_BODY, 14), text_color="#97a2bd").pack(side="left", padx=(0, 4))
        self.ent_buscar_item = ctk.CTkEntry(table_toolbar2, placeholder_text="Nombre del ítem...", width=260,
                                             fg_color="#0d111c", text_color="#eef1f8")
        self.ent_buscar_item.pack(side="left")
        self.ent_buscar_item.bind("<KeyRelease>", lambda e: self.filtrar_tabla())

        btn_copiar_lista = ctk.CTkButton(table_toolbar2, text="Copiar Lista", width=120, fg_color="#1e2536",
                                          hover_color=ACCENT_ICE, text_color="#eef1f8", font=(self.F_BODY, 12, "bold"),
                                          command=self.copiar_lista_portapapeles)
        btn_copiar_lista.pack(side="left", padx=(10, 0))

        self.lbl_contador_estados = ctk.CTkLabel(table_toolbar2, text="", font=(self.F_MONO, 13, "bold"), text_color="#97a2bd")
        self.lbl_contador_estados.pack(side="right", padx=5)

        table_toolbar3 = ctk.CTkFrame(panel, fg_color="transparent")
        table_toolbar3.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(table_toolbar3, text="Filtro rápido:", font=(self.F_BODY, 12, "bold"), text_color="#97a2bd").pack(side="left", padx=(0, 8))
        self.filtro_estado_actual = "Todo"
        self._botones_filtro_estado = {}
        for etiqueta, estado_clave, color in (
            ("Todo", "Todo", "#97a2bd"),
            ("Recibido", "✓ Recibido", "#3ddc84"),
            ("En Proceso", "⚙ En Proceso", "#e3a53c"),
            ("Cancelado", "✕ Cancelado", "#ef5350"),
        ):
            b = ctk.CTkButton(table_toolbar3, text=etiqueta, width=110, height=24, font=(self.F_BODY, 11, "bold"),
                               fg_color="#1c2130" if etiqueta != "Todo" else color, text_color="#eef1f8" if etiqueta != "Todo" else "#12141c",
                               hover_color=color, command=lambda e=estado_clave: self.aplicar_filtro_estado(e))
            b.pack(side="left", padx=3)
            self._botones_filtro_estado[estado_clave] = b

        self.table_scroll = ctk.CTkScrollableFrame(panel, fg_color="#0d111c", corner_radius=10)
        self.table_scroll.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        headers_frame = ctk.CTkFrame(self.table_scroll, fg_color="transparent")
        headers_frame.pack(fill="x", pady=(5, 10))
        headers = ["★", "Estado", "Nombre del Ítem", "Cant.", "Tier", "Valor Compra O/C", "Precio de Venta", "Margen %", "OC"]
        widths = [30, 130, 320, 70, 80, 170, 170, 100, 100]
        for h, w in zip(headers, widths):
            lbl = ctk.CTkLabel(headers_frame, text=h, font=(self.F_BODY, 14, "bold"), text_color="#97a2bd", width=w, anchor="w" if h not in ("Estado", "★") else "center")
            lbl.pack(side="left", padx=4)

        self.bind_mousewheel_recursive(self.table_scroll, self.table_scroll, orient="y")

        self.refrescar_selector_cargas()
        self.cargar_items_tabla()

    def cargar_items_tabla(self):
        """
        Trae de la base de datos los ítems de la carga/hub actual y arma
        las filas en la tabla. Se usa al construir la página 'Mi Carga' y
        cada vez que cambiás de hub o de carga.
        """
        if not hasattr(self, "table_scroll"):
            return
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
        conn.close()
        for item in items:
            self.create_row_ui(item)

        self.refresh_manifest_label()
        self.calculate_metrics()
        self.actualizar_contador_estados()

    # ------------------------------------------------------------------ #
    # PÁGINA: RUNAS Y NOTAS
    # ------------------------------------------------------------------ #
    def build_pagina_runas_notas(self, parent):
        columnas = ctk.CTkFrame(parent, fg_color="transparent")
        columnas.pack(fill="both", expand=True)

        izq = ctk.CTkFrame(columnas, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        izq.pack(side="left", fill="y", padx=(0, 15))

        der = ctk.CTkFrame(columnas, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        der.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(izq, text="Runas, Almas y Reliquias", font=(self.F_DISPLAY, 16, "bold"), text_color="#e3a53c").pack(pady=(18, 4), padx=20, anchor="w")
        ctk.CTkLabel(izq, text=f"Guardadas para {self.current_hub}", font=(self.F_BODY, 12), text_color="#97a2bd").pack(pady=(0, 12), padx=20, anchor="w")

        self.essence_scroll = ctk.CTkFrame(izq, fg_color="#0d111c", corner_radius=10)
        self.essence_scroll.pack(padx=20, pady=(0, 20))
        self.render_essence_inputs()

        ctk.CTkLabel(der, text="Inteligencia de Zona / Notas Extra", font=(self.F_DISPLAY, 16, "bold"), text_color="#eef1f8").pack(pady=(18, 4), padx=20, anchor="w")
        ctk.CTkLabel(der, text=f"Notas guardadas para {self.current_hub}", font=(self.F_BODY, 12), text_color="#97a2bd").pack(pady=(0, 12), padx=20, anchor="w")

        self.txt_notes = ctk.CTkTextbox(der, fg_color="#0d111c", font=(self.F_BODY, 15), border_color="#2a3142", border_width=1)
        self.txt_notes.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.txt_notes.bind("<KeyRelease>", lambda e: self.save_notes())

        self.cargar_esencias_y_notas()

    def cargar_esencias_y_notas(self):
        """
        Trae de la base de datos las esencias y las notas del hub actual y
        las pone en los campos. Se usa al construir la página 'Runas y
        Notas' y cada vez que cambiás de ciudad.
        """
        if not hasattr(self, "essence_inputs") or not self.essence_inputs:
            return
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        for t in range(4, 9):
            self.essence_inputs[f"r_t{t}"].delete(0, "end")
            self.essence_inputs[f"r_t{t}"].insert(0, "0")
            self.essence_inputs[f"a_t{t}"].delete(0, "end")
            self.essence_inputs[f"a_t{t}"].insert(0, "0")
            self.essence_inputs[f"re_t{t}"].delete(0, "end")
            self.essence_inputs[f"re_t{t}"].insert(0, "0")
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

        if hasattr(self, "txt_notes") and self.txt_notes.winfo_exists():
            cursor.execute("SELECT texto FROM notas WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
            nota = cursor.fetchone()
            self.txt_notes.delete("1.0", "end")
            if nota:
                self.txt_notes.insert("1.0", nota[0])
        conn.close()

    def create_row_ui(self, db_row=None):
        if db_row:
            db_id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, oc_timestamp = db_row
        else:
            db_id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, oc_timestamp = (
                None, "processing", "", 1, "", 0.0, 0.0, 0, datetime.now().strftime("%Y-%m-%d %H:%M")
            )

        row_bg = "#1c2130" if len(self.row_inputs) % 2 == 0 else "#181d29"
        row_frame = ctk.CTkFrame(self.table_scroll, fg_color=row_bg, corner_radius=8)
        row_frame.pack(fill="x", pady=5, padx=2)

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
            self.aplicar_bloqueo_precio_mn(self.row_inputs_lookup(sel))
            self.sync_and_calc()

        status_sel.configure(command=on_status_change)
        status_sel.pack(side="left", padx=4)

        ent_nombre = ctk.CTkEntry(row_frame, width=320, placeholder_text="Nombre Ítem...", fg_color="#0d111c", text_color="#eef1f8")
        ent_nombre.insert(0, nombre)
        ent_nombre.pack(side="left", padx=4)
        ent_nombre.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        ent_nombre.bind("<FocusOut>", lambda e, w=ent_nombre: self.corregir_nombre_item(w))

        ent_cant = ctk.CTkEntry(row_frame, width=70, fg_color="#0d111c", justify="center", text_color="#eef1f8")
        ent_cant.insert(0, str(cantidad))
        ent_cant.pack(side="left", padx=4)
        ent_cant.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_tier = ctk.CTkEntry(row_frame, width=80, placeholder_text="T6.1", fg_color="#0d111c", justify="center", text_color="#eef1f8")
        ent_tier.insert(0, tier)
        ent_tier.pack(side="left", padx=4)
        ent_tier.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_oc = ctk.CTkEntry(row_frame, width=170, fg_color="#0d111c", text_color="#48c9dc")
        ent_oc.insert(0, f"{int(valor_oc):,}")
        ent_oc.pack(side="left", padx=4)
        ent_oc.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        ent_oc.bind("<FocusOut>", lambda e, w=ent_oc: (self.formatear_numero_visual(w), self.sync_and_calc()))

        ent_mn = ctk.CTkEntry(row_frame, width=170, fg_color="#232838", text_color="#3ddc84", state="readonly")
        ent_mn.configure(state="normal")
        ent_mn.insert(0, f"{int(precio_mn):,}")
        ent_mn.configure(state="readonly")
        ent_mn.pack(side="left", padx=4)
        ent_mn.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        ent_mn.bind("<FocusOut>", lambda e, w=ent_mn: (self.formatear_numero_visual(w), self.sync_and_calc()))

        lbl_margen = ctk.CTkLabel(row_frame, text="---", width=100, font=(self.F_MONO, 13, "bold"), text_color="#97a2bd", anchor="center")
        lbl_margen.pack(side="left", padx=4)

        lbl_oc_fila = ctk.CTkLabel(row_frame, text="", width=100, font=(self.F_MONO, 11, "bold"), text_color="#97a2bd", anchor="center")
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
        self.aplicar_bloqueo_precio_mn(row_data)

        for key, widget in (("nombre", ent_nombre), ("cantidad", ent_cant), ("tier", ent_tier), ("valor_oc", ent_oc), ("precio_mn", ent_mn)):
            widget.bind("<Up>", lambda e, w=widget: self.move_focus_table(w, -1, 0))
            widget.bind("<Down>", lambda e, w=widget: self.move_focus_table(w, 1, 0))
            widget.bind("<Left>", lambda e, w=widget: self.move_focus_table(w, 0, -1))
            widget.bind("<Right>", lambda e, w=widget: self.move_focus_table(w, 0, 1))

        self.bind_mousewheel_recursive(row_frame, self.table_scroll, orient="y")

    def row_inputs_lookup(self, status_widget):
        for row in self.row_inputs:
            if row["status"] is status_widget:
                return row
        return None

    def aplicar_bloqueo_precio_mn(self, row):
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
        if not self.row_inputs:
            self.dialogo_warning("Carga Vacía", "No tenés ningún ítem cargado.")
            return

        self.fase_venta_activa = True
        for row in self.row_inputs:
            self.aplicar_bloqueo_precio_mn(row)

        self.btn_fase.configure(text="Venta Activa", fg_color="#3ddc84")
        self.btn_regresar.pack(side="right", padx=5)
        self.sync_and_calc()

    def regresar_fase_compra(self):
        self.fase_venta_activa = False
        for row in self.row_inputs:
            self.aplicar_bloqueo_precio_mn(row)

        self.btn_fase.configure(text="Pasar a Venta", fg_color="#e3a53c")
        self.btn_regresar.pack_forget()
        self.sync_and_calc()

    def add_item_row(self):
        self.create_row_ui()
        self.sync_and_calc()
        self.actualizar_contador_estados()

    def render_essence_inputs(self):
        h_frame = ctk.CTkFrame(self.essence_scroll, fg_color="transparent")
        h_frame.pack(fill="x", pady=6, padx=6)
        ctk.CTkLabel(h_frame, text="T", width=40, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=4)
        ctk.CTkLabel(h_frame, text="Runas", width=110, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=4)
        ctk.CTkLabel(h_frame, text="Almas", width=110, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=4)
        ctk.CTkLabel(h_frame, text="Reliquias", width=110, font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(side="left", padx=4)

        for t in range(4, 9):
            row = ctk.CTkFrame(self.essence_scroll, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=6)

            ctk.CTkLabel(row, text=f"T{t}", width=40, font=(self.F_DISPLAY, 14, "bold"), text_color="#e3a53c").pack(side="left", padx=4)

            r_in = ctk.CTkEntry(row, width=110, fg_color="#0d111c", justify="center", text_color="#eef1f8")
            r_in.insert(0, "0")
            r_in.pack(side="left", padx=4)
            r_in.bind("<KeyRelease>", lambda e: self.save_esencias_and_calc())

            a_in = ctk.CTkEntry(row, width=110, fg_color="#0d111c", justify="center", text_color="#eef1f8")
            a_in.insert(0, "0")
            a_in.pack(side="left", padx=4)
            a_in.bind("<KeyRelease>", lambda e: self.save_esencias_and_calc())

            re_in = ctk.CTkEntry(row, width=110, fg_color="#0d111c", justify="center", text_color="#eef1f8")
            re_in.insert(0, "0")
            re_in.pack(side="left", padx=4)
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
        if not hasattr(self, "lbl_contador_estados") or not self.lbl_contador_estados.winfo_exists():
            return
        recibidos = sum(1 for r in self.row_inputs if r["status"].get() == "✓ Recibido")
        procesos = sum(1 for r in self.row_inputs if r["status"].get() == "⚙ En Proceso")
        cancelados = sum(1 for r in self.row_inputs if r["status"].get() == "✕ Cancelado")
        self.lbl_contador_estados.configure(
            text=f"{recibidos} Recibidos   {procesos} En Proceso   {cancelados} Cancelados"
        )

    def filtrar_tabla(self):
        texto = normalizar_texto(self.ent_buscar_item.get())
        filtro = getattr(self, "filtro_estado_actual", "Todo")
        for row in self.row_inputs:
            nombre_norm = normalizar_texto(row["nombre"].get())
            coincide_texto = (texto == "" or texto in nombre_norm)
            coincide_estado = (filtro == "Todo" or row["status"].get() == filtro)
            if coincide_texto and coincide_estado:
                row["frame"].pack(fill="x", pady=5, padx=2)
            else:
                row["frame"].pack_forget()

    def aplicar_filtro_estado(self, estado_clave):
        self.filtro_estado_actual = estado_clave
        colores = {"Todo": "#97a2bd", "✓ Recibido": "#3ddc84", "⚙ En Proceso": "#e3a53c", "✕ Cancelado": "#ef5350"}
        for clave, btn in self._botones_filtro_estado.items():
            activo = (clave == estado_clave)
            btn.configure(fg_color=colores[clave] if activo else "#1c2130",
                           text_color="#12141c" if activo else "#eef1f8")
        self.filtrar_tabla()

    def copiar_lista_portapapeles(self):
        lineas = []
        for row in self.row_inputs:
            if row["status"].get() == "✕ Cancelado":
                continue
            nombre = row["nombre"].get().strip() or "(sin nombre)"
            cantidad = row["cantidad"].get()
            tier = row["tier"].get().strip()
            tier_txt = f" {tier}" if tier else ""
            lineas.append(f"- {cantidad} {nombre}{tier_txt}")

        if not lineas:
            self.dialogo_warning("Nada para copiar", "No hay ítems activos en la tabla.")
            return

        texto = "\n".join(lineas)
        try:
            self.clipboard_clear()
            self.clipboard_append(texto)
            self.dialogo_info("Copiado", f"Se copió tu lista de {len(lineas)} ítem(s) al portapapeles.")
        except Exception as ex:
            self.dialogo_error("Error", f"No se pudo copiar al portapapeles:\n{ex}")

    def corregir_nombre_item(self, entry_widget):
        texto_actual = entry_widget.get().strip()
        if not texto_actual:
            return
        texto_formateado = " ".join(p.capitalize() if p.isalpha() else p for p in texto_actual.split(" "))

        normalizado = normalizar_texto(texto_formateado)
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT nombre FROM banco_items WHERE user_id = ?", (self.current_user_id,))
        nombres_previos = [r[0] for r in c.fetchall()]
        conn.close()

        texto_final = texto_formateado
        for nombre_previo in nombres_previos:
            if normalizar_texto(nombre_previo) == normalizado:
                texto_final = nombre_previo
                break

        if texto_final != texto_actual:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, texto_final)
            self.sync_and_calc()

    def save_esencias_and_calc(self):
        if not self.essence_inputs:
            return
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
        if hasattr(self, "lbl_costo_materiales"):
            self.calcular_costo_materiales()

    def save_notes(self):
        if not hasattr(self, "txt_notes") or not self.txt_notes.winfo_exists():
            return
        texto = self.txt_notes.get("1.0", "end-1c")
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO notas (user_id, hub, texto) VALUES (?, ?, ?)", (self.current_user_id, self.current_hub, texto))
        conn.commit()
        conn.close()

    def delete_row(self, db_id, frame_widget):
        nombre_item = ""
        for row in self.row_inputs:
            if row["db_id"] == db_id and row["frame"] is frame_widget:
                nombre_item = row["nombre"].get().strip()
                break
        etiqueta = f'"{nombre_item}"' if nombre_item else "este ítem"
        if not self.confirmar_dialogo("Confirmar Borrado", f"¿Seguro que querés borrar {etiqueta}? Esto no se puede deshacer."):
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
        self.load_hub_data()

    def load_hub_data(self):
        """
        Función central que se llama cada vez que cambiás de hub o de
        carga: recarga la config de esa ciudad (destino/ruta/mochila/etc,
        que siempre están visibles arriba) y reconstruye la página que
        tengas abierta en ese momento con los datos correctos.
        """
        self.cargar_config_hub_actual()
        self.mostrar_pagina(self.pagina_actual)

    def cargar_config_hub_actual(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT destino, ruta_tipo, costo_transporte, valor_mochila FROM config_hub WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
        cfg = c.fetchone()
        conn.close()
        if cfg:
            destino_g, ruta_g, costo_g, mochila_g = cfg
        else:
            destino_g, ruta_g, costo_g, mochila_g = DESTINO_MERCADO, "", 0.0, 0.0

        self.current_destino = destino_g or DESTINO_MERCADO
        self.sel_destino.set(self.current_destino)

        if self.current_hub == "Caerleon":
            self.lbl_ruta.pack_forget()
            self.sel_ruta.pack_forget()
            self.current_ruta_tipo = ""
        else:
            opciones_ruta = [self.current_hub, f"{self.current_hub} Portal"]
            self.sel_ruta.configure(values=opciones_ruta)
            self.current_ruta_tipo = ruta_g if ruta_g in opciones_ruta else self.current_hub
            self.sel_ruta.set(self.current_ruta_tipo)
            self.lbl_ruta.pack(side="left", padx=(20, 10), pady=15)
            self.sel_ruta.pack(side="left", padx=5, pady=15)

        self.ent_costo_transporte.delete(0, "end")
        self.ent_costo_transporte.insert(0, f"{int(costo_g):,}")
        self.ent_mochila_global.delete(0, "end")
        self.ent_mochila_global.insert(0, f"{int(mochila_g):,}")
        if hasattr(self, "ent_impuesto_retorno") and self.ent_impuesto_retorno.winfo_exists():
            self.ent_impuesto_retorno.delete(0, "end")
            self.ent_impuesto_retorno.insert(0, "0")
        self.update_destino_ui()
        self.refresh_manifest_label()

    def refresh_manifest_label(self):
        if not hasattr(self, "lbl_manifest") or not self.lbl_manifest.winfo_exists():
            return
        ruta_txt = f" ({self.current_ruta_tipo})" if self.current_ruta_tipo and self.current_hub != "Caerleon" else ""
        self.set_display_text(self.lbl_manifest, f"Ruta: {self.current_hub}{ruta_txt} → {self.current_destino}")

    # ------------------------------------------------------------------ #
    # CARGAS PARALELAS DENTRO DEL MISMO HUB
    # ------------------------------------------------------------------ #
    def refrescar_selector_cargas(self):
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
        if hasattr(self, "sel_carga") and self.sel_carga.winfo_exists():
            self.sel_carga.configure(values=nombres)
            self.sel_carga.set(self.current_carga)

    def change_carga(self, nombre):
        self.current_carga = nombre
        self.fase_venta_activa = False
        self.cargar_items_tabla()

    def crear_nueva_carga(self):
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
            self.dialogo_warning("Ya existe", f"Ya hay una carga llamada '{nombre}' en este hub.")
            conn.close()
            return
        conn.close()
        self.current_carga = nombre
        self.fase_venta_activa = False
        self.refrescar_selector_cargas()
        self.cargar_items_tabla()

    def retirar_carga_actual(self):
        nombre_a_borrar = self.current_carga
        if not self.confirmar_dialogo(
            "Retirar Carga",
            f"¿Seguro? Esto borra TODOS los ítems de '{nombre_a_borrar}' en {self.current_hub}. No se puede deshacer."
        ):
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM inventario WHERE user_id = ? AND hub = ? AND carga_nombre = ?",
                  (self.current_user_id, self.current_hub, nombre_a_borrar))
        c.execute("DELETE FROM cargas_meta WHERE user_id = ? AND hub = ? AND carga_nombre = ?",
                  (self.current_user_id, self.current_hub, nombre_a_borrar))
        conn.commit()
        conn.close()

        self.fase_venta_activa = False
        self.refrescar_selector_cargas()
        self.cargar_items_tabla()
        self.dialogo_info("Carga Retirada", f"Listo, '{nombre_a_borrar}' ya no existe.")

    def actualizar_oc_por_fila(self):
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
                lbl.configure(text="Expiró", text_color="#ef5350")
            else:
                horas = int(restante.total_seconds() // 3600)
                minutos = int((restante.total_seconds() % 3600) // 60)
                color = "#e3a53c" if restante.total_seconds() > 3600 else "#ef5350"
                lbl.configure(text=f"{horas}h {minutos}m", text_color=color)

            try:
                horas_transcurridas = (datetime.now() - inicio).total_seconds() / 3600
                atascada = row["status"].get() == "⚙ En Proceso" and horas_transcurridas > HORAS_OC_ATASCADA
                frame = row.get("frame")
                if frame is not None and frame.winfo_exists():
                    if atascada:
                        frame.configure(fg_color="#3a2a12", border_color="#e3a53c", border_width=1)
                    else:
                        frame.configure(border_width=0)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # INICIO CARGA/COMPRA
    # ------------------------------------------------------------------ #
    def cargar_inicio_carga(self):
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
        nuevo = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.ent_start_time.delete(0, "end")
        self.ent_start_time.insert(0, nuevo)
        self.guardar_inicio_carga(nuevo)
        self.update_oc_expira_label()

    # ------------------------------------------------------------------ #
    # DESTINO / RUTA / PREMIUM
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

    def _guardar_config_hub(self, destino=None, ruta_tipo=None, costo_transporte=None, valor_mochila=None):
        if destino is None:
            destino = self.current_destino
        if ruta_tipo is None:
            ruta_tipo = self.current_ruta_tipo
        if costo_transporte is None:
            costo_transporte = self.get_float(self.ent_costo_transporte.get()) if hasattr(self, "ent_costo_transporte") else 0.0
        if valor_mochila is None:
            valor_mochila = self.get_float(self.ent_mochila_global.get()) if hasattr(self, "ent_mochila_global") else 0.0
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO config_hub (user_id, hub, destino, ruta_tipo, costo_transporte, valor_mochila)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, hub) DO UPDATE SET destino=excluded.destino, ruta_tipo=excluded.ruta_tipo, costo_transporte=excluded.costo_transporte, valor_mochila=excluded.valor_mochila
        ''', (self.current_user_id, self.current_hub, destino, ruta_tipo, costo_transporte, valor_mochila))
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

    def guardar_valor_mochila(self):
        self._guardar_config_hub(valor_mochila=self.get_float(self.ent_mochila_global.get()))
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
    # BACKUP DE DB
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
            self.dialogo_info("Backup Completo", f"Copia de seguridad guardada en:\n{destino}")
        except Exception as ex:
            self.dialogo_error("Error de Backup", f"No se pudo copiar la base de datos:\n{ex}")

    def restaurar_backup(self):
        try:
            origen = filedialog.askopenfilename(
                title="Restaurar Backup de la Base de Datos",
                filetypes=[("Base de datos SQLite", "*.db")]
            )
            if not origen:
                return
            confirmar = self.confirmar_dialogo(
                "Confirmar Restauración",
                "Esto reemplaza TODA tu base de datos actual por la del backup elegido. "
                "¿Seguro que querés seguir? Se recomienda cerrar y reabrir la app después."
            )
            if not confirmar:
                return
            shutil.copyfile(origen, DB_NAME)
            self.dialogo_info("Restauración Completa", "Backup restaurado. Cerrá y volvé a abrir la app para verlo reflejado.")
        except Exception as ex:
            self.dialogo_error("Error de Restauración", f"No se pudo restaurar el backup:\n{ex}")

    # ------------------------------------------------------------------ #
    # EXPORTAR A CSV/EXCEL
    # ------------------------------------------------------------------ #
    def export_to_csv(self):
        if not self.row_inputs:
            self.dialogo_warning("CSV Vacío", "No hay datos en la tabla para exportar.")
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
            self.dialogo_info("CSV Exportado", f"Archivo guardado exitosamente en:\n{filename}")
            try:
                self.archivar_carga_silenciosa()
            except Exception:
                pass
            self.vaciar_carga_actual()
            self.reiniciar_inicio_carga()
        except Exception as ex:
            self.dialogo_error("Error CSV", f"No se pudo exportar:\n{ex}")

    # ------------------------------------------------------------------ #
    # PESO DE CARGA VS CAPACIDAD DE MONTURA
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
            texto = f"{pct:.0f}% de carga"
        elif pct <= 140:
            color = "#e3a53c"
            texto = f"{pct:.0f}% sobrecarga (más lento)"
        else:
            color = "#ef5350"
            texto = f"{pct:.0f}% no podés moverte"
        self.lbl_peso_status.configure(text=texto, text_color=color)

    # ------------------------------------------------------------------ #
    # ETIQUETA DE RIESGO POR RUTA
    # ------------------------------------------------------------------ #
    def update_riesgo_ui(self):
        riesgo = self.sel_riesgo.get()
        multiplicadores = {
            "Zona Segura": "Riesgo x1 (sin PvP)",
            "Zona Amarilla": "Riesgo x2 (flag PvP opcional)",
            "Zona Roja": "Riesgo x4 (PvP total, sin fama al morir)",
            "Zona Negra": "Riesgo x8 (PvP total + gremios hostiles)",
        }
        self.lbl_riesgo_mult.configure(text=multiplicadores.get(riesgo, ""))

    # ------------------------------------------------------------------ #
    # AVISO DE EXPIRACIÓN DE ÓRDENES DE COMPRA (24H)
    # ------------------------------------------------------------------ #
    def update_oc_expira_label(self):
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
            self.lbl_oc_expira.configure(text="Órdenes de Compra expiradas", text_color="#ef5350")
        else:
            horas = int(restante.total_seconds() // 3600)
            minutos = int((restante.total_seconds() % 3600) // 60)
            color = "#e3a53c" if restante.total_seconds() > 3600 else "#ef5350"
            self.lbl_oc_expira.configure(text=f"OC expira en {horas}h {minutos}m", text_color=color)

    # ------------------------------------------------------------------ #
    # PÁGINA: VIAJE (Pánico, Impuesto de Retorno, Comparador Premium)
    # ------------------------------------------------------------------ #
    def build_tab_viaje(self, parent):
        ctk.CTkLabel(parent, text="Antes y Durante el Viaje", font=(self.F_DISPLAY, 18, "bold"), text_color="#e3a53c").pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text="Todo lo que sirve mirar antes de salir con la carga, o si te la pasás mal en el camino.",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left").pack(padx=10, pady=(0, 15), anchor="w")

        cols = ctk.CTkFrame(parent, fg_color="transparent")
        cols.pack(fill="both", expand=True, padx=10)

        col_izq = ctk.CTkFrame(cols, fg_color="transparent")
        col_izq.pack(side="left", fill="both", expand=True, padx=(0, 10))
        col_der = ctk.CTkFrame(cols, fg_color="transparent")
        col_der.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # --- Botón de Pánico ---
        frame_panico = ctk.CTkFrame(col_izq, fg_color="#161a26", border_color="#ef5350", border_width=1, corner_radius=14)
        frame_panico.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(frame_panico, text="Si me matan, ¿cuánto pierdo?", font=(self.F_DISPLAY, 15, "bold"), text_color="#ef5350").pack(anchor="w", padx=15, pady=(15, 6))
        ctk.CTkLabel(frame_panico, text="Toma la inversión de la carga actual y tu profit promedio de las cargas archivadas, y te dice cuántos viajes buenos necesitás para recuperarte.",
                     font=(self.F_BODY, 12), text_color="#97a2bd", justify="left", wraplength=460).pack(anchor="w", padx=15, pady=(0, 10))
        btn_panico = ctk.CTkButton(frame_panico, text="Calcular Pérdida", fg_color="#ef5350", text_color="#eef1f8",
                                    font=(self.F_BODY, 13, "bold"), command=self.calcular_boton_panico)
        btn_panico.pack(padx=15, pady=(0, 10), anchor="w")
        self.lbl_panico_resultado = ctk.CTkLabel(frame_panico, text="", font=(self.F_MONO, 13, "bold"), text_color="#ef5350", justify="left", wraplength=460)
        self.lbl_panico_resultado.pack(anchor="w", padx=15, pady=(0, 15))

        # --- Impuesto de Retorno ---
        frame_retorno = ctk.CTkFrame(col_izq, fg_color="#161a26", border_color=ACCENT_ICE, border_width=1, corner_radius=14)
        frame_retorno.pack(fill="x")
        ctk.CTkLabel(frame_retorno, text="Impuesto de Retorno Rápido", font=(self.F_DISPLAY, 15, "bold"), text_color=ACCENT_ICE).pack(anchor="w", padx=15, pady=(15, 6))
        ctk.CTkLabel(frame_retorno, text="Si volvés rápido sin equipaje, el juego te cobra una tasa. Anotala acá y se descuenta del profit final de esta carga.",
                     font=(self.F_BODY, 12), text_color="#97a2bd", justify="left", wraplength=460).pack(anchor="w", padx=15, pady=(0, 10))
        ctk.CTkLabel(frame_retorno, text="Costo del retorno rápido:", font=(self.F_BODY, 12, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(0, 2))
        self.ent_impuesto_retorno = ctk.CTkEntry(frame_retorno, placeholder_text="0", fg_color="#0d111c", text_color=ACCENT_ICE, font=(self.F_MONO, 13, "bold"))
        self.ent_impuesto_retorno.insert(0, "0")
        self.ent_impuesto_retorno.pack(fill="x", padx=15, pady=(0, 15))
        self.ent_impuesto_retorno.bind("<KeyRelease>", lambda e: self.calculate_metrics())
        self.ent_impuesto_retorno.bind("<FocusOut>", lambda e: (self.formatear_numero_visual(self.ent_impuesto_retorno), self.calculate_metrics()))

        # --- Comparador de Premium ---
        frame_premium = ctk.CTkFrame(col_der, fg_color="#161a26", border_color=ACCENT_LIME, border_width=1, corner_radius=14)
        frame_premium.pack(fill="x")
        ctk.CTkLabel(frame_premium, text="Cuánto te ahorra el Premium", font=(self.F_DISPLAY, 15, "bold"), text_color=ACCENT_LIME).pack(anchor="w", padx=15, pady=(15, 6))
        self.lbl_premium_ahorro = ctk.CTkLabel(frame_premium, text="", font=(self.F_MONO, 13, "bold"), text_color=ACCENT_LIME, justify="left", wraplength=460)
        self.lbl_premium_ahorro.pack(anchor="w", padx=15, pady=(0, 15))

        self.calculate_metrics()

    def calcular_boton_panico(self):
        inversion_actual = self.last_metrics.get("inversion", 0.0)
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT AVG(profit_final) FROM historial_cargas WHERE user_id = ? AND profit_final > 0", (self.current_user_id,))
        row = c.fetchone()
        conn.close()
        profit_promedio = row[0] if row and row[0] else 0.0

        if inversion_actual <= 0:
            self.lbl_panico_resultado.configure(text="Todavía no metiste inversión en esta carga.")
            return
        if profit_promedio <= 0:
            self.lbl_panico_resultado.configure(
                text=(f"Si perdés esta carga, se van {int(inversion_actual):,} silver.\n"
                      "Todavía no tenés suficientes cargas con ganancia en tu Historial para estimar cuánto tardarías en recuperarte.")
            )
            return

        viajes_necesarios = inversion_actual / profit_promedio
        self.lbl_panico_resultado.configure(
            text=(f"Si te matan, perdés {int(inversion_actual):,} silver.\n"
                  f"Con tu profit promedio ({int(profit_promedio):,} silver/viaje), necesitás como {viajes_necesarios:.1f} viajes buenos para recuperarte.")
        )

    # ------------------------------------------------------------------ #
    # PÁGINA: REFINO
    # ------------------------------------------------------------------ #
    def build_tab_refino(self, parent):
        ctk.CTkLabel(parent, text="Calculadora de Refinado", font=(self.F_DISPLAY, 18, "bold"), text_color="#e3a53c").pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text="Ratio base: 5 materia prima por 1 refinado (T4 en adelante). El bonus de ciudad y de foco bajan cuánta materia prima gastás por unidad refinada.",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left", wraplength=900).pack(padx=10, pady=(0, 15), anchor="w")

        frame_in = ctk.CTkFrame(parent, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        frame_in.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(frame_in, text="Cantidad Refinado Deseado:", font=(self.F_BODY, 14, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(15, 2))
        self.ent_refino_cantidad = ctk.CTkEntry(frame_in, placeholder_text="Ej: 100", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_refino_cantidad.insert(0, "100")
        self.ent_refino_cantidad.pack(anchor="w", padx=15, pady=4)
        self.ent_refino_cantidad.bind("<KeyRelease>", lambda e: self.calcular_refino())

        ctk.CTkLabel(frame_in, text="Bonus de Ciudad (%):", font=(self.F_BODY, 14, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(8, 2))
        self.ent_refino_bonus_ciudad = ctk.CTkEntry(frame_in, placeholder_text="Ej: 44 (capital de ese recurso)", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_refino_bonus_ciudad.insert(0, "0")
        self.ent_refino_bonus_ciudad.pack(anchor="w", padx=15, pady=4)
        self.ent_refino_bonus_ciudad.bind("<KeyRelease>", lambda e: self.calcular_refino())

        ctk.CTkLabel(frame_in, text="Bonus de Foco/Especialización (%):", font=(self.F_BODY, 14, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(8, 2))
        self.ent_refino_bonus_foco = ctk.CTkEntry(frame_in, placeholder_text="Ej: 25 (foco 100% activo)", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_refino_bonus_foco.insert(0, "0")
        self.ent_refino_bonus_foco.pack(anchor="w", padx=15, pady=(4, 15))
        self.ent_refino_bonus_foco.bind("<KeyRelease>", lambda e: self.calcular_refino())

        self.lbl_refino_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 15, "bold"), text_color="#48c9dc", justify="left")
        self.lbl_refino_resultado.pack(padx=10, pady=20, anchor="w")

        self.calcular_refino()

    def calcular_refino(self):
        cantidad = self.get_float(self.ent_refino_cantidad.get())
        bonus_ciudad = self.get_float(self.ent_refino_bonus_ciudad.get()) / 100
        bonus_foco = self.get_float(self.ent_refino_bonus_foco.get()) / 100

        ratio_base = 5.0
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
    # PÁGINA: ENCANTAR (antes era una ventanita cortada, ahora es su
    # propia página completa así se lee bien y nada se corta)
    # ------------------------------------------------------------------ #
    def build_tab_encantar(self, parent):
        ctk.CTkLabel(parent, text="Requisitos de Encantado", font=(self.F_DISPLAY, 18, "bold"), text_color=ACCENT_GOLD).pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text="Cada nivel de encantamiento pide UNA esencia distinta, no las tres juntas: el .1 pide Runas, el .2 pide Almas, el .3 pide Reliquias. El número es el mismo para los tres niveles, solo cambia según el tipo de pieza.",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left", wraplength=1000).pack(padx=10, pady=(0, 15), anchor="w")

        cols = ctk.CTkFrame(parent, fg_color="transparent")
        cols.pack(fill="x", padx=10)

        for i, (nombre_cat, costo, color) in enumerate(ENCANTE_COSTOS):
            card = ctk.CTkFrame(cols, fg_color="#161a26", border_color=color, border_width=1, corner_radius=14)
            card.grid(row=0, column=i, padx=8, pady=5, sticky="nsew")
            cols.grid_columnconfigure(i, weight=1)
            strip = ctk.CTkFrame(card, fg_color=color, height=3, corner_radius=0)
            strip.pack(fill="x", side="top")
            ctk.CTkLabel(card, text=nombre_cat, font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", wraplength=220, justify="left").pack(anchor="w", padx=14, pady=(12, 8))

            for nivel, esencia in ((".1", "Runas"), (".2", "Almas"), (".3", "Reliquias")):
                fila = ctk.CTkFrame(card, fg_color="transparent")
                fila.pack(fill="x", padx=14, pady=2)
                ctk.CTkLabel(fila, text=f"Nivel {nivel} pide {esencia}:", font=(self.F_BODY, 12), text_color="#c7cede").pack(side="left")
                ctk.CTkLabel(fila, text=f"{costo:,}", font=(self.F_MONO, 13, "bold"), text_color=color).pack(side="right")
            ctk.CTkFrame(card, fg_color="transparent", height=10).pack()

        frame_calc = ctk.CTkFrame(parent, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        frame_calc.pack(fill="x", padx=10, pady=(20, 5))
        ctk.CTkLabel(frame_calc, text="Calculadora Rápida", font=(self.F_DISPLAY, 15, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(15, 8))

        fila_calc = ctk.CTkFrame(frame_calc, fg_color="transparent")
        fila_calc.pack(fill="x", padx=15, pady=(0, 15))

        col1 = ctk.CTkFrame(fila_calc, fg_color="transparent")
        col1.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ctk.CTkLabel(col1, text="Tipo de Equipo:", font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(anchor="w", pady=(0, 4))
        self.sel_encante_cat = ctk.CTkOptionMenu(
            col1, values=[c[0] for c in ENCANTE_COSTOS], command=lambda v: self.calcular_encantado(),
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color=ACCENT_GOLD, dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 12, "bold"))
        self.sel_encante_cat.pack(fill="x")

        col2 = ctk.CTkFrame(fila_calc, fg_color="transparent")
        col2.pack(side="left", fill="x", expand=True, padx=10)
        ctk.CTkLabel(col2, text="¿Hasta qué nivel?", font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(anchor="w", pady=(0, 4))
        self.sel_encante_nivel = ctk.CTkOptionMenu(
            col2, values=[".1 (solo Runas)", ".2 (Runas + Almas)", ".3 (Runas + Almas + Reliquias)"],
            command=lambda v: self.calcular_encantado(),
            fg_color="#1e2536", button_color="#232a3c", button_hover_color="#38445c",
            dropdown_fg_color="#12151f", dropdown_hover_color=ACCENT_GOLD, dropdown_text_color="#eef1f8",
            font=(self.F_BODY, 12, "bold"))
        self.sel_encante_nivel.set(".3 (Runas + Almas + Reliquias)")
        self.sel_encante_nivel.pack(fill="x")

        col3 = ctk.CTkFrame(fila_calc, fg_color="transparent")
        col3.pack(side="left", fill="x", expand=True, padx=(10, 0))
        ctk.CTkLabel(col3, text="Cantidad de Piezas:", font=(self.F_BODY, 13, "bold"), text_color="#97a2bd").pack(anchor="w", pady=(0, 4))
        self.ent_encante_piezas = ctk.CTkEntry(col3, fg_color="#0d111c", text_color="#eef1f8")
        self.ent_encante_piezas.insert(0, "1")
        self.ent_encante_piezas.pack(fill="x")
        self.ent_encante_piezas.bind("<KeyRelease>", lambda e: self.calcular_encantado())

        self.lbl_encante_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 14, "bold"), text_color=ACCENT_GOLD, justify="left")
        self.lbl_encante_resultado.pack(padx=10, pady=20, anchor="w")

        self.calcular_encantado()

    def calcular_encantado(self):
        cat = self.sel_encante_cat.get()
        costo_base = next((costo for nombre, costo, color in ENCANTE_COSTOS if nombre == cat), 0)
        piezas = max(int(self.get_float(self.ent_encante_piezas.get())), 0)
        nivel_txt = self.sel_encante_nivel.get()
        total_por_esencia = costo_base * piezas

        lineas = [f"Para {piezas} pieza(s) de '{cat}':"]
        if nivel_txt.startswith(".1") or nivel_txt.startswith(".2") or nivel_txt.startswith(".3"):
            lineas.append(f"Runas necesarias (nivel .1): {total_por_esencia:,}")
        if nivel_txt.startswith(".2") or nivel_txt.startswith(".3"):
            lineas.append(f"Almas necesarias (nivel .2): {total_por_esencia:,}")
        if nivel_txt.startswith(".3"):
            lineas.append(f"Reliquias necesarias (nivel .3): {total_por_esencia:,}")

        self.lbl_encante_resultado.configure(text="\n".join(lineas))

    # ------------------------------------------------------------------ #
    # PÁGINA: ROUNDTRIP
    # ------------------------------------------------------------------ #
    def build_tab_roundtrip(self, parent):
        ctk.CTkLabel(parent, text="Simulador de Roundtrip", font=(self.F_DISPLAY, 18, "bold"), text_color="#e3a53c").pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text="Comprar directo al vendedor NPC (sin esperar Orden de Compra) y vender directo en el Mercado Negro. Sin el 2.5% de fee de setup.",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left", wraplength=900).pack(padx=10, pady=(0, 15), anchor="w")

        frame_in = ctk.CTkFrame(parent, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        frame_in.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(frame_in, text="Precio Compra NPC (por unidad):", font=(self.F_BODY, 14, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(15, 2))
        self.ent_rt_compra = ctk.CTkEntry(frame_in, placeholder_text="0", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_rt_compra.insert(0, "0")
        self.ent_rt_compra.pack(anchor="w", padx=15, pady=4)
        self.ent_rt_compra.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        ctk.CTkLabel(frame_in, text="Precio Venta Mercado Negro (por unidad):", font=(self.F_BODY, 14, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(8, 2))
        self.ent_rt_venta = ctk.CTkEntry(frame_in, placeholder_text="0", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_rt_venta.insert(0, "0")
        self.ent_rt_venta.pack(anchor="w", padx=15, pady=4)
        self.ent_rt_venta.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        ctk.CTkLabel(frame_in, text="Cantidad de Unidades:", font=(self.F_BODY, 14, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(8, 2))
        self.ent_rt_cantidad = ctk.CTkEntry(frame_in, placeholder_text="1", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_rt_cantidad.insert(0, "1")
        self.ent_rt_cantidad.pack(anchor="w", padx=15, pady=(4, 10))
        self.ent_rt_cantidad.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        self.switch_rt_premium = ctk.CTkSwitch(frame_in, text="Usar Premium en este cálculo (4% en vez de 8%)",
                                                font=(self.F_BODY, 12, "bold"), progress_color="#3ddc84",
                                                command=self.calcular_roundtrip)
        self.switch_rt_premium.pack(padx=15, pady=(0, 15), anchor="w")

        self.lbl_rt_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 15, "bold"), text_color="#3ddc84", justify="left")
        self.lbl_rt_resultado.pack(padx=10, pady=20, anchor="w")

        self.calcular_roundtrip()

    def calcular_roundtrip(self):
        compra = self.get_float(self.ent_rt_compra.get())
        venta = self.get_float(self.ent_rt_venta.get())
        cantidad = self.get_float(self.ent_rt_cantidad.get())
        tax_rate = 0.04 if self.switch_rt_premium.get() else 0.08
        ajuste_rate = 0.025

        inversion_total = compra * cantidad
        venta_bruta = venta * cantidad
        impuesto = venta_bruta * tax_rate
        ajuste = venta_bruta * ajuste_rate
        venta_neta = venta_bruta - impuesto - ajuste
        profit = venta_neta - inversion_total

        color = "#3ddc84" if profit >= 0 else "#ef5350"
        self.lbl_rt_resultado.configure(
            text=(f"Inversión total (NPC): {int(inversion_total):,} silver\n"
                  f"Venta neta (Mercado Negro): {int(venta_neta):,} silver\n"
                  f"Profit Roundtrip: {int(profit):,} silver"),
            text_color=color
        )

    # ------------------------------------------------------------------ #
    # PÁGINA: MATERIALES (lee las esencias DIRECTO de la base de datos,
    # así funciona sin importar si la página Runas y Notas está abierta)
    # ------------------------------------------------------------------ #
    def build_tab_materiales(self, parent):
        ctk.CTkLabel(parent, text="Costo de tus Materiales Guardados", font=(self.F_DISPLAY, 18, "bold"), text_color=ACCENT_PURPLE).pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text=f"Ponés cuánto vale cada Runa/Alma/Reliquia ahora mismo, y te digo cuánto silver tenés parado sin vender (según lo que anotaste para {self.current_hub}).",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left", wraplength=900).pack(padx=10, pady=(0, 15), anchor="w")

        frame_precios = ctk.CTkFrame(parent, fg_color="#161a26", border_color="#2a3142", border_width=1, corner_radius=14)
        frame_precios.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(frame_precios, text="Precio de 1 Runa:", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(15, 2))
        self.ent_precio_runa = ctk.CTkEntry(frame_precios, placeholder_text="0", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_precio_runa.insert(0, "0")
        self.ent_precio_runa.pack(anchor="w", padx=15, pady=4)
        self.ent_precio_runa.bind("<KeyRelease>", lambda e: self.calcular_costo_materiales())

        ctk.CTkLabel(frame_precios, text="Precio de 1 Alma:", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(8, 2))
        self.ent_precio_alma = ctk.CTkEntry(frame_precios, placeholder_text="0", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_precio_alma.insert(0, "0")
        self.ent_precio_alma.pack(anchor="w", padx=15, pady=4)
        self.ent_precio_alma.bind("<KeyRelease>", lambda e: self.calcular_costo_materiales())

        ctk.CTkLabel(frame_precios, text="Precio de 1 Reliquia:", font=(self.F_BODY, 13, "bold"), text_color="#eef1f8").pack(anchor="w", padx=15, pady=(8, 2))
        self.ent_precio_reliquia = ctk.CTkEntry(frame_precios, placeholder_text="0", fg_color="#0d111c", text_color="#eef1f8", width=300)
        self.ent_precio_reliquia.insert(0, "0")
        self.ent_precio_reliquia.pack(anchor="w", padx=15, pady=(4, 15))
        self.ent_precio_reliquia.bind("<KeyRelease>", lambda e: self.calcular_costo_materiales())

        btn_calc_mat = ctk.CTkButton(parent, text="Calcular con lo que tengo ahora", fg_color=ACCENT_PURPLE, text_color="#12141c",
                                      font=(self.F_BODY, 13, "bold"), command=self.calcular_costo_materiales)
        btn_calc_mat.pack(padx=10, pady=(15, 5), anchor="w")

        self.lbl_costo_materiales = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 14, "bold"), text_color=ACCENT_PURPLE, justify="left")
        self.lbl_costo_materiales.pack(padx=10, pady=15, anchor="w")

        self.calcular_costo_materiales()

    def calcular_costo_materiales(self):
        # Idea: lee las esencias directo de la base de datos en vez de los
        # campos en pantalla, así funciona esté abierta o no la página
        # "Runas y Notas" (que ahora es una página aparte y puede no estar
        # construida en este momento).
        precio_runa = self.get_float(self.ent_precio_runa.get())
        precio_alma = self.get_float(self.ent_precio_alma.get())
        precio_reliquia = self.get_float(self.ent_precio_reliquia.get())

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT runas, almas, reliquias FROM esencias WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
        filas = c.fetchall()
        conn.close()

        total_runas = sum(f[0] for f in filas)
        total_almas = sum(f[1] for f in filas)
        total_reliquias = sum(f[2] for f in filas)

        valor_runas = total_runas * precio_runa
        valor_almas = total_almas * precio_alma
        valor_reliquias = total_reliquias * precio_reliquia
        total = valor_runas + valor_almas + valor_reliquias

        self.lbl_costo_materiales.configure(
            text=(f"Tenés {total_runas} Runas ({int(valor_runas):,} silver)\n"
                  f"Tenés {total_almas} Almas ({int(valor_almas):,} silver)\n"
                  f"Tenés {total_reliquias} Reliquias ({int(valor_reliquias):,} silver)\n"
                  f"Total parado en el banco: {int(total):,} silver")
        )

    # ------------------------------------------------------------------ #
    # PÁGINA: HISTORIAL
    # ------------------------------------------------------------------ #
    def build_tab_historial(self, parent):
        ctk.CTkLabel(parent, text="Historial de Cargas Archivadas", font=(self.F_DISPLAY, 18, "bold"), text_color="#e3a53c").pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text="Usá el botón 'Archivar al Historial' en Mi Carga para guardar una foto de esta carga y compararla con las anteriores.",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left", wraplength=900).pack(padx=10, pady=(0, 10), anchor="w")

        self.lbl_resumen_periodo = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 13, "bold"), text_color=ACCENT_ICE, justify="left")
        self.lbl_resumen_periodo.pack(padx=10, pady=(0, 10), anchor="w")

        botones_hist = ctk.CTkFrame(parent, fg_color="transparent")
        botones_hist.pack(fill="x", padx=10, pady=(0, 10))
        btn_ranking = ctk.CTkButton(botones_hist, text="Ranking de Ítems", width=160, fg_color=ACCENT_GOLD, text_color="#12141c",
                                     font=(self.F_BODY, 12, "bold"), command=self.mostrar_ranking_items)
        btn_ranking.pack(side="left", padx=(0, 8))
        btn_fama = ctk.CTkButton(botones_hist, text="Salón de la Fama", width=160, fg_color=ACCENT_PINK, text_color="#12141c",
                                  font=(self.F_BODY, 12, "bold"), command=self.mostrar_salon_de_la_fama)
        btn_fama.pack(side="left", padx=8)
        btn_exp_hist = ctk.CTkButton(botones_hist, text="CSV del Historial", width=160, fg_color="#97a2bd", text_color="#12141c",
                                      font=(self.F_BODY, 12, "bold"), command=self.exportar_historial_csv)
        btn_exp_hist.pack(side="left", padx=8)

        self.grafico_crecimiento_canvas = tk.Canvas(parent, height=110, bg="#0d111c", highlightthickness=0, bd=0)
        self.grafico_crecimiento_canvas.pack(fill="x", padx=10, pady=(0, 10))

        self.historial_scroll = ctk.CTkScrollableFrame(parent, fg_color="#0d111c", corner_radius=10)
        self.historial_scroll.pack(fill="both", expand=True, padx=10, pady=5)
        self.bind_mousewheel_recursive(self.historial_scroll, self.historial_scroll, orient="y")

    def dibujar_grafico_crecimiento(self, registros):
        canvas = getattr(self, "grafico_crecimiento_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        canvas.delete("all")
        if not registros:
            canvas.create_text(10, 55, anchor="w", fill="#97a2bd", font=(self.F_BODY, 12),
                                text="Archivá cargas para ver tu curva de crecimiento acá.")
            return

        cronologico = list(reversed(registros))
        acumulado = []
        total = 0.0
        for hub, destino, fecha, inversion, venta_neta, profit in cronologico:
            total += profit
            acumulado.append(total)

        canvas.update_idletasks()
        w = canvas.winfo_width() or 1000
        h = 110
        margen = 15
        minimo = min(acumulado + [0])
        maximo = max(acumulado + [0])
        rango = (maximo - minimo) or 1

        n = len(acumulado)
        paso_x = (w - 2 * margen) / max(n - 1, 1)

        def y_de(valor):
            return h - margen - ((valor - minimo) / rango) * (h - 2 * margen)

        puntos = []
        for i, val in enumerate(acumulado):
            x = margen + i * paso_x
            y = y_de(val)
            puntos.append((x, y))

        color_linea = "#3ddc84" if acumulado[-1] >= 0 else "#ef5350"
        if len(puntos) > 1:
            flat = [coord for punto in puntos for coord in punto]
            canvas.create_line(*flat, fill=color_linea, width=2, smooth=True)
        for x, y in puntos:
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color_linea, outline="")

        canvas.create_text(w - margen, 15, anchor="ne", fill=color_linea, font=(self.F_MONO, 12, "bold"),
                            text=f"{int(acumulado[-1]):,} silver acumulado")

    def mostrar_salon_de_la_fama(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            SELECT nombre, SUM(profit_total) as total, AVG(profit_unidad) as promedio, SUM(cantidad) as unidades
            FROM banco_items WHERE user_id = ? AND estado != '✕ Cancelado'
            GROUP BY nombre HAVING SUM(cantidad) > 0
        ''', (self.current_user_id,))
        filas = c.fetchall()
        conn.close()

        if not filas:
            self.dialogo_info("Todavía sin Fama", "Necesitás archivar o exportar al menos una carga con ítems para que esto se llene.")
            return

        top_total = sorted(filas, key=lambda f: f[1], reverse=True)[:3]
        top_margen = sorted(filas, key=lambda f: f[2], reverse=True)[:3]

        win, borde = self._ventana_emergente("Salón de la Fama", ACCENT_PINK)
        win.geometry("500x520")
        ctk.CTkLabel(borde, text="Tus Ítems Estrella", font=(self.F_DISPLAY, 18, "bold"), text_color=ACCENT_PINK).pack(pady=(18, 10), padx=20, anchor="w")

        scroll = ctk.CTkScrollableFrame(borde, fg_color="#0d111c", corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.bind_mousewheel_recursive(scroll, scroll, orient="y")

        ctk.CTkLabel(scroll, text="Top 3 por Profit Total", font=(self.F_BODY, 14, "bold"), text_color=ACCENT_GOLD).pack(anchor="w", padx=8, pady=(8, 4))
        for i, (nombre, total, promedio, unidades) in enumerate(top_total, start=1):
            ctk.CTkLabel(scroll, text=f"{i}. {nombre} — {int(total):,} silver total ({int(unidades)} unidades)",
                         font=(self.F_MONO, 12, "bold"), text_color="#eef1f8", anchor="w").pack(fill="x", padx=8, pady=2)

        ctk.CTkFrame(scroll, fg_color="#232a3c", height=1).pack(fill="x", padx=8, pady=10)

        ctk.CTkLabel(scroll, text="Top 3 por Mejor Margen Promedio", font=(self.F_BODY, 14, "bold"), text_color=ACCENT_ICE).pack(anchor="w", padx=8, pady=(0, 4))
        for i, (nombre, total, promedio, unidades) in enumerate(top_margen, start=1):
            ctk.CTkLabel(scroll, text=f"{i}. {nombre} — {int(promedio):,} silver/unidad en promedio",
                         font=(self.F_MONO, 12, "bold"), text_color="#eef1f8", anchor="w").pack(fill="x", padx=8, pady=2)

        ctk.CTkButton(borde, text="Cerrar", fg_color=ACCENT_PINK, text_color="#12141c",
                      font=(self.F_BODY, 13, "bold"), width=140, command=win.destroy).pack(pady=(0, 16))
        self._centrar_ventana(win, 500, 520)
        win.grab_set()

    def _guardar_snapshot_banco(self, cursor):
        tax_rate = self.last_metrics.get("tax_rate", 0.08)
        ajuste_rate = 0.025
        setup_rate = 0.025
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

        conn2 = sqlite3.connect(DB_NAME)
        c2 = conn2.cursor()
        c2.execute("SELECT status, nombre, cantidad, tier, valor_oc, precio_mn, favorito FROM inventario WHERE user_id=? AND hub=? AND carga_nombre=?",
                   (self.current_user_id, self.current_hub, self.current_carga))
        items = c2.fetchall()
        conn2.close()

        for status, nombre, cantidad, tier, valor_oc, precio_mn, favorito in items:
            estado = "✕ Cancelado" if status == "canceled" else ("⚙ En Proceso" if status == "processing" else "✓ Recibido")
            nombre = (nombre or "").strip() or "(sin nombre)"
            cantidad = int(cantidad) or 1

            if estado == "✕ Cancelado":
                profit_unidad = 0.0
            else:
                costo_unit = valor_oc * (1 + setup_rate)
                venta_neta_unit = precio_mn * (1 - tax_rate - ajuste_rate)
                profit_unidad = venta_neta_unit - costo_unit
            profit_total = profit_unidad * cantidad

            cursor.execute('''
                INSERT INTO banco_items (user_id, hub, carga_nombre, nombre, cantidad, tier, estado,
                                          valor_oc, precio_mn, profit_unidad, profit_total, favorito, fecha)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (self.current_user_id, self.current_hub, self.current_carga, nombre, cantidad, tier, estado,
                  valor_oc, precio_mn, profit_unidad, profit_total, favorito, fecha))

    def vaciar_carga_actual(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM inventario WHERE user_id = ? AND hub = ? AND carga_nombre = ?",
                  (self.current_user_id, self.current_hub, self.current_carga))
        conn.commit()
        conn.close()
        if self.pagina_actual == "📦 Mi Carga":
            self.cargar_items_tabla()
        self.calculate_metrics()

    def archivar_carga(self):
        conn0 = sqlite3.connect(DB_NAME)
        c0 = conn0.cursor()
        c0.execute("SELECT COUNT(*) FROM inventario WHERE user_id=? AND hub=? AND carga_nombre=?",
                   (self.current_user_id, self.current_hub, self.current_carga))
        hay_items = c0.fetchone()[0] > 0
        conn0.close()
        if not hay_items:
            self.dialogo_warning("Nada que archivar", "No hay ítems cargados para archivar.")
            return
        self.calculate_metrics()
        m = self.last_metrics
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO historial_cargas (user_id, hub, destino, fecha, inversion, venta_neta, profit_final)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (self.current_user_id, self.current_hub, self.current_destino,
              datetime.now().strftime("%Y-%m-%d %H:%M"), m["inversion"], m["venta_neta"], m["profit_final"]))
        self._guardar_snapshot_banco(cursor)
        conn.commit()
        conn.close()
        self.dialogo_info("Carga Archivada", "Se guardó una foto de esta carga en el Historial y en el Banco.")
        if self.pagina_actual == "📊 Historial":
            self.refresh_historial_tab()
        if self.pagina_actual == "🏦 Banco":
            self.refrescar_banco_tab()
        if self.pagina_actual == "★ Favoritos":
            self.refrescar_favoritos_tab()

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

        self.dibujar_grafico_crecimiento(registros)

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
                text=(f"Esta semana: {int(profit_semana):,} silver ({cargas_semana} cargas)   |   "
                      f"Este mes: {int(profit_mes):,} silver ({cargas_mes} cargas)")
            )

        if not registros:
            ctk.CTkLabel(self.historial_scroll, text="Todavía no archivaste ninguna carga.", font=(self.F_BODY, 13), text_color="#97a2bd").pack(pady=10)
            return

        max_abs_profit = max(abs(r[5]) for r in registros) or 1

        for hub, destino, fecha, inversion, venta_neta, profit in registros:
            item_frame = ctk.CTkFrame(self.historial_scroll, fg_color="#1c2130", corner_radius=8)
            item_frame.pack(fill="x", pady=4, padx=2)

            top_line = ctk.CTkLabel(item_frame, text=f"{fecha}  •  {hub} → {destino.split('(')[0].strip()}",
                                     font=(self.F_BODY, 13, "bold"), text_color="#eef1f8", anchor="w")
            top_line.pack(fill="x", padx=10, pady=(8, 0))

            barra_len = int((abs(profit) / max_abs_profit) * 30)
            barra = "█" * max(barra_len, 1)
            color_barra = "#3ddc84" if profit >= 0 else "#ef5350"
            bar_line = ctk.CTkLabel(item_frame, text=f"{barra}  {int(profit):,} silver",
                                     font=(self.F_MONO, 13, "bold"), text_color=color_barra, anchor="w")
            bar_line.pack(fill="x", padx=10, pady=(2, 8))
            self.bind_mousewheel_recursive(item_frame, self.historial_scroll, orient="y")

    UMBRAL_AUTO_FAVORITO = 4

    def mostrar_ranking_items(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            SELECT nombre, SUM(cantidad) as total_cant, COUNT(*) as veces,
                   SUM(profit_total) as profit_total, AVG(profit_unidad) as profit_prom, MAX(favorito) as ya_fav
            FROM banco_items WHERE user_id = ?
            GROUP BY nombre ORDER BY total_cant DESC LIMIT 15
        ''', (self.current_user_id,))
        filas = c.fetchall()

        recien_favoritos = []
        for nombre, total_cant, veces, profit_total, profit_prom, ya_fav in filas:
            if veces >= self.UMBRAL_AUTO_FAVORITO and not ya_fav:
                c.execute("UPDATE banco_items SET favorito = 1 WHERE user_id = ? AND nombre = ?",
                          (self.current_user_id, nombre))
                recien_favoritos.append(nombre)
        conn.commit()
        conn.close()

        if not filas:
            self.dialogo_info("Sin Datos Todavía", "Todavía no exportaste ni archivaste ninguna carga. Cuando exportes un CSV o PDF, esto se llena solo.")
            return

        win, borde = self._ventana_emergente("Ítems Más Comprados", ACCENT_GOLD)
        win.geometry("540x580")
        ctk.CTkLabel(borde, text="Lo que más compraste", font=(self.F_DISPLAY, 18, "bold"), text_color=ACCENT_GOLD).pack(pady=(18, 2), padx=20, anchor="w")
        ctk.CTkLabel(borde, text="Ordenado por cuánto compraste de cada uno, con lo que te dejó de ganancia.",
                     font=(self.F_BODY, 12), text_color="#97a2bd", justify="left").pack(padx=20, pady=(0, 12), anchor="w")

        scroll = ctk.CTkScrollableFrame(borde, fg_color="#0d111c", corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.bind_mousewheel_recursive(scroll, scroll, orient="y")

        for nombre, total_cant, veces, profit_total, profit_prom, ya_fav in filas:
            es_fav = bool(ya_fav) or nombre in recien_favoritos
            if profit_prom >= 500:
                tag, color_tag = "Muy rentable", "#3ddc84"
            elif profit_prom >= 0:
                tag, color_tag = "Rentable", ACCENT_ICE
            elif profit_prom >= -200:
                tag, color_tag = "Al filo", "#e3a53c"
            else:
                tag, color_tag = "Te está costando plata", "#ef5350"

            card = ctk.CTkFrame(scroll, fg_color="#1c2130", corner_radius=10)
            card.pack(fill="x", pady=4, padx=4)
            titulo = f"{'★ ' if es_fav else ''}{nombre}"
            ctk.CTkLabel(card, text=titulo, font=(self.F_DISPLAY, 14, "bold"), text_color="#eef1f8" if not es_fav else ACCENT_GOLD).pack(anchor="w", padx=12, pady=(10, 2))
            ctk.CTkLabel(card, text=f"Lo compraste {veces} veces ({int(total_cant):,} unidades en total)",
                         font=(self.F_BODY, 12), text_color="#97a2bd").pack(anchor="w", padx=12)
            ctk.CTkLabel(card, text=f"Profit total: {int(profit_total):,} silver   ·   Promedio por unidad: {int(profit_prom):,} silver",
                         font=(self.F_MONO, 12, "bold"), text_color=color_tag).pack(anchor="w", padx=12, pady=(2, 2))
            fila_tag = ctk.CTkFrame(card, fg_color=color_tag, corner_radius=8)
            fila_tag.pack(anchor="w", padx=12, pady=(2, 10))
            ctk.CTkLabel(fila_tag, text=f" {tag} ", font=(self.F_BODY, 11, "bold"), text_color="#12141c").pack(padx=2, pady=2)
            self.bind_mousewheel_recursive(card, scroll, orient="y")

        if recien_favoritos:
            aviso = ctk.CTkLabel(borde, text=f"Se marcaron {len(recien_favoritos)} ítem(s) nuevo(s) como favorito automáticamente.",
                                  font=(self.F_BODY, 12, "bold"), text_color=ACCENT_GOLD)
            aviso.pack(padx=20, pady=(0, 10))

        ctk.CTkButton(borde, text="Cerrar", fg_color=ACCENT_GOLD, text_color="#12141c",
                      font=(self.F_BODY, 13, "bold"), width=140, command=win.destroy).pack(pady=(0, 16))
        self._centrar_ventana(win, 540, 580)
        win.grab_set()
        if self.pagina_actual == "★ Favoritos":
            self.refrescar_favoritos_tab()

    def exportar_historial_csv(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            SELECT fecha, hub, destino, inversion, venta_neta, profit_final
            FROM historial_cargas WHERE user_id = ? ORDER BY id DESC
        ''', (self.current_user_id,))
        filas = c.fetchall()
        conn.close()

        if not filas:
            self.dialogo_warning("Sin Datos", "No hay cargas archivadas todavía para exportar.")
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
            self.dialogo_info("Historial Exportado", f"Archivo guardado exitosamente en:\n{filename}")
        except Exception as ex:
            self.dialogo_error("Error CSV", f"No se pudo exportar el historial:\n{ex}")

    # ------------------------------------------------------------------ #
    # PÁGINA: BANCO
    # ------------------------------------------------------------------ #
    def build_tab_banco(self, parent):
        ctk.CTkLabel(parent, text="El Banco", font=(self.F_DISPLAY, 18, "bold"), text_color=ACCENT_GOLD).pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text="Acá queda guardado todo lo que compraste, aunque exportes el CSV o el PDF y la tabla de Mi Carga se vacíe.",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left", wraplength=900).pack(padx=10, pady=(0, 10), anchor="w")

        btn_refrescar_banco = ctk.CTkButton(parent, text="Refrescar", width=110, fg_color="#1e2536", hover_color=ACCENT_GOLD,
                                             text_color="#eef1f8", font=(self.F_BODY, 12, "bold"), command=self.refrescar_banco_tab)
        btn_refrescar_banco.pack(anchor="w", padx=10, pady=(0, 10))

        cols = ctk.CTkFrame(parent, fg_color="transparent")
        cols.pack(fill="both", expand=True, padx=10)

        col_izq = ctk.CTkFrame(cols, fg_color="transparent")
        col_izq.pack(side="left", fill="both", expand=True, padx=(0, 10))
        col_der = ctk.CTkFrame(cols, fg_color="transparent")
        col_der.pack(side="left", fill="both", expand=True, padx=(10, 0))

        ctk.CTkLabel(col_izq, text="Todos los Pedidos", font=(self.F_BODY, 14, "bold"), text_color=ACCENT_ICE).pack(anchor="w", pady=(0, 6))
        self.banco_pedidos_scroll = ctk.CTkScrollableFrame(col_izq, fg_color="#0d111c", corner_radius=10)
        self.banco_pedidos_scroll.pack(fill="both", expand=True)
        self.bind_mousewheel_recursive(self.banco_pedidos_scroll, self.banco_pedidos_scroll, orient="y")

        ctk.CTkLabel(col_der, text="Compras con Profit", font=(self.F_BODY, 14, "bold"), text_color=ACCENT_GOLD).pack(anchor="w", pady=(0, 6))
        self.banco_compras_scroll = ctk.CTkScrollableFrame(col_der, fg_color="#0d111c", corner_radius=10)
        self.banco_compras_scroll.pack(fill="both", expand=True)
        self.bind_mousewheel_recursive(self.banco_compras_scroll, self.banco_compras_scroll, orient="y")

        self.refrescar_banco_tab()

    def refrescar_banco_tab(self):
        if not hasattr(self, "banco_pedidos_scroll") or not self.banco_pedidos_scroll.winfo_exists():
            return
        for w in self.banco_pedidos_scroll.winfo_children():
            w.destroy()
        for w in self.banco_compras_scroll.winfo_children():
            w.destroy()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''SELECT nombre, cantidad, tier, valor_oc, estado, fecha FROM banco_items
                     WHERE user_id = ? ORDER BY id DESC LIMIT 80''', (self.current_user_id,))
        pedidos = c.fetchall()
        c.execute('''SELECT nombre, cantidad, precio_mn, profit_total, fecha FROM banco_items
                     WHERE user_id = ? AND estado != '✕ Cancelado' ORDER BY id DESC LIMIT 80''', (self.current_user_id,))
        compras = c.fetchall()
        conn.close()

        if not pedidos:
            ctk.CTkLabel(self.banco_pedidos_scroll, text="Todavía no pediste nada. Cuando exportes o archives una carga, va a aparecer acá.",
                         font=(self.F_BODY, 12), text_color="#97a2bd", wraplength=380, justify="left").pack(pady=10, padx=8, anchor="w")
        for nombre, cantidad, tier, valor_oc, estado, fecha in pedidos:
            linea = ctk.CTkLabel(self.banco_pedidos_scroll,
                                  text=f"{fecha} · {estado} · {nombre} x{cantidad} ({tier}) — base {int(valor_oc):,} silver",
                                  font=(self.F_MONO, 11), text_color="#eef1f8", anchor="w")
            linea.pack(fill="x", padx=8, pady=2)
            self.bind_mousewheel_recursive(linea, self.banco_pedidos_scroll, orient="y")

        if not compras:
            ctk.CTkLabel(self.banco_compras_scroll, text="Todavía no hay compras con profit registradas.",
                         font=(self.F_BODY, 12), text_color="#97a2bd", wraplength=380, justify="left").pack(pady=10, padx=8, anchor="w")
        for nombre, cantidad, precio_mn, profit_total, fecha in compras:
            color = "#3ddc84" if profit_total >= 0 else "#ef5350"
            linea = ctk.CTkLabel(self.banco_compras_scroll,
                                  text=f"{fecha} · {nombre} x{cantidad} — venta {int(precio_mn):,}/u · profit {int(profit_total):,} silver",
                                  font=(self.F_MONO, 11, "bold"), text_color=color, anchor="w")
            linea.pack(fill="x", padx=8, pady=2)
            self.bind_mousewheel_recursive(linea, self.banco_compras_scroll, orient="y")

    # ------------------------------------------------------------------ #
    # PÁGINA: FAVORITOS
    # ------------------------------------------------------------------ #
    def build_tab_favoritos(self, parent):
        ctk.CTkLabel(parent, text="Tus Favoritos", font=(self.F_DISPLAY, 18, "bold"), text_color=ACCENT_GOLD).pack(pady=(10, 4), padx=10, anchor="w")
        ctk.CTkLabel(parent, text="Los ítems que marcaste con estrella (a mano, o automático si los compraste muchas veces), con lo que te dieron de ganancia.",
                     font=(self.F_BODY, 13), text_color="#97a2bd", justify="left", wraplength=900).pack(padx=10, pady=(0, 10), anchor="w")

        btn_refrescar_fav = ctk.CTkButton(parent, text="Refrescar", width=110, fg_color="#1e2536", hover_color=ACCENT_GOLD,
                                           text_color="#eef1f8", font=(self.F_BODY, 12, "bold"), command=self.refrescar_favoritos_tab)
        btn_refrescar_fav.pack(anchor="w", padx=10, pady=(0, 10))

        self.favoritos_scroll = ctk.CTkScrollableFrame(parent, fg_color="#0d111c", corner_radius=10)
        self.favoritos_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        self.bind_mousewheel_recursive(self.favoritos_scroll, self.favoritos_scroll, orient="y")

        self.refrescar_favoritos_tab()

    def refrescar_favoritos_tab(self):
        if not hasattr(self, "favoritos_scroll") or not self.favoritos_scroll.winfo_exists():
            return
        for w in self.favoritos_scroll.winfo_children():
            w.destroy()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            SELECT nombre, SUM(cantidad), SUM(profit_total), AVG(profit_unidad), COUNT(*)
            FROM banco_items WHERE user_id = ? AND favorito = 1
            GROUP BY nombre ORDER BY SUM(profit_total) DESC
        ''', (self.current_user_id,))
        filas = c.fetchall()
        conn.close()

        if not filas:
            ctk.CTkLabel(self.favoritos_scroll,
                         text="Todavía no tenés favoritos con historial. Marcá la estrella en la tabla y exportá o archivá la carga, o compra el mismo ítem varias veces y la app lo marca sola.",
                         font=(self.F_BODY, 13), text_color="#97a2bd", wraplength=800, justify="left").pack(pady=10, padx=8, anchor="w")
            return

        for nombre, total_cant, total_profit, avg_profit, veces in filas:
            color = "#3ddc84" if total_profit >= 0 else "#ef5350"
            card = ctk.CTkFrame(self.favoritos_scroll, fg_color="#1c2130", border_color=ACCENT_GOLD, border_width=1, corner_radius=10)
            card.pack(fill="x", pady=4, padx=2)
            ctk.CTkLabel(card, text=f"★ {nombre}", font=(self.F_DISPLAY, 14, "bold"), text_color=ACCENT_GOLD).pack(anchor="w", padx=12, pady=(10, 2))
            ctk.CTkLabel(card, text=f"Comprado {veces} veces ({int(total_cant):,} unidades)",
                         font=(self.F_BODY, 12), text_color="#97a2bd").pack(anchor="w", padx=12)
            ctk.CTkLabel(card, text=f"Profit total: {int(total_profit):,} silver · Promedio/unidad: {int(avg_profit):,} silver",
                         font=(self.F_MONO, 12, "bold"), text_color=color).pack(anchor="w", padx=12, pady=(2, 10))
            self.bind_mousewheel_recursive(card, self.favoritos_scroll, orient="y")

    # ------------------------------------------------------------------ #
    # COMPARAR CUENTAS
    # ------------------------------------------------------------------ #
    def comparar_cuentas(self):
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
            self.dialogo_info("Sin Cuentas", "No hay personajes registrados todavía.")
            return

        lineas = []
        for i, (username, region, total, cargas) in enumerate(filas, start=1):
            marca = " (esta cuenta)" if username == self.current_username and region == self.current_region else ""
            lineas.append(f"{i}. {username} ({region}): {int(total):,} silver en {cargas} cargas archivadas{marca}")
        self.dialogo_info("Comparación de Cuentas", "\n".join(lineas))

    # ------------------------------------------------------------------ #
    # VISTA DE SOLO LECTURA PARA EL GREMIO
    # ------------------------------------------------------------------ #
    def exportar_vista_gremio(self):
        if not self.row_inputs:
            self.dialogo_warning("Nada que exportar", "No hay ítems cargados para compartir.")
            return
        self.calculate_metrics()
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
            self.dialogo_info("Vista Exportada", f"Archivo guardado exitosamente en:\n{filename}\n\nSe puede abrir con cualquier navegador, sin necesidad de la app ni tu contraseña.")
        except Exception as ex:
            self.dialogo_error("Error", f"No se pudo exportar la vista:\n{ex}")

    # ------------------------------------------------------------------ #
    # CHECKLIST PRE-VIAJE
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
        win.configure(fg_color="#12151f")
        win.attributes("-topmost", True)
        win.resizable(False, False)

        ctk.CTkLabel(win, text="Antes de salir a Mercado Negro", font=(self.F_DISPLAY, 16, "bold"), text_color=ACCENT_LIME).pack(pady=(20, 10), padx=20, anchor="w")

        for key, texto in self.CHECKLIST_ITEMS:
            var = tk.BooleanVar(value=bool(estado_guardado.get(key, 0)))
            chk = ctk.CTkCheckBox(win, text=texto, variable=var, font=(self.F_BODY, 13),
                                   text_color="#eef1f8", fg_color=ACCENT_LIME, hover_color=ACCENT_TEAL,
                                   command=lambda k=key, v=var: self.guardar_checklist_item(k, v.get()))
            chk.pack(anchor="w", padx=30, pady=8)

        btn_cerrar = ctk.CTkButton(win, text="Cerrar", fg_color=ACCENT_LIME, text_color="#12141c",
                                    font=(self.F_BODY, 13, "bold"), command=win.destroy)
        btn_cerrar.pack(pady=20)
        self._centrar_ventana(win, 420, 360)

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
    # MODO PRESENTACIÓN
    # ------------------------------------------------------------------ #
    def toggle_modo_presentacion(self):
        self.modo_presentacion = not self.modo_presentacion
        if self.modo_presentacion:
            self._titulo_real_txt = self.ent_title.get()
            self.set_display_text(self.ent_title, "•••••• MODO PRESENTACIÓN ••••••")
            self.btn_presentacion.configure(text="Salir de Presentación", fg_color=ACCENT_PINK)
            for row in self.row_inputs:
                for campo in ("nombre", "cantidad", "tier", "valor_oc"):
                    row[campo].configure(state="readonly")
        else:
            self.set_display_text(self.ent_title, self._titulo_real_txt)
            self.btn_presentacion.configure(text="Modo Presentación", fg_color="#1e2536")
            for row in self.row_inputs:
                for campo in ("nombre", "cantidad", "tier", "valor_oc"):
                    row[campo].configure(state="normal")
                self.aplicar_bloqueo_precio_mn(row)

    # ------------------------------------------------------------------ #
    # CÁLCULOS (lee los ítems directo de la base de datos, así los
    # contadores de arriba siempre están bien sin importar qué página
    # tengas abierta en este momento)
    # ------------------------------------------------------------------ #
    def calculate_metrics(self):
        if not hasattr(self, "card_budget"):
            return

        tax_rate = 0.04 if self.is_premium else 0.08
        ajuste_rate = 0.025
        setup_rate = 0.025

        total_inversion_oc = 0.0
        total_venta_bruta = 0.0

        val_mochila_manual = self.get_float(self.ent_mochila_global.get()) if hasattr(self, "ent_mochila_global") else 0.0

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT status, cantidad, valor_oc, precio_mn FROM inventario WHERE user_id=? AND hub=? AND carga_nombre=?",
                  (self.current_user_id, self.current_hub, self.current_carga))
        filas_db = c.fetchall()
        conn.close()

        for status, cantidad, valor_oc, precio_mn in filas_db:
            if status == "canceled":
                continue
            cantidad = int(cantidad) or 1
            total_inversion_oc += (cantidad * valor_oc * (1 + setup_rate))
            total_venta_bruta += (cantidad * precio_mn)

        # Si la página "Mi Carga" está abierta, también actualizamos el %
        # margen que se muestra al lado de cada fila.
        if getattr(self, "row_inputs", None):
            for row in self.row_inputs:
                status = row["status"].get()
                valor_oc_fila = self.get_float(row["valor_oc"].get())
                precio_mn_fila = self.get_float(row["precio_mn"].get())
                lbl_margen = row.get("lbl_margen")
                if lbl_margen is not None and lbl_margen.winfo_exists():
                    if status == "✕ Cancelado" or valor_oc_fila <= 0:
                        lbl_margen.configure(text="---", text_color="#97a2bd")
                    else:
                        costo_unit = valor_oc_fila * (1 + setup_rate)
                        venta_neta_unit = precio_mn_fila * (1 - tax_rate - ajuste_rate)
                        margen_pct = ((venta_neta_unit - costo_unit) / costo_unit) * 100
                        color_margen = "#3ddc84" if margen_pct >= 0 else "#ef5350"
                        lbl_margen.configure(text=f"{margen_pct:.0f}%", text_color=color_margen)

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
        impuesto_retorno = self.get_float(self.ent_impuesto_retorno.get()) if hasattr(self, "ent_impuesto_retorno") else 0.0

        if self.current_destino == DESTINO_HIDEOUT:
            self.card_pt_label.configure(text="COSTO TOTAL DE TRANSPORTE (Uso Personal a Hideout)")
            self.set_display_text(self.card_pt, f"{int(costo_transporte):,} silver", "#48c9dc")
            self.lbl_desglose.configure(
                text=("Carga con destino Hideout: no se vende en el Mercado Negro. "
                      f"Costo de transporte registrado: {int(costo_transporte):,} silver.")
            )
            profit_final_real = -costo_transporte - impuesto_retorno
        else:
            tax_pct = int(round(tax_rate * 100))
            self.card_pt_label.configure(text=f"PROFIT FINAL MERCADO NEGRO (-{tax_pct}% Imp. -2.5% Ajuste)")
            profit_final_real = total_venta_neta - total_inversion_oc - impuesto_retorno

            factor_neto = (1 - tax_rate - ajuste_rate)
            venta_equilibrio = (total_inversion_oc / factor_neto) if factor_neto > 0 else 0.0

            if self.fase_venta_activa:
                self.set_display_text(self.card_pt, f"{int(profit_final_real):,} silver", "#3ddc84" if profit_final_real >= 0 else "#ef5350")
                extra_retorno = f"  |  Retorno rápido: -{int(impuesto_retorno):,} silver" if impuesto_retorno > 0 else ""
                self.lbl_desglose.configure(
                    text=(f"Venta Bruta: {int(total_venta_bruta):,}  |  "
                          f"-{tax_pct}% Impuesto: -{int(total_impuesto):,}  |  "
                          f"-2.5% Ajuste: -{int(total_ajuste):,}  |  "
                          f"Neto: {int(total_venta_neta):,} silver{extra_retorno}  |  "
                          f"Punto de Equilibrio (Venta Bruta mínima): {int(venta_equilibrio):,} silver")
                )
            else:
                self.set_display_text(self.card_pt, "---", "#3ddc84")
                self.lbl_desglose.configure(
                    text=(f"Activá la fase de venta para ver el desglose de impuestos y ajuste.  |  "
                          f"Punto de Equilibrio estimado (Venta Bruta mínima): {int(venta_equilibrio):,} silver")
                )

        if hasattr(self, "lbl_premium_ahorro") and self.lbl_premium_ahorro.winfo_exists():
            ahorro_premium = total_venta_bruta * 0.04
            if self.is_premium:
                self.lbl_premium_ahorro.configure(
                    text=f"Con Premium activo, en esta carga te ahorrás como {int(ahorro_premium):,} silver de impuesto (pagás 4% en vez de 8%)."
                )
            else:
                self.lbl_premium_ahorro.configure(
                    text=f"Sin Premium estás pagando el doble de impuesto. Si lo activaras, en esta carga te ahorrarías como {int(ahorro_premium):,} silver."
                )

        if hasattr(self, "lbl_eficiencia") and hasattr(self, "ent_start_time"):
            try:
                inicio_dt = datetime.strptime(self.ent_start_time.get().strip(), "%Y-%m-%d %H:%M")
                horas_transcurridas = max((datetime.now() - inicio_dt).total_seconds() / 3600, 0.1)
                eficiencia = profit_final_real / horas_transcurridas
                color_efi = "#3ddc84" if eficiencia >= 0 else "#ef5350"
                self.lbl_eficiencia.configure(text=f"Eficiencia: {int(eficiencia):,} silver/hora", text_color=color_efi)
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
            "impuesto_retorno": impuesto_retorno,
        }

    # ------------------------------------------------------------------ #
    # EXPORTAR PDF
    # ------------------------------------------------------------------ #
    def export_to_pdf(self):
        if not self.row_inputs:
            self.dialogo_warning("PDF Vacío", "No hay datos en la tabla para exportar.")
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
                return h - 50
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
        c.drawString(40, h - 50, f"Mi carga: {self.current_hub} -> {self.current_destino}")

        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.6, 0.7, 0.8)
        c.drawString(40, h - 75, f"{self.current_username}  -  {self.current_region}")

        c.setFillColorRGB(1, 1, 1)
        c.drawString(40, h - 100, f"Arranque la compra: {start_str} (hora local)  /  {start_utc}")
        c.drawString(40, h - 115, f"Este reporte lo saque el: {generado_str}")
        estado_fase = "Ya estoy vendiendo en el Mercado Negro" if self.last_metrics.get("fase_venta_activa") else "Todavia en fase de compra (no empece a vender)"
        c.drawString(40, h - 130, f"Estado: {estado_fase}")

        y = h - 160
        y = draw_section_title(y, "Como me fue con la plata")
        m = self.last_metrics
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(1, 1, 1)

        ruta_txt = f"{self.current_hub}"
        if m['ruta_tipo'] and self.current_hub != "Caerleon":
            ruta_txt += f" ({m['ruta_tipo']})"

        resumen_lines = [
            f"Premium: {'Si, pago menos impuesto (4%)' if m['premium'] else 'No, impuesto normal (8%)'}",
            f"Ruta: {ruta_txt}  ->  {m['destino']}",
            f"Lo que puse en Ordenes de Compra (+2.5% de setup): {int(m['inversion']):,} silver",
            f"Lo que anote que vale mi mochila: {int(m['mochila']):,} silver",
            f"Profit estimado (mochila menos inversion): {int(m['profit_est']):,} silver",
        ]

        if m['destino'] == DESTINO_HIDEOUT:
            resumen_lines += [
                "",
                "Esta carga va para el Hideout (uso personal), no se vende en el Mercado Negro.",
                f"Lo que gaste en transporte: {int(m['costo_transporte']):,} silver",
            ]
        else:
            tax_pct = int(round(m['tax_rate'] * 100))
            resumen_lines += [
                f"Venta bruta proyectada en el Mercado Negro: {int(m['venta_bruta']):,} silver",
                f"  -{tax_pct}% de impuesto: -{int(m['impuesto']):,} silver",
                f"  -2.5% de ajuste de mercado: -{int(m['ajuste']):,} silver",
                f"Venta neta: {int(m['venta_neta']):,} silver",
                f"Profit final (venta neta menos inversion): {int(m['profit_final']):,} silver",
            ]

        for line in resumen_lines:
            y = ensure_space(y, 16)
            c.drawString(40, y, line)
            y -= 16

        y -= 10
        y = draw_section_title(y, "Mis items (los cancelados tambien quedan anotados)")

        def draw_table_headers(y):
            c.setFillColorRGB(1, 0.66, 0)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(40, y, "Estado")
            c.drawString(115, y, "Item")
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

            fav_marca = "* " if row["favorito"]["on"] else ""

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
        y = draw_section_title(y, "Cuantas esencias tengo guardadas")

        conn = sqlite3.connect(DB_NAME)
        cq = conn.cursor()
        cq.execute("SELECT tier, runas, almas, reliquias FROM esencias WHERE user_id=? AND hub=? ORDER BY tier", (self.current_user_id, self.current_hub))
        filas_esencias = {t: (r, a, re) for t, r, a, re in cq.fetchall()}
        conn.close()

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
            r, a, re = filas_esencias.get(t, (0, 0, 0))
            c.drawString(40, y, f"T{t}")
            c.drawString(120, y, str(r))
            c.drawString(220, y, str(a))
            c.drawString(320, y, str(re))
            y -= 16

        y -= 10
        y = draw_section_title(y, "Notas mias")

        conn = sqlite3.connect(DB_NAME)
        cq = conn.cursor()
        cq.execute("SELECT texto FROM notas WHERE user_id=? AND hub=?", (self.current_user_id, self.current_hub))
        fila_nota = cq.fetchone()
        conn.close()
        nota_texto = (fila_nota[0] if fila_nota else "").strip()

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
            c.drawString(40, y, "(No anote nada para esta ciudad)")
            y -= 14

        c.save()

        try:
            self.archivar_carga_silenciosa()
        except Exception:
            pass

        self.dialogo_info("Carga Exportada", f"Archivo guardado exitosamente en:\n{filename}")
        self.vaciar_carga_actual()
        if self.pagina_actual == "🏦 Banco":
            self.refrescar_banco_tab()
        if self.pagina_actual == "★ Favoritos":
            self.refrescar_favoritos_tab()
        self.reiniciar_inicio_carga()

    def archivar_carga_silenciosa(self):
        self.calculate_metrics()
        m = self.last_metrics
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO historial_cargas (user_id, hub, destino, fecha, inversion, venta_neta, profit_final)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (self.current_user_id, self.current_hub, self.current_destino,
              datetime.now().strftime("%Y-%m-%d %H:%M"), m["inversion"], m["venta_neta"], m["profit_final"]))
        self._guardar_snapshot_banco(cursor)
        conn.commit()
        conn.close()
        if self.pagina_actual == "📊 Historial":
            self.refresh_historial_tab()


if __name__ == "__main__":
    app = AlbionCargoApp()
    app.mainloop()
