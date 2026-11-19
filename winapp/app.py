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
    lo = "есть" if converters.soffice() else "не найден (без него не работают .doc, .ppt, .odt, .rtf и др.; бесплатно: libreoffice.org)"

    def refresh_targets():
        exts = {converters.canon(Path(f).suffix.lstrip(".")) for f in files}
        if not exts:
            cb["values"] = []
            target.set("")
            return
        common = None
        for e in exts:
            t = set(converters.targets(e))
            common = t if common is None else common & t
        order = [x for x in converters.targets(next(iter(exts))) if x in (common or set())]
        cb["values"] = [target_label(x) for x in order]
        cb.target_codes = order
        if order:
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

    def worker(codes_label):
        codes = getattr(cb, "target_codes", [])
        t = codes[list(cb["values"]).index(codes_label)]
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
        btn.config(state="disabled")
        prog["value"] = 0
        threading.Thread(target=worker, args=(target.get(),), daemon=True).start()

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
    ttk.Label(root, text=f"LibreOffice: {lo}", foreground="#666", wraplength=600).pack(pady=(0, 8))
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--cli":  # проверка без окна: app --cli файл формат
        print(convert_one(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "."))
    else:
        run_gui()
