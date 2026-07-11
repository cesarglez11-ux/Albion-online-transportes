import os
import sqlite3
import time
import textwrap
from datetime import datetime
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            region TEXT NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            hub TEXT NOT NULL,
            status TEXT NOT NULL,
            nombre TEXT,
            calidad TEXT,
            cantidad INTEGER,
            tier TEXT,
            valor_oc REAL,
            precio_mn REAL,
            FOREIGN KEY(user_id) REFERENCES usuarios(id)
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
    conn.commit()
    conn.close()

init_db()

class AlbionCargoApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Albion Online - Black Market Cargo Terminal")
        self.geometry("1420x920")
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
        self.fase_venta_activa = False
        
        self.row_inputs = []
        self.essence_inputs = {}
        self.last_metrics = {
            "inversion": 0.0, "mochila": 0.0, "venta_mn": 0.0,
            "profit_est": 0.0, "profit_final": 0.0, "fase_venta_activa": False,
        }

        # Click global para desenfocar las cajas de texto (Actúa como Esc)
        self.bind_all("<Button-1>", self.quitar_foco_clic)
        self.bind("<Escape>", lambda e: self.focus_set())

        self.show_auth_screen()

    def quitar_foco_clic(self, event):
        # Soltar foco solo si hacemos clic fuera de una entrada de texto
        try:
            widget = event.widget
            # Verificamos si el widget clickeado es parte de una caja de entrada
            if not isinstance(widget, (tk.Entry, tk.Text, tk.Listbox)):
                self.focus_set()
        except:
            pass

    def get_float(self, value):
        # Función a prueba de balas para convertir textos a números sin crashear
        try:
            val = str(value).replace(',', '.')
            if val.strip() == "": return 0.0
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

    def show_auth_screen(self):
        self.auth_frame = ctk.CTkFrame(self, fg_color="#0b0e14", border_color="#ffaa00", border_width=2, corner_radius=18, width=460, height=580)
        self.auth_frame.place(relx=0.5, rely=0.5, anchor="center")
        self.auth_frame.pack_propagate(False)
        
        title_label = ctk.CTkLabel(self.auth_frame, text="LOGISTICS TERMINAL", font=(self.F_DISPLAY, 28, "bold"), text_color="#ffaa00")
        title_label.pack(pady=(40, 5))
        sub_label = ctk.CTkLabel(self.auth_frame, text="Control de Manifiestos y Rutas de Contrabando", font=(self.F_BODY, 14), text_color="#8b9bb4")
        sub_label.pack(pady=(0, 30))
        
        lbl_user = ctk.CTkLabel(self.auth_frame, text="Nombre del Personaje:", font=(self.F_BODY, 15, "bold"), text_color="#fff")
        lbl_user.pack(anchor="w", padx=45, pady=(10, 2))
        self.ent_username = ctk.CTkEntry(self.auth_frame, placeholder_text="Ej: XitSsoTox", fg_color="#06080c", font=(self.F_BODY, 16), border_color="#21262d")
        self.ent_username.pack(fill="x", padx=45, pady=5)
        
        lbl_region = ctk.CTkLabel(self.auth_frame, text="Servidor Oficial / Región:", font=(self.F_BODY, 15, "bold"), text_color="#fff")
        lbl_region.pack(anchor="w", padx=45, pady=(10, 2))
        
        self.sel_region = ctk.CTkOptionMenu(self.auth_frame, values=["Albion West (América)", "Albion East (Asia)", "Albion Europe (Europa)"], fg_color="#06080c", button_color="#1f242c", font=(self.F_BODY, 16))
        self.sel_region.pack(fill="x", padx=45, pady=5)
        
        lbl_pass = ctk.CTkLabel(self.auth_frame, text="Clave de Encriptación de Datos:", font=(self.F_BODY, 15, "bold"), text_color="#fff")
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
            self.ent_password.focus_set() # Te pone directo para teclear la clave

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
        cursor.execute("SELECT id, region, password FROM usuarios WHERE username = ?", (username,))
        row = cursor.fetchone()
        
        if row:
            user_id, db_region, db_password = row
            if db_password == password:
                self.current_user_id = user_id
                self.current_username = username
                self.current_region = db_region
                self.auth_frame.destroy()
                self.show_main_hud()
            else:
                messagebox.showerror("Error", "Clave incorrecta.")
        else:
            cursor.execute("INSERT INTO usuarios (username, region, password) VALUES (?, ?, ?)", (username, region, password))
            conn.commit()
            self.current_user_id = cursor.lastrowid
            self.current_username = username
            self.current_region = region
            self.auth_frame.destroy()
            self.show_main_hud()
        conn.close()

    def show_main_hud(self):
        # Header Principal
        self.header_frame = ctk.CTkFrame(self, fg_color="#0d1117", border_color="#ffaa00", border_width=1, corner_radius=14, height=90)
        self.header_frame.pack(fill="x", padx=25, pady=(20, 10))
        self.header_frame.pack_propagate(False)
        
        title_txt = f"PILOTO: {self.current_username.upper()} • {self.current_region.upper()}"
        # Entry de solo lectura: se puede seleccionar/copiar pero no editar.
        self.ent_title = ctk.CTkEntry(self.header_frame, width=340, font=(self.F_DISPLAY, 18, "bold"), text_color="#fff",
                                      fg_color="transparent", border_width=0, justify="left")
        self.ent_title.pack(side="left", padx=25, pady=30)
        self.set_display_text(self.ent_title, title_txt)
        
        self.lbl_live_clock = ctk.CTkLabel(self.header_frame, text="", font=(self.F_MONO, 14, "bold"), text_color="#00d2ff")
        self.lbl_live_clock.pack(side="left", padx=50, pady=30)
        self.update_live_clock()
        
        self.hub_selector = ctk.CTkOptionMenu(self.header_frame, values=["Fort Sterling", "Lymhurst", "Bridgewatch", "Martlock", "Thetford", "Caerleon"], command=self.change_hub, fg_color="#090d13", button_color="#1f242c", font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00", width=160)
        self.hub_selector.set(self.current_hub)
        self.hub_selector.pack(side="right", padx=25, pady=30)

        # Bloque Tiempos y MOCHILA (Movido Arriba)
        self.top_bar = ctk.CTkFrame(self, fg_color="#0d1117", border_color="#21262d", border_width=1, corner_radius=14)
        self.top_bar.pack(fill="x", padx=25, pady=10)
        
        lbl_t1 = ctk.CTkLabel(self.top_bar, text="Inicio Carga/Compra:", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        lbl_t1.pack(side="left", padx=(20, 10), pady=15)
        self.ent_start_time = ctk.CTkEntry(self.top_bar, placeholder_text="Ej: 2026-07-11 12:00", width=160, fg_color="#090d13")
        self.ent_start_time.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.ent_start_time.pack(side="left", padx=5, pady=15)
        
        lbl_t2 = ctk.CTkLabel(self.top_bar, text="Fin Carga/Venta:", font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4")
        lbl_t2.pack(side="left", padx=(20, 10), pady=15)
        self.ent_end_time = ctk.CTkEntry(self.top_bar, placeholder_text="En curso...", width=160, fg_color="#090d13")
        self.ent_end_time.pack(side="left", padx=5, pady=15)

        # Mochila Manual, Global e Independiente
        lbl_mochila_section = ctk.CTkLabel(self.top_bar, text="Valor Neto Mochila (Inventario):", font=(self.F_DISPLAY, 14, "bold"), text_color="#00d2ff")
        lbl_mochila_section.pack(side="left", padx=(40, 10), pady=15)
        
        self.ent_mochila_global = ctk.CTkEntry(self.top_bar, placeholder_text="0", fg_color="#090d13", font=(self.F_MONO, 14, "bold"), text_color="#00d2ff", width=180)
        self.ent_mochila_global.insert(0, "0")
        self.ent_mochila_global.pack(side="left", padx=5, pady=15)
        self.ent_mochila_global.bind("<KeyRelease>", lambda e: self.calculate_metrics())

        # Cuadros de Contadores Elitizados
        self.counters_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.counters_frame.pack(fill="x", padx=25, pady=15)
        
        self.card_budget = self.create_counter_card(self.counters_frame, "INVERSIÓN ÓRDENES DE COMPRA (+2.5% Setup)", "#ffaa00")
        self.card_bag_value = self.create_counter_card(self.counters_frame, "VALOR DECLARADO MOCHILA", "#00d2ff")
        self.card_status = self.create_counter_card(self.counters_frame, "PROFIT ESTIMADO (MOCHILA - INVERSIÓN)", "#fff")
        self.card_pt = self.create_counter_card(self.counters_frame, "PROFIT FINAL MERCADO NEGRO (-10.5% Imp.)", "#00ff66")

        # Workspace Central
        self.workspace = ctk.CTkFrame(self, fg_color="transparent")
        self.workspace.pack(fill="both", expand=True, padx=25, pady=(10, 25))
        
        # Panel Izquierdo (Tabla de Ítems)
        self.left_panel = ctk.CTkFrame(self.workspace, fg_color="#0d1117", border_color="#21262d", border_width=1, corner_radius=14)
        self.left_panel.pack(side="left", fill="both", expand=True, padx=(0, 15))
        
        table_actions = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        table_actions.pack(fill="x", padx=20, pady=15)
        
        # Entry de solo lectura para el título del manifiesto (seleccionable/copiable)
        self.lbl_manifest = ctk.CTkEntry(table_actions, width=260, font=(self.F_DISPLAY, 18, "bold"), text_color="#fff",
                                         fg_color="transparent", border_width=0, justify="left")
        self.lbl_manifest.pack(side="left")
        self.set_display_text(self.lbl_manifest, f"Ruta de Origen: {self.current_hub}")

        self.btn_fase = ctk.CTkButton(table_actions, text="✓ LISTO: PASAR A VENTA M/N", fg_color="#ffaa00", text_color="#000", font=(self.F_DISPLAY, 12, "bold"), width=210, command=self.activar_fase_venta)
        self.btn_fase.pack(side="right", padx=5)

        self.btn_regresar = ctk.CTkButton(table_actions, text="↩ REGRESAR A FASE COMPRA", fg_color="#ff3b30", text_color="#fff", font=(self.F_DISPLAY, 12, "bold"), width=210, command=self.regresar_fase_compra)

        btn_add = ctk.CTkButton(table_actions, text="+ Meter Ítem", fg_color="#00d2ff", text_color="#000", font=(self.F_DISPLAY, 13, "bold"), width=110, command=self.add_item_row)
        btn_add.pack(side="right", padx=5)
        
        btn_pdf = ctk.CTkButton(table_actions, text="Generar Manifiesto PDF", fg_color="#00ff66", text_color="#000", font=(self.F_DISPLAY, 13, "bold"), width=170, command=self.export_to_pdf)
        btn_pdf.pack(side="right", padx=5)

        self.table_scroll = ctk.CTkScrollableFrame(self.left_panel, fg_color="#090d13", corner_radius=10)
        self.table_scroll.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        
        headers_frame = ctk.CTkFrame(self.table_scroll, fg_color="transparent")
        headers_frame.pack(fill="x", pady=(5, 10))
        
        headers = ["Estado Logística", "Nombre del Ítem", "Calidad", "Cant.", "Tier", "Valor Compra O/C", "Precio Caerleon M/N"]
        widths = [130, 210, 130, 60, 70, 130, 130]
        for h, w in zip(headers, widths):
            lbl = ctk.CTkLabel(headers_frame, text=h, font=(self.F_BODY, 14, "bold"), text_color="#8b9bb4", width=w, anchor="w" if h != "Estado Logística" else "center")
            lbl.pack(side="left", padx=4)

        # Panel Derecho (Utilidades Auxiliares)
        self.right_panel = ctk.CTkFrame(self.workspace, fg_color="#0d1117", width=360, border_color="#21262d", border_width=1, corner_radius=14)
        self.right_panel.pack(side="right", fill="y")
        self.right_panel.pack_propagate(False)
        
        lbl_esencias = ctk.CTkLabel(self.right_panel, text="Refino / Precios de Esencias", font=(self.F_DISPLAY, 14, "bold"), text_color="#ffaa00")
        lbl_esencias.pack(pady=(15, 2), padx=15, anchor="w")
        
        self.essence_scroll = ctk.CTkFrame(self.right_panel, fg_color="#090d13", corner_radius=10)
        self.essence_scroll.pack(fill="x", padx=15, pady=5)
        self.render_essence_inputs()

        lbl_notas = ctk.CTkLabel(self.right_panel, text="Inteligencia de Zona / Notas Extra", font=(self.F_DISPLAY, 14, "bold"), text_color="#fff")
        lbl_notas.pack(pady=(15, 2), padx=15, anchor="w")
        
        self.txt_notes = ctk.CTkTextbox(self.right_panel, fg_color="#090d13", font=(self.F_BODY, 15), border_color="#21262d", border_width=1)
        self.txt_notes.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.txt_notes.bind("<KeyRelease>", lambda e: self.save_notes())

        self.load_hub_data()

    def update_live_clock(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        self.lbl_live_clock.configure(text=f"HORA LOCAL: {current_time}")
        self.after(1000, self.update_live_clock)

    def create_counter_card(self, parent, label_text, color):
        card = ctk.CTkFrame(parent, fg_color="#0d1117", border_color=color, border_width=1, corner_radius=14)
        card.pack(side="left", fill="x", expand=True, padx=8)
        lbl = ctk.CTkLabel(card, text=label_text, font=(self.F_BODY, 12, "bold"), text_color="#8b9bb4")
        lbl.pack(pady=(12, 4), padx=18, anchor="w")
        # CTkEntry en modo 'readonly': el valor se puede seleccionar y copiar
        # (mouse / Ctrl+C) pero no se puede escribir ni borrar sobre él.
        val = ctk.CTkEntry(card, font=(self.F_MONO, 18, "bold"), text_color=color,
                            fg_color="transparent", border_width=0, justify="left")
        val.pack(pady=(0, 12), padx=18, anchor="w", fill="x")
        val.insert(0, "---")
        val.configure(state="readonly")
        return val

    def load_hub_data(self):
        self.set_display_text(self.lbl_manifest, f"Ruta de Origen: {self.current_hub}")
        for row in self.row_inputs:
            row["frame"].destroy()
        self.row_inputs.clear()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, nombre, calidad, cantidad, tier, valor_oc, precio_mn FROM inventario WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
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

        # Cargar data de esencias si existe para esta ciudad
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
            
        cursor.execute("SELECT texto FROM notas WHERE user_id = ? AND hub = ?", (self.current_user_id, self.current_hub))
        nota = cursor.fetchone()
        self.txt_notes.delete("1.0", "end")
        if nota: 
            self.txt_notes.insert("1.0", nota[0])
        conn.close()
        self.calculate_metrics()

    def create_row_ui(self, db_row=None):
        db_id, status, nombre, calidad, cantidad, tier, valor_oc, precio_mn = db_row if db_row else (None, "checked", "", "Normal", 1, "", 0.0, 0.0)
        
        # Alternamos el color de fondo de cada fila (zebra striping) para
        # que sea más fácil seguir una línea con la vista en manifiestos largos.
        row_bg = "#161b22" if len(self.row_inputs) % 2 == 0 else "#12161d"
        row_frame = ctk.CTkFrame(self.table_scroll, fg_color=row_bg, corner_radius=8)
        row_frame.pack(fill="x", pady=5, padx=2)
        
        status_sel = ctk.CTkOptionMenu(row_frame, values=["✓ Recibido", "⚙ En Proceso", "✕ Cancelado"], width=130, font=(self.F_BODY, 13, "bold"), command=lambda v: self.sync_and_calc())
        if status == "canceled": status_sel.set("✕ Cancelado")
        elif status == "processing": status_sel.set("⚙ En Proceso")
        else: status_sel.set("✓ Recibido")
        status_sel.pack(side="left", padx=4)
        
        ent_nombre = ctk.CTkEntry(row_frame, width=210, placeholder_text="Nombre Ítem...", fg_color="#090d13")
        ent_nombre.insert(0, nombre)
        ent_nombre.pack(side="left", padx=4)
        ent_nombre.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        
        sel_calidad = ctk.CTkOptionMenu(row_frame, values=["Normal", "Bueno", "Notable", "Sobresaliente", "Obra Maestra"], width=130, command=lambda v: self.sync_and_calc())
        sel_calidad.set(calidad)
        sel_calidad.pack(side="left", padx=4)
        
        ent_cant = ctk.CTkEntry(row_frame, width=60, fg_color="#090d13", justify="center")
        ent_cant.insert(0, str(cantidad))
        ent_cant.pack(side="left", padx=4)
        ent_cant.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        
        ent_tier = ctk.CTkEntry(row_frame, width=70, placeholder_text="T6.1", fg_color="#090d13", justify="center")
        ent_tier.insert(0, tier)
        ent_tier.pack(side="left", padx=4)
        ent_tier.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        
        ent_oc = ctk.CTkEntry(row_frame, width=130, fg_color="#090d13", text_color="#00d2ff")
        ent_oc.insert(0, str(int(valor_oc)))
        ent_oc.pack(side="left", padx=4)
        ent_oc.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        
        state_mn = "normal" if self.fase_venta_activa else "disabled"
        fg_mn = "#090d13" if self.fase_venta_activa else "#1f242c"
        
        ent_mn = ctk.CTkEntry(row_frame, width=130, fg_color=fg_mn, text_color="#00ff66", state=state_mn)
        ent_mn.insert(0, str(int(precio_mn)))
        ent_mn.pack(side="left", padx=4)
        ent_mn.bind("<KeyRelease>", lambda e: self.sync_and_calc())
        
        btn_del = ctk.CTkButton(row_frame, text="✕", width=35, corner_radius=8, fg_color="transparent", hover_color="#ff3b30", text_color="#8b9bb4", font=(self.F_BODY, 14, "bold"), command=lambda: self.delete_row(db_id, row_frame))
        btn_del.pack(side="left", padx=6)
        
        self.row_inputs.append({
            "db_id": db_id, "status": status_sel, "nombre": ent_nombre, "calidad": sel_calidad,
            "cantidad": ent_cant, "tier": ent_tier, "valor_oc": ent_oc, "precio_mn": ent_mn, "frame": row_frame
        })

    def activar_fase_venta(self):
        if not self.row_inputs:
            messagebox.showwarning("Manifiesto Vacío", "No tienes ningún ítem registrado.")
            return

        self.fase_venta_activa = True
        for row in self.row_inputs:
            if row["status"].get() == "⚙ En Proceso":
                row["status"].set("✓ Recibido")
            row["precio_mn"].configure(state="normal", fg_color="#090d13")
        
        self.btn_fase.configure(text="FASE DE VENTA ACTIVA ✓", fg_color="#00ff66")
        self.btn_regresar.pack(side="right", padx=5)
        self.sync_and_calc()

    def regresar_fase_compra(self):
        self.fase_venta_activa = False
        for row in self.row_inputs:
            row["precio_mn"].configure(state="disabled", fg_color="#1f242c")
        
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
            calidad = row["calidad"].get()
            cantidad = int(self.get_float(row["cantidad"].get()))
            tier = row["tier"].get()
            valor_oc = self.get_float(row["valor_oc"].get())
            precio_mn = self.get_float(row["precio_mn"].get())
            
            if row["db_id"] is None:
                cursor.execute('''
                    INSERT INTO inventario (user_id, hub, status, nombre, calidad, cantidad, tier, valor_oc, precio_mn)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (self.current_user_id, self.current_hub, status, nombre, calidad, cantidad, tier, valor_oc, precio_mn))
                row["db_id"] = cursor.lastrowid
            else:
                cursor.execute('''
                    UPDATE inventario SET status=?, nombre=?, calidad=?, cantidad=?, tier=?, valor_oc=?, precio_mn=?
                    WHERE id=?
                ''', (status, nombre, calidad, cantidad, tier, valor_oc, precio_mn, row["db_id"]))
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

    def calculate_metrics(self):
        total_inversion_oc = 0.0
        total_venta_mn = 0.0
        
        val_mochila_manual = self.get_float(self.ent_mochila_global.get())
        
        for row in self.row_inputs:
            status = row["status"].get()
            # Validacion Estricta: Si está cancelado, ignorar el coste completamente
            if status == "✕ Cancelado":
                continue
                
            cantidad = int(self.get_float(row["cantidad"].get()))
            if cantidad == 0: cantidad = 1 # Prevencion para que no multiplique por 0 si borran el texto
            
            valor_oc = self.get_float(row["valor_oc"].get())
            precio_mn = self.get_float(row["precio_mn"].get())
            
            total_inversion_oc += (cantidad * valor_oc * 1.025)
            total_venta_mn += (cantidad * precio_mn * (1 - 0.105))

        self.set_display_text(self.card_budget, f"{int(total_inversion_oc):,} silver")

        # Independientemente de la fase, la mochila ahora siempre muestra el valor ingresado arriba
        self.set_display_text(self.card_bag_value, f"{int(val_mochila_manual):,} silver")

        profit_estimado_mochila = val_mochila_manual - total_inversion_oc
        profit_final_real = total_venta_mn - total_inversion_oc

        if self.fase_venta_activa:
            self.set_display_text(self.card_status, f"{int(profit_estimado_mochila):,} silver", "#00ff66" if profit_estimado_mochila >= 0 else "#ff3b30")
            self.set_display_text(self.card_pt, f"{int(profit_final_real):,} silver", "#00ff66" if profit_final_real >= 0 else "#ff3b30")
        else:
            self.set_display_text(self.card_status, "---", "#fff")
            self.set_display_text(self.card_pt, "---", "#00ff66")

        # Guardamos los últimos valores calculados para poder volcarlos completos al PDF
        self.last_metrics = {
            "inversion": total_inversion_oc,
            "mochila": val_mochila_manual,
            "venta_mn": total_venta_mn,
            "profit_est": profit_estimado_mochila,
            "profit_final": profit_final_real,
            "fase_venta_activa": self.fase_venta_activa,
        }

    def export_to_pdf(self):
        if not self.row_inputs:
            messagebox.showwarning("PDF Vacío", "No hay datos en la tabla para exportar.")
            return

        # Aseguramos que todo esté guardado y recalculado antes de exportar
        self.sync_and_calc()

        # Diálogo para que elijas dónde y cómo guardar
        filename = filedialog.asksaveasfilename(
            title="Guardar Manifiesto PDF",
            defaultextension=".pdf",
            initialfile=f"Manifiesto_{self.current_hub.replace(' ', '_')}.pdf",
            filetypes=[("Archivos PDF", "*.pdf")]
        )
        
        if not filename:
            return # Cancelaste la ventana de guardar

        start_str = self.ent_start_time.get().strip()
        end_str = self.ent_end_time.get().strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
        
        def to_utc_str(local_str):
            try:
                dt = datetime.strptime(local_str, "%Y-%m-%d %H:%M")
                utc_timestamp = time.mktime(dt.timetuple()) + (6 * 3600)
                dt_utc = datetime.utcfromtimestamp(utc_timestamp)
                return dt_utc.strftime("%Y-%m-%d %H:%M") + " UTC"
            except:
                return "En curso..."

        start_utc = to_utc_str(start_str)
        end_utc = to_utc_str(end_str)

        c = canvas.Canvas(filename, pagesize=letter)
        w, h = letter
        BOTTOM_MARGIN = 60

        def draw_page_background():
            c.setFillColorRGB(0.04, 0.05, 0.07)
            c.rect(0, 0, w, h, fill=1)

        def ensure_space(y, needed=20):
            """Si no cabe otra línea, saltamos de página y devolvemos la nueva y."""
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
        c.drawString(40, h - 50, f"CAERLEON FREIGHT FREQUENCY REPORT: {self.current_hub.upper()}")
        
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.6, 0.7, 0.8)
        c.drawString(40, h - 75, f"Piloto: {self.current_username}   |   Servidor: {self.current_region}")
        
        c.setFillColorRGB(1, 1, 1)
        c.drawString(40, h - 100, f"REGISTRO INICIAL LOGÍSTICA: {start_str} (Local)  /  {start_utc}")
        c.drawString(40, h - 115, f"REGISTRO FINAL LOGÍSTICA: {end_str} (Local)  /  {end_utc}")
        estado_fase = "VENTA MERCADO NEGRO ACTIVA" if self.last_metrics.get("fase_venta_activa") else "FASE DE COMPRA (Venta aún no iniciada)"
        c.drawString(40, h - 130, f"ESTADO DEL MANIFIESTO: {estado_fase}")

        y = h - 160

        # --- RESUMEN FINANCIERO COMPLETO (todo lo que se ve arriba en la app) ---
        y = draw_section_title(y, "RESUMEN FINANCIERO GENERAL")
        m = self.last_metrics
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(1, 1, 1)
        resumen_lines = [
            f"Inversión Órdenes de Compra (+2.5% Setup): {int(m['inversion']):,} silver",
            f"Valor Declarado Mochila: {int(m['mochila']):,} silver",
            f"Total Venta Proyectada en Mercado Negro (bruto, -10.5% impuesto ya aplicado): {int(m['venta_mn']):,} silver",
            f"Profit Estimado (Mochila - Inversión): {int(m['profit_est']):,} silver",
            f"Profit Final Mercado Negro (Venta M/N - Inversión, -10.5% Imp.): {int(m['profit_final']):,} silver",
        ]
        for line in resumen_lines:
            y = ensure_space(y, 16)
            c.drawString(40, y, line)
            y -= 16

        y -= 10

        # --- TABLA DE ÍTEMS (incluye cancelados y ambas monedas) ---
        y = draw_section_title(y, "MANIFIESTO DE ÍTEMS (incluye cancelados)")

        def draw_table_headers(y):
            c.setFillColorRGB(1, 0.66, 0)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(40, y, "Estado")
            c.drawString(115, y, "Ítem")
            c.drawString(250, y, "Calidad")
            c.drawString(320, y, "Cant.")
            c.drawString(350, y, "Tier")
            c.drawString(385, y, "Costo Compra O/C")
            c.drawString(480, y, "Precio Venta M/N")
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

            cantidad_val = self.get_float(row["cantidad"].get())
            valor_oc_raw = self.get_float(row["valor_oc"].get())
            precio_mn_raw = self.get_float(row["precio_mn"].get())
            costo_oc_total = valor_oc_raw * 1.025
            venta_mn_total = precio_mn_raw * (1 - 0.105)

            c.setFillColorRGB(1, 0.3, 0.3) if cancelado else c.setFillColorRGB(1, 1, 1)
            c.drawString(40, y, status)
            c.drawString(115, y, (row["nombre"].get() or "(sin nombre)")[:26])
            c.drawString(250, y, row["calidad"].get())
            c.drawString(320, y, row["cantidad"].get())
            c.drawString(350, y, row["tier"].get())

            if cancelado:
                c.drawString(385, y, f"{int(costo_oc_total):,} (Cancelado, no incluido)")
                c.drawString(480, y, f"{int(venta_mn_total):,} (Cancelado, no incluido)")
            else:
                c.drawString(385, y, f"{int(costo_oc_total):,} silver")
                c.drawString(480, y, f"{int(venta_mn_total):,} silver")

            y -= 16

        c.setFillColorRGB(1, 1, 1)
        y -= 10

        # --- ESENCIAS / REFINO ---
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

        # --- NOTAS / INTELIGENCIA DE ZONA ---
        y = draw_section_title(y, "INTELIGENCIA DE ZONA / NOTAS")
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
        messagebox.showinfo("Manifiesto Exportado", f"Archivo guardado exitosamente en:\n{filename}")

if __name__ == "__main__":
    app = AlbionCargoApp()
    app.mainloop()
