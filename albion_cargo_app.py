import os
import sqlite3
import time
import textwrap
import shutil
import csv
from datetime import datetime, timedelta
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox
import customtkinter as ctk
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

DB_NAME = "albion_cargo.db"

# Listas de candidatos por estilo, en orden de preferencia. Si la PC de otro
# jugador no tiene "Oxanium"/"Rajdhani"/"JetBrains Mono" instaladas, la app
# cae automáticamente en la mejor alternativa disponible en su sistema en
# vez de mostrar la fuente genérica fea de tkinter por defecto.
FONT_DISPLAY_CANDIDATES = ["Oxanium", "Orbitron", "Audiowide", "Segoe UI Semibold", "Ubuntu", "Noto Sans", "DejaVu Sans"]
FONT_BODY_CANDIDATES = ["Rajdhani", "Ubuntu", "Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"]
FONT_MONO_CANDIDATES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Ubuntu Mono", "DejaVu Sans Mono", "Courier New"]

# Colores según el estado de la fila (pedido: proceso=naranja, recibido=verde, cancelado=rojo)
STATUS_COLORS = {
    "✓ Recibido": "#00ff66",
    "⚙ En Proceso": "#ffaa00",
    "✕ Cancelado": "#ff3b30",
}

# Destinos posibles de la carga
DESTINO_MERCADO = "Mercado Negro (Caerleon)"
DESTINO_HIDEOUT = "Hideout (Uso Personal)"
DESTINOS = [DESTINO_MERCADO, DESTINO_HIDEOUT]

# Orden de columnas navegables con el teclado (igual que celdas de Excel)
TABLE_COLS = ["nombre", "cantidad", "tier", "valor_oc", "precio_mn"]
ESSENCE_COLS = ["r", "a", "re"]

