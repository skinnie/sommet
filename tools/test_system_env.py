import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import system_env  # noqa: E402

system_env.selftest()
print("ok")
