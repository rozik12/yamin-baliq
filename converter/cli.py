"""Конвертация одним вызовом в отдельном процессе: python cli.py <файл> <из> <в> <папка>.
Печатает путь к результату; при ошибке пишет код причины в stderr и выходит с кодом 2."""
import sys

import converters

if __name__ == "__main__":
    try:
        if sys.argv[1] == "merge":  # cli.py merge <out.pdf> <файлы...>
            converters.merge_files(sys.argv[3:], sys.argv[2])
            print(sys.argv[2])
            sys.exit(0)
        src, src_ext, dst_ext, outdir = sys.argv[1:5]
        print(converters.convert(src, src_ext, dst_ext, outdir))
    except converters.ConvertError as e:
        print(f"{e.code}: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(f"bad_file: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
