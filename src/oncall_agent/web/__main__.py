"""Start the chat: python -m oncall_agent.web, then open http://127.0.0.1:8765"""

import uvicorn

from oncall_agent.web.app import create_app

HOST = "127.0.0.1"  # only this computer: the page has no login
PORT = 8765


def main() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
