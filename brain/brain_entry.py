"""PyInstaller entry point (absolute import so the package-relative imports in __main__ work when frozen)."""

from familiar_brain.__main__ import main

if __name__ == "__main__":
    main()
