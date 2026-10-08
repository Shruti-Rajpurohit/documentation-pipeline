from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.schemas import ErrorResponse, ValidationErrorResponse, ValidationIssue
from app.db.database import close_db, init_db


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
	await init_db()
	try:
		yield
	finally:
		await close_db()


app = FastAPI(
	title="Automated Documentation Pipeline",
	version="1.0.0",
	lifespan=lifespan,
)
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
	response = ValidationErrorResponse(
		detail="Request validation failed",
		errors=[
			ValidationIssue(location=tuple(item["loc"]), message=item["msg"], type=item["type"])
			for item in error.errors()
		],
	)
	return JSONResponse(
		status_code=422,
		content=response.model_dump(mode="json"),
	)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
	detail = error.detail if isinstance(error.detail, str) else "Request could not be completed"
	return JSONResponse(
		status_code=error.status_code,
		content=ErrorResponse(detail=detail).model_dump(mode="json"),
		headers=error.headers,
	)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
	logger.exception("Unhandled API error", exc_info=error)
	return JSONResponse(
		status_code=500,
		content=ErrorResponse(detail="Internal server error").model_dump(mode="json"),
	)