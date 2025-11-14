"""Database models for the control plane."""
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, JSON, Text, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class PolicyModel(Base):
    """Policy configuration stored in database."""
    __tablename__ = "policies"
    
    id = Column(String(100), primary_key=True)
    description = Column(Text, nullable=True)
    rules = Column(JSON, nullable=False)  # Store rules as JSON
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = Column(Integer, nullable=False, default=1)
    
    __table_args__ = (
        Index('idx_policy_updated', 'updated_at'),
    )


class SignalModel(Base):
    """Signal data stored in database."""
    __tablename__ = "signals"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    service = Column(String(64), nullable=False, index=True)
    environment = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    latency_ms = Column(Float, nullable=True)
    error = Column(Boolean, nullable=False, default=False)
    attrs = Column(JSON, nullable=False, default=dict)
    
    __table_args__ = (
        Index('idx_service_env_ts', 'service', 'environment', 'timestamp'),
    )


class PolicyAuditLog(Base):
    """Audit log for policy changes."""
    __tablename__ = "policy_audit_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_id = Column(String(100), nullable=False, index=True)
    action = Column(String(20), nullable=False)  # 'create', 'update', 'delete'
    changed_by = Column(String(100), nullable=True)  # Future: user/service that made change
    old_version = Column(Integer, nullable=True)
    new_version = Column(Integer, nullable=True)
    changes = Column(JSON, nullable=True)  # Summary of what changed
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
