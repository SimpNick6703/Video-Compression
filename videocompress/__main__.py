import sys
from pathlib import Path

# When executed as a directory (`python videocompress`), ensure parent directory is in sys.path
_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from videocompress.cli import main

if __name__ == "__main__":
    main()
