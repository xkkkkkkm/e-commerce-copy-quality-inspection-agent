"""Authenticated synthetic product previews and catalog ingestion."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.admin_auth import require_admin
from db.session import get_db
from services.catalog import CatalogError
from services.simulation import DeliveryRequest, SimulationRequest, deliver_batch, generate_preview


router = APIRouter(prefix="/api/admin/simulation", tags=["模拟商品接入"],
                   dependencies=[Depends(require_admin)])


@router.post("/preview")
def preview(data: SimulationRequest, db: Session = Depends(get_db)):
    db.rollback()  # Release the completed authentication read transaction.
    return generate_preview(data)


@router.post("/deliver")
def deliver(data: DeliveryRequest, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    db.rollback()
    try:
        return deliver_batch(db, data, actor)
    except CatalogError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
