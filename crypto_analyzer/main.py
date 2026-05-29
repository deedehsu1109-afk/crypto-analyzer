import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from database.db import init_db
from gui.main_window import App

if __name__ == "__main__":
    init_db()
    app = App()
    app.mainloop()