# Ventana de expiración de una Orden de Compra en Albion (pedido: aviso de 24h)
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
        self.minsize(1100, 700)

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
        self.is_premium = False
        self.fase_venta_activa = False

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

        self.show_auth_screen()

    def quitar_foco_clic(self, event):
        # Soltar foco solo si hacemos clic fuera de una entrada de texto
        try:
            widget = event.widget
            if not isinstance(widget, (tk.Entry, tk.Text, tk.Listbox)):
                self.focus_set()
        except Exception:
            pass

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
        self.auth_frame = ctk.CTkFrame(self, fg_color="#0b0e14", border_color="#ffaa00", border_width=2, corner_radius=18, width=460, height=610)
        self.auth_frame.place(relx=0.5, rely=0.5, anchor="center")
        self.auth_frame.pack_propagate(False)

        title_label = ctk.CTkLabel(self.auth_frame, text="TRANSPORTES", font=(self.F_DISPLAY, 28, "bold"), text_color="#ffaa00")
        title_label.pack(pady=(40, 5))
        sub_label = ctk.CTkLabel(self.auth_frame, text="Control de transporte", font=(self.F_BODY, 14), text_color="#8b9bb4")
        sub_label.pack(pady=(0, 30))

        lbl_user = ctk.CTkLabel(self.auth_frame, text="Nombre del Personaje:", font=(self.F_BODY, 15, "bold"), text_color="#fff")
        lbl_user.pack(anchor="w", padx=45, pady=(10, 2))
        self.ent_username = ctk.CTkEntry(self.auth_frame, placeholder_text="Ej: XitSsoTox", fg_color="#06080c", font=(self.F_BODY, 16), border_color="#21262d")
        self.ent_username.pack(fill="x", padx=45, pady=5)

        lbl_region = ctk.CTkLabel(self.auth_frame, text="Region del servidor", font=(self.F_BODY, 15, "bold"), text_color="#fff")
        lbl_region.pack(anchor="w", padx=45, pady=(10, 2))

        self.sel_region = ctk.CTkOptionMenu(
            self.auth_frame, values=["Albion West (América)", "Albion East (Asia)", "Albion Europe (Europa)"],
            fg_color="#161b22", button_color="#1f242c", button_hover_color="#2b333f",
            dropdown_fg_color="#0b0e14", dropdown_hover_color="#ffaa00", dropdown_text_color="#fff",
            font=(self.F_BODY, 16))
        self.sel_region.pack(fill="x", padx=45, pady=5)

        lbl_note_region = ctk.CTkLabel(self.auth_frame, text="El nombre debe ser único dentro de tu región.", font=(self.F_BODY, 11), text_color="#8b9bb4")
        lbl_note_region.pack(anchor="w", padx=45, pady=(0, 5))

        lbl_pass = ctk.CTkLabel(self.auth_frame, text="Contraseña:", font=(self.F_BODY, 15, "bold"), text_color="#fff")
        lbl_pass.pack(anchor="w", padx=45, pady=(10, 2))
        self.ent_password = ctk.CTkEntry(self.auth_frame, placeholder_text="••••••••", show="*", fg_color="#06080c", font=(self.F_BODY, 16), border_color="#21262d")
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

        btn_login = ctk.CTkButton(self.auth_frame, text="INICIAR SESIÓN / REGISTRAR", font=(self.F_DISPLAY, 14, "bold"), fg_color="#ffaa00", text_color="#000", hover_color="#fff", command=self.handle_auth)
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
                self.show_main_hud()
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
            self.show_main_hud()
        conn.close()

    # ------------------------------------------------------------------ #
    # HUD PRINCIPAL
    # ------------------------------------------------------------------ #
    def show_main_hud(self):
        # Header Principal
        self.header_frame = ctk.CTkFrame(self, fg_color="#0d1117", border_color="#ffaa00", border_width=1, corner_radius=14, height=90)
        self.header_frame.pack(fill="x", padx=25, pady=(20, 10))
        self.header_frame.pack_propagate(False)

        title_txt = f"Nombre: {self.current_username.upper()} • {self.current_region.upper()}"
        self.ent_title = ctk.CTkEntry(self.header_frame, width=340, font=(self.F_DISPLAY, 18, "bold"), text_color="#fff",
                                      fg_color="transparent", border_width=0, justify="left")
        self.ent_title.pack(side="left", padx=25, pady=30)
        self.set_display_text(self.ent_title, title_txt)

        self.lbl_live_clock = ctk.CTkLabel(self.header_frame, text="", font=(self.F_MONO, 14, "bold"), text_color="#00d2ff")
        self.lbl_live_clock.pack(side="left", padx=50, pady=30)
        self.update_live_clock()

        # Botón de tema claro/oscuro (idea nueva: calidad de vida)
        self.btn_theme = ctk.CTkButton(self.header_frame, text="☀ / 🌙", width=70, fg_color="#1b2430",
                                        hover_color="#33404f", font=(self.F_BODY, 13, "bold"),
                                        command=self.toggle_theme)
        self.btn_theme.pack(side="right", padx=(5, 10), pady=30)

        # Botón de backup manual de la base de datos (idea nueva: calidad de vida)
        self.btn_backup = ctk.CTkButton(self.header_frame, text="💾 Backup DB", width=110, fg_color="#1b2430",
                                         hover_color="#33404f", font=(self.F_BODY, 13, "bold"),
                                         command=self.backup_database)
        self.btn_backup.pack(side="right", padx=5, pady=30)

        self.hub_selector = ctk.CTkOptionMenu(
            self.header_frame, values=["Fort Sterling", "Lymhurst", "Bridgewatch", "Martlock", "Thetford", "Caerleon"],
            command=self.change_hub, fg_color="#1b2430", button_color="#242c38", button_hover_color="#33404f",
            dropdown_fg_color="#0b0e14", dropdown_hover_color="#ffaa00", dropdown_text_color="#ffffff",
            font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00", width=170)
        self.hub_selector.set(self.current_hub)
        self.hub_selector.pack(side="right", padx=25, pady=30)

        # --- Barra superior: tiempos, destino, ruta, premium y mochila ---
        self.top_bar = ctk.CTkFrame(self, fg_color="#0d1117", border_color="#21262d", border_width=1, corner_radius=14)
        self.top_bar.pack(fill="x", padx=25, pady=10)

        row1 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row1.pack(fill="x")
        row2 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row2.pack(fill="x")
        row3 = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        row3.pack(fill="x")

        lbl_t1 = ctk.CTkLabel(row1, text="Inicio Carga/Compra:", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        lbl_t1.pack(side="left", padx=(20, 10), pady=15)
        self.ent_start_time = ctk.CTkEntry(row1, placeholder_text="Ej: 2026-07-11 12:00", width=160, fg_color="#090d13")
        self.ent_start_time.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.ent_start_time.pack(side="left", padx=5, pady=15)
        self.ent_start_time.bind("<KeyRelease>", lambda e: self.update_oc_expira_label())

        lbl_destino = ctk.CTkLabel(row1, text="Destino de la Carga:", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        lbl_destino.pack(side="left", padx=(30, 10), pady=15)
        self.sel_destino = ctk.CTkOptionMenu(
            row1, values=DESTINOS, command=self.change_destino,
            fg_color="#1b2430", button_color="#242c38", button_hover_color="#33404f",
            dropdown_fg_color="#0b0e14", dropdown_hover_color="#00d2ff", dropdown_text_color="#ffffff",
            font=(self.F_BODY, 13, "bold"), text_color="#fff", width=220)
        self.sel_destino.set(self.current_destino)
        self.sel_destino.pack(side="left", padx=5, pady=15)

        self.lbl_ruta = ctk.CTkLabel(row1, text="Punto de Entrada:", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        self.sel_ruta = ctk.CTkOptionMenu(
            row1, values=[self.current_hub], command=self.change_ruta,
            fg_color="#1b2430", button_color="#242c38", button_hover_color="#33404f",
            dropdown_fg_color="#0b0e14", dropdown_hover_color="#00d2ff", dropdown_text_color="#ffffff",
            font=(self.F_BODY, 13, "bold"), text_color="#fff", width=220)

        # Aviso de expiración de OC (idea nueva: logística/riesgo). Se calcula sobre
        # ent_start_time + 24h, en texto, sin depender de que la app siga abierta.
        self.lbl_oc_expira = ctk.CTkLabel(row1, text="", font=(self.F_MONO, 13, "bold"), text_color="#ffaa00")
        self.lbl_oc_expira.pack(side="left", padx=(20, 10), pady=15)

        # Premium (pedido #5): baja el impuesto de venta del 8% al 4%
        self.switch_premium = ctk.CTkSwitch(
            row2, text="Cuenta Premium (Impuesto Mercado Negro 8% → 4%)",
            font=(self.F_BODY, 13, "bold"), progress_color="#00ff66",
            command=self.toggle_premium)
        self.switch_premium.pack(side="left", padx=(20, 30), pady=15)

        # Costo de transporte, solo relevante y visible si el destino es Hideout
        self.lbl_costo_transporte = ctk.CTkLabel(row2, text="Costo de Transporte a Hideout:", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        self.ent_costo_transporte = ctk.CTkEntry(row2, placeholder_text="0", width=140, fg_color="#090d13", text_color="#00d2ff", font=(self.F_MONO, 14, "bold"))
        self.ent_costo_transporte.insert(0, "0")
        self.ent_costo_transporte.bind("<KeyRelease>", lambda e: self.save_costo_transporte())

        # Mochila Manual, Global e Independiente
        lbl_mochila_section = ctk.CTkLabel(row2, text="Valor De La Mochila (Inventario):", font=(self.F_DISPLAY, 14, "bold"), text_color="#00d2ff")
        lbl_mochila_section.pack(side="right", padx=(10, 20), pady=15)

        self.ent_mochila_global = ctk.CTkEntry(row2, placeholder_text="0", fg_color="#090d13", font=(self.F_MONO, 14, "bold"), text_color="#00d2ff", width=180)
        self.ent_mochila_global.insert(0, "0")
        self.ent_mochila_global.pack(side="right", padx=5, pady=15)
        self.ent_mochila_global.bind("<KeyRelease>", lambda e: self.calculate_metrics())

        # Peso de carga vs. capacidad de montura (idea nueva: logística/riesgo)
        lbl_peso = ctk.CTkLabel(row3, text="Peso Carga (kg):", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        lbl_peso.pack(side="left", padx=(20, 10), pady=15)
        self.ent_peso_carga = ctk.CTkEntry(row3, placeholder_text="0", width=100, fg_color="#090d13", text_color="#fff", font=(self.F_MONO, 14, "bold"))
        self.ent_peso_carga.insert(0, "0")
        self.ent_peso_carga.pack(side="left", padx=5, pady=15)
        self.ent_peso_carga.bind("<KeyRelease>", lambda e: self.update_peso_ui())

        lbl_capacidad = ctk.CTkLabel(row3, text="Capacidad Montura (kg):", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        lbl_capacidad.pack(side="left", padx=(20, 10), pady=15)
        self.sel_montura = ctk.CTkOptionMenu(
            row3, values=["Mula (700)", "Caballo T5 (409)", "Buey Acorazado T4 (1650)", "Camello (940)", "Personalizado"],
            command=lambda v: self.update_peso_ui(),
            fg_color="#1b2430", button_color="#242c38", button_hover_color="#33404f",
            dropdown_fg_color="#0b0e14", dropdown_hover_color="#00d2ff", dropdown_text_color="#ffffff",
            font=(self.F_BODY, 13, "bold"), text_color="#fff", width=210)
        self.sel_montura.pack(side="left", padx=5, pady=15)

        self.ent_capacidad_custom = ctk.CTkEntry(row3, placeholder_text="kg", width=90, fg_color="#090d13", text_color="#fff", font=(self.F_MONO, 14, "bold"))
        self.ent_capacidad_custom.bind("<KeyRelease>", lambda e: self.update_peso_ui())

        self.lbl_peso_status = ctk.CTkLabel(row3, text="", font=(self.F_MONO, 13, "bold"), text_color="#00ff66")
        self.lbl_peso_status.pack(side="left", padx=(15, 10), pady=15)

        # Etiqueta de riesgo por ruta (idea nueva: logística/riesgo)
        lbl_riesgo = ctk.CTkLabel(row3, text="Riesgo de Ruta:", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        lbl_riesgo.pack(side="right", padx=(10, 20), pady=15)
        self.sel_riesgo = ctk.CTkOptionMenu(
            row3, values=["🟢 Zona Segura", "🟡 Zona Amarilla", "🔴 Zona Roja", "⚫ Zona Negra"],
            command=lambda v: self.update_riesgo_ui(),
            fg_color="#1b2430", button_color="#242c38", button_hover_color="#33404f",
            dropdown_fg_color="#0b0e14", dropdown_hover_color="#ff3b30", dropdown_text_color="#ffffff",
            font=(self.F_BODY, 13, "bold"), text_color="#fff", width=180)
        self.sel_riesgo.pack(side="right", padx=5, pady=15)
        self.lbl_riesgo_mult = ctk.CTkLabel(row3, text="", font=(self.F_MONO, 13, "bold"), text_color="#ff3b30")
        self.lbl_riesgo_mult.pack(side="right", padx=(10, 5), pady=15)

        # Cuadros de Contadores Elitizados
        self.counters_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.counters_frame.pack(fill="x", padx=25, pady=(15, 0))

        _, self.card_budget = self.create_counter_card(self.counters_frame, "INVERSIÓN ÓRDENES DE COMPRA (+2.5% Setup)", "#ffaa00")
        _, self.card_bag_value = self.create_counter_card(self.counters_frame, "VALOR DE LA MOCHILA", "#00d2ff")
        _, self.card_status = self.create_counter_card(self.counters_frame, "PROFIT ESTIMADO (MOCHILA - INVERSIÓN)", "#fff")
        self.card_pt_label, self.card_pt = self.create_counter_card(self.counters_frame, "PROFIT FINAL MERCADO NEGRO (-10.5% Imp.)", "#00ff66")

        self.lbl_desglose = ctk.CTkLabel(self, text="", font=(self.F_BODY, 12), text_color="#8b9bb4", anchor="w", justify="left")
        self.lbl_desglose.pack(fill="x", padx=33, pady=(6, 0))

        self.workspace = ctk.CTkFrame(self, fg_color="transparent")
        self.workspace.pack(fill="both", expand=True, padx=25, pady=(10, 25))

        self.left_panel = ctk.CTkFrame(self.workspace, fg_color="#0d1117", border_color="#21262d", border_width=1, corner_radius=14)
        self.left_panel.pack(side="left", fill="both", expand=True, padx=(0, 15))

        table_actions = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        table_actions.pack(fill="x", padx=20, pady=15)

        self.lbl_manifest = ctk.CTkEntry(table_actions, width=340, font=(self.F_DISPLAY, 18, "bold"), text_color="#fff",
                                         fg_color="transparent", border_width=0, justify="left")
        self.lbl_manifest.pack(side="left")

        self.btn_fase = ctk.CTkButton(table_actions, text="✓ LISTO: PASAR A VENTA M/N", fg_color="#ffaa00", text_color="#000", font=(self.F_DISPLAY, 12, "bold"), width=210, command=self.activar_fase_venta)
        self.btn_fase.pack(side="right", padx=5)

        self.btn_regresar = ctk.CTkButton(table_actions, text="↩ REGRESAR A FASE COMPRA", fg_color="#ff3b30", text_color="#fff", font=(self.F_DISPLAY, 12, "bold"), width=210, command=self.regresar_fase_compra)

        btn_add = ctk.CTkButton(table_actions, text="+ Meter Ítem", fg_color="#00d2ff", text_color="#000", font=(self.F_DISPLAY, 13, "bold"), width=110, command=self.add_item_row)
        btn_add.pack(side="right", padx=5)

        btn_pdf = ctk.CTkButton(table_actions, text="Generar PDF", fg_color="#00ff66", text_color="#000", font=(self.F_DISPLAY, 13, "bold"), width=170, command=self.export_to_pdf)
        btn_pdf.pack(side="right", padx=5)

        # Exportar a CSV/Excel (idea nueva: calidad de vida)
        btn_csv = ctk.CTkButton(table_actions, text="Exportar CSV", fg_color="#8b9bb4", text_color="#000", font=(self.F_DISPLAY, 13, "bold"), width=140, command=self.export_to_csv)
        btn_csv.pack(side="right", padx=5)

        # Archivar carga al historial (idea nueva: historial de rentabilidad)
        btn_archivar = ctk.CTkButton(table_actions, text="📌 Archivar al Historial", fg_color="#ffaa00", text_color="#000", font=(self.F_DISPLAY, 12, "bold"), width=190, command=self.archivar_carga)
        btn_archivar.pack(side="right", padx=5)

        self.table_scroll = ctk.CTkScrollableFrame(self.left_panel, fg_color="#090d13", corner_radius=10)
        self.table_scroll.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        headers_frame = ctk.CTkFrame(self.table_scroll, fg_color="transparent")
        headers_frame.pack(fill="x", pady=(5, 10))

        headers = ["★", "Estado", "Nombre del Ítem", "Cant.", "Tier", "Valor Compra O/C", "Precio de Venta"]
        widths = [30, 130, 300, 70, 80, 160, 160]
        for h, w in zip(headers, widths):
            lbl = ctk.CTkLabel(headers_frame, text=h, font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4", width=w, anchor="w" if h not in ("Estado", "★") else "center")
            lbl.pack(side="left", padx=4)

        # Panel Derecho (Utilidades Auxiliares)
        self.right_panel = ctk.CTkFrame(self.workspace, fg_color="#0d1117", width=380, border_color="#21262d", border_width=1, corner_radius=14)
        self.right_panel.pack(side="right", fill="y")
        self.right_panel.pack_propagate(False)

        # Pestañas del panel derecho: separamos Esencias/Notas de las cosas nuevas
        # (refinado, roundtrip, historial) para no amontonar todo en una sola columna.
        self.right_tabs = ctk.CTkTabview(self.right_panel, fg_color="#0d1117",
                                          segmented_button_fg_color="#161b22",
                                          segmented_button_selected_color="#ffaa00",
                                          segmented_button_selected_hover_color="#fff",
                                          segmented_button_unselected_color="#161b22",
                                          text_color="#000", width=360)
        self.right_tabs.pack(fill="both", expand=True, padx=10, pady=10)
        tab_general = self.right_tabs.add("General")
        tab_refino = self.right_tabs.add("Refino")
        tab_round = self.right_tabs.add("Roundtrip")
        tab_hist = self.right_tabs.add("Historial")

        # --- Tab General: esencias + notas (igual que antes) ---
        lbl_esencias = ctk.CTkLabel(tab_general, text="Runas, Almas y Reliquias", font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00")
        lbl_esencias.pack(pady=(10, 2), padx=5, anchor="w")

        self.essence_scroll = ctk.CTkFrame(tab_general, fg_color="#090d13", corner_radius=10)
        self.essence_scroll.pack(fill="x", padx=5, pady=5)
        self.render_essence_inputs()

        lbl_notas = ctk.CTkLabel(tab_general, text="Inteligencia de Zona / Notas Extra", font=(self.F_DISPLAY, 14, "bold"), text_color="#fff")
        lbl_notas.pack(pady=(15, 2), padx=5, anchor="w")

        self.txt_notes = ctk.CTkTextbox(tab_general, fg_color="#090d13", font=(self.F_BODY, 15), border_color="#21262d", border_width=1, height=160)
        self.txt_notes.pack(fill="both", expand=True, padx=5, pady=(0, 10))
        self.txt_notes.bind("<KeyRelease>", lambda e: self.save_notes())

        # --- Tab Refino: calculadora de ratio materia prima -> refinado ---
        self.build_tab_refino(tab_refino)

        # --- Tab Roundtrip: comprar en NPC y vender directo en Mercado Negro ---
        self.build_tab_roundtrip(tab_round)

        # --- Tab Historial: lista simple + "gráfico" ascii de rentabilidad ---
        self.build_tab_historial(tab_hist)

        if self.is_premium:
            self.switch_premium.select()

        self.load_hub_data()
        self.update_peso_ui()
        self.update_riesgo_ui()

    def update_live_clock(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        self.lbl_live_clock.configure(text=f"HORA LOCAL: {current_time}")
        self.update_oc_expira_label()
        self.after(1000, self.update_live_clock)

    def create_counter_card(self, parent, label_text, color):
        card = ctk.CTkFrame(parent, fg_color="#0d1117", border_color=color, border_width=1, corner_radius=14)
        card.pack(side="left", fill="x", expand=True, padx=8)
        lbl = ctk.CTkLabel(card, text=label_text, font=(self.F_BODY, 12, "bold"), text_color="#8b9bb4")
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
        cursor.execute("SELECT id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito FROM inventario WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
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
            db_id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito = db_row
        else:
            db_id, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito = (None, "processing", "", 1, "", 0.0, 0.0, 0)

        row_bg = "#161b22" if len(self.row_inputs) % 2 == 0 else "#12161d"
        row_frame = ctk.CTkFrame(self.table_scroll, fg_color=row_bg, corner_radius=8)
        row_frame.pack(fill="x", pady=5, padx=2)

        # Favorito / tag de ítem rentable (idea nueva: calidad de vida). Es solo
        # un toggle visual + guardado en DB, no afecta ningún cálculo.
        fav_var = {"on": bool(favorito)}
        btn_fav = ctk.CTkButton(row_frame, text=("★" if fav_var["on"] else "☆"), width=30, corner_radius=8,
                                 fg_color="transparent", hover_color="#ffaa00",
                                 text_color=("#ffaa00" if fav_var["on"] else "#8b9bb4"),
                                 font=(self.F_BODY, 15, "bold"),
                                 command=lambda: self.toggle_favorito(fav_var, btn_fav))
        btn_fav.pack(side="left", padx=4)

        status_sel = ctk.CTkOptionMenu(row_frame, values=list(STATUS_COLORS.keys()), width=130, font=(self.F_BODY, 13, "bold"), text_color="#000000")
        if status == "canceled":
            status_sel.set("✕ Cancelado")
        elif status == "processing":
            status_sel.set("⚙ En Proceso")
        else:
            status_sel.set("✓ Recibido")
        status_sel.configure(fg_color=STATUS_COLORS.get(status_sel.get(), "#1f242c"))

        def on_status_change(value, sel=status_sel):
            sel.configure(fg_color=STATUS_COLORS.get(value, "#1f242c"))
            # CAMBIO PEDIDO #3: el habilitado/deshabilitado del precio MN depende
            # del estado de ESTA fila (solo Recibido se puede tocar en venta).
            self.aplicar_bloqueo_precio_mn(self.row_inputs_lookup(sel))
            self.sync_and_calc()

        status_sel.configure(command=on_status_change)
        status_sel.pack(side="left", padx=4)

        ent_nombre = ctk.CTkEntry(row_frame, width=300, placeholder_text="Nombre Ítem...", fg_color="#090d13")
        ent_nombre.insert(0, nombre)
        ent_nombre.pack(side="left", padx=4)
        ent_nombre.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_cant = ctk.CTkEntry(row_frame, width=70, fg_color="#090d13", justify="center")
        ent_cant.insert(0, str(cantidad))
        ent_cant.pack(side="left", padx=4)
        ent_cant.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_tier = ctk.CTkEntry(row_frame, width=80, placeholder_text="T6.1", fg_color="#090d13", justify="center")
        ent_tier.insert(0, tier)
        ent_tier.pack(side="left", padx=4)
        ent_tier.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        ent_oc = ctk.CTkEntry(row_frame, width=160, fg_color="#090d13", text_color="#00d2ff")
        ent_oc.insert(0, str(int(valor_oc)))
        ent_oc.pack(side="left", padx=4)
        ent_oc.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        # El estado inicial del campo MN se resuelve más abajo con
        # aplicar_bloqueo_precio_mn, una vez que la fila ya está en row_inputs.
        ent_mn = ctk.CTkEntry(row_frame, width=160, fg_color="#1f242c", text_color="#00ff66", state="disabled")
        ent_mn.insert(0, str(int(precio_mn)))
        ent_mn.pack(side="left", padx=4)
        ent_mn.bind("<KeyRelease>", lambda e: self.sync_and_calc())

        btn_del = ctk.CTkButton(row_frame, text="✕", width=35, corner_radius=8, fg_color="transparent", hover_color="#ff3b30", text_color="#8b9bb4", font=(self.F_BODY, 14, "bold"), command=lambda: self.delete_row(db_id, row_frame))
        btn_del.pack(side="left", padx=6)

        row_data = {
            "db_id": db_id, "status": status_sel, "nombre": ent_nombre,
            "cantidad": ent_cant, "tier": ent_tier, "valor_oc": ent_oc, "precio_mn": ent_mn,
            "frame": row_frame, "favorito": fav_var, "btn_fav": btn_fav,
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
        """
        if row is None:
            return
        estado = row["status"].get()
        editable = self.fase_venta_activa and estado == "✓ Recibido"
        if editable:
            row["precio_mn"].configure(state="normal", fg_color="#090d13")
        else:
            row["precio_mn"].configure(state="disabled", fg_color="#1f242c")

    def toggle_favorito(self, fav_var, btn_fav):
        fav_var["on"] = not fav_var["on"]
        btn_fav.configure(text=("★" if fav_var["on"] else "☆"),
                           text_color=("#ffaa00" if fav_var["on"] else "#8b9bb4"))
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

        self.btn_fase.configure(text="FASE DE VENTA ACTIVA ✓", fg_color="#00ff66")
        self.btn_regresar.pack(side="right", padx=5)
        self.sync_and_calc()

    def regresar_fase_compra(self):
        self.fase_venta_activa = False
        for row in self.row_inputs:
            self.aplicar_bloqueo_precio_mn(row)

        self.btn_fase.configure(text="✓ LISTO: PASAR A VENTA M/N", fg_color="#ffaa00")
        self.btn_regresar.pack_forget()
        self.sync_and_calc()

    def add_item_row(self):
        self.create_row_ui()
        self.sync_and_calc()

    def render_essence_inputs(self):
        h_frame = ctk.CTkFrame(self.essence_scroll, fg_color="transparent")
        h_frame.pack(fill="x", pady=6)
        ctk.CTkLabel(h_frame, text="T", width=35, font=(self.F_BODY, 13, "bold"), text_color="#8b9bb4").pack(side="left", padx=4)
        ctk.CTkLabel(h_frame, text="Runas", width=85, font=(self.F_BODY, 13, "bold"), text_color="#8b9bb4").pack(side="left", padx=4)
        ctk.CTkLabel(h_frame, text="Almas", width=85, font=(self.F_BODY, 13, "bold"), text_color="#8b9bb4").pack(side="left", padx=4)
        ctk.CTkLabel(h_frame, text="Reliquias", width=85, font=(self.F_BODY, 13, "bold"), text_color="#8b9bb4").pack(side="left", padx=4)

        for t in range(4, 9):
            row = ctk.CTkFrame(self.essence_scroll, fg_color="transparent")
            row.pack(fill="x", pady=4)

            ctk.CTkLabel(row, text=f"T{t}", width=35, font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00").pack(side="left", padx=4)

            r_in = ctk.CTkEntry(row, width=85, fg_color="#090d13", justify="center")
            r_in.insert(0, "0")
            r_in.pack(side="left", padx=4)
            r_in.bind("<KeyRelease>", lambda e: self.save_esencias_and_calc())

            a_in = ctk.CTkEntry(row, width=85, fg_color="#090d13", justify="center")
            a_in.insert(0, "0")
            a_in.pack(side="left", padx=4)
            a_in.bind("<KeyRelease>", lambda e: self.save_esencias_and_calc())

            re_in = ctk.CTkEntry(row, width=85, fg_color="#090d13", justify="center")
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
                    INSERT INTO inventario (user_id, hub, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, oc_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (self.current_user_id, self.current_hub, status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, datetime.now().strftime("%Y-%m-%d %H:%M")))
                row["db_id"] = cursor.lastrowid
            else:
                cursor.execute('''
                    UPDATE inventario SET status=?, nombre=?, cantidad=?, tier=?, valor_oc=?, precio_mn=?, favorito=?
                    WHERE id=?
                ''', (status, nombre, cantidad, tier, valor_oc, precio_mn, favorito, row["db_id"]))
        conn.commit()
        conn.close()
        self.calculate_metrics()

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
        if db_id:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM inventario WHERE id = ?", (db_id,))
            conn.commit()
            conn.close()
        frame_widget.destroy()
        self.row_inputs = [r for r in self.row_inputs if r["db_id"] != db_id]
        self.calculate_metrics()

    def change_hub(self, chosen_hub):
        self.current_hub = chosen_hub
        self.fase_venta_activa = False
        self.btn_fase.configure(text="✓ LISTO: PASAR A VENTA M/N", fg_color="#ffaa00")
        self.btn_regresar.pack_forget()
        self.load_hub_data()

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
    # IDEAS NUEVAS: TEMA CLARO/OSCURO Y BACKUP DE DB (Calidad de vida)
    # ------------------------------------------------------------------ #
    def toggle_theme(self):
        modo_actual = ctk.get_appearance_mode()
        nuevo_modo = "Light" if modo_actual == "Dark" else "Dark"
        ctk.set_appearance_mode(nuevo_modo)

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
            self.lbl_peso_status.configure(text="Definí una capacidad", text_color="#8b9bb4")
            return

        pct = (peso / capacidad) * 100
        if pct <= 100:
            color = "#00ff66"
            texto = f"{pct:.0f}% de carga ✓"
        elif pct <= 140:
            color = "#ffaa00"
            texto = f"{pct:.0f}% ¡SOBRECARGA! (más lento)"
        else:
            color = "#ff3b30"
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
        try:
            inicio = datetime.strptime(self.ent_start_time.get().strip(), "%Y-%m-%d %H:%M")
        except Exception:
            self.lbl_oc_expira.configure(text="")
            return
        expira = inicio + timedelta(hours=OC_EXPIRA_HORAS)
        restante = expira - datetime.now()
        if restante.total_seconds() <= 0:
            self.lbl_oc_expira.configure(text="⚠ ÓRDENES DE COMPRA EXPIRADAS", text_color="#ff3b30")
        else:
            horas = int(restante.total_seconds() // 3600)
            minutos = int((restante.total_seconds() % 3600) // 60)
            color = "#ffaa00" if restante.total_seconds() > 3600 else "#ff3b30"
            self.lbl_oc_expira.configure(text=f"⏳ OC expira en {horas}h {minutos}m", text_color=color)

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: CALCULADORA DE REFINADO
    # ------------------------------------------------------------------ #
    def build_tab_refino(self, parent):
        ctk.CTkLabel(parent, text="Calculadora de Refinado", font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00").pack(pady=(10, 4), padx=5, anchor="w")
        ctk.CTkLabel(parent, text="Ratio base: 5 materia prima → 1 refinado (T4+).\nEl bonus de ciudad/foco reduce cuánta materia prima\nse gasta realmente por unidad refinada.",
                     font=(self.F_BODY, 12), text_color="#8b9bb4", justify="left").pack(padx=5, pady=(0, 10), anchor="w")

        frame_in = ctk.CTkFrame(parent, fg_color="#090d13", corner_radius=10)
        frame_in.pack(fill="x", padx=5, pady=5)

        ctk.CTkLabel(frame_in, text="Cantidad Refinado Deseado:", font=(self.F_BODY, 13, "bold"), text_color="#fff").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_refino_cantidad = ctk.CTkEntry(frame_in, placeholder_text="Ej: 100", fg_color="#0d1117")
        self.ent_refino_cantidad.insert(0, "100")
        self.ent_refino_cantidad.pack(fill="x", padx=10, pady=4)
        self.ent_refino_cantidad.bind("<KeyRelease>", lambda e: self.calcular_refino())

        ctk.CTkLabel(frame_in, text="Bonus de Ciudad (%):", font=(self.F_BODY, 13, "bold"), text_color="#fff").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_refino_bonus_ciudad = ctk.CTkEntry(frame_in, placeholder_text="Ej: 44 (capital de ese recurso)", fg_color="#0d1117")
        self.ent_refino_bonus_ciudad.insert(0, "0")
        self.ent_refino_bonus_ciudad.pack(fill="x", padx=10, pady=4)
        self.ent_refino_bonus_ciudad.bind("<KeyRelease>", lambda e: self.calcular_refino())

        ctk.CTkLabel(frame_in, text="Bonus de Foco/Especialización (%):", font=(self.F_BODY, 13, "bold"), text_color="#fff").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_refino_bonus_foco = ctk.CTkEntry(frame_in, placeholder_text="Ej: 25 (foco 100% activo)", fg_color="#0d1117")
        self.ent_refino_bonus_foco.insert(0, "0")
        self.ent_refino_bonus_foco.pack(fill="x", padx=10, pady=(4, 10))
        self.ent_refino_bonus_foco.bind("<KeyRelease>", lambda e: self.calcular_refino())

        self.lbl_refino_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 14, "bold"), text_color="#00d2ff", justify="left")
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
        ctk.CTkLabel(parent, text="Simulador de Roundtrip", font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00").pack(pady=(10, 4), padx=5, anchor="w")
        ctk.CTkLabel(parent, text="Comprar directo al NPC vendor (sin esperar\nOrden de Compra) y vender directo en el\nMercado Negro. Sin fee de setup del 2.5%.",
                     font=(self.F_BODY, 12), text_color="#8b9bb4", justify="left").pack(padx=5, pady=(0, 10), anchor="w")

        frame_in = ctk.CTkFrame(parent, fg_color="#090d13", corner_radius=10)
        frame_in.pack(fill="x", padx=5, pady=5)

        ctk.CTkLabel(frame_in, text="Precio Compra NPC (por unidad):", font=(self.F_BODY, 13, "bold"), text_color="#fff").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_rt_compra = ctk.CTkEntry(frame_in, placeholder_text="0", fg_color="#0d1117")
        self.ent_rt_compra.insert(0, "0")
        self.ent_rt_compra.pack(fill="x", padx=10, pady=4)
        self.ent_rt_compra.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        ctk.CTkLabel(frame_in, text="Precio Venta Mercado Negro (por unidad):", font=(self.F_BODY, 13, "bold"), text_color="#fff").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_rt_venta = ctk.CTkEntry(frame_in, placeholder_text="0", fg_color="#0d1117")
        self.ent_rt_venta.insert(0, "0")
        self.ent_rt_venta.pack(fill="x", padx=10, pady=4)
        self.ent_rt_venta.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        ctk.CTkLabel(frame_in, text="Cantidad de Unidades:", font=(self.F_BODY, 13, "bold"), text_color="#fff").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_rt_cantidad = ctk.CTkEntry(frame_in, placeholder_text="1", fg_color="#0d1117")
        self.ent_rt_cantidad.insert(0, "1")
        self.ent_rt_cantidad.pack(fill="x", padx=10, pady=(4, 10))
        self.ent_rt_cantidad.bind("<KeyRelease>", lambda e: self.calcular_roundtrip())

        self.switch_rt_premium = ctk.CTkSwitch(parent, text="Usar Premium en este cálculo (4% en vez de 8%)",
                                                font=(self.F_BODY, 12, "bold"), progress_color="#00ff66",
                                                command=self.calcular_roundtrip)
        self.switch_rt_premium.pack(padx=5, pady=(5, 10), anchor="w")

        self.lbl_rt_resultado = ctk.CTkLabel(parent, text="", font=(self.F_MONO, 14, "bold"), text_color="#00ff66", justify="left")
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

        color = "#00ff66" if profit >= 0 else "#ff3b30"
        self.lbl_rt_resultado.configure(
            text=(f"Inversión total (NPC): {int(inversion_total):,} silver\n"
                  f"Venta neta (M. Negro): {int(venta_neta):,} silver\n"
                  f"Profit Roundtrip: {int(profit):,} silver"),
            text_color=color
        )

    # ------------------------------------------------------------------ #
    # IDEA NUEVA: HISTORIAL DE CARGAS CON "GRÁFICO" DE RENTABILIDAD
    # ------------------------------------------------------------------ #
    def build_tab_historial(self, parent):
        ctk.CTkLabel(parent, text="Historial de Cargas Archivadas", font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00").pack(pady=(10, 4), padx=5, anchor="w")
        ctk.CTkLabel(parent, text="Usá el botón '📌 Archivar al Historial' arriba\nde la tabla para guardar una foto de esta\ncarga y compararla con las anteriores.",
                     font=(self.F_BODY, 12), text_color="#8b9bb4", justify="left").pack(padx=5, pady=(0, 10), anchor="w")

        self.historial_scroll = ctk.CTkScrollableFrame(parent, fg_color="#090d13", corner_radius=10, height=400)
        self.historial_scroll.pack(fill="both", expand=True, padx=5, pady=5)

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

        if not registros:
            ctk.CTkLabel(self.historial_scroll, text="(Sin cargas archivadas todavía)", font=(self.F_BODY, 12), text_color="#8b9bb4").pack(pady=10)
            return

        # "Gráfico" simple con barras de texto: cada carga es una fila con una
        # barra proporcional al profit (verde si es positivo, roja si es negativo).
        max_abs_profit = max(abs(r[5]) for r in registros) or 1

        for hub, destino, fecha, inversion, venta_neta, profit in registros:
            item_frame = ctk.CTkFrame(self.historial_scroll, fg_color="#161b22", corner_radius=8)
            item_frame.pack(fill="x", pady=4, padx=2)

            top_line = ctk.CTkLabel(item_frame, text=f"{fecha}  •  {hub} → {destino.split('(')[0].strip()}",
                                     font=(self.F_BODY, 12, "bold"), text_color="#fff", anchor="w")
            top_line.pack(fill="x", padx=10, pady=(8, 0))

            barra_len = int((abs(profit) / max_abs_profit) * 20)
            barra = "█" * max(barra_len, 1)
            color_barra = "#00ff66" if profit >= 0 else "#ff3b30"
            bar_line = ctk.CTkLabel(item_frame, text=f"{barra}  {int(profit):,} silver",
                                     font=(self.F_MONO, 13, "bold"), text_color=color_barra, anchor="w")
            bar_line.pack(fill="x", padx=10, pady=(2, 8))

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

        for row in self.row_inputs:
            status = row["status"].get()
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
            self.set_display_text(self.card_status, f"{int(profit_estimado_mochila):,} silver", "#00ff66" if profit_estimado_mochila >= 0 else "#ff3b30")
        else:
            self.set_display_text(self.card_status, "---", "#fff")

        costo_transporte = self.get_float(self.ent_costo_transporte.get()) if hasattr(self, "ent_costo_transporte") else 0.0

        if self.current_destino == DESTINO_HIDEOUT:
            self.card_pt_label.configure(text="COSTO TOTAL DE TRANSPORTE (Uso Personal a Hideout)")
            self.set_display_text(self.card_pt, f"{int(costo_transporte):,} silver", "#00d2ff")
            self.lbl_desglose.configure(
                text=("Carga con destino Hideout: no se vende en el Mercado Negro. "
                      f"Costo de transporte registrado: {int(costo_transporte):,} silver.")
            )
            profit_final_real = -costo_transporte
        else:
            tax_pct = int(round(tax_rate * 100))
            self.card_pt_label.configure(text=f"PROFIT FINAL MERCADO NEGRO (-{tax_pct}% Imp. -2.5% Ajuste)")
            profit_final_real = total_venta_neta - total_inversion_oc
            if self.fase_venta_activa:
                self.set_display_text(self.card_pt, f"{int(profit_final_real):,} silver", "#00ff66" if profit_final_real >= 0 else "#ff3b30")
                self.lbl_desglose.configure(
                    text=(f"Venta Bruta: {int(total_venta_bruta):,}  |  "
                          f"-{tax_pct}% Impuesto: -{int(total_impuesto):,}  |  "
                          f"-2.5% Ajuste: -{int(total_ajuste):,}  |  "
                          f"Neto: {int(total_venta_neta):,} silver")
                )
            else:
                self.set_display_text(self.card_pt, "---", "#00ff66")
                self.lbl_desglose.configure(text="Activa la fase de venta para ver el desglose de impuestos y ajuste.")

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
        conn.commit()
        conn.close()
        self.refresh_historial_tab()


if __name__ == "__main__":
    app = AlbionCargoApp()
    app.mainloop()
