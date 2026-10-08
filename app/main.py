from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
	parser = argparse.ArgumentParser(description="Run the documentation pipeline API.")
	parser.add_argument("--host", default="127.0.0.1", help="API bind address")
	parser.add_argument("--port", type=int, default=8000, help="API port")
	parser.add_argument("--reload", action="store_true", help="Enable development auto-reload")
	args = parser.parse_args()
	uvicorn.run("app.api.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
	main()