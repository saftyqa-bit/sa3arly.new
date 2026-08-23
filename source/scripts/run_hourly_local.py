from __future__ import annotations

import json

from app.hourly import create_refresh_run

if __name__ == "__main__":
    print(json.dumps(create_refresh_run("local-script"), ensure_ascii=False, indent=2))
