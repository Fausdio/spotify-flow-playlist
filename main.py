import sys

# Windows costuma abrir o terminal em cp1252, que não representa os
# símbolos usados nos logs (⚠ ✅ ≈). Força UTF-8 pra evitar UnicodeEncodeError.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from flowlist.cli import main

if __name__ == "__main__":
    main()
