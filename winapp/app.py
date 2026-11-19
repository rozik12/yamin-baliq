"""Конвертер документов для Windows: всё работает на вашем компьютере, файлы никуда не отправляются."""
import os
import shutil
import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "converter"))


def add_known_programs_to_path():
    """Находит LibreOffice, Tesseract и Pandoc в обычных папках установки и добавляет их в PATH."""
    cands = []
    for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA"))):
        cands += [Path(base) / "LibreOffice" / "program", Path(base) / "Tesseract-OCR", Path(base) / "Pandoc", Path(base) / "Programs" / "Pandoc"]
    for c in cands:
        if c.is_dir():
            os.environ["PATH"] = str(c) + os.pathsep + os.environ.get("PATH", "")


add_known_programs_to_path()
import converters  # noqa: E402

SPECIAL = {"compress": ("_сжатый", "pdf", "Сжать PDF"), "split": ("_страницы", "zip", "Разбить PDF на страницы (zip)"), "ocrpdf": ("_ocr", "pdf", "Распознать текст (PDF с поиском)")}


def target_label(t):
    return SPECIAL[t][2] if t in SPECIAL else t.upper()


def result_name(src, target, produced):
    stem = Path(src).stem
    if target in SPECIAL:
        return f"{stem}{SPECIAL[target][0]}.{SPECIAL[target][1]}"
    return f"{stem}.{produced.suffix.lstrip('.')}"


def unique(path):
    path = Path(path)
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path


def convert_one(src, target, outdir):
    """Конвертирует один файл; возвращает путь результата в outdir."""
    import tempfile
    src = Path(src)
    ext = converters.canon(src.suffix.lstrip("."))
    with tempfile.TemporaryDirectory() as wd:
        produced = converters.convert(src, ext, target, Path(wd))
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        final = unique(outdir / result_name(src, target, produced))
        shutil.copy(produced, final)
    return final


def targets_with_lo(ext):
    """[(формат, нужен_LibreOffice)]: показываем и то, что станет доступно после установки LibreOffice (старые .doc/.xls/.ppt и др.)."""
    real = converters.targets(ext)
    if converters.soffice():
        return [(t, False) for t in real]
    orig = converters.soffice
    converters.soffice = lambda: "soffice"
    try:
        full = converters.targets(ext)
    finally:
        converters.soffice = orig
    return [(t, t not in real) for t in full]


def latest_libreoffice_msi():
    """Адрес установщика последней стабильной версии LibreOffice для Windows (64 бит)."""
    import re
    import urllib.request
    base = "https://download.documentfoundation.org/libreoffice/stable/"
    html = urllib.request.urlopen(base, timeout=20).read().decode("utf-8", "replace")
    vers = sorted({tuple(map(int, v.split("."))) for v in re.findall(r'href="(\d+\.\d+\.\d+)/"', html)})
    v = ".".join(map(str, vers[-1]))
    return f"{base}{v}/win/x86_64/LibreOffice_{v}_Win_x86-64.msi"


def install_libreoffice(progress):
    """Скачивает и тихо ставит LibreOffice (Windows спросит разрешение). progress(доля 0..1, текст)."""
    import subprocess
    import tempfile
    import urllib.request
    url = latest_libreoffice_msi()
    dst = Path(tempfile.gettempdir()) / url.rsplit("/", 1)[1]
    with urllib.request.urlopen(url, timeout=60) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                progress(got / total, f"Скачиваю LibreOffice: {got >> 20} из {total >> 20} МБ")
    progress(1, "Устанавливаю LibreOffice (подтвердите разрешение Windows)…")
    subprocess.run(["msiexec", "/i", str(dst), "/passive", "/norestart"], check=True)
    add_known_programs_to_path()
    try:
        dst.unlink()
    except OSError:
        pass
    return bool(converters.soffice())


