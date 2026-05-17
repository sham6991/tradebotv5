import tkinter as tk
from ui import TradeBotUI


def main():
    root = tk.Tk()
    app = TradeBotUI(root)
    app.run()


if __name__ == "__main__":
    main()
