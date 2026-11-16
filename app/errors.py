from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

class DomainError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)

class AuthError(PermissionError):
    """越权读取/授权已收回：不返回原件，只给最小错误信息。"""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)

class NotFoundError(LookupError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)

def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=422, content={"error": exc.code, "message": exc.message})

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError):
        return JSONResponse(status_code=403, content={"error": exc.code, "message": exc.message})

    @app.exception_handler(NotFoundError)
    async def not_found_error(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"error": exc.code, "message": exc.message})