ERRORS = {"unsupported": "этот формат не поддерживается", "bad_file": "файл повреждён или не читается", "too_big": "файл слишком большой",
          "empty": "в файле нет данных"}


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Конвертер документов")
    root.geometry("640x520")
    files, out_dir = [], tk.StringVar(value=str(Path.home() / "Documents" / "Конвертер"))
    ttk.Label(root, text="Конвертер документов", font=("Segoe UI", 16, "bold")).pack(pady=(14, 0))
    ttk.Label(root, text="Всё работает на вашем компьютере, файлы никуда не отправляются").pack()
    lb = tk.Listbox(root, height=8, selectmode=tk.EXTENDED)
    lb.pack(fill="both", expand=True, padx=14, pady=10)
    row = ttk.Frame(root)
    row.pack(fill="x", padx=14)
    target = tk.StringVar()
    cb = ttk.Combobox(row, textvariable=target, state="readonly", width=34)
    status = tk.StringVar(value="Добавьте файлы и выберите, во что превратить.")
    prog = ttk.Progressbar(root, mode="determinate")
    lo_label = tk.StringVar(value="LibreOffice: " + ("есть" if converters.soffice() else "не установлен — поставится сам, когда понадобится старый формат (.doc, .xls, .ppt)"))

    def refresh_targets():
        exts = {converters.canon(Path(f).suffix.lstrip(".")) for f in files}
        if not exts:
            cb["values"] = []
            cb.target_codes = []
            target.set("")
            return
        common = None
        for e in exts:
            t = dict(targets_with_lo(e))
            common = t if common is None else {k: v and common[k] for k, v in t.items() if k in common}
        first = [x for x, _ in targets_with_lo(next(iter(exts))) if x in (common or {})]
        cb.target_codes = first
        cb.needs_lo = {x: common[x] for x in first}
        cb["values"] = [target_label(x) + (" (нужен LibreOffice)" if common[x] else "") for x in first]
        if first:
            cb.current(0)
        else:
            target.set("")
            status.set("Для такого набора файлов общих форматов нет: выберите файлы одного типа.")

    def add_files():
        for p in filedialog.askopenfilenames(title="Выберите файлы"):
            if p not in files:
                files.append(p)
                lb.insert(tk.END, p)
        refresh_targets()

    def clear():
        files.clear()
        lb.delete(0, tk.END)
        refresh_targets()
        prog["value"] = 0

    def pick_out():
        d = filedialog.askdirectory(title="Куда сохранять")
        if d:
            out_dir.set(d)

    def worker(t):
        ok, bad, last = 0, [], None
        for i, f in enumerate(list(files), 1):
            root.after(0, status.set, f"Конвертирую {i} из {len(files)}: {Path(f).name}")
            try:
                last = convert_one(f, t, out_dir.get())
                ok += 1
            except converters.ConvertError as e:
                bad.append(f"{Path(f).name}: {ERRORS.get(e.code, e.code)}")
            except Exception as e:  # noqa: BLE001
                bad.append(f"{Path(f).name}: {type(e).__name__}")
                traceback.print_exc()
            root.after(0, prog.configure, {"value": 100 * i / len(files)})
        root.after(0, finish, ok, bad, last)

    def install_then(t):
        def prog_cb(frac, text):
            root.after(0, status.set, text)
            root.after(0, prog.configure, {"value": 100 * frac})
        try:
            have = install_libreoffice(prog_cb)
        except Exception as e:  # noqa: BLE001
            have = False
            traceback.print_exc()
        if not have:
            root.after(0, lambda: (btn.config(state="normal"), messagebox.showerror("LibreOffice", "Не удалось установить LibreOffice. Скачайте и поставьте его вручную: libreoffice.org")))
            return
        root.after(0, lo_label.set, "LibreOffice: есть")
        root.after(0, refresh_targets)
        worker(t)

    def finish(ok, bad, last):
        btn.config(state="normal")
        status.set(f"Готово: {ok} из {len(files)}.")
        if bad:
            messagebox.showwarning("Не всё получилось", "\n".join(bad[:10]))
        if ok:
            try:
                os.startfile(str(Path(out_dir.get())))  # noqa: S606  (только Windows)
            except Exception:
                pass

    def go():
        if not files or not target.get():
            return messagebox.showinfo("Конвертер", "Добавьте файлы и выберите формат.")
        t = cb.target_codes[list(cb["values"]).index(target.get())]
        btn.config(state="disabled")
        prog["value"] = 0
        if cb.needs_lo.get(t) and not converters.soffice():
            if not messagebox.askyesno("Нужен LibreOffice", "Для этого формата (старые .doc, .xls, .ppt и др.) нужна бесплатная программа LibreOffice.\n"
                                       "Скачать и установить её сейчас? (около 350 МБ, нужен интернет один раз; Windows попросит разрешение)"):
                btn.config(state="normal")
                return
            threading.Thread(target=install_then, args=(t,), daemon=True).start()
        else:
            threading.Thread(target=worker, args=(t,), daemon=True).start()

    ttk.Button(row, text="Добавить файлы…", command=add_files).pack(side="left")
    ttk.Button(row, text="Очистить", command=clear).pack(side="left", padx=6)
    ttk.Label(row, text="  Во что:").pack(side="left")
    cb.pack(side="left", padx=4)
    row2 = ttk.Frame(root)
    row2.pack(fill="x", padx=14, pady=8)
    ttk.Label(row2, text="Сохранять в:").pack(side="left")
    ttk.Entry(row2, textvariable=out_dir).pack(side="left", fill="x", expand=True, padx=6)
    ttk.Button(row2, text="Изменить…", command=pick_out).pack(side="left")
    btn = ttk.Button(root, text="Конвертировать", command=go)
    btn.pack(pady=6)
    prog.pack(fill="x", padx=14)
    ttk.Label(root, textvariable=status).pack(pady=4)
    ttk.Label(root, textvariable=lo_label, foreground="#666", wraplength=600).pack(pady=(0, 8))
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--targets":  # app --targets docx: что предложит список «Во что»
        line = " ".join(f"{t}{'*' if lo else ''}" for t, lo in targets_with_lo(sys.argv[2]))
        if len(sys.argv) > 3:
            Path(sys.argv[3]).write_text(line, encoding="utf-8")
        else:
            print(line)
    elif len(sys.argv) >= 3 and sys.argv[1] == "--cli":  # проверка без окна: app --cli файл формат
        print(convert_one(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "."))
    else:
        run_gui()
