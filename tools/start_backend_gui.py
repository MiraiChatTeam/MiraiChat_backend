try:
    from tools.backend_gui_app import run
except ModuleNotFoundError:
    from backend_gui_app import run


if __name__ == "__main__":
    run()