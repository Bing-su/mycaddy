from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def index(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "query": dict(request.query_params.items())})


app = Starlette(
    routes=[
        Route("/", index),
    ],
)
