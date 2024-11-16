from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class FaxDocument(BaseModel):
    id: str
    content: bytes
    received_at: datetime
    processed_at: Optional[datetime] = None
    category: Optional[str] = None

class ProcessingResult(BaseModel):
    document_id: str
    category: str
    confidence: float
    processed_at: datetime