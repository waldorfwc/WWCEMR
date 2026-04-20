from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import init_db
from app.routers import imports, claims, patients, denials, appeals, eob, audit
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, service_lines, claim_adjustments, service_line_adjustments

BILLING = [Depends(auth.require_group("admin", "billing"))]
ADMIN_ONLY = [Depends(auth.require_group("admin"))]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.services.fax_poller import start_scheduler
    sched = start_scheduler()
    try:
        yield
    finally:
        sched.shutdown(wait=False)


app = FastAPI(
    title="GW Migration System",
    description="Greenway PrimeSuite migration — patient charts, documents, ERA 835 payment posting, denial management.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://100.114.128.14:3000",
        "http://wwcs-mac-mini.tailb1a9cf.ts.net:3000",
        "https://gw.waldorfwomenscare.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(imports.router, prefix="/api", dependencies=BILLING)
app.include_router(claims.router, prefix="/api", dependencies=BILLING)
app.include_router(service_lines.router, prefix="/api", dependencies=BILLING)
app.include_router(claim_adjustments.router, prefix="/api", dependencies=BILLING)
app.include_router(service_line_adjustments.router, prefix="/api", dependencies=BILLING)
app.include_router(patients.router, prefix="/api")
app.include_router(denials.router, prefix="/api", dependencies=BILLING)
app.include_router(appeals.router, prefix="/api", dependencies=BILLING)
app.include_router(eob.router, prefix="/api", dependencies=BILLING)
app.include_router(audit.router, prefix="/api", dependencies=BILLING)
app.include_router(waystar.router, prefix="/api", dependencies=BILLING)
app.include_router(ar.router, prefix="/api", dependencies=BILLING)
app.include_router(documents.router, prefix="/api")
app.include_router(intake.router, prefix="/api")
app.include_router(chart.router, prefix="/api")
app.include_router(fax.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api", dependencies=BILLING)
app.include_router(fax_batch.router, prefix="/api")
app.include_router(fax_batch.log_router, prefix="/api")
app.include_router(admin_users.router, prefix="/api", dependencies=ADMIN_ONLY)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "GW Migration System"}
