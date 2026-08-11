"""
Interface gráfica (Tkinter — já vem com o Python, nada novo pra instalar).

Reaproveita 100% da lógica de flowlist/pipeline.py (o mesmo núcleo que o
CLI usa) — a GUI só coleta os campos, roda o pipeline numa thread separada
(pra não travar a janela) e mostra o log + a tabela de resultado.
"""

from __future__ import annotations

import contextlib
import io
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from dotenv import load_dotenv
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOauthError

from . import config, errors, pipeline, spotify_client
from .enrichment import key_mode_to_camelot

APP_TITLE = "flowlist — playlists Spotify em ordem de mixagem de DJ"

LOGIN_TIMEOUT_SECONDS = 120


def _get_authorized_client(account: str | None):
    """Autentica com um timeout de verdade.

    O spotipy, sem token em cache, abre um mini-servidor local e ESPERA PRA
    SEMPRE a resposta do navegador — se `webbrowser.open()` falhar ou abrir
    sem foco (acontece), trava em silêncio, sem exceção nem log nenhum
    (o erro dela vai por `logging`, não por print, e nem por aí a GUI
    percebe). Aqui a autenticação roda numa sub-thread com timeout; se não
    voltar a tempo, a GUI recupera o controle e mostra a URL pra abrir na
    mão em vez de ficar "Rodando…" pra sempre sem explicação.
    """
    sp = spotify_client.get_spotify_client(account=account)
    auth_url = sp.auth_manager.get_authorize_url()
    print(
        f"🌐 Se o navegador não abrir sozinho em alguns segundos, copie e cole esta "
        f"URL nele pra fazer login:\n   {auth_url}\n"
    )

    result: dict = {}

    def _touch() -> None:
        try:
            sp.current_user()  # chamada pequena só pra forçar o login agora
            result["ok"] = True
        except Exception as e:  # noqa: BLE001
            result["error"] = e

    t = threading.Thread(target=_touch, daemon=True)
    t.start()
    t.join(timeout=LOGIN_TIMEOUT_SECONDS)
    if t.is_alive():
        raise TimeoutError(
            f"O login não foi concluído em {LOGIN_TIMEOUT_SECONDS}s. Confira se um "
            "navegador abriu (às vezes abre atrás de outras janelas — olha a barra de "
            "tarefas) e faça login/autorize lá. Se nada abriu, copie a URL impressa no "
            "log acima e cole no navegador na mão."
        )
    if "error" in result:
        raise result["error"]
    return sp


class _QueueWriter(io.TextIOBase):
    """Arquivo-like que joga cada .write() numa fila, pra thread de fundo
    mandar o log pra thread da GUI sem mexer direto em widget Tkinter
    (Tkinter não é thread-safe)."""

    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, s: str) -> int:
        if s:
            self._q.put(("log", s))
        return len(s)

    def flush(self) -> None:
        pass


class FlowlistGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("880x640")
        root.minsize(760, 560)

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_result: pipeline.RunResult | None = None

        self._build_widgets()
        self._refresh_env_files()
        self.root.after(100, self._poll_queue)

    # ---------------------------------------------------------- layout ----

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        source_frame = ttk.LabelFrame(self.root, text="Fonte")
        source_frame.pack(fill="x", **pad)

        self.mode = tk.StringVar(value="artist")
        ttk.Radiobutton(
            source_frame, text="Melhores músicas de um artista", variable=self.mode,
            value="artist", command=self._update_mode
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 0))
        ttk.Radiobutton(
            source_frame, text="Remixar playlist minha já existente", variable=self.mode,
            value="playlist", command=self._update_mode
        ).grid(row=0, column=1, sticky="w", padx=6, pady=(6, 0))

        ttk.Label(source_frame, text="Artista:").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.artist_entry = ttk.Entry(source_frame, width=40)
        self.artist_entry.grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(source_frame, text="Top N:").grid(row=1, column=2, sticky="e", padx=6, pady=4)
        self.top_entry = ttk.Spinbox(source_frame, from_=5, to=500, width=6)
        self.top_entry.set(30)
        self.top_entry.grid(row=1, column=3, sticky="w", padx=6, pady=4)

        ttk.Label(source_frame, text="URL/ID da playlist:").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.playlist_entry = ttk.Entry(source_frame, width=55)
        self.playlist_entry.grid(row=2, column=1, columnspan=3, sticky="w", padx=6, pady=4)

        opts_frame = ttk.LabelFrame(self.root, text="Opções")
        opts_frame.pack(fill="x", **pad)

        ttk.Label(opts_frame, text="Credenciais (.env):").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.env_combo = ttk.Combobox(opts_frame, width=22, state="readonly")
        self.env_combo.grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Button(opts_frame, text="Recarregar lista", command=self._refresh_env_files).grid(
            row=0, column=2, sticky="w", padx=6, pady=4
        )

        ttk.Label(opts_frame, text="Nome da playlist (opcional):").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.name_entry = ttk.Entry(opts_frame, width=40)
        self.name_entry.grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=4)

        self.public_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts_frame, text="Playlist pública", variable=self.public_var).grid(
            row=2, column=0, sticky="w", padx=6, pady=2
        )
        self.getsongbpm_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frame, text="Usar getsongbpm.com se a Spotify bloquear BPM/tom",
            variable=self.getsongbpm_var
        ).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        self.refresh_cache_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frame, text="Forçar busca nova (ignorar cache local)",
            variable=self.refresh_cache_var
        ).grid(row=2, column=2, sticky="w", padx=6, pady=2)

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        self.preview_btn = ttk.Button(btn_frame, text="🔍 Pré-visualizar", command=self._on_preview)
        self.preview_btn.pack(side="left", padx=4)
        self.create_btn = ttk.Button(
            btn_frame, text="✅ Criar playlist no Spotify", command=self._on_create, state="disabled"
        )
        self.create_btn.pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(btn_frame, textvariable=self.status_var, foreground="#555").pack(side="left", padx=12)

        # --- resultado: tabela + log, lado a lado ---
        results_frame = ttk.PanedWindow(self.root, orient="horizontal")
        results_frame.pack(fill="both", expand=True, **pad)

        table_frame = ttk.Frame(results_frame)
        columns = ("pos", "faixa", "artista", "bpm", "tom", "nota")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=18)
        headers = {
            "pos": ("#", 32), "faixa": ("Faixa", 220), "artista": ("Artista", 140),
            "bpm": ("BPM", 55), "tom": ("Tom", 55), "nota": ("Transição", 200),
        }
        for col, (label, width) in headers.items():
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        results_frame.add(table_frame, weight=3)

        log_frame = ttk.Frame(results_frame)
        self.log_text = tk.Text(log_frame, width=40, wrap="word", state="disabled", bg="#111", fg="#ddd")
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_vsb.pack(side="right", fill="y")
        results_frame.add(log_frame, weight=2)

        self._update_mode()

    def _update_mode(self) -> None:
        artist_mode = self.mode.get() == "artist"
        self.artist_entry.configure(state="normal" if artist_mode else "disabled")
        self.top_entry.configure(state="normal" if artist_mode else "disabled")
        self.playlist_entry.configure(state="disabled" if artist_mode else "normal")

    def _refresh_env_files(self) -> None:
        files = config.discover_env_files(".")
        current = self.env_combo.get()
        self.env_combo["values"] = files
        if current in files:
            self.env_combo.set(current)
        elif files:
            self.env_combo.set(files[0])
        else:
            self.env_combo.set("")
            self._alert(
                "showwarning",
                "Nenhum arquivo .env encontrado nesta pasta. Copie .env.example para .env "
                "e preencha suas credenciais do Spotify Developer Dashboard antes de usar.",
            )

    # ------------------------------------------------------- ações ----

    def _on_preview(self) -> None:
        try:
            self._start_run(dry_run=True)
        except Exception as e:  # noqa: BLE001 — nunca deixar o clique morrer em silêncio
            self._alert("showerror", f"Erro ao iniciar a pré-visualização: {e}")

    def _on_create(self) -> None:
        try:
            name = self._last_result.default_name if self._last_result else "esta playlist"
            self.root.lift()
            confirmed = messagebox.askyesno(
                APP_TITLE,
                f"Isso vai criar uma playlist de verdade na sua conta Spotify ('{name}' "
                "ou o nome que você definiu). Confirma?",
                parent=self.root,
            )
            if not confirmed:
                return
            self._start_run(dry_run=False)
        except Exception as e:  # noqa: BLE001
            self._alert("showerror", f"Erro ao iniciar a criação: {e}")

    def _alert(self, kind: str, message: str) -> None:
        # showerror/showinfo sem 'parent' às vezes abrem sem roubar o foco no
        # Windows e ficam escondidos atrás da janela principal — parece que
        # "não fez nada". Isso força a janela principal (e o diálogo) pra
        # frente antes de mostrar.
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(10, lambda: self.root.attributes("-topmost", False))
        getattr(messagebox, kind)(APP_TITLE, message, parent=self.root)

    def _collect_params(self) -> pipeline.RunParams | None:
        env_file = self.env_combo.get()
        if not env_file:
            self._alert("showerror", "Selecione um arquivo de credenciais (.env).")
            return None

        artist_mode = self.mode.get() == "artist"
        artist = self.artist_entry.get().strip() if artist_mode else None
        playlist = self.playlist_entry.get().strip() if not artist_mode else None

        if artist_mode and not artist:
            self._alert("showerror", "Digite o nome do artista.")
            return None
        if not artist_mode and not playlist:
            self._alert("showerror", "Cole a URL ou o ID da playlist.")
            return None

        try:
            top = int(self.top_entry.get())
        except ValueError:
            top = 30

        self._env_file = env_file
        return pipeline.RunParams(
            artist=artist,
            playlist=playlist,
            top=top,
            name=self.name_entry.get().strip() or None,
            public=self.public_var.get(),
            use_getsongbpm=self.getsongbpm_var.get(),
            refresh_cache=self.refresh_cache_var.get(),
            dry_run=True,  # sobrescrito abaixo por _start_run
        )

    def _start_run(self, dry_run: bool) -> None:
        if self._worker and self._worker.is_alive():
            self.status_var.set("Ainda rodando a execução anterior — espera terminar.")
            self._alert(
                "showwarning",
                "Ainda tem uma busca/criação rodando. Espera ela terminar (olha o log e a "
                "barra de status) antes de clicar de novo.",
            )
            return
        self.status_var.set("Verificando os campos…")
        self.root.update_idletasks()
        params = self._collect_params()
        if params is None:
            self.status_var.set("Corrija os campos indicados e tente de novo.")
            return
        params.dry_run = dry_run

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        for row in self.tree.get_children():
            self.tree.delete(row)

        self.preview_btn.configure(state="disabled")
        self.create_btn.configure(state="disabled")
        self.status_var.set(
            f"Rodando… o navegador pode abrir pra você aprovar o login (até {LOGIN_TIMEOUT_SECONDS}s)"
        )

        self._worker = threading.Thread(target=self._worker_run, args=(params, self._env_file), daemon=True)
        self._worker.start()

    def _worker_run(self, params: pipeline.RunParams, env_file: str) -> None:
        writer = _QueueWriter(self._queue)
        try:
            with contextlib.redirect_stdout(writer):
                if not load_dotenv(env_file, override=True):
                    print(f"⚠ Não achei/'{env_file}' está vazio — conferindo variáveis já existentes.")
                account = config.derive_account(env_file)
                sp = _get_authorized_client(account)
                result = pipeline.run(sp, params)
            self._queue.put(("done", result))
        except (SpotifyOauthError, SpotifyException) as e:
            account = config.derive_account(env_file)
            msg = errors.describe_error(e, env_file=env_file, cache_path=config.cache_path_for(account))
            self._queue.put(("error", msg))
        except Exception as e:  # noqa: BLE001 — GUI: qualquer coisa vira mensagem, nunca crash
            self._queue.put(("error", f"⛔ Erro inesperado: {e}"))

    def _poll_queue(self) -> None:
        # try/finally é de propósito: se QUALQUER coisa aqui dentro (inclusive
        # dentro de _on_run_done/_on_run_error) levantar uma exceção não
        # prevista, o `self.root.after(...)` no fim NUNCA rodaria — e sem
        # ele, a fila para de ser lida pra sempre e a janela para de reagir
        # a qualquer clique daí em diante, silenciosamente. Já foi assim
        # antes desse fix; agora um erro aqui vira log, não trava a GUI.
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", payload)
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "done":
                    self._on_run_done(payload)
                elif kind == "error":
                    self._on_run_error(payload)
        except queue.Empty:
            pass
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"Erro interno ao atualizar a tela: {e}")
        finally:
            self.root.after(100, self._poll_queue)

    def _on_run_done(self, result: pipeline.RunResult) -> None:
        self._last_result = result
        self.tree.delete(*self.tree.get_children())
        prev_tempo = None
        for i, t in enumerate(result.ordered_tracks, 1):
            bpm = f"{t.tempo:.0f}" if t.tempo else "?"
            tom = self._camelot(t) or "?"
            note = ""
            if prev_tempo and t.tempo:
                delta = abs(prev_tempo - t.tempo)
                note = "mix direto" if delta <= 3 else ("suave" if delta <= 10 else "salto de BPM")
            self.tree.insert("", "end", values=(i, t.name, t.artists, bpm, tom, note))
            prev_tempo = t.tempo or prev_tempo

        self.preview_btn.configure(state="normal")
        if result.playlist_url:
            self.status_var.set(f"✅ Playlist criada: {result.playlist_url}")
            self.create_btn.configure(state="disabled")
            self._alert("showinfo", f"Playlist criada!\n\n{result.playlist_url}")
        else:
            self.status_var.set(f"Pré-visualização pronta — {len(result.ordered_tracks)} faixas.")
            self.create_btn.configure(state="normal")

    def _on_run_error(self, message: str) -> None:
        self.preview_btn.configure(state="normal")
        self.create_btn.configure(state="disabled")
        self.status_var.set("Erro — veja o log.")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "\n" + message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._alert("showerror", message)

    @staticmethod
    def _camelot(track) -> str | None:
        if track.key is None or track.mode is None:
            return None
        return key_mode_to_camelot(track.key, track.mode)


def main() -> None:
    root = tk.Tk()
    FlowlistGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
