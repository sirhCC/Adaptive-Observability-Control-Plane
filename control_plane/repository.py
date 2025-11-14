"""Repository layer for database operations."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from control_plane.models import PolicyModel, SignalModel, PolicyAuditLog


class PolicyRepository:
    """Repository for policy operations."""
    
    @staticmethod
    async def get_current_policy(db: AsyncSession) -> Optional[PolicyModel]:
        """Get the most recently updated policy."""
        result = await db.execute(
            select(PolicyModel).order_by(PolicyModel.updated_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def create_policy(
        db: AsyncSession,
        policy_id: str,
        rules: List[Dict[str, Any]],
        description: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> PolicyModel:
        """Create a new policy."""
        policy = PolicyModel(
            id=policy_id,
            description=description,
            rules=rules,
            version=1,
        )
        db.add(policy)
        
        # Add audit log
        audit = PolicyAuditLog(
            policy_id=policy_id,
            action="create",
            changed_by=changed_by,
            new_version=1,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(audit)
        
        await db.flush()
        logger.info(f"Created policy: {policy_id}")
        return policy
    
    @staticmethod
    async def update_policy(
        db: AsyncSession,
        policy_id: str,
        rules: List[Dict[str, Any]],
        description: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> PolicyModel:
        """Update an existing policy."""
        result = await db.execute(select(PolicyModel).where(PolicyModel.id == policy_id))
        policy = result.scalar_one_or_none()
        
        if policy:
            old_version = policy.version
            policy.rules = rules
            policy.description = description
            policy.version += 1
            policy.updated_at = datetime.now(timezone.utc)
            
            # Add audit log
            audit = PolicyAuditLog(
                policy_id=policy_id,
                action="update",
                changed_by=changed_by,
                old_version=old_version,
                new_version=policy.version,
                timestamp=datetime.now(timezone.utc),
            )
            db.add(audit)
            
            await db.flush()
            logger.info(f"Updated policy: {policy_id} (v{old_version} -> v{policy.version})")
        else:
            # Create if doesn't exist
            policy = await PolicyRepository.create_policy(
                db, policy_id, rules, description, changed_by
            )
        
        return policy
    
    @staticmethod
    async def get_audit_log(
        db: AsyncSession,
        policy_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[PolicyAuditLog]:
        """Get policy audit log."""
        query = select(PolicyAuditLog).order_by(PolicyAuditLog.timestamp.desc()).limit(limit)
        if policy_id:
            query = query.where(PolicyAuditLog.policy_id == policy_id)
        
        result = await db.execute(query)
        return list(result.scalars().all())


class SignalRepository:
    """Repository for signal operations."""
    
    @staticmethod
    async def add_signal(
        db: AsyncSession,
        service: str,
        environment: str,
        timestamp: datetime,
        latency_ms: Optional[float],
        error: bool,
        attrs: Dict[str, str],
    ) -> SignalModel:
        """Add a new signal."""
        signal = SignalModel(
            service=service,
            environment=environment,
            timestamp=timestamp,
            latency_ms=latency_ms,
            error=error,
            attrs=attrs,
        )
        db.add(signal)
        await db.flush()
        return signal
    
    @staticmethod
    async def get_signals(
        db: AsyncSession,
        service: str,
        environment: str,
        window_seconds: int = 300,
    ) -> List[SignalModel]:
        """Get signals for a service/environment within time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        result = await db.execute(
            select(SignalModel)
            .where(
                and_(
                    SignalModel.service == service,
                    SignalModel.environment == environment,
                    SignalModel.timestamp >= cutoff,
                )
            )
            .order_by(SignalModel.timestamp.desc())
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def prune_old_signals(
        db: AsyncSession,
        max_age_seconds: int = 300,
        batch_size: int = 1000,
    ) -> int:
        """Delete signals older than max_age_seconds."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        result = await db.execute(
            delete(SignalModel)
            .where(SignalModel.timestamp < cutoff)
            .execution_options(synchronize_session=False)
        )
        deleted = result.rowcount
        if deleted > 0:
            logger.info(f"Pruned {deleted} old signals")
        return deleted
    
    @staticmethod
    async def enforce_buffer_limit(
        db: AsyncSession,
        service: str,
        environment: str,
        max_signals: int = 10000,
    ) -> int:
        """Keep only the most recent max_signals for a service/environment."""
        # Get count
        count_result = await db.execute(
            select(SignalModel)
            .where(
                and_(
                    SignalModel.service == service,
                    SignalModel.environment == environment,
                )
            )
        )
        signals = list(count_result.scalars().all())
        
        if len(signals) <= max_signals:
            return 0
        
        # Sort by timestamp descending and get IDs to keep
        signals.sort(key=lambda s: s.timestamp, reverse=True)
        keep_ids = [s.id for s in signals[:max_signals]]
        
        # Delete older ones
        result = await db.execute(
            delete(SignalModel)
            .where(
                and_(
                    SignalModel.service == service,
                    SignalModel.environment == environment,
                    SignalModel.id.notin_(keep_ids),
                )
            )
            .execution_options(synchronize_session=False)
        )
        deleted = result.rowcount
        if deleted > 0:
            logger.info(f"Enforced buffer limit for {service}/{environment}: deleted {deleted} signals")
        return deleted
