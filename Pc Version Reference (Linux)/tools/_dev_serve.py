"""Throwaway dev server for looking at the built UI against real project
data. Not part of the shipped app - avg.shell owns the real launch path.
Deletes itself from nobody's import graph; just run it directly.
"""

from pathlib import Path

import uvicorn

from avg.api.app import create_app

TOKEN = "devtoken"

app = create_app(token=TOKEN, output_root=Path(r"D:\AI_Video_Gen\output"))

if __name__ == "__main__":
    print(f"http://127.0.0.1:8756/?token={TOKEN}")
    uvicorn.run(app, host="127.0.0.1", port=8756, log_level="info")
