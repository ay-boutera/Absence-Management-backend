import uuid

from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class Salle(Base):
    __tablename__ = "salles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)
    nom = Column(String(100), nullable=True)

    sessions = relationship("Session", back_populates="room")

    def __repr__(self) -> str:
        return f"<Salle {self.code}>"
